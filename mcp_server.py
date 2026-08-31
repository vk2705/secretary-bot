#!/usr/bin/env python3
"""
MCP server for secretary-bot.

Exposes tasks, habits, and trackers from state.json, plus notes, journal,
and profile/episodic memory from bot_memory.db (SQLite), as MCP tools so
Claude Desktop / Claude Code can read and write any user's data directly.

Run with:
    python3 mcp_server.py          # stdio transport (for Claude Desktop)
    mcp run mcp_server.py          # same, via mcp CLI
"""

import calendar
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx
import jwt
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
_log = logging.getLogger("secretary_mcp")

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

STATE_FILE = Path(os.environ.get("BOT_STATE_FILE", Path(__file__).parent / "state.json"))
DB_FILE = Path(os.environ.get("BOT_DB_FILE", Path(__file__).parent / "bot_memory.db"))

# Public hostname for the remote HTTPS deployment (see "remote HTTPS mode"
# below). Drives the DNS-rebinding Host-header check, the OAuth issuer/
# resource URLs, and the Google redirect_uri. Irrelevant to the default
# stdio transport used by Claude Desktop/Code.
_REMOTE_DOMAIN = os.environ.get("MCP_REMOTE_DOMAIN", "")


def _db() -> sqlite3.Connection:
    """Thread-local SQLite connection, same schema/mode as bot.py's _db()."""
    con = sqlite3.connect(DB_FILE, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row
    return con


# ─────────────── Google OIDC + OAuth Authorization Server (Milestone B) ───────────────
#
# This server is its own thin OAuth 2.1 Authorization Server for the MCP
# resource (dynamic client registration, /authorize, /token, /revoke — all
# auto-wired by the `mcp` SDK once `auth=`/`auth_server_provider=` are set
# below). Google is used only to verify *identity*; it never sees the MCP
# resource and issues no tokens we rely on.
#
# Two independent Google round-trips share the single /oauth/google/callback
# route below, disambiguated by the `state` prefix:
#
#   "link:<code>"   — user-initiated, via /link in Telegram -> /link/<code>.
#                      Binds a verified (google_sub, email) to a chat_id in
#                      mcp_identity. Never touches claude.ai's OAuth dance.
#   "authz:<id>"    — claude.ai-initiated, via its own /authorize request
#                      (parked in mcp_pending_authorize while we round-trip
#                      through Google). Resolves the caller's chat_id by
#                      looking up the already-bound mcp_identity row, mints
#                      our own authorization code, and redirects back to
#                      claude.ai's redirect_uri.
#
# All tables (mcp_link_codes, mcp_identity, mcp_oauth_clients,
# mcp_pending_authorize, mcp_auth_codes, mcp_tokens) are created by bot.py's
# _init_db() — this module never creates schema, matching its existing
# read/write-only relationship to bot_memory.db.

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ["https://accounts.google.com", "accounts.google.com"]

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
_GOOGLE_CALLBACK_URL = f"https://{_REMOTE_DOMAIN}/oauth/google/callback" if _REMOTE_DOMAIN else ""

_google_jwks_client: "jwt.PyJWKClient | None" = None  # lazy: no network fetch at import time


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── /link code + verified-identity binding (also used by bot.py's db_create_link_code) ──

def _link_code_valid(code: str) -> bool:
    now = datetime.utcnow().isoformat()
    with _db() as con:
        row = con.execute(
            "SELECT expires_at, used_at FROM mcp_link_codes WHERE code=?", (code,)
        ).fetchone()
    return row is not None and row["used_at"] is None and row["expires_at"] >= now


def _link_consume_code(code: str) -> str | None:
    """Validate and consume a one-time /link code. Returns the bound chat_id,
    or None if the code is unknown, expired, or already used."""
    now = datetime.utcnow().isoformat()
    with _db() as con:
        row = con.execute(
            "SELECT chat_id, expires_at, used_at FROM mcp_link_codes WHERE code=?", (code,)
        ).fetchone()
        if row is None or row["used_at"] is not None or row["expires_at"] < now:
            return None
        con.execute("UPDATE mcp_link_codes SET used_at=? WHERE code=?", (now, code))
        return row["chat_id"]


def _link_bind_identity(google_sub: str, email: str | None, chat_id: str) -> None:
    with _db() as con:
        con.execute(
            "INSERT INTO mcp_identity(google_sub, email, chat_id, linked_at) VALUES(?,?,?,?) "
            "ON CONFLICT(google_sub) DO UPDATE SET "
            "email=excluded.email, chat_id=excluded.chat_id, linked_at=excluded.linked_at",
            (google_sub, email, str(chat_id), datetime.utcnow().isoformat())
        )


def _link_lookup_chat_id(google_sub: str) -> str | None:
    with _db() as con:
        row = con.execute(
            "SELECT chat_id FROM mcp_identity WHERE google_sub=?", (google_sub,)
        ).fetchone()
    return row["chat_id"] if row else None


# ── Google OIDC calls ──

def _google_authorize_url(state: str) -> str:
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _GOOGLE_CALLBACK_URL,
        "response_type": "code",
        "scope": "openid email",
        "state": state,
    }
    return f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"


async def _google_exchange_code(code: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(GOOGLE_TOKEN_ENDPOINT, data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": _GOOGLE_CALLBACK_URL,
            "grant_type": "authorization_code",
        })
        resp.raise_for_status()
        return resp.json()


def _google_verify_id_token(id_token: str) -> dict:
    """Verify signature, audience and issuer; return the decoded claims
    (in particular `sub`, `email`). Raises on any verification failure —
    callers must treat that as a failed login, never fall back to trusting
    unverified claims (AUTH-02)."""
    global _google_jwks_client
    if _google_jwks_client is None:
        _google_jwks_client = jwt.PyJWKClient(GOOGLE_JWKS_URL)
    signing_key = _google_jwks_client.get_signing_key_from_jwt(id_token)
    return jwt.decode(
        id_token, signing_key.key, algorithms=["RS256"],
        audience=GOOGLE_CLIENT_ID, issuer=GOOGLE_ISSUERS,
    )


# ── our own OAuth Authorization Server storage ──

def _oauth_get_client(client_id: str) -> OAuthClientInformationFull | None:
    with _db() as con:
        row = con.execute(
            "SELECT info_json FROM mcp_oauth_clients WHERE client_id=?", (client_id,)
        ).fetchone()
    return OAuthClientInformationFull.model_validate_json(row["info_json"]) if row else None


def _oauth_save_client(info: OAuthClientInformationFull) -> None:
    with _db() as con:
        con.execute(
            "INSERT INTO mcp_oauth_clients(client_id, info_json, created_at) VALUES(?,?,?)",
            (info.client_id, info.model_dump_json(), datetime.utcnow().isoformat())
        )


def _pending_authorize_save(state: str, client_id: str, params: AuthorizationParams, ttl_seconds: int = 600) -> None:
    with _db() as con:
        con.execute(
            "INSERT INTO mcp_pending_authorize(state, client_id, scopes, code_challenge, redirect_uri, "
            "redirect_uri_explicit, resource, client_state, created_at, expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                state, client_id, " ".join(params.scopes or []), params.code_challenge,
                str(params.redirect_uri), int(params.redirect_uri_provided_explicitly), params.resource,
                params.state, datetime.utcnow().isoformat(), time.time() + ttl_seconds,
            )
        )


def _pending_authorize_load(state: str) -> dict | None:
    """Single-use: deletes the row regardless of outcome."""
    with _db() as con:
        row = con.execute("SELECT * FROM mcp_pending_authorize WHERE state=?", (state,)).fetchone()
        if row is not None:
            con.execute("DELETE FROM mcp_pending_authorize WHERE state=?", (state,))
    if row is None or row["expires_at"] < time.time():
        return None
    return dict(row)


def _auth_code_save(
    code: str, client_id: str, chat_id: str, scopes: list[str], code_challenge: str,
    redirect_uri: str, redirect_uri_explicit: bool, resource: str | None, ttl_seconds: int = 120,
) -> None:
    with _db() as con:
        con.execute(
            "INSERT INTO mcp_auth_codes(code, client_id, chat_id, scopes, code_challenge, redirect_uri, "
            "redirect_uri_explicit, resource, expires_at, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                code, client_id, str(chat_id), " ".join(scopes), code_challenge, redirect_uri,
                int(redirect_uri_explicit), resource, time.time() + ttl_seconds, datetime.utcnow().isoformat(),
            )
        )


def _auth_code_load(code: str) -> dict | None:
    with _db() as con:
        row = con.execute("SELECT * FROM mcp_auth_codes WHERE code=?", (code,)).fetchone()
    return dict(row) if row else None


def _auth_code_delete(code: str) -> None:
    with _db() as con:
        con.execute("DELETE FROM mcp_auth_codes WHERE code=?", (code,))


def _auth_code_consume(code: str, client_id: str) -> bool:
    with _db() as con:
        con.execute("BEGIN IMMEDIATE")
        cursor = con.execute(
            "DELETE FROM mcp_auth_codes WHERE code=? AND client_id=? AND expires_at>=?",
            (code, client_id, time.time()),
        )
    return cursor.rowcount == 1


def _token_save(kind: str, token: str, client_id: str, chat_id: str, scopes: list[str], expires_at: float | None) -> None:
    with _db() as con:
        con.execute(
            "INSERT INTO mcp_tokens(token_hash, kind, client_id, chat_id, scopes, expires_at, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (_hash_token(token), kind, client_id, str(chat_id), " ".join(scopes), expires_at, datetime.utcnow().isoformat())
        )


def _token_load(kind: str, token: str) -> dict | None:
    with _db() as con:
        row = con.execute(
            "SELECT * FROM mcp_tokens WHERE token_hash=? AND kind=?", (_hash_token(token), kind)
        ).fetchone()
    return dict(row) if row else None


def _token_delete(kind: str, token: str) -> None:
    with _db() as con:
        con.execute("DELETE FROM mcp_tokens WHERE token_hash=? AND kind=?", (_hash_token(token), kind))


def _refresh_token_consume(token: str, client_id: str) -> bool:
    with _db() as con:
        con.execute("BEGIN IMMEDIATE")
        cursor = con.execute(
            "DELETE FROM mcp_tokens WHERE token_hash=? AND kind='refresh' AND client_id=?",
            (_hash_token(token), client_id),
        )
    return cursor.rowcount == 1


class SecretaryOAuthProvider(OAuthAuthorizationServerProvider):
    """Our own Authorization Server. Google establishes identity; this class
    binds it to a chat_id (via mcp_identity, established once through
    /link) and issues opaque bearer tokens stored as sha256 hashes."""

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return _oauth_get_client(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        _oauth_save_client(client_info)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        state = f"authz:{secrets.token_urlsafe(24)}"
        _pending_authorize_save(state, client.client_id, params)
        return _google_authorize_url(state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        row = _auth_code_load(authorization_code)
        if row is None or row["client_id"] != client.client_id:
            return None
        return AuthorizationCode(
            code=row["code"],
            scopes=row["scopes"].split(),
            expires_at=row["expires_at"],
            client_id=row["client_id"],
            code_challenge=row["code_challenge"],
            redirect_uri=row["redirect_uri"],
            redirect_uri_provided_explicitly=bool(row["redirect_uri_explicit"]),
            resource=row["resource"],
            subject=row["chat_id"],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if not _auth_code_consume(authorization_code.code, client.client_id):
            raise TokenError("invalid_grant", "Authorization code is expired or already used")
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        expires_in = 3600
        _token_save("access", access_token, client.client_id, authorization_code.subject,
                    authorization_code.scopes, time.time() + expires_in)
        _token_save("refresh", refresh_token, client.client_id, authorization_code.subject,
                    authorization_code.scopes, None)  # no expiry — revoked only by explicit action
        return OAuthToken(
            access_token=access_token, token_type="Bearer", expires_in=expires_in,
            scope=" ".join(authorization_code.scopes), refresh_token=refresh_token,
        )

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        row = _token_load("refresh", refresh_token)
        if row is None or row["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token, client_id=row["client_id"], scopes=row["scopes"].split(),
            expires_at=None, subject=row["chat_id"],
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        if not _refresh_token_consume(refresh_token.token, client.client_id):
            raise TokenError("invalid_grant", "Refresh token is expired or already used")
        new_access = secrets.token_urlsafe(32)
        new_refresh = secrets.token_urlsafe(32)
        expires_in = 3600
        _token_save("access", new_access, client.client_id, refresh_token.subject, scopes, time.time() + expires_in)
        _token_save("refresh", new_refresh, client.client_id, refresh_token.subject, scopes, None)
        return OAuthToken(
            access_token=new_access, token_type="Bearer", expires_in=expires_in,
            scope=" ".join(scopes), refresh_token=new_refresh,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = _token_load("access", token)
        if row is None:
            return None
        if row["expires_at"] and row["expires_at"] < time.time():
            return None
        return AccessToken(
            token=token, client_id=row["client_id"], scopes=row["scopes"].split(),
            expires_at=int(row["expires_at"]) if row["expires_at"] else None, subject=row["chat_id"],
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        kind = "refresh" if isinstance(token, RefreshToken) else "access"
        _token_delete(kind, token.token)


_oauth_provider = (
    SecretaryOAuthProvider()
    if (_REMOTE_DOMAIN and GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
    else None
)
if _REMOTE_DOMAIN and not _oauth_provider:
    _log.warning(
        "MCP_REMOTE_DOMAIN is set but GOOGLE_OAUTH_CLIENT_ID/GOOGLE_OAUTH_CLIENT_SECRET are not — "
        "remote transport will refuse to start until both are configured (Milestone B)."
    )


mcp = FastMCP(
    "secretary-bot",
    instructions=(
        "Access a user's secretary-bot data: tasks, habits, trackers (state.json), "
        "and notes, journal entries, and profile/episodic memory (SQLite). "
        "Always identify the user by their numeric Telegram chat_id. "
        "Use list_users() first if you don't know the chat_id."
    ),
    transport_security=TransportSecuritySettings(
        allowed_hosts=["127.0.0.1:*", "localhost:*", f"{_REMOTE_DOMAIN}:*", _REMOTE_DOMAIN],
        allowed_origins=["https://claude.ai"],
    ) if _REMOTE_DOMAIN else None,
    auth=AuthSettings(
        issuer_url=f"https://{_REMOTE_DOMAIN}",
        resource_server_url=f"https://{_REMOTE_DOMAIN}/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True, default_scopes=["secretary"]),
        revocation_options=RevocationOptions(enabled=True),
    ) if _oauth_provider else None,
    auth_server_provider=_oauth_provider,
)


# ─────────────── Google OAuth HTTP routes (Milestone B) ───────────────
#
# Both routes are no-ops (never reachable) under stdio transport or when
# _oauth_provider is None — they're only wired for completeness so the
# module always imports cleanly regardless of transport.

@mcp.custom_route("/link/{code}", methods=["GET"])
async def link_start(request: Request) -> Response:
    """Landing point for the URL /link sends in Telegram. Immediately
    redirects into Google sign-in; the callback below does the actual
    verification and binding."""
    code = request.path_params["code"]
    if not _oauth_provider:
        return PlainTextResponse("MCP OAuth is not configured on this server.", status_code=503)
    if not _link_code_valid(code):
        return PlainTextResponse(
            "This link has expired or was already used. Send /link to the bot again.", status_code=400
        )
    return RedirectResponse(_google_authorize_url(f"link:{code}"))


@mcp.custom_route("/oauth/google/callback", methods=["GET"])
async def google_callback(request: Request) -> Response:
    """Single callback for both Google round-trips (see the module docstring
    above the OAuth section for the link: vs authz: state prefixes)."""
    if not _oauth_provider:
        return PlainTextResponse("MCP OAuth is not configured on this server.", status_code=503)

    error = request.query_params.get("error")
    if error:
        return PlainTextResponse(f"Google sign-in failed: {error}", status_code=400)
    code = request.query_params.get("code")
    state = request.query_params.get("state", "")
    if not code or not state:
        return PlainTextResponse("Missing code/state.", status_code=400)

    try:
        google_tokens = await _google_exchange_code(code)
        claims = _google_verify_id_token(google_tokens["id_token"])
    except Exception:
        _log.exception("Google OIDC exchange/verification failed")
        return PlainTextResponse("Google sign-in failed. Try again from the bot.", status_code=502)

    sub = claims["sub"]
    email = claims.get("email")

    if state.startswith("link:"):
        link_code = state[len("link:"):]
        chat_id = _link_consume_code(link_code)
        if chat_id is None:
            return PlainTextResponse(
                "This link has expired or was already used. Send /link to the bot again.", status_code=400
            )
        _link_bind_identity(sub, email, chat_id)
        return HTMLResponse("<h1>✅ Linked</h1><p>Claude can now access your secretary-bot data. "
                             "You can close this tab.</p>")

    if state.startswith("authz:"):
        pending = _pending_authorize_load(state)
        if pending is None:
            return PlainTextResponse(
                "This sign-in attempt expired. Go back to Claude and reconnect.", status_code=400
            )
        chat_id = _link_lookup_chat_id(sub)
        if chat_id is None:
            return PlainTextResponse(
                "This Google account isn't connected to a Telegram account yet. "
                "Message the bot /link, tap the link it sends you, then try connecting Claude again.",
                status_code=403,
            )
        auth_code = secrets.token_urlsafe(32)
        _auth_code_save(
            auth_code, pending["client_id"], chat_id, pending["scopes"].split(),
            pending["code_challenge"], pending["redirect_uri"], bool(pending["redirect_uri_explicit"]),
            pending["resource"],
        )
        redirect_uri = construct_redirect_uri(pending["redirect_uri"], code=auth_code, state=pending["client_state"])
        return RedirectResponse(redirect_uri)

    return PlainTextResponse("Unrecognized sign-in state.", status_code=400)


# ─────────────── helpers ───────────────

def _load() -> dict:
    if not STATE_FILE.exists():
        return {"users": {}}
    with open(STATE_FILE) as f:
        return json.load(f)


def _save(state: dict) -> None:
    file_descriptor, temporary = tempfile.mkstemp(
        dir=STATE_FILE.parent, prefix=".state-mcp-", suffix=".tmp"
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        temporary_path.replace(STATE_FILE)
    finally:
        temporary_path.unlink(missing_ok=True)


def _user(state: dict, chat_id: str) -> dict | None:
    return state.get("users", {}).get(str(chat_id))


def _require_user(state: dict, chat_id: str) -> dict:
    u = _user(state, chat_id)
    if u is None:
        raise ValueError(f"User {chat_id} not found. Use list_users() to see registered users.")
    return u


def _task_text(task) -> str:
    return task["text"] if isinstance(task, dict) else task


def _task_due(task) -> str | None:
    return task.get("due") if isinstance(task, dict) else None


def _habit_streak(completions: list) -> int:
    if not completions:
        return 0
    today = date.today()
    done = set(completions)
    streak, check = 0, today
    while check.isoformat() in done:
        streak += 1
        check -= timedelta(days=1)
    return streak


def _fmt_task(task, n: int) -> dict:
    return {
        "number": n,
        "text": _task_text(task),
        "due": _task_due(task),
        "recur": task.get("recur") if isinstance(task, dict) else None,
    }


def _timezone(chat_id: str, fallback: str) -> str:
    """Timezone lives in state.json but is overridden by SQLite user_prefs,
    same precedence as bot.py's get_user() — state.json can go stale."""
    with _db() as con:
        row = con.execute(
            "SELECT value FROM user_prefs WHERE chat_id=? AND key='timezone'",
            (str(chat_id),)
        ).fetchone()
    return row["value"] if row else fallback


# ─────────────── tools: user discovery ───────────────

def _list_users_impl() -> list[dict]:
    """List all registered bot users with basic info. stdio-only (trusted
    local operator) — never exposed on remote: AUTH-03 forbids a directory
    of every other user's chat_id/timezone/activity to an authenticated
    caller who only owns one of them."""
    state = _load()
    result = []
    for cid, u in state.get("users", {}).items():
        activity = sorted(u.get("activity_days", []))
        result.append({
            "chat_id": cid,
            "timezone": _timezone(cid, u.get("timezone", "UTC")),
            "subscribed": u.get("checkin_enabled", False),
            "tasks": len(u.get("tasks", [])),
            "habits": len(u.get("habits", {})),
            "journal_entries": len(u.get("journal", [])),
            "streak_days": len(activity),
            "last_active": activity[-1] if activity else None,
            "context_snippet": (u.get("context") or "")[:80],
        })
    return result


# ─────────────── tools: tasks ───────────────

def _get_tasks(chat_id: str) -> dict:
    """Get all active tasks for a user."""
    state = _load()
    u = _require_user(state, chat_id)
    return {
        "tasks": [_fmt_task(t, i + 1) for i, t in enumerate(u.get("tasks", []))],
        "count": len(u.get("tasks", [])),
    }


def _add_task(chat_id: str, text: str, due_date: str = "", recur: str = "") -> dict:
    """
    Add a task for a user.
    due_date: optional YYYY-MM-DD string.
    recur: optional 'daily', 'weekly', or 'monthly'.
    """
    state = _load()
    u = _require_user(state, chat_id)
    due = None
    if due_date:
        try:
            date.fromisoformat(due_date)
            due = due_date
        except ValueError:
            return {"error": f"Invalid due_date '{due_date}'. Use YYYY-MM-DD."}
    recur_val = recur.lower() if recur in ("daily", "weekly", "monthly") else None
    if recur and not recur_val:
        return {"error": f"Invalid recur '{recur}'. Use daily, weekly, or monthly."}
    if due or recur_val:
        task: dict | str = {"text": text}
        if due:
            task["due"] = due  # type: ignore[index]
        elif recur_val:
            task["due"] = date.today().isoformat()  # type: ignore[index]
        if recur_val:
            task["recur"] = recur_val  # type: ignore[index]
    else:
        task = text
    u.setdefault("tasks", []).append(task)
    _save(state)
    return {"success": True, "task": _fmt_task(task, len(u["tasks"]))}


def _complete_task(chat_id: str, task_number: int) -> dict:
    """
    Mark a task as done. Non-recurring tasks are archived; recurring tasks
    have their due date rolled forward (daily +1d, weekly +7d, monthly +1mo).
    """
    state = _load()
    u = _require_user(state, chat_id)
    tasks = u.get("tasks", [])
    n = task_number
    if n < 1 or n > len(tasks):
        return {"error": f"Task {n} not found. There are {len(tasks)} tasks."}
    task = tasks[n - 1]
    recur = task.get("recur") if isinstance(task, dict) else None
    archived = u.setdefault("archived_tasks", [])
    archived.append({
        "text": _task_text(task),
        "due": _task_due(task),
        "completed_at": datetime.utcnow().isoformat(),
    })
    if len(archived) > 100:
        u["archived_tasks"] = archived[-100:]
    if recur:
        current_due = _task_due(task) or date.today().isoformat()
        try:
            base = date.fromisoformat(current_due)
        except ValueError:
            base = date.today()
        if recur == "daily":
            next_due = base + timedelta(days=1)
        elif recur == "weekly":
            next_due = base + timedelta(weeks=1)
        else:
            m = base.month % 12 + 1
            y = base.year + (1 if base.month == 12 else 0)
            d = min(base.day, calendar.monthrange(y, m)[1])
            next_due = date(y, m, d)
        tasks[n - 1] = {"text": _task_text(task), "due": next_due.isoformat(), "recur": recur}
        _save(state)
        return {"success": True, "completed": _task_text(task), "recurs": recur, "next_due": next_due.isoformat()}
    else:
        tasks.pop(n - 1)
        _save(state)
        return {"success": True, "completed": _task_text(task)}


def _remove_task(chat_id: str, task_number: int) -> dict:
    """Permanently delete a task (use complete_task to archive instead)."""
    state = _load()
    u = _require_user(state, chat_id)
    tasks = u.get("tasks", [])
    if task_number < 1 or task_number > len(tasks):
        return {"error": f"Task {task_number} not found."}
    removed = _task_text(tasks.pop(task_number - 1))
    _save(state)
    return {"success": True, "removed": removed}


def _get_archived_tasks(chat_id: str, limit: int = 20) -> dict:
    """Get recently completed tasks (most recent first)."""
    state = _load()
    u = _require_user(state, chat_id)
    archived = list(reversed(u.get("archived_tasks", [])))[:limit]
    return {"archived": archived, "total": len(u.get("archived_tasks", []))}


# ─────────────── tools: habits ───────────────

def _get_habits(chat_id: str) -> list[dict]:
    """Get all habits with current streak and today's completion status."""
    state = _load()
    u = _require_user(state, chat_id)
    today = date.today().isoformat()
    result = []
    for name, h in u.get("habits", {}).items():
        completions = h.get("completions", [])
        result.append({
            "name": name,
            "done_today": today in completions,
            "streak": _habit_streak(completions),
            "total_completions": len(completions),
            "created": h.get("created"),
        })
    return result


def _log_habit(chat_id: str, habit_name: str) -> dict:
    """Mark a habit as done for today."""
    state = _load()
    u = _require_user(state, chat_id)
    habits = u.get("habits", {})
    name = habit_name.lower()
    if name not in habits:
        available = list(habits.keys())
        return {"error": f"Habit '{name}' not found.", "available": available}
    today = date.today().isoformat()
    completions = habits[name].setdefault("completions", [])
    if today in completions:
        return {"already_done": True, "habit": name, "streak": _habit_streak(completions)}
    completions.append(today)
    completions.sort()
    _save(state)
    return {"success": True, "habit": name, "streak": _habit_streak(completions)}


# ─────────────── tools: trackers ───────────────

def _get_trackers(chat_id: str) -> list[dict]:
    """Get all custom trackers with their latest value and recent history."""
    state = _load()
    u = _require_user(state, chat_id)
    result = []
    for name, tr in u.get("trackers", {}).items():
        log = tr.get("log", [])
        recent = log[-7:] if log else []
        result.append({
            "name": name,
            "unit": tr.get("unit", ""),
            "latest_value": log[-1]["value"] if log else None,
            "latest_ts": log[-1]["ts"] if log else None,
            "entry_count": len(log),
            "recent": recent,
        })
    return result


def _log_tracker(chat_id: str, tracker_name: str, value: float) -> dict:
    """Log a numeric value to a user's tracker."""
    state = _load()
    u = _require_user(state, chat_id)
    trackers = u.get("trackers", {})
    name = tracker_name.lower().strip()
    if name not in trackers:
        return {"error": f"Tracker '{name}' not found.", "available": list(trackers.keys())}
    entry = {"ts": datetime.utcnow().isoformat(), "value": float(value)}
    trackers[name].setdefault("log", []).append(entry)
    if len(trackers[name]["log"]) > 500:
        trackers[name]["log"] = trackers[name]["log"][-500:]
    _save(state)
    return {"success": True, "tracker": name, "value": value, "unit": trackers[name].get("unit", "")}


# ─────────────── tools: journal (SQLite) ───────────────

def _get_journal(chat_id: str, limit: int = 10) -> list[dict]:
    """Get recent journal entries from bot_memory.db (most recent first)."""
    with _db() as con:
        rows = con.execute(
            "SELECT id, entry, ts, auto FROM journal WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (str(chat_id), limit)
        ).fetchall()
    return [dict(r) for r in rows]


def _add_journal_entry(chat_id: str, text: str) -> dict:
    """Save a journal entry for a user into bot_memory.db."""
    ts = datetime.utcnow().isoformat()
    with _db() as con:
        cur = con.execute(
            "INSERT INTO journal(chat_id, entry, ts, auto) VALUES(?,?,?,0)",
            (str(chat_id), text.strip(), ts)
        )
        row_id = cur.lastrowid
    return {"success": True, "id": row_id, "saved_at": ts}


# ─────────────── tools: notes (SQLite) ───────────────

def _get_notes(chat_id: str) -> dict:
    """Get all quick notes for a user from bot_memory.db."""
    with _db() as con:
        rows = con.execute(
            "SELECT id, text, ts, auto FROM notes WHERE chat_id=? ORDER BY id",
            (str(chat_id),)
        ).fetchall()
    return {
        "notes": [{"number": i + 1, **dict(r)} for i, r in enumerate(rows)],
        "count": len(rows),
    }


def _add_note(chat_id: str, text: str) -> dict:
    """Save a quick note for a user into bot_memory.db."""
    ts = datetime.utcnow().isoformat()
    with _db() as con:
        cur = con.execute(
            "INSERT INTO notes(chat_id, text, ts, auto) VALUES(?,?,?,0)",
            (str(chat_id), text.strip(), ts)
        )
        row_id = cur.lastrowid
    return {"success": True, "id": row_id, "saved_at": ts}


def _remove_note(chat_id: str, note_number: int) -> dict:
    """Delete a note by its 1-based number, as shown by get_notes()."""
    with _db() as con:
        rows = con.execute(
            "SELECT id, text FROM notes WHERE chat_id=? ORDER BY id",
            (str(chat_id),)
        ).fetchall()
        if note_number < 1 or note_number > len(rows):
            return {"error": f"Note {note_number} not found. There are {len(rows)} notes."}
        target = rows[note_number - 1]
        con.execute("DELETE FROM notes WHERE id=? AND chat_id=?", (target["id"], str(chat_id)))
    return {"success": True, "removed": target["text"]}


# ─────────────── tools: memory (SQLite) ───────────────

def _get_memory(chat_id: str) -> dict:
    """
    Get everything the bot has learned/remembered about a user:
    permanent profile facts and non-expired episodic (30-day TTL) observations.
    """
    now = datetime.utcnow().isoformat()
    with _db() as con:
        profile = con.execute(
            "SELECT id, fact, ts FROM profile_memory WHERE chat_id=? ORDER BY id",
            (str(chat_id),)
        ).fetchall()
        episodic = con.execute(
            "SELECT id, event, ts, expires_at FROM episodic_memory "
            "WHERE chat_id=? AND expires_at > ? ORDER BY id DESC",
            (str(chat_id), now)
        ).fetchall()
    return {
        "profile": [dict(r) for r in profile],
        "episodic": [dict(r) for r in episodic],
    }


def _voice_instruction(u: dict) -> str:
    """Mirrors bot.py's build_system_prompt() persona/honorific instruction
    text (persona_instruction + honorific_instruction there), kept as a
    small standalone copy rather than importing bot.py — mcp_server.py
    deliberately never goes through bot.py's runtime (see CLAUDE.md), and
    bot.py pulls in python-telegram-bot/AsyncOpenAI client setup that has
    no business running inside this process. Only the two short instruction
    strings are duplicated; if bot.py's wording changes, update both.
    Claude via claude.ai sees this only as tool-result text — it's a
    request, not an enforced system prompt, so it isn't guaranteed to hold
    the way it does inside bot.py's own chat() loop."""
    persona = (u.get("persona") or "Jeeves").strip()
    honorific = (u.get("honorific") or "").strip()
    parts = []
    if persona.lower() not in ("plain", "none", "off"):
        parts.append(
            f"Adopt the voice of {persona} in how you phrase replies — word choice, tone, "
            "small mannerisms. This is decoration on phrasing only; a literal request "
            "(an exact word, number, or format) always wins over voice."
        )
    if honorific:
        parts.append(f'Address the user as "{honorific}" where it fits naturally.')
    language = (u.get("language") or "").strip()
    if language:
        parts.append(f"Reply in {language} unless the user switches language first.")
    return " ".join(parts)


# ─────────────── tools: stats & overview ───────────────

def _get_user_stats(chat_id: str) -> dict:
    """Get a full stats overview for a user. Call this first, before any
    real conversation: it includes persona, honorific, and language — how
    this person wants to be addressed and in what voice — plus a ready
    voice_instruction string summarizing it. Follow it for the rest of the
    conversation the way you would a system instruction, unless the user
    asks for something literal (an exact word, number, format), which
    always wins over voice."""
    state = _load()
    u = _require_user(state, chat_id)
    activity = sorted(u.get("activity_days", []))
    today = date.today()
    # Streak
    streak, check = 0, today
    done_set = set(activity)
    while check.isoformat() in done_set:
        streak += 1
        check -= timedelta(days=1)
    # Habits today
    today_str = today.isoformat()
    habits_today = {
        name: today_str in h.get("completions", [])
        for name, h in u.get("habits", {}).items()
    }
    with _db() as con:
        journal_count = con.execute(
            "SELECT COUNT(*) FROM journal WHERE chat_id=?", (str(chat_id),)
        ).fetchone()[0]
        notes_count = con.execute(
            "SELECT COUNT(*) FROM notes WHERE chat_id=?", (str(chat_id),)
        ).fetchone()[0]
    return {
        "chat_id": chat_id,
        "timezone": _timezone(chat_id, u.get("timezone", "UTC")),
        "streak": streak,
        "active_days": len(activity),
        "first_seen": activity[0] if activity else None,
        "last_active": activity[-1] if activity else None,
        "active_tasks": len(u.get("tasks", [])),
        "completed_tasks": len(u.get("archived_tasks", [])),
        "habits": habits_today,
        "trackers": list(u.get("trackers", {}).keys()),
        "journal_entries": journal_count,
        "notes": notes_count,
        "reminders": len(u.get("reminders", [])),
        "subscribed": u.get("checkin_enabled", False),
        "context": u.get("context", ""),
        "model": u.get("llm", {}).get("model"),
        "persona": u.get("persona") or "Jeeves",
        "honorific": u.get("honorific") or "",
        "language": u.get("language") or "",
        "voice_instruction": _voice_instruction(u),
    }


def _get_reminders(chat_id: str) -> list[dict]:
    """List all scheduled reminders for a user."""
    state = _load()
    u = _require_user(state, chat_id)
    return u.get("reminders", [])


# ─────────────── tool registration: transport-scoped (AUTH-03) ───────────────
#
# stdio (Claude Desktop/Code): trusted local operator, chat_id stays an
# explicit caller-supplied argument exactly as before — same tool set,
# same schema, list_users()/resources included.
#
# remote (claude.ai, behind Google OAuth): chat_id is NEVER a parameter a
# caller can supply. It's resolved from the authenticated access token's
# `subject` (bound once, verified, via /link — see the OAuth section near
# the top of this file) and silently applied server-side. list_users() and
# the bot://users* resources are not registered at all on this transport —
# a directory of every other user's data has no legitimate use once each
# caller is bound to exactly one identity.

_IS_REMOTE = os.environ.get("MCP_TRANSPORT") == "remote"


def _authed_chat_id() -> str:
    """The calling identity's bound chat_id, resolved from the OAuth access
    token — never from a caller-supplied argument. Raises if somehow called
    with no authenticated token (shouldn't happen: RequireAuthMiddleware
    already rejects unauthenticated requests before a tool call can run)."""
    from mcp.server.auth.middleware.auth_context import get_access_token
    token = get_access_token()
    if token is None or not token.subject:
        raise ValueError("Not authenticated.")
    return token.subject


if _IS_REMOTE:
    @mcp.tool()
    def get_tasks() -> dict:
        """Get all active tasks for the connected user."""
        return _get_tasks(_authed_chat_id())

    @mcp.tool()
    def add_task(text: str, due_date: str = "", recur: str = "") -> dict:
        """
        Add a task for the connected user.
        due_date: optional YYYY-MM-DD string.
        recur: optional 'daily', 'weekly', or 'monthly'.
        """
        return _add_task(_authed_chat_id(), text, due_date, recur)

    @mcp.tool()
    def complete_task(task_number: int) -> dict:
        """
        Mark a task as done. Non-recurring tasks are archived; recurring tasks
        have their due date rolled forward (daily +1d, weekly +7d, monthly +1mo).
        """
        return _complete_task(_authed_chat_id(), task_number)

    @mcp.tool()
    def remove_task(task_number: int) -> dict:
        """Permanently delete a task (use complete_task to archive instead)."""
        return _remove_task(_authed_chat_id(), task_number)

    @mcp.tool()
    def get_archived_tasks(limit: int = 20) -> dict:
        """Get recently completed tasks (most recent first)."""
        return _get_archived_tasks(_authed_chat_id(), limit)

    @mcp.tool()
    def get_habits() -> list[dict]:
        """Get all habits with current streak and today's completion status."""
        return _get_habits(_authed_chat_id())

    @mcp.tool()
    def log_habit(habit_name: str) -> dict:
        """Mark a habit as done for today."""
        return _log_habit(_authed_chat_id(), habit_name)

    @mcp.tool()
    def get_trackers() -> list[dict]:
        """Get all custom trackers with their latest value and recent history."""
        return _get_trackers(_authed_chat_id())

    @mcp.tool()
    def log_tracker(tracker_name: str, value: float) -> dict:
        """Log a numeric value to a tracker."""
        return _log_tracker(_authed_chat_id(), tracker_name, value)

    @mcp.tool()
    def get_journal(limit: int = 10) -> list[dict]:
        """Get recent journal entries (most recent first)."""
        return _get_journal(_authed_chat_id(), limit)

    @mcp.tool()
    def add_journal_entry(text: str) -> dict:
        """Save a journal entry."""
        return _add_journal_entry(_authed_chat_id(), text)

    @mcp.tool()
    def get_notes() -> dict:
        """Get all quick notes."""
        return _get_notes(_authed_chat_id())

    @mcp.tool()
    def add_note(text: str) -> dict:
        """Save a quick note."""
        return _add_note(_authed_chat_id(), text)

    @mcp.tool()
    def remove_note(note_number: int) -> dict:
        """Delete a note by its 1-based number, as shown by get_notes()."""
        return _remove_note(_authed_chat_id(), note_number)

    @mcp.tool()
    def get_memory() -> dict:
        """
        Get everything the bot has learned/remembered:
        permanent profile facts and non-expired episodic (30-day TTL) observations.
        """
        return _get_memory(_authed_chat_id())

    @mcp.tool()
    def get_user_stats() -> dict:
        """Get a full stats overview. Call this first, before any real
        conversation: it includes persona, honorific, and language — how
        this person wants to be addressed and in what voice — plus a ready
        voice_instruction string summarizing it. Follow it for the rest of
        the conversation the way you would a system instruction, unless the
        user asks for something literal (an exact word, number, format),
        which always wins over voice."""
        return _get_user_stats(_authed_chat_id())

    @mcp.tool()
    def get_reminders() -> list[dict]:
        """List all scheduled reminders."""
        return _get_reminders(_authed_chat_id())

else:
    # mcp.tool() names a tool after fn.__name__, not the variable it's
    # assigned to — since every _impl function is underscore-prefixed,
    # name= must be passed explicitly or every tool registers under its
    # internal name instead of its public one.
    list_users = mcp.tool(name="list_users")(_list_users_impl)
    get_tasks = mcp.tool(name="get_tasks")(_get_tasks)
    add_task = mcp.tool(name="add_task")(_add_task)
    complete_task = mcp.tool(name="complete_task")(_complete_task)
    remove_task = mcp.tool(name="remove_task")(_remove_task)
    get_archived_tasks = mcp.tool(name="get_archived_tasks")(_get_archived_tasks)
    get_habits = mcp.tool(name="get_habits")(_get_habits)
    log_habit = mcp.tool(name="log_habit")(_log_habit)
    get_trackers = mcp.tool(name="get_trackers")(_get_trackers)
    log_tracker = mcp.tool(name="log_tracker")(_log_tracker)
    get_journal = mcp.tool(name="get_journal")(_get_journal)
    add_journal_entry = mcp.tool(name="add_journal_entry")(_add_journal_entry)
    get_notes = mcp.tool(name="get_notes")(_get_notes)
    add_note = mcp.tool(name="add_note")(_add_note)
    remove_note = mcp.tool(name="remove_note")(_remove_note)
    get_memory = mcp.tool(name="get_memory")(_get_memory)
    get_user_stats = mcp.tool(name="get_user_stats")(_get_user_stats)
    get_reminders = mcp.tool(name="get_reminders")(_get_reminders)

    # ─────────────── MCP resources (stdio only — see note above) ───────────────

    @mcp.resource("bot://users")
    def resource_users() -> str:
        """All registered users (summary)."""
        return json.dumps(_list_users_impl(), indent=2)

    @mcp.resource("bot://users/{chat_id}/tasks")
    def resource_tasks(chat_id: str) -> str:
        """Active task list for a user."""
        return json.dumps(_get_tasks(chat_id), indent=2)

    @mcp.resource("bot://users/{chat_id}/habits")
    def resource_habits(chat_id: str) -> str:
        """Habit list for a user."""
        return json.dumps(_get_habits(chat_id), indent=2)

    @mcp.resource("bot://users/{chat_id}/journal")
    def resource_journal(chat_id: str) -> str:
        """Last 10 journal entries for a user."""
        return json.dumps(_get_journal(chat_id, limit=10), indent=2)

    @mcp.resource("bot://users/{chat_id}/notes")
    def resource_notes(chat_id: str) -> str:
        """All notes for a user."""
        return json.dumps(_get_notes(chat_id), indent=2)

    @mcp.resource("bot://users/{chat_id}/memory")
    def resource_memory(chat_id: str) -> str:
        """Profile + episodic memory the bot has learned about a user."""
        return json.dumps(_get_memory(chat_id), indent=2)

    @mcp.resource("bot://users/{chat_id}/trackers")
    def resource_trackers(chat_id: str) -> str:
        """Tracker data for a user."""
        return json.dumps(_get_trackers(chat_id), indent=2)

    @mcp.resource("bot://users/{chat_id}/stats")
    def resource_stats(chat_id: str) -> str:
        """Full stats for a user."""
        return json.dumps(_get_user_stats(chat_id), indent=2)


# ─────────────── remote HTTPS mode ───────────────
#
# Set MCP_TRANSPORT=remote to serve streamable-http instead of the default
# stdio transport, so claude.ai's web app (which can only reach remote
# HTTP(S) MCP servers, not local stdio ones) can connect directly.
#
# This process itself speaks plain HTTP on localhost — nginx terminates TLS
# on 443 for the public hostname (MCP_REMOTE_DOMAIN) and reverse-proxies to
# MCP_REMOTE_PORT, the same pattern used for the Alteon MCP server on this
# box. Do not point this at 0.0.0.0/443 directly; that port belongs to nginx.
#
# Auth is Google OAuth (Milestone B), not a shared ?key= token: the `auth=`/
# `auth_server_provider=` settings on `mcp` above make the SDK itself gate
# /mcp behind a valid Bearer token (RequireAuthMiddleware) and auto-wire
# /authorize, /token, /register, /revoke and the discovery well-known paths.
# See the "Google OIDC + OAuth Authorization Server" section near the top of
# this file for how a token gets issued in the first place.

def _run_remote() -> None:
    import uvicorn

    if _oauth_provider is None:
        raise RuntimeError(
            "Remote transport requires MCP_REMOTE_DOMAIN, GOOGLE_OAUTH_CLIENT_ID and "
            "GOOGLE_OAUTH_CLIENT_SECRET to all be set (Milestone B OAuth replaces the old "
            "?key= token — there is no unauthenticated remote mode)."
        )
    host = os.environ.get("MCP_REMOTE_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_REMOTE_PORT", "8545"))
    uvicorn.run(mcp.streamable_http_app(), host=host, port=port)


if __name__ == "__main__":
    if os.environ.get("MCP_TRANSPORT") == "remote":
        _run_remote()
    else:
        mcp.run()
