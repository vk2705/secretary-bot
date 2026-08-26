"""
test_mcp_server.py — Milestone B (Google OIDC + OAuth Authorization Server)

Covers the storage helpers and SecretaryOAuthProvider protocol methods in
mcp_server.py, plus the /link and /oauth/google/callback custom routes.
Google's own endpoints are always mocked — no real network calls.

mcp_server.py never creates SQLite schema itself (bot.py's _init_db() owns
that, per the existing architecture — see mcp_server.py's module docstring
on the OAuth section). So this file imports bot.py too, purely to get a
real schema into the shared temp DB each test points both modules at.

Run:
    python -m pytest tests/test_mcp_server.py -v
"""

import importlib
import os
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── minimal stubs so bot.py imports without a running Telegram app ────────────
# Self-contained (doesn't rely on test_bot.py having run first), but the
# guards make it harmless if it has.
for _mod in ["telegram", "telegram.ext", "telegram.ext._application", "timezonefinder"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

_tg = sys.modules["telegram"]
for _attr in ["Update", "BotCommand"]:
    if not hasattr(_tg, _attr):
        setattr(_tg, _attr, MagicMock)
for _attr in ["InlineKeyboardMarkup", "InlineKeyboardButton"]:
    if not hasattr(_tg, _attr):
        setattr(_tg, _attr, MagicMock(side_effect=lambda *a, **kw: MagicMock()))

_tgext = sys.modules["telegram.ext"]
for _attr in ["Application", "CommandHandler", "MessageHandler", "CallbackQueryHandler",
              "filters", "ContextTypes", "ApplicationBuilder"]:
    if not hasattr(_tgext, _attr):
        setattr(_tgext, _attr, MagicMock)
if not hasattr(_tgext.ContextTypes, "DEFAULT_TYPE"):
    _tgext.ContextTypes.DEFAULT_TYPE = type(None)

if "timezonefinder" in sys.modules and not hasattr(sys.modules["timezonefinder"], "TimezoneFinder"):
    _tf_stub = MagicMock()
    _tf_stub.return_value.timezone_at.return_value = "Europe/London"
    setattr(sys.modules["timezonefinder"], "TimezoneFinder", _tf_stub)

os.environ.setdefault("TELEGRAM_TOKEN", "TEST_TOKEN")
os.environ.setdefault("OPENAI_API_KEY", "TEST_KEY")

_real_exists = os.path.exists


def _patched_exists(path):
    if str(path).endswith("state.json"):
        return False
    return _real_exists(path)


with patch("os.path.exists", side_effect=_patched_exists):
    import bot  # noqa: E402

import mcp_server  # noqa: E402
from mcp.server.auth.provider import AuthorizationCode, AuthorizationParams, RefreshToken  # noqa: E402
from mcp.shared.auth import OAuthClientInformationFull  # noqa: E402


def _fresh_db():
    """Point both bot.py and mcp_server.py at the same brand-new temp DB and
    temp state.json — without repointing STATE_FILE too, any test touching
    tasks/habits/trackers (_load()/_save()) would silently hit the real
    production state.json next to mcp_server.py."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    bot.DB_FILE = tmp.name
    mcp_server.DB_FILE = tmp.name

    state_tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    state_tmp.close()
    bot.STATE_FILE = state_tmp.name          # bot.py treats this as a plain str path
    mcp_server.STATE_FILE = Path(state_tmp.name)  # mcp_server.py calls .exists() on it
    bot.state = {"users": {}}

    bot._init_db()


@pytest.fixture(autouse=True)
def isolate_db():
    _fresh_db()
    yield


def run(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _client(client_id="client-1", redirect_uri="https://claude.ai/api/mcp/callback") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(client_id=client_id, redirect_uris=[redirect_uri])


def _params(
    state="client-state", code_challenge="chal123",
    redirect_uri="https://claude.ai/api/mcp/callback", resource=None,
) -> AuthorizationParams:
    return AuthorizationParams(
        state=state, scopes=["secretary"], code_challenge=code_challenge,
        redirect_uri=redirect_uri, redirect_uri_provided_explicitly=True, resource=resource,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Link code + identity binding (the other side of bot.py's db_create_link_code)
# ─────────────────────────────────────────────────────────────────────────────

class TestLinkCodeAndIdentity:
    def test_valid_code_from_bot_is_seen_as_valid(self):
        code = bot.db_create_link_code("111")
        assert mcp_server._link_code_valid(code) is True

    def test_unknown_code_invalid(self):
        assert mcp_server._link_code_valid("deadbeef") is False

    def test_expired_code_invalid(self):
        code = bot.db_create_link_code("111", ttl_minutes=-1)
        assert mcp_server._link_code_valid(code) is False

    def test_consume_returns_chat_id_and_marks_used(self):
        code = bot.db_create_link_code("222")
        chat_id = mcp_server._link_consume_code(code)
        assert chat_id == "222"
        assert mcp_server._link_code_valid(code) is False  # now used

    def test_consume_twice_fails_second_time(self):
        code = bot.db_create_link_code("222")
        assert mcp_server._link_consume_code(code) == "222"
        assert mcp_server._link_consume_code(code) is None

    def test_consume_unknown_code_returns_none(self):
        assert mcp_server._link_consume_code("nope") is None

    def test_bind_and_lookup_identity(self):
        mcp_server._link_bind_identity("google-sub-1", "a@example.com", "333")
        assert mcp_server._link_lookup_chat_id("google-sub-1") == "333"

    def test_lookup_unbound_sub_returns_none(self):
        assert mcp_server._link_lookup_chat_id("never-bound") is None

    def test_rebinding_same_sub_updates_chat_id(self):
        """Re-running /link from a different Telegram account with the same
        Google identity should move the binding, not duplicate it."""
        mcp_server._link_bind_identity("google-sub-2", "a@example.com", "111")
        mcp_server._link_bind_identity("google-sub-2", "a@example.com", "999")
        assert mcp_server._link_lookup_chat_id("google-sub-2") == "999"


# ─────────────────────────────────────────────────────────────────────────────
# OAuth client registry
# ─────────────────────────────────────────────────────────────────────────────

class TestOAuthClientStore:
    def test_save_and_get_round_trips(self):
        info = _client()
        mcp_server._oauth_save_client(info)
        loaded = mcp_server._oauth_get_client("client-1")
        assert loaded is not None
        assert loaded.client_id == "client-1"
        assert str(loaded.redirect_uris[0]) == "https://claude.ai/api/mcp/callback"

    def test_get_unknown_client_returns_none(self):
        assert mcp_server._oauth_get_client("nope") is None


# ─────────────────────────────────────────────────────────────────────────────
# Pending /authorize parking (survives the Google round-trip)
# ─────────────────────────────────────────────────────────────────────────────

class TestPendingAuthorize:
    def test_save_and_load_round_trips_then_deletes(self):
        params = _params()
        mcp_server._pending_authorize_save("authz:abc", "client-1", params)
        loaded = mcp_server._pending_authorize_load("authz:abc")
        assert loaded["client_id"] == "client-1"
        assert loaded["code_challenge"] == "chal123"
        assert loaded["client_state"] == "client-state"
        # single-use: gone on second load
        assert mcp_server._pending_authorize_load("authz:abc") is None

    def test_expired_pending_returns_none(self):
        params = _params()
        mcp_server._pending_authorize_save("authz:old", "client-1", params, ttl_seconds=-1)
        assert mcp_server._pending_authorize_load("authz:old") is None

    def test_unknown_state_returns_none(self):
        assert mcp_server._pending_authorize_load("authz:nope") is None


# ─────────────────────────────────────────────────────────────────────────────
# Authorization codes (our own AS's codes, minted after Google confirms identity)
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthCodeStore:
    def test_save_load_delete(self):
        mcp_server._auth_code_save(
            "code-1", "client-1", "chat-1", ["secretary"], "chal", "https://x/cb", True, None
        )
        row = mcp_server._auth_code_load("code-1")
        assert row["chat_id"] == "chat-1"
        mcp_server._auth_code_delete("code-1")
        assert mcp_server._auth_code_load("code-1") is None


# ─────────────────────────────────────────────────────────────────────────────
# Token store — always hashed, never plaintext
# ─────────────────────────────────────────────────────────────────────────────

class TestTokenStore:
    def test_save_and_load_round_trip(self):
        mcp_server._token_save("access", "secret-token", "client-1", "chat-1", ["secretary"], time.time() + 3600)
        row = mcp_server._token_load("access", "secret-token")
        assert row["chat_id"] == "chat-1"

    def test_token_stored_hashed_not_plaintext(self):
        mcp_server._token_save("access", "secret-token", "client-1", "chat-1", ["secretary"], time.time() + 3600)
        with mcp_server._db() as con:
            row = con.execute("SELECT token_hash FROM mcp_tokens").fetchone()
        assert row["token_hash"] != "secret-token"
        assert row["token_hash"] == mcp_server._hash_token("secret-token")

    def test_wrong_kind_not_found(self):
        mcp_server._token_save("access", "tok", "client-1", "chat-1", ["secretary"], time.time() + 3600)
        assert mcp_server._token_load("refresh", "tok") is None

    def test_delete_removes_token(self):
        mcp_server._token_save("refresh", "tok", "client-1", "chat-1", ["secretary"], None)
        mcp_server._token_delete("refresh", "tok")
        assert mcp_server._token_load("refresh", "tok") is None


# ─────────────────────────────────────────────────────────────────────────────
# SecretaryOAuthProvider — full protocol, the security-critical surface
# ─────────────────────────────────────────────────────────────────────────────

class TestSecretaryOAuthProvider:
    def setup_method(self):
        self.provider = mcp_server.SecretaryOAuthProvider()

    def test_register_then_get_client(self):
        info = _client("client-2")
        run(self.provider.register_client(info))
        loaded = run(self.provider.get_client("client-2"))
        assert loaded.client_id == "client-2"

    def test_get_unregistered_client_none(self):
        assert run(self.provider.get_client("ghost")) is None

    def test_authorize_parks_request_and_redirects_to_google(self):
        client = _client()
        params = _params()
        url = run(self.provider.authorize(client, params))
        assert url.startswith(mcp_server.GOOGLE_AUTH_ENDPOINT)
        assert "state=authz%3A" in url or "state=authz:" in url
        # the state embedded in the URL must resolve back via _pending_authorize_load
        import urllib.parse
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        state = qs["state"][0]
        assert state.startswith("authz:")
        pending = mcp_server._pending_authorize_load(state)
        assert pending["client_id"] == client.client_id

    def test_exchange_authorization_code_mints_tokens_bound_to_subject(self):
        auth_code = AuthorizationCode(
            code="code-xyz", scopes=["secretary"], expires_at=time.time() + 60,
            client_id="client-1", code_challenge="chal", redirect_uri="https://x/cb",
            redirect_uri_provided_explicitly=True, subject="chat-42",
        )
        client = _client()
        tokens = run(self.provider.exchange_authorization_code(client, auth_code))
        assert tokens.token_type == "Bearer"
        assert tokens.refresh_token is not None

        access = run(self.provider.load_access_token(tokens.access_token))
        assert access.subject == "chat-42"
        assert access.scopes == ["secretary"]

        refresh = run(self.provider.load_refresh_token(client, tokens.refresh_token))
        assert refresh.subject == "chat-42"

    def test_authorization_code_is_single_use(self):
        mcp_server._auth_code_save(
            "one-shot", "client-1", "chat-9", ["secretary"], "chal", "https://x/cb", True, None
        )
        client = _client()
        loaded = run(self.provider.load_authorization_code(client, "one-shot"))
        assert loaded is not None
        run(self.provider.exchange_authorization_code(client, loaded))
        # exchanging deletes it — a second load must fail
        assert run(self.provider.load_authorization_code(client, "one-shot")) is None

    def test_load_authorization_code_rejects_wrong_client(self):
        mcp_server._auth_code_save(
            "code-a", "client-1", "chat-1", ["secretary"], "chal", "https://x/cb", True, None
        )
        other_client = _client("client-2")
        assert run(self.provider.load_authorization_code(other_client, "code-a")) is None

    def test_refresh_token_rotates_on_use(self):
        auth_code = AuthorizationCode(
            code="code-r", scopes=["secretary"], expires_at=time.time() + 60,
            client_id="client-1", code_challenge="chal", redirect_uri="https://x/cb",
            redirect_uri_provided_explicitly=True, subject="chat-7",
        )
        client = _client()
        first = run(self.provider.exchange_authorization_code(client, auth_code))
        old_refresh = run(self.provider.load_refresh_token(client, first.refresh_token))
        second = run(self.provider.exchange_refresh_token(client, old_refresh, ["secretary"]))
        assert second.access_token != first.access_token
        assert second.refresh_token != first.refresh_token
        # old refresh token must no longer work (rotated, not reusable)
        assert run(self.provider.load_refresh_token(client, first.refresh_token)) is None
        # new tokens resolve to the same subject
        access = run(self.provider.load_access_token(second.access_token))
        assert access.subject == "chat-7"

    def test_load_access_token_rejects_expired(self):
        mcp_server._token_save("access", "stale", "client-1", "chat-1", ["secretary"], time.time() - 10)
        assert run(self.provider.load_access_token("stale")) is None

    def test_load_access_token_rejects_unknown(self):
        assert run(self.provider.load_access_token("never-issued")) is None

    def test_revoke_access_token(self):
        mcp_server._token_save("access", "tok-a", "client-1", "chat-1", ["secretary"], time.time() + 3600)
        access = run(self.provider.load_access_token("tok-a"))
        run(self.provider.revoke_token(access))
        assert run(self.provider.load_access_token("tok-a")) is None

    def test_revoke_refresh_token(self):
        mcp_server._token_save("refresh", "tok-r", "client-1", "chat-1", ["secretary"], None)
        client = _client()
        refresh = run(self.provider.load_refresh_token(client, "tok-r"))
        run(self.provider.revoke_token(refresh))
        assert run(self.provider.load_refresh_token(client, "tok-r")) is None


# ─────────────────────────────────────────────────────────────────────────────
# Google OIDC wiring — network always mocked
# ─────────────────────────────────────────────────────────────────────────────

class TestGoogleExchange:
    def test_exchange_code_posts_expected_fields(self):
        captured = {}

        class _FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id_token": "fake.jwt.token"}

        class _FakeAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, data=None):
                captured["url"] = url
                captured["data"] = data
                return _FakeResp()

        with patch.object(mcp_server, "GOOGLE_CLIENT_ID", "test-client-id"), \
             patch.object(mcp_server, "GOOGLE_CLIENT_SECRET", "test-secret"), \
             patch.object(mcp_server.httpx, "AsyncClient", _FakeAsyncClient):
            result = run(mcp_server._google_exchange_code("google-code"))

        assert result == {"id_token": "fake.jwt.token"}
        assert captured["url"] == mcp_server.GOOGLE_TOKEN_ENDPOINT
        assert captured["data"]["code"] == "google-code"
        assert captured["data"]["client_id"] == "test-client-id"
        assert captured["data"]["client_secret"] == "test-secret"
        assert captured["data"]["grant_type"] == "authorization_code"


class TestGoogleVerifyIdToken:
    def test_verify_calls_jwt_decode_with_correct_audience_and_issuer(self):
        fake_key = MagicMock()
        fake_key.key = "public-key-material"
        fake_jwks_client = MagicMock()
        fake_jwks_client.get_signing_key_from_jwt.return_value = fake_key

        with patch.object(mcp_server, "_google_jwks_client", fake_jwks_client), \
             patch.object(mcp_server, "GOOGLE_CLIENT_ID", "test-client-id"), \
             patch.object(mcp_server.jwt, "decode", return_value={"sub": "123", "email": "a@b.com"}) as mock_decode:
            claims = mcp_server._google_verify_id_token("some.jwt.token")

        assert claims == {"sub": "123", "email": "a@b.com"}
        _, kwargs = mock_decode.call_args
        assert kwargs["audience"] == "test-client-id"
        assert kwargs["issuer"] == mcp_server.GOOGLE_ISSUERS
        assert kwargs["algorithms"] == ["RS256"]

    def test_verify_propagates_failure(self):
        """A bad signature/audience/issuer must surface as an exception, never
        as a quietly-accepted claim (AUTH-02: verified, not trusted)."""
        fake_jwks_client = MagicMock()
        fake_jwks_client.get_signing_key_from_jwt.side_effect = mcp_server.jwt.InvalidTokenError("bad token")
        with patch.object(mcp_server, "_google_jwks_client", fake_jwks_client):
            with pytest.raises(mcp_server.jwt.InvalidTokenError):
                mcp_server._google_verify_id_token("garbage")


# ─────────────────────────────────────────────────────────────────────────────
# Custom routes: /link/{code} and /oauth/google/callback
# ─────────────────────────────────────────────────────────────────────────────

def _request(path_params=None, query_params=None):
    req = MagicMock()
    req.path_params = path_params or {}
    req.query_params = query_params or {}
    return req


class TestLinkStartRoute:
    def test_valid_code_redirects_to_google_with_link_state(self):
        code = bot.db_create_link_code("55")
        with patch.object(mcp_server, "_oauth_provider", MagicMock()):
            resp = run(mcp_server.link_start(_request(path_params={"code": code})))
        assert resp.status_code == 307 or resp.status_code == 302
        assert f"state=link%3A{code}" in resp.headers["location"] or f"state=link:{code}" in resp.headers["location"]

    def test_invalid_code_rejected(self):
        with patch.object(mcp_server, "_oauth_provider", MagicMock()):
            resp = run(mcp_server.link_start(_request(path_params={"code": "bogus"})))
        assert resp.status_code == 400

    def test_oauth_not_configured_503(self):
        with patch.object(mcp_server, "_oauth_provider", None):
            resp = run(mcp_server.link_start(_request(path_params={"code": "anything"})))
        assert resp.status_code == 503


class TestGoogleCallbackRoute:
    def _mock_google(self, sub="google-sub-x", email="x@example.com"):
        return (
            patch.object(mcp_server, "_google_exchange_code", AsyncMock(return_value={"id_token": "fake"})),
            patch.object(mcp_server, "_google_verify_id_token", return_value={"sub": sub, "email": email}),
        )

    def test_google_error_param_rejected(self):
        with patch.object(mcp_server, "_oauth_provider", MagicMock()):
            resp = run(mcp_server.google_callback(_request(query_params={"error": "access_denied"})))
        assert resp.status_code == 400

    def test_missing_code_or_state_rejected(self):
        with patch.object(mcp_server, "_oauth_provider", MagicMock()):
            resp = run(mcp_server.google_callback(_request(query_params={"code": "abc"})))
        assert resp.status_code == 400

    def test_google_exchange_failure_502(self):
        with patch.object(mcp_server, "_oauth_provider", MagicMock()), \
             patch.object(mcp_server, "_google_exchange_code", AsyncMock(side_effect=RuntimeError("boom"))):
            resp = run(mcp_server.google_callback(
                _request(query_params={"code": "c", "state": "link:whatever"})
            ))
        assert resp.status_code == 502

    def test_link_flow_binds_identity_on_valid_code(self):
        code = bot.db_create_link_code("77")
        p1, p2 = self._mock_google(sub="sub-link-1")
        with patch.object(mcp_server, "_oauth_provider", MagicMock()), p1, p2:
            resp = run(mcp_server.google_callback(
                _request(query_params={"code": "g-code", "state": f"link:{code}"})
            ))
        assert resp.status_code == 200
        assert mcp_server._link_lookup_chat_id("sub-link-1") == "77"

    def test_link_flow_rejects_expired_code(self):
        p1, p2 = self._mock_google(sub="sub-link-2")
        with patch.object(mcp_server, "_oauth_provider", MagicMock()), p1, p2:
            resp = run(mcp_server.google_callback(
                _request(query_params={"code": "g-code", "state": "link:bogus"})
            ))
        assert resp.status_code == 400
        assert mcp_server._link_lookup_chat_id("sub-link-2") is None

    def test_authz_flow_unlinked_identity_gets_403(self):
        client = _client()
        params = _params()
        state = "authz:test-1"
        mcp_server._pending_authorize_save(state, client.client_id, params)
        p1, p2 = self._mock_google(sub="sub-unlinked")
        with patch.object(mcp_server, "_oauth_provider", MagicMock()), p1, p2:
            resp = run(mcp_server.google_callback(
                _request(query_params={"code": "g-code", "state": state})
            ))
        assert resp.status_code == 403

    def test_authz_flow_expired_pending_rejected(self):
        p1, p2 = self._mock_google(sub="sub-any")
        with patch.object(mcp_server, "_oauth_provider", MagicMock()), p1, p2:
            resp = run(mcp_server.google_callback(
                _request(query_params={"code": "g-code", "state": "authz:never-parked"})
            ))
        assert resp.status_code == 400

    def test_authz_flow_linked_identity_redirects_with_code(self):
        mcp_server._link_bind_identity("sub-linked", "y@example.com", "88")
        client = _client()
        params = _params(state="claude-echo-state")
        state = "authz:test-2"
        mcp_server._pending_authorize_save(state, client.client_id, params)
        p1, p2 = self._mock_google(sub="sub-linked")
        with patch.object(mcp_server, "_oauth_provider", MagicMock()), p1, p2:
            resp = run(mcp_server.google_callback(
                _request(query_params={"code": "g-code", "state": state})
            ))
        assert resp.status_code in (302, 307)
        location = resp.headers["location"]
        assert location.startswith("https://claude.ai/api/mcp/callback")
        assert "state=claude-echo-state" in location
        # the minted auth code must resolve to the linked chat_id
        import urllib.parse
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        minted_code = qs["code"][0]
        row = mcp_server._auth_code_load(minted_code)
        assert row["chat_id"] == "88"

    def test_unrecognized_state_prefix_rejected(self):
        p1, p2 = self._mock_google()
        with patch.object(mcp_server, "_oauth_provider", MagicMock()), p1, p2:
            resp = run(mcp_server.google_callback(
                _request(query_params={"code": "g-code", "state": "whatever:1"})
            ))
        assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# Transport-scoped tool registration (AUTH-03) — the fix for the reported
# leak: an authenticated remote caller was able to see every other user's
# chat_id/timezone/task-count via list_users(), because every tool still
# took chat_id as a model-supplied argument regardless of who authenticated.
# _IS_REMOTE is read once at import time from MCP_TRANSPORT, so these tests
# reload the module with the env var set/unset to exercise both branches.
# ─────────────────────────────────────────────────────────────────────────────

class TestTransportScopedRegistration:
    def teardown_method(self):
        # always leave the module back in its default (stdio) shape for
        # every other test in this file, regardless of what ran here
        os.environ.pop("MCP_TRANSPORT", None)
        importlib.reload(mcp_server)
        _fresh_db()

    def test_stdio_keeps_list_users_and_explicit_chat_id(self):
        os.environ.pop("MCP_TRANSPORT", None)
        importlib.reload(mcp_server)
        tools = run(mcp_server.mcp.list_tools())
        names = {t.name for t in tools}
        assert "list_users" in names
        get_tasks_tool = next(t for t in tools if t.name == "get_tasks")
        assert "chat_id" in get_tasks_tool.inputSchema.get("properties", {})
        resources = run(mcp_server.mcp.list_resource_templates())
        assert len(resources) == 7

    def test_remote_drops_list_users_and_chat_id_param(self):
        os.environ["MCP_TRANSPORT"] = "remote"
        importlib.reload(mcp_server)
        tools = run(mcp_server.mcp.list_tools())
        names = {t.name for t in tools}
        assert "list_users" not in names
        assert len(names) == 17  # 18 stdio tools minus list_users
        get_tasks_tool = next(t for t in tools if t.name == "get_tasks")
        assert "chat_id" not in get_tasks_tool.inputSchema.get("properties", {})
        add_task_tool = next(t for t in tools if t.name == "add_task")
        assert set(add_task_tool.inputSchema.get("properties", {})) == {"text", "due_date", "recur"}
        resources = run(mcp_server.mcp.list_resource_templates())
        assert resources == []

    def test_remote_tool_call_resolves_chat_id_from_token_not_argument(self):
        """The actual regression: an authenticated caller must only ever see
        their own data, no matter what chat_id a tool call argument claims."""
        os.environ["MCP_TRANSPORT"] = "remote"
        importlib.reload(mcp_server)
        _fresh_db()

        bot.state["users"]["555"] = bot._new_user()
        bot.state["users"]["555"]["tasks"] = ["mine"]
        bot.state["users"]["999"] = bot._new_user()
        bot.state["users"]["999"]["tasks"] = ["someone else's"]
        bot.save_state(bot.state)

        from mcp.server.auth.middleware.auth_context import auth_context_var
        from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
        from mcp.server.auth.provider import AccessToken

        tok = AccessToken(token="tk", client_id="c1", scopes=["secretary"], subject="555")
        reset_token = auth_context_var.set(AuthenticatedUser(tok))
        try:
            own_text = run(mcp_server.mcp.call_tool("get_tasks", {}))[0].text
            # a caller-supplied chat_id isn't even in the schema — it's a no-op
            spoofed_text = run(mcp_server.mcp.call_tool("get_tasks", {"chat_id": "999"}))[0].text
        finally:
            auth_context_var.reset(reset_token)

        assert "mine" in own_text
        assert "someone else's" not in own_text
        assert own_text == spoofed_text
        assert "someone else's" not in spoofed_text
