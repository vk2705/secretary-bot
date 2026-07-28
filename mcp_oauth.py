"""Google OIDC login for mcp_server.py's remote HTTP transport.

mcp_server.py's remote deployment used to gate access with a single shared
`?key=` token (see git history / README). This module replaces that with a
real OIDC-backed login: it makes mcp_server.py act as an OAuth 2.1
Authorization Server (so MCP clients like claude.ai can register a client,
do the authorize+PKCE dance, and get a bearer token) while the actual
identity check is delegated to Google -- the `authorize` step redirects the
user's browser to Google, and `handle_google_callback` verifies Google's
signed ID token before minting anything.

Once a Google login succeeds, the resulting access token is a short-lived
HS256 JWT this server signs itself (not a Google token), carrying the
Telegram chat_id resolved from the MCP_OIDC_USERS mapping as `sub`. That's
what mcp_server.py's `_check_access()` checks on every tool call, so a
logged-in caller can only touch their own chat_id's data (or every chat_id,
for entries mapped to the special "*" admin value).

All OAuth state (pending logins, issued codes, refresh tokens) lives in
memory only -- consistent with this project's existing stance on ephemeral
runtime state (APScheduler jobs, the auto-generated MASTER_KEY fallback in
bot.py). A server restart just forces callers to log in again; nothing
sensitive is persisted to disk.
"""

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx
import jwt
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

_log = logging.getLogger("secretary_mcp.oauth")

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}

ACCESS_TOKEN_TTL = 3600  # seconds
AUTH_CODE_TTL = 300  # seconds
LOGIN_TTL = 600  # seconds a pending Google login stays valid

ADMIN_SCOPE = "*"  # MCP_OIDC_USERS value granting access to every chat_id


def load_user_map() -> dict[str, str]:
    """email (lowercased) -> chat_id (or "*" for admin), from MCP_OIDC_USERS."""
    raw = os.environ.get("MCP_OIDC_USERS", "")
    if not raw:
        return {}
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"MCP_OIDC_USERS is not valid JSON: {e}") from e
    return {str(k).lower(): str(v) for k, v in mapping.items()}


@dataclass
class _PendingLogin:
    client_id: str
    params: AuthorizationParams
    created_at: float = field(default_factory=time.time)


class GoogleOAuthProxyProvider:
    """OAuthAuthorizationServerProvider that delegates the login step to Google.

    Implements the protocol from mcp.server.auth.provider structurally
    (duck-typed, per that module's Protocol definition) rather than by
    inheritance.
    """

    def __init__(self, callback_url: str):
        self.callback_url = callback_url
        self.google_client_id = os.environ["GOOGLE_OIDC_CLIENT_ID"]
        self.google_client_secret = os.environ["GOOGLE_OIDC_CLIENT_SECRET"]
        self.jwt_secret = os.environ.get("MCP_OIDC_JWT_SECRET") or secrets.token_hex(32)
        if not os.environ.get("MCP_OIDC_JWT_SECRET"):
            _log.warning(
                "MCP_OIDC_JWT_SECRET not set; using an ephemeral secret for this "
                "process. Every access/refresh token issued will stop validating "
                "the moment the server restarts, forcing callers to log in again."
            )
        self.user_map = load_user_map()
        if not self.user_map:
            _log.warning("MCP_OIDC_USERS is empty -- no Google account will be able to log in.")
        self._jwks_client = jwt.PyJWKClient(GOOGLE_JWKS_URL)

        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._pending: dict[str, _PendingLogin] = {}        # google `state` -> pending login
        self._auth_codes: dict[str, AuthorizationCode] = {}  # our code -> AuthorizationCode
        self._refresh_tokens: dict[str, RefreshToken] = {}   # our refresh token -> RefreshToken

    # ---- dynamic client registration (RFC 7591) ----

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._clients[client_info.client_id] = client_info

    # ---- authorization: send the MCP client's browser to Google ----

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        self._expire_pending()
        state = secrets.token_urlsafe(32)
        self._pending[state] = _PendingLogin(client_id=client.client_id, params=params)
        query = {
            "client_id": self.google_client_id,
            "redirect_uri": self.callback_url,
            "response_type": "code",
            "scope": "openid email",
            "state": state,
            "prompt": "select_account",
        }
        return f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(query)}"

    def _expire_pending(self) -> None:
        cutoff = time.time() - LOGIN_TTL
        for state in [s for s, p in self._pending.items() if p.created_at < cutoff]:
            del self._pending[state]

    async def handle_google_callback(self, request: Request) -> Response:
        """Unauthenticated route (registered via @mcp.custom_route) that Google
        redirects the user's browser back to after they approve/deny login."""
        state = request.query_params.get("state", "")
        pending = self._pending.pop(state, None)
        if pending is None:
            return PlainTextResponse(
                "Login session expired or was already used. Please retry from the MCP client.",
                status_code=400,
            )

        error = request.query_params.get("error")
        if error:
            return RedirectResponse(
                construct_redirect_uri(str(pending.params.redirect_uri), error=error, state=pending.params.state),
                status_code=302,
            )

        code = request.query_params.get("code")
        if not code:
            return PlainTextResponse("Google did not return an authorization code.", status_code=400)

        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(
                GOOGLE_TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": self.google_client_id,
                    "client_secret": self.google_client_secret,
                    "redirect_uri": self.callback_url,
                    "grant_type": "authorization_code",
                },
            )
        if resp.status_code != 200:
            _log.warning("Google token exchange failed: %s %s", resp.status_code, resp.text)
            return PlainTextResponse("Google login failed.", status_code=502)

        id_token = resp.json().get("id_token", "")
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(id_token)
            claims = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.google_client_id,
            )
        except jwt.PyJWTError as e:
            _log.warning("Google id_token verification failed: %s", e)
            return PlainTextResponse("Could not verify Google identity.", status_code=401)

        if claims.get("iss") not in GOOGLE_ISSUERS or not claims.get("email_verified"):
            return PlainTextResponse("Google account email is not verified.", status_code=401)

        email = str(claims.get("email", "")).lower()
        chat_id = self.user_map.get(email)
        if chat_id is None:
            _log.info("Rejected MCP login from unrecognized Google account %s", email)
            return PlainTextResponse(
                f"'{email}' is not authorized to use this MCP server. "
                "Ask the owner to add it to MCP_OIDC_USERS.",
                status_code=403,
            )

        our_code = secrets.token_urlsafe(32)
        self._auth_codes[our_code] = AuthorizationCode(
            code=our_code,
            scopes=pending.params.scopes or [],
            expires_at=time.time() + AUTH_CODE_TTL,
            client_id=pending.client_id,
            code_challenge=pending.params.code_challenge,
            redirect_uri=pending.params.redirect_uri,
            redirect_uri_provided_explicitly=pending.params.redirect_uri_provided_explicitly,
            resource=pending.params.resource,
            subject=chat_id,
        )
        _log.info("Google login OK for %s -> chat_id=%s", email, "(admin)" if chat_id == ADMIN_SCOPE else chat_id)
        return RedirectResponse(
            construct_redirect_uri(str(pending.params.redirect_uri), code=our_code, state=pending.params.state),
            status_code=302,
        )

    # ---- authorization code / token exchange ----

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return self._auth_codes.get(authorization_code)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        del self._auth_codes[authorization_code.code]
        return self._issue_tokens(
            client.client_id, authorization_code.scopes, authorization_code.subject, authorization_code.resource
        )

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        rt = self._refresh_tokens.get(refresh_token)
        if rt is None or rt.client_id != client.client_id:
            return None
        return rt

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        del self._refresh_tokens[refresh_token.token]
        return self._issue_tokens(client.client_id, scopes or refresh_token.scopes, refresh_token.subject, None)

    def _issue_tokens(self, client_id: str, scopes: list[str], subject: str | None, resource: str | None) -> OAuthToken:
        now = int(time.time())
        access_token = jwt.encode(
            {
                "sub": subject,
                "client_id": client_id,
                "scope": " ".join(scopes),
                "resource": resource,
                "iat": now,
                "exp": now + ACCESS_TOKEN_TTL,
            },
            self.jwt_secret,
            algorithm="HS256",
        )
        refresh_token = secrets.token_urlsafe(32)
        self._refresh_tokens[refresh_token] = RefreshToken(
            token=refresh_token, client_id=client_id, scopes=scopes, subject=subject
        )
        return OAuthToken(
            access_token=access_token,
            expires_in=ACCESS_TOKEN_TTL,
            scope=" ".join(scopes) or None,
            refresh_token=refresh_token,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        try:
            claims = jwt.decode(token, self.jwt_secret, algorithms=["HS256"])
        except jwt.PyJWTError:
            return None
        return AccessToken(
            token=token,
            client_id=claims.get("client_id", ""),
            scopes=(claims.get("scope") or "").split(),
            expires_at=claims.get("exp"),
            resource=claims.get("resource"),
            subject=claims.get("sub"),
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        # Access tokens are stateless JWTs (no server-side record to drop); just
        # kill the refresh token so the session can't be renewed.
        self._refresh_tokens.pop(getattr(token, "token", None), None)
