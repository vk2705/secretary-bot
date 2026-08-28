import os
import re
import sys
import json
import time
import logging
import sqlite3
import secrets
import uuid
import calendar
import tempfile
from collections import defaultdict
from datetime import datetime, date, timedelta, time as dt_time
from io import BytesIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from cryptography.fernet import Fernet, InvalidToken
from timezonefinder import TimezoneFinder
_tf = TimezoneFinder()
from openai import AsyncOpenAI
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
DEFAULT_API_KEY = os.environ["OPENAI_API_KEY"]
# Groq API key — if set, users without their own key use Groq (free tier)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"
# Optional: used only once to migrate old single-user state.json to new format
MY_CHAT_ID = os.environ.get("MY_CHAT_ID")

# Fallback model when no Groq key and using bot owner's OpenAI key
DEFAULT_MODEL = "gpt-4o-mini"

STATE_FILE = "state.json"
DB_FILE = "bot_memory.db"
MAX_HISTORY = 20

# Encryption master key — generate once with: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Then add MASTER_KEY=<value> to your env file.
MASTER_KEY = os.environ.get("MASTER_KEY", "")
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet, MASTER_KEY
    if _fernet:
        return _fernet
    if not MASTER_KEY:
        # Auto-generate for single-node dev; loudly warn that it won't survive restart
        generated = Fernet.generate_key().decode()
        # Print the key to stderr only (never into the persistent log file)
        print(f"MASTER_KEY={generated}", file=sys.stderr, flush=True)
        logger.warning(
            "MASTER_KEY not set in env — generated a temporary key (printed to stderr). "
            "API keys will be unreadable after restart until you add MASTER_KEY to your env file."
        )
        MASTER_KEY = generated
    try:
        _fernet = Fernet(MASTER_KEY.encode() if isinstance(MASTER_KEY, str) else MASTER_KEY)
    except Exception as e:
        raise RuntimeError(f"Invalid MASTER_KEY: {e}") from e
    return _fernet


# Rate limiting: max AI calls per hour per user (backed by SQLite)
RATE_LIMIT = 30
RATE_WINDOW = 3600
# Per-tracker retention cap, enforced on write (log_tracker) and on /import.
TRACKER_LOG_CAP = 5000
# /debug prompt sends the prompt inline up to this length, as a document above it
# (Telegram's own message cap is 4096; this leaves room for delivery overhead).
DEBUG_PROMPT_INLINE_MAX = 4000
# Legacy in-memory snooze store. Superseded by the snooze_tokens SQLite table
# (which survives restarts); kept only so a button delivered before that switch
# still resolves. Nothing writes to it any more.
_snooze_cache: dict[str, dict] = {}  # token → {"message": ..., "reason": ...}
_app = None  # set in main(); used by tool executor to schedule reminders


# ─────────────────────── SQLite memory store ───────────────────────

def _db() -> sqlite3.Connection:
    """Return a thread-local SQLite connection with WAL mode."""
    con = sqlite3.connect(DB_FILE, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row
    return con


def _init_db() -> None:
    """Create tables and migrate existing notes/journal from state.json."""
    with _db() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS notes (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT    NOT NULL,
                text    TEXT    NOT NULL,
                ts      TEXT    NOT NULL,
                auto    INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS journal (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT    NOT NULL,
                entry   TEXT    NOT NULL,
                ts      TEXT    NOT NULL,
                auto    INTEGER DEFAULT 0
            );
            -- Encrypted user API keys
            CREATE TABLE IF NOT EXISTS api_keys (
                chat_id      TEXT PRIMARY KEY,
                encrypted_key TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );
            -- Persistent rate limiting (survives restarts)
            CREATE TABLE IF NOT EXISTS rate_log (
                chat_id TEXT NOT NULL,
                ts      REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rate_log ON rate_log(chat_id, ts);
            -- Profile memory: permanent facts about the user
            CREATE TABLE IF NOT EXISTS profile_memory (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT    NOT NULL,
                fact    TEXT    NOT NULL,
                ts      TEXT    NOT NULL
            );
            -- Episodic memory: recent events/observations with TTL
            CREATE TABLE IF NOT EXISTS episodic_memory (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    TEXT    NOT NULL,
                event      TEXT    NOT NULL,
                ts         TEXT    NOT NULL,
                expires_at TEXT    NOT NULL
            );
            -- Job fire log: detect missed jobs after restarts
            CREATE TABLE IF NOT EXISTS job_log (
                chat_id  TEXT NOT NULL,
                job_type TEXT NOT NULL,
                fired_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, job_type, fired_at)
            );
            -- User preferences: survive state.json overwrites
            CREATE TABLE IF NOT EXISTS user_prefs (
                chat_id TEXT NOT NULL,
                key     TEXT NOT NULL,
                value   TEXT NOT NULL,
                PRIMARY KEY (chat_id, key)
            );
            -- Reminder history: every reminder ever created, so past (removed) ones
            -- stay searchable even after they're gone from state.json.
            CREATE TABLE IF NOT EXISTS reminder_log (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id        TEXT    NOT NULL,
                reminder_uuid  TEXT,
                message        TEXT    NOT NULL,
                reason         TEXT,
                kind           TEXT    NOT NULL,
                time           TEXT,
                created_at     TEXT    NOT NULL,
                removed_at     TEXT
            );
            -- Payload behind each "🔁 Snooze 30 min" button. Persisted (not just
            -- an in-memory dict) so a restart between delivering a reminder and
            -- the user tapping Snooze doesn't turn the button into "Snooze
            -- expired" — restarts are routine now that a bot.py commit triggers one.
            CREATE TABLE IF NOT EXISTS snooze_tokens (
                token      TEXT PRIMARY KEY,
                chat_id    TEXT NOT NULL,
                message    TEXT NOT NULL,
                reason     TEXT,
                created_at TEXT NOT NULL
            );
            -- One-time codes proving a Telegram account requested an MCP Google link
            -- (Milestone B / AUTH-02). 10-minute TTL, single-use, consumed by mcp_server.py.
            CREATE TABLE IF NOT EXISTS mcp_link_codes (
                code       TEXT PRIMARY KEY,
                chat_id    TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at    TEXT
            );
            -- Verified Google identity -> Telegram chat_id, established once via
            -- mcp_link_codes. Read/written by mcp_server.py's OAuth provider.
            CREATE TABLE IF NOT EXISTS mcp_identity (
                google_sub TEXT PRIMARY KEY,
                email      TEXT,
                chat_id    TEXT NOT NULL,
                linked_at  TEXT NOT NULL
            );
            -- Dynamically registered OAuth clients (claude.ai etc.) against our
            -- own thin Authorization Server. Owned entirely by mcp_server.py.
            CREATE TABLE IF NOT EXISTS mcp_oauth_clients (
                client_id  TEXT PRIMARY KEY,
                info_json  TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            -- In-flight claude.ai /authorize requests, parked while we round-trip
            -- through Google to establish identity. Single-use, short TTL.
            CREATE TABLE IF NOT EXISTS mcp_pending_authorize (
                state                 TEXT PRIMARY KEY,
                client_id             TEXT NOT NULL,
                scopes                TEXT NOT NULL,
                code_challenge        TEXT NOT NULL,
                redirect_uri          TEXT NOT NULL,
                redirect_uri_explicit INTEGER NOT NULL,
                resource              TEXT,
                client_state          TEXT,
                created_at            TEXT NOT NULL,
                expires_at            REAL NOT NULL
            );
            -- Our own short-lived authorization codes, minted after a completed
            -- Google login resolves to a bound chat_id.
            CREATE TABLE IF NOT EXISTS mcp_auth_codes (
                code                  TEXT PRIMARY KEY,
                client_id             TEXT NOT NULL,
                chat_id               TEXT NOT NULL,
                scopes                TEXT NOT NULL,
                code_challenge        TEXT NOT NULL,
                redirect_uri          TEXT NOT NULL,
                redirect_uri_explicit INTEGER NOT NULL,
                resource              TEXT,
                expires_at            REAL NOT NULL,
                created_at            TEXT NOT NULL
            );
            -- Issued access/refresh tokens, stored as sha256 hashes only —
            -- never the plaintext token, mirroring how api_keys are never
            -- stored in plaintext.
            CREATE TABLE IF NOT EXISTS mcp_tokens (
                token_hash TEXT PRIMARY KEY,
                kind       TEXT NOT NULL,   -- 'access' | 'refresh'
                client_id  TEXT NOT NULL,
                chat_id    TEXT NOT NULL,
                scopes     TEXT NOT NULL,
                expires_at REAL,            -- NULL = no expiry (refresh tokens)
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_notes_chat        ON notes(chat_id);
            CREATE INDEX IF NOT EXISTS idx_journal_chat      ON journal(chat_id);
            CREATE INDEX IF NOT EXISTS idx_profile_chat      ON profile_memory(chat_id);
            CREATE INDEX IF NOT EXISTS idx_episodic_chat     ON episodic_memory(chat_id);
            CREATE INDEX IF NOT EXISTS idx_reminder_log_chat ON reminder_log(chat_id);
            CREATE INDEX IF NOT EXISTS idx_mcp_identity_chat ON mcp_identity(chat_id);
            CREATE INDEX IF NOT EXISTS idx_mcp_tokens_chat   ON mcp_tokens(chat_id);
        """)
    # one-time migration of notes/journal still living in state.json
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
            changed = False
            with _db() as con:
                for cid, u in s.get("users", {}).items():
                    for n in u.pop("notes", []):
                        con.execute(
                            "INSERT INTO notes(chat_id,text,ts,auto) VALUES(?,?,?,0)",
                            (cid, n, datetime.utcnow().isoformat())
                        )
                        changed = True
                    for e in u.pop("journal", []):
                        con.execute(
                            "INSERT INTO journal(chat_id,entry,ts,auto) VALUES(?,?,?,0)",
                            (cid, e["entry"], e.get("ts", datetime.utcnow().isoformat()))
                        )
                        changed = True
            if changed:
                with open(STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump(s, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("DB migration failed: %s", e)
    # Migrate plaintext API keys from state.json to encrypted DB
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
            migrated_keys = False
            for cid, u in s.get("users", {}).items():
                plain = u.get("llm", {}).get("api_key")
                if plain:
                    db_store_key(cid, plain)
                    u["llm"]["api_key"] = None
                    migrated_keys = True
                    logger.info("Migrated API key for user %s to encrypted DB", cid)
            if migrated_keys:
                with open(STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Key migration failed: %s", e)


# ── MCP link-code helpers (Milestone B / AUTH-02) ──

def db_create_link_code(chat_id: str, ttl_minutes: int = 10) -> str:
    """Create a one-time code proving this Telegram account requested an MCP
    Google link. Consumed by mcp_server.py's OAuth provider, which owns the
    verification and (google_sub -> chat_id) binding — this only proves the
    Telegram side of the handshake."""
    code = secrets.token_hex(4)
    now = datetime.utcnow()
    with _db() as con:
        con.execute(
            "INSERT INTO mcp_link_codes(code, chat_id, created_at, expires_at) VALUES(?,?,?,?)",
            (code, str(chat_id), now.isoformat(), (now + timedelta(minutes=ttl_minutes)).isoformat())
        )
    return code


# ── notes helpers ──

def db_add_note(chat_id: str, text: str, auto: bool = False) -> int:
    with _db() as con:
        cur = con.execute(
            "INSERT INTO notes(chat_id,text,ts,auto) VALUES(?,?,?,?)",
            (str(chat_id), text, datetime.utcnow().isoformat(), int(auto))
        )
        return cur.lastrowid


# ── user_prefs helpers ──

def db_set_pref(chat_id: str, key: str, value: str) -> None:
    """Upsert a single user preference into SQLite."""
    with _db() as con:
        con.execute(
            "INSERT INTO user_prefs(chat_id, key, value) VALUES(?,?,?) "
            "ON CONFLICT(chat_id, key) DO UPDATE SET value=excluded.value",
            (str(chat_id), key, value)
        )


def db_get_pref(chat_id: str, key: str) -> str | None:
    """Return a user preference value, or None if not set."""
    with _db() as con:
        row = con.execute(
            "SELECT value FROM user_prefs WHERE chat_id=? AND key=?",
            (str(chat_id), key)
        ).fetchone()
    return row["value"] if row else None


def db_get_all_prefs(chat_id: str) -> dict:
    """Return all stored prefs for a user as a dict."""
    with _db() as con:
        rows = con.execute(
            "SELECT key, value FROM user_prefs WHERE chat_id=?",
            (str(chat_id),)
        ).fetchall()
    return {r["key"]: r["value"] for r in rows}


def db_delete_pref(chat_id: str, key: str) -> None:
    """Delete a single user preference from SQLite, if present."""
    with _db() as con:
        con.execute(
            "DELETE FROM user_prefs WHERE chat_id=? AND key=?",
            (str(chat_id), key)
        )


def db_get_notes(chat_id: str) -> list[sqlite3.Row]:
    with _db() as con:
        return con.execute(
            "SELECT id,text,ts,auto FROM notes WHERE chat_id=? ORDER BY id",
            (str(chat_id),)
        ).fetchall()


def db_remove_note(chat_id: str, row_id: int) -> bool:
    with _db() as con:
        cur = con.execute(
            "DELETE FROM notes WHERE id=? AND chat_id=?",
            (row_id, str(chat_id))
        )
        return cur.rowcount > 0


def db_search_notes(chat_id: str, q: str) -> list[sqlite3.Row]:
    with _db() as con:
        return con.execute(
            "SELECT id,text,ts FROM notes WHERE chat_id=? AND lower(text) LIKE ?",
            (str(chat_id), f"%{q.lower()}%")
        ).fetchall()


# ── journal helpers ──

def db_add_journal(chat_id: str, entry: str, auto: bool = False) -> int:
    with _db() as con:
        cur = con.execute(
            "INSERT INTO journal(chat_id,entry,ts,auto) VALUES(?,?,?,?)",
            (str(chat_id), entry, datetime.utcnow().isoformat(), int(auto))
        )
        return cur.lastrowid


def db_get_journal(chat_id: str, limit: int = 0) -> list[sqlite3.Row]:
    sql = "SELECT id,entry,ts,auto FROM journal WHERE chat_id=? ORDER BY id DESC"
    params: tuple = (str(chat_id),)
    if limit:
        sql += " LIMIT ?"
        params = (str(chat_id), limit)
    with _db() as con:
        rows = con.execute(sql, params).fetchall()
    return list(reversed(rows))


def db_search_journal(chat_id: str, q: str) -> list[sqlite3.Row]:
    with _db() as con:
        return con.execute(
            "SELECT id,entry,ts FROM journal WHERE chat_id=? AND lower(entry) LIKE ?",
            (str(chat_id), f"%{q.lower()}%")
        ).fetchall()


# ── reminder history (survives removal, unlike state.json reminders) ──

def db_log_reminder(chat_id: str, reminder_uuid: str | None, message: str,
                     reason: str | None, kind: str, time: str | None) -> int:
    with _db() as con:
        cur = con.execute(
            "INSERT INTO reminder_log(chat_id,reminder_uuid,message,reason,kind,time,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (str(chat_id), reminder_uuid, message, reason, kind, time, datetime.utcnow().isoformat())
        )
        return cur.lastrowid


def db_mark_reminder_removed(chat_id: str, reminder_uuid: str) -> None:
    with _db() as con:
        con.execute(
            "UPDATE reminder_log SET removed_at=? WHERE chat_id=? AND reminder_uuid=? AND removed_at IS NULL",
            (datetime.utcnow().isoformat(), str(chat_id), reminder_uuid)
        )


def db_save_snooze(token: str, chat_id: int, message: str, reason: str | None) -> None:
    """Persist the payload behind a Snooze button. Also prunes entries older
    than a day — a button nobody tapped in 24h is not coming back."""
    with _db() as con:
        con.execute(
            "INSERT OR REPLACE INTO snooze_tokens(token, chat_id, message, reason, created_at) "
            "VALUES(?,?,?,?,?)",
            (token, str(chat_id), message, reason, datetime.utcnow().isoformat()),
        )
        con.execute(
            "DELETE FROM snooze_tokens WHERE created_at < ?",
            ((datetime.utcnow() - timedelta(days=1)).isoformat(),),
        )


def db_take_snooze(token: str) -> dict | None:
    """Pop a snooze payload by token (single-use, like the in-memory cache)."""
    with _db() as con:
        row = con.execute(
            "SELECT message, reason FROM snooze_tokens WHERE token=?", (token,)
        ).fetchone()
        if row is None:
            return None
        con.execute("DELETE FROM snooze_tokens WHERE token=?", (token,))
    return {"message": row["message"], "reason": row["reason"]}


def db_get_reminder_history(chat_id: str, limit: int = 50) -> list[sqlite3.Row]:
    with _db() as con:
        return con.execute(
            "SELECT * FROM reminder_log WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (str(chat_id), limit)
        ).fetchall()


def db_search_reminders(chat_id: str, q: str) -> list[sqlite3.Row]:
    with _db() as con:
        return con.execute(
            "SELECT * FROM reminder_log WHERE chat_id=? AND "
            "(lower(message) LIKE ? OR lower(reason) LIKE ?) ORDER BY id DESC",
            (str(chat_id), f"%{q.lower()}%", f"%{q.lower()}%")
        ).fetchall()


# ── encrypted API key store ──

def db_store_key(chat_id: str, plaintext_key: str) -> None:
    """Encrypt and persist a user's API key."""
    encrypted = _get_fernet().encrypt(plaintext_key.encode()).decode()
    with _db() as con:
        con.execute(
            "INSERT OR REPLACE INTO api_keys(chat_id, encrypted_key, updated_at) VALUES(?,?,?)",
            (str(chat_id), encrypted, datetime.utcnow().isoformat())
        )


def db_get_key(chat_id: str) -> str | None:
    """Decrypt and return a user's API key, or None if not stored."""
    with _db() as con:
        row = con.execute(
            "SELECT encrypted_key FROM api_keys WHERE chat_id=?", (str(chat_id),)
        ).fetchone()
    if not row:
        return None
    try:
        return _get_fernet().decrypt(row[0].encode()).decode()
    except Exception:
        logger.warning("Failed to decrypt key for %s — possibly wrong MASTER_KEY", chat_id)
        return None


def db_delete_key(chat_id: str) -> None:
    with _db() as con:
        con.execute("DELETE FROM api_keys WHERE chat_id=?", (str(chat_id),))


# ── profile & episodic memory ──

def db_add_profile_memory(chat_id: str, fact: str) -> None:
    with _db() as con:
        con.execute(
            "INSERT INTO profile_memory(chat_id,fact,ts) VALUES(?,?,?)",
            (str(chat_id), fact, datetime.utcnow().isoformat())
        )


def db_get_profile_memory(chat_id: str) -> list[sqlite3.Row]:
    with _db() as con:
        return con.execute(
            "SELECT id,fact,ts FROM profile_memory WHERE chat_id=? ORDER BY id",
            (str(chat_id),)
        ).fetchall()


def db_add_episodic_memory(chat_id: str, event: str, ttl_days: int = 30) -> None:
    expires = (datetime.utcnow() + timedelta(days=ttl_days)).isoformat()
    with _db() as con:
        con.execute(
            "INSERT INTO episodic_memory(chat_id,event,ts,expires_at) VALUES(?,?,?,?)",
            (str(chat_id), event, datetime.utcnow().isoformat(), expires)
        )


def db_get_episodic_memory(chat_id: str, limit: int = 20) -> list[sqlite3.Row]:
    now = datetime.utcnow().isoformat()
    with _db() as con:
        # Purge expired entries opportunistically to keep the table bounded.
        con.execute(
            "DELETE FROM episodic_memory WHERE chat_id=? AND expires_at <= ?",
            (str(chat_id), now)
        )
        return con.execute(
            "SELECT id,event,ts FROM episodic_memory "
            "WHERE chat_id=? AND expires_at > ? ORDER BY id DESC LIMIT ?",
            (str(chat_id), now, limit)
        ).fetchall()


def db_expire_episodic(chat_id: str) -> None:
    """Delete expired episodic memories."""
    now = datetime.utcnow().isoformat()
    with _db() as con:
        con.execute(
            "DELETE FROM episodic_memory WHERE chat_id=? AND expires_at <= ?",
            (str(chat_id), now)
        )


# ── job log (missed-job detection) ──

def db_log_job(chat_id: str, job_type: str) -> None:
    with _db() as con:
        con.execute(
            "INSERT OR IGNORE INTO job_log(chat_id,job_type,fired_at) VALUES(?,?,?)",
            (str(chat_id), job_type, datetime.utcnow().isoformat())
        )


def db_last_job_fired(chat_id: str, job_type: str) -> str | None:
    with _db() as con:
        row = con.execute(
            "SELECT MAX(fired_at) FROM job_log WHERE chat_id=? AND job_type=?",
            (str(chat_id), job_type)
        ).fetchone()
    return row[0] if row else None

# Reserved command names that cannot be used as tracker names
RESERVED_COMMANDS = {
    "start", "tasks", "addtask", "removetask", "setcontext", "context",
    "subscribe", "unsubscribe", "settimezone", "remind", "addtracker",
    "trackers", "removetracker", "checkin", "clear", "setmodel",
    "setapikey", "clearapikey", "journal", "weekly", "export",
    "streak", "adminstats", "habit", "mystats", "pomodoro",
    "quiethours", "insights", "setcheckin",
    "donetask", "archive", "reset", "help",
    "prioritize", "today", "note", "notes", "removenote", "search",
    "setlanguage", "clearlanguage", "compress", "broadcast", "feedback",
    "time", "suggest", "duedate", "extend", "swap", "reflect", "focus",
    "mute", "unmute", "limit",
}

# ─────────────────────── state ───────────────────────

def _new_user(**overrides) -> dict:
    base = {
        "tasks": [],
        "history": [],
        "context": "",
        "checkin_enabled": False,
        "timezone": "UTC",
        "timezone_confirmed": False,
        "reminders": [],
        "trackers": {},
        "habits": {},
        "journal": [],
        "activity_days": [],
        "quiet_hours": {"start": None, "end": None},
        "checkin_times": {"morning": "08:00", "evening": "21:00"},
        "archived_tasks": [],
        "notes": [],
        "today_focus": {"date": "", "text": ""},
        "language": "",
        "milestones_sent": [],
        "muted_until": "",
        "llm": {"model": None, "api_key": None},
        "pending_checkin": None,
        "debug_clock": "",
        "debug_clock_expires": "",
        # Persona is a Milestone A / Phase 4 concept, landed early and minimally
        # (see project memory: onboarding + persona, Milestone B side-quest).
        # Full Phase 4 (pressure dial, never-do rules, drift resistance) is not
        # this — just a character voice, always on, defaulting to Jeeves.
        "persona": "Jeeves",
        "honorific": "",
    }
    base.update(overrides)
    return base


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "users" not in data:
            # Migrate old single-user format
            if MY_CHAT_ID:
                return {
                    "users": {
                        MY_CHAT_ID: _new_user(
                            tasks=data.get("tasks", []),
                            history=data.get("history", []),
                            checkin_enabled=True,
                        )
                    }
                }
            return {"users": {}}
        return data
    return {"users": {}}


def save_state(state: dict) -> None:
    # Atomic write: dump to a temp file in the same dir, then replace.
    d = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


state = load_state()


def get_user(chat_id: int) -> dict:
    key = str(chat_id)
    if key not in state["users"]:
        state["users"][key] = _new_user()
    u = state["users"][key]
    # Forward-fill top-level keys added in newer versions
    for k, v in _new_user().items():
        u.setdefault(k, v)
    # Forward-fill nested llm dict
    for k, v in _new_user()["llm"].items():
        u["llm"].setdefault(k, v)
    # Overlay critical prefs from SQLite (survive state.json overwrites)
    db_tz = db_get_pref(key, "timezone")
    if db_tz:
        u["timezone"] = db_tz
    if db_get_pref(key, "timezone_confirmed") == "1":
        u["timezone_confirmed"] = True
    # Debug-only simulated clock (DEBUG-02): SQLite is the durable store so
    # the override survives a state.json overwrite/restore, exactly like
    # timezone above. The expiry is overlaid onto the user dict too, so
    # _debug_now stays a pure function of the user dict and never needs its
    # own database access.
    db_clock = db_get_pref(key, "debug_clock")
    if db_clock:
        u["debug_clock"] = db_clock
    db_clock_expires = db_get_pref(key, "debug_clock_expires")
    if db_clock_expires:
        u["debug_clock_expires"] = db_clock_expires
    return u


def _set_user_timezone(chat_id: int, user: dict, tz_str: str) -> None:
    """Set the timezone and mark it as explicitly confirmed by the user (as
    opposed to sitting on the "UTC" default nobody actually chose). Persists
    both to SQLite so they survive a state.json overwrite/restore, exactly
    like the timezone override itself. Call sites still call save_state()
    afterwards, same as before this helper existed."""
    user["timezone"] = tz_str
    user["timezone_confirmed"] = True
    db_set_pref(str(chat_id), "timezone", tz_str)
    db_set_pref(str(chat_id), "timezone_confirmed", "1")


_TZ_NOT_CONFIRMED_MSG = (
    "⚠️ I don't know your timezone yet, so I can't schedule this correctly. "
    "Set it first with /settimezone <city or IANA zone> (or share your 📍 location), then try again."
)


def _timezone_gate(user: dict) -> dict | None:
    """Return a tool-error dict if the user's timezone hasn't been explicitly
    confirmed yet -- scheduling anything at an absolute clock time against a
    guessed/default timezone risks silently landing at the wrong real-world
    time (PLAN.md #61). None means it's fine to proceed. Not applied to
    relative delays (e.g. "in 30 minutes"), which don't depend on timezone."""
    if user.get("timezone_confirmed"):
        return None
    return {
        "error": "Timezone not confirmed yet. Ask the user what city or timezone they're in, "
                 "call set_timezone with it, then retry this exact request.",
    }


def _is_new_user(user: dict) -> bool:
    """True for an account that hasn't meaningfully engaged yet — no context
    set, no tasks, at most one recorded activity day. Shared by start()'s
    onboarding message and build_system_prompt()'s honorific-ask instruction,
    so the two can never silently diverge on what "new" means."""
    return not user["context"] and not user["tasks"] and len(user.get("activity_days", [])) <= 1


# ─────────────────────── rate limiting ───────────────────────

def is_rate_limited(chat_id: int) -> bool:
    """Return True if user exceeded RATE_LIMIT calls in RATE_WINDOW seconds. Persistent across restarts."""
    key = str(chat_id)
    now = time.time()
    cutoff = now - RATE_WINDOW
    with _db() as con:
        # prune old entries for this user
        con.execute("DELETE FROM rate_log WHERE chat_id=? AND ts < ?", (key, cutoff))
        count = con.execute(
            "SELECT count(*) FROM rate_log WHERE chat_id=?", (key,)
        ).fetchone()[0]
        if count >= RATE_LIMIT:
            return True
        con.execute("INSERT INTO rate_log(chat_id,ts) VALUES(?,?)", (key, now))
    return False


# ─────────────────────── date helpers ───────────────────────

def _local_date(ts_iso: str, tz_str: str = "UTC") -> str:
    """Convert a stored UTC-naive ISO timestamp to the user's local calendar date.

    ts columns are written with datetime.utcnow(), so a truncated ts[:10] shows
    the UTC calendar day, which is off by one for anyone whose local time has
    already crossed midnight while it's still the previous day in UTC (or vice
    versa). Converting to the user's timezone before taking the date fixes that.
    """
    try:
        dt = datetime.fromisoformat(ts_iso).replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(ZoneInfo(tz_str)).date().isoformat()
    except (ValueError, ZoneInfoNotFoundError, KeyError):
        return ts_iso[:10]


# ─────────────────────── activity / streak ───────────────────────

def _touch_activity(user: dict) -> None:
    today = datetime.utcnow().date().isoformat()
    days = user.setdefault("activity_days", [])
    if today not in days:
        days.append(today)
        if len(days) > 400:
            user["activity_days"] = days[-365:]


def _get_streak(user: dict) -> int:
    days = set(user.get("activity_days", []))
    if not days:
        return 0
    streak = 0
    d = _today(user=user)
    while d.isoformat() in days:
        streak += 1
        d -= timedelta(days=1)
    return streak


async def _check_milestones(chat_id: int, app) -> None:
    """Send congratulation messages for newly crossed milestones."""
    user = get_user(chat_id)
    if _debug_now(user) is not None:
        # Durable-write path (milestones_sent) + real Telegram send -- never
        # let a simulated /debug clock create or permanently suppress a real
        # milestone. See CR-02.
        return
    sent = user.setdefault("milestones_sent", [])
    msgs = []

    streak = _get_streak(user)
    for s in (7, 14, 30, 60, 100):
        key = f"streak_{s}"
        if streak >= s and key not in sent:
            sent.append(key)
            msgs.append(f"🏆 {s}-day streak! You've been active every day for {s} days in a row. Excellent consistency!")

    n_done = len(user.get("archived_tasks", []))
    for n in (5, 10, 25, 50, 100):
        key = f"tasks_{n}"
        if n_done >= n and key not in sent:
            sent.append(key)
            msgs.append(f"🎉 You've completed {n} tasks total! Every one of them counts.")

    if msgs:
        save_state(state)
        for m in msgs:
            try:
                await app.bot.send_message(chat_id=chat_id, text=m)
            except Exception as e:
                logger.error("Milestone message failed for %s: %s", chat_id, e)


# ─────────────────────── LLM tool definitions ───────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get the user's current local time and date.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_timezone",
            "description": (
                "Save the timezone the user has EXPLICITLY told you they are in. Convert the "
                "city/country they named to IANA form (e.g. a user who says they're in Berlin "
                "→ 'Europe/Berlin'). Also accepts offsets like 'UTC+3'. Never infer a timezone "
                "from the language they write in, their name, or any other indirect hint — if "
                "they haven't stated where they are, ask instead of calling this. Calling it "
                "marks the timezone as user-confirmed, which suppresses later prompts to ask."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "IANA name (e.g. 'Asia/Jerusalem') or offset (e.g. 'UTC+3').",
                    }
                },
                "required": ["timezone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_checkins",
            "description": (
                "Enable/disable daily morning+evening check-ins and alerts. enabled=true for "
                "check-ins, daily plans, accountability prompts or 'subscribe'; false to stop."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "description": "True to enable, false to disable."},
                    "morning": {"type": "string", "description": "HH:MM, default 08:00."},
                    "evening": {"type": "string", "description": "HH:MM, default 21:00."},
                },
                "required": ["enabled"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_persona",
            "description": (
                "Change the voice the bot speaks in — 'talk like Yoda', 'stop the butler thing', "
                "'talk normally'. Pass the character as given, or 'plain' for no persona. "
                "All later messages adopt that voice while staying substantively helpful."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "character": {
                        "type": "string",
                        "description": "Character/style name or short description, e.g. 'Yoda', 'Jeeves', 'plain'.",
                    }
                },
                "required": ["character"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_honorific",
            "description": (
                "Save how the user wants to be addressed ('call me Sir', 'just use my name, Alex'). "
                "Pass the exact form, or an empty string to drop it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "form": {
                        "type": "string",
                        "description": "e.g. 'Sir', 'Miss', 'Alex'. Empty string clears it.",
                    }
                },
                "required": ["form"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tasks",
            "description": "Get the user's current task list with their numbers.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "Add a new task to the user's task list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The task description."},
                    "due_date": {
                        "type": "string",
                        "description": "Optional due date in YYYY-MM-DD format.",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Mark a task as done and archive it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_number": {
                        "type": "integer",
                        "description": "1-based task number from the task list.",
                    }
                },
                "required": ["task_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_tracker",
            "description": "Log a numeric value to one of the user's custom trackers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tracker_name": {
                        "type": "string",
                        "description": "Name of the tracker (e.g. 'weight', 'steps').",
                    },
                    "value": {"type": "number", "description": "The numeric value to log."},
                },
                "required": ["tracker_name", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_reminder",
            "description": (
                "Schedule one reminder. Relative ('in 5 minutes') → delay_minutes, always one-off. "
                "Clock time → time, daily unless the user explicitly says one-time. "
                "For several times in one message, call once per time with the SAME once value."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The reminder message."},
                    "reason": {
                        "type": "string",
                        "description": "Why it matters to the user, if known (e.g. 'to keep your leg mobile'). Shown when it fires. Omit rather than invent.",
                    },
                    "delay_minutes": {
                        "type": "integer",
                        "description": "Fire once N minutes from now. Overrides time and once.",
                    },
                    "time": {
                        "type": "string",
                        "description": "Local HH:MM (24h). Required unless delay_minutes is set.",
                    },
                    "once": {
                        "type": "boolean",
                        "description": "Default false = daily. True only on explicit one-time request.",
                    },
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_reminders",
            "description": "List active reminders with numbers and reasons, so remove_reminder can target one. Call first when the user names a reminder by description rather than number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_history": {
                        "type": "boolean",
                        "description": "Also include past/removed reminders. Default false.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_reminder",
            "description": "Delete a reminder by its number (from get_reminders or /remind list).",
            "parameters": {
                "type": "object",
                "properties": {
                    "reminder_number": {
                        "type": "integer",
                        "description": "The reminder's number as shown by get_reminders (1-based).",
                    },
                },
                "required": ["reminder_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_journal_entry",
            "description": "Save a journal entry for the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The journal entry text."}
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": (
                "Silently save anything worth remembering — facts, decisions, plans, reflections. "
                "Call automatically whenever the user shares something meaningful; no command needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The content to save."},
                    "type": {
                        "type": "string",
                        "enum": ["profile", "episodic", "note", "journal"],
                        "description": "profile=stable user facts; episodic=time-bound events (~30d); note=facts/plans; journal=reflections.",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_journal",
            "description": "Retrieve the user's recent journal entries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max number of recent entries to return (default 10).",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_tracker",
            "description": "Create a new custom tracker (steps, weight, mood, sleep…) when the user asks to track something that doesn't exist yet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Lowercase letters only, e.g. 'steps'."},
                    "unit": {"type": "string", "description": "Optional unit, e.g. 'kg'. Empty if none."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_task",
            "description": "Permanently delete a task from the list without archiving it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_number": {"type": "integer", "description": "1-based task number."},
                },
                "required": ["task_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_trackers",
            "description": "List all the user's custom trackers with their latest logged value.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_habits",
            "description": "List all the user's habits with today's completion status and current streak.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_habit",
            "description": "Create a new daily habit to track.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Habit name, lowercase, no spaces (e.g. 'meditation', 'running')."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_habit",
            "description": "Mark a habit as done for today.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Habit name."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_habit",
            "description": "Delete a habit permanently.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Habit name to delete."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_notes",
            "description": "List all the user's quick notes.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_note",
            "description": "Save a quick note to the user's scratchpad.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The note text."},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_note",
            "description": "Delete a quick note by its number (from get_notes or /notes).",
            "parameters": {
                "type": "object",
                "properties": {
                    "note_number": {
                        "type": "integer",
                        "description": "The note's number as shown by get_notes (1-based).",
                    },
                },
                "required": ["note_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_today_focus",
            "description": "Set the user's main focus or intention for today.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Today's focus text."},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search across the user's tasks, notes, journal entries, and reminders (current and past/removed).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term (case-insensitive)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_streak",
            "description": "Get the user's current activity streak and total active days.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


async def _execute_tool(chat_id: int, name: str, args: dict) -> dict:
    """Run one tool call and return a JSON-serialisable result dict."""
    user = get_user(chat_id)

    if name == "set_timezone":
        tz_raw = (args.get("timezone") or "").strip()
        if not tz_raw:
            return {"error": "timezone is required"}
        tz_str = _normalize_tz(tz_raw)
        try:
            ZoneInfo(tz_str)
        except (ZoneInfoNotFoundError, KeyError):
            return {"error": f"Unknown timezone: {tz_raw}. Use an IANA name like Asia/Jerusalem or a UTC offset like UTC+3."}
        _set_user_timezone(chat_id, user, tz_str)
        save_state(state)
        if _app:
            schedule_user_checkins(_app, chat_id)
            schedule_user_alerts(_app, chat_id)
            for reminder in list(user.get("reminders", [])):
                if not reminder.get("annual"):
                    schedule_user_reminder(_app, chat_id, reminder)
        # Build friendly label
        try:
            _tz = ZoneInfo(tz_str)
            _now_dt = datetime.now(_tz)
            _off = _now_dt.strftime("%z")
            _label = f"UTC{_off[:3]}:{_off[3:]}" if len(_off) == 5 else tz_str
            _local_now = _now_dt.strftime("%H:%M")
        except Exception:
            _label = tz_str
            _local_now = None
        # The model does the place→IANA conversion itself, unverified, so for an
        # unknown or misspelled place it can land on a plausible-but-wrong zone
        # and then report it back in the user's own words ("7am Muhosransk
        # time") — hiding the substitution. Hand back what was actually stored,
        # plus the resulting local time, and require the model to say it.
        return {
            "success": True,
            "timezone": tz_str,
            "utc_offset": _label,
            "local_time_now": _local_now,
            "confirm_to_user": (
                f"Tell the user plainly which timezone was set — '{tz_str}' ({_label}) — and that "
                f"it is currently {_local_now} there. Use this zone name, never the place name they "
                "typed, unless the two clearly match. If it does not match where they said they "
                "are, say so and ask them to correct it."
            ) if _local_now else None,
        }

    if name == "set_checkins":
        enabled = bool(args.get("enabled", True))
        if enabled:
            gate = _timezone_gate(user)
            if gate:
                return gate
        morning = (args.get("morning") or "").strip()
        evening = (args.get("evening") or "").strip()
        def _valid_time(s):
            try:
                h, m = s.split(":")
                assert 0 <= int(h) <= 23 and 0 <= int(m) <= 59
                return True
            except Exception:
                return False
        times = user.get("checkin_times", {"morning": "08:00", "evening": "21:00"})
        if morning and _valid_time(morning):
            times["morning"] = morning
        if evening and _valid_time(evening):
            times["evening"] = evening
        user["checkin_times"] = times
        user["checkin_enabled"] = enabled
        save_state(state)
        if _app:
            schedule_user_checkins(_app, chat_id)
            schedule_user_alerts(_app, chat_id)
        if enabled:
            tz = user.get("timezone", "UTC")
            return {
                "success": True,
                "enabled": True,
                "morning": times["morning"],
                "evening": times["evening"],
                "timezone": tz,
            }
        return {"success": True, "enabled": False}

    if name == "set_persona":
        character = (args.get("character") or "").strip()
        if not character:
            return {"error": "character is required"}
        user["persona"] = character
        save_state(state)
        return {"success": True, "persona": character}

    if name == "set_honorific":
        form = (args.get("form") or "").strip()
        user["honorific"] = form
        save_state(state)
        return {"success": True, "honorific": form}

    if name == "get_current_time":
        try:
            tz = ZoneInfo(user.get("timezone", "UTC"))
        except Exception:
            tz = ZoneInfo("UTC")
        now = _now(tz, user=user)
        return {
            "time": now.strftime("%H:%M"),
            "date": now.strftime("%Y-%m-%d"),
            "weekday": now.strftime("%A"),
            "timezone": user.get("timezone", "UTC"),
        }

    if name == "get_tasks":
        tasks = user.get("tasks", [])
        return {
            "count": len(tasks),
            "tasks": [
                {"number": i + 1, "text": _task_text(t), "due": _task_due(t)}
                for i, t in enumerate(tasks)
            ],
        }

    if name == "add_task":
        text = (args.get("text") or "").strip()
        if not text:
            return {"error": "Task text is required"}
        raw_due = args.get("due_date")
        due = None
        due_error = None
        if raw_due:
            try:
                date.fromisoformat(raw_due)
                due = raw_due
            except ValueError:
                due_error = f"due_date '{raw_due}' is invalid (use YYYY-MM-DD); task added without due date."
        task = {"text": text, "due": due} if due else text
        user["tasks"].append(task)
        save_state(state)
        result = {"success": True, "task": text, "due": due}
        if due_error:
            result["warning"] = due_error
        return result

    if name == "complete_task":
        n = int(args.get("task_number", 0))
        tasks = user.get("tasks", [])
        if n < 1 or n > len(tasks):
            return {"error": f"Task {n} not found. There are {len(tasks)} tasks."}
        task = tasks[n - 1]
        recur = task.get("recur") if isinstance(task, dict) else None
        archived = user.setdefault("archived_tasks", [])
        archived.append({
            "text": _task_text(task),
            "due": _task_due(task),
            "completed_at": datetime.utcnow().isoformat(),
        })
        if len(archived) > 100:
            user["archived_tasks"] = archived[-100:]
        if recur:
            today = _today(user=user)
            current_due = _task_due(task) or today.isoformat()
            try:
                base = date.fromisoformat(current_due)
            except ValueError:
                base = today
            # Roll forward from today when the stored due date is already in the
            # past, otherwise completing a task neglected for a month just moves
            # it from 30 days overdue to 29 — still overdue, still in the 09:00
            # deadline alert, and needing 30 completions to catch up.
            if base < today:
                base = today

            def _advance(d0: date) -> date:
                if recur == "daily":
                    return d0 + timedelta(days=1)
                if recur == "weekly":
                    return d0 + timedelta(weeks=1)
                m = d0.month % 12 + 1
                y = d0.year + (1 if d0.month == 12 else 0)
                return date(y, m, min(d0.day, calendar.monthrange(y, m)[1]))

            next_due = _advance(base)
            tasks[n - 1] = {"text": _task_text(task), "due": next_due.isoformat(), "recur": recur}
            save_state(state)
            return {"success": True, "completed": _task_text(task), "next_due": next_due.isoformat(), "recurs": recur}
        else:
            tasks.pop(n - 1)
            save_state(state)
            await _check_milestones(chat_id, _app)
            return {"success": True, "completed": _task_text(task)}

    if name == "log_tracker":
        tname = (args.get("tracker_name") or "").lower().strip()
        trackers = user.get("trackers", {})
        if tname not in trackers:
            return {"error": f"Tracker '{tname}' not found.", "available": list(trackers.keys())}
        try:
            value = float(args["value"])
        except (TypeError, ValueError, KeyError):
            return {"error": "value must be a number"}
        log = trackers[tname].setdefault("log", [])
        log.append({"ts": datetime.utcnow().isoformat(), "value": value})
        if len(log) > TRACKER_LOG_CAP:
            trackers[tname]["log"] = log[-TRACKER_LOG_CAP:]
        unit = trackers[tname].get("unit", "")
        save_state(state)
        return {"success": True, "logged": f"{value}{unit}", "tracker": tname}

    if name == "add_reminder":
        message = (args.get("message") or "").strip()
        if not message:
            return {"error": "Reminder message is required"}
        reason = (args.get("reason") or "").strip() or None
        delay_minutes = args.get("delay_minutes")
        once = bool(args.get("once", False))
        # ── relative: fire once after N minutes ──
        if delay_minutes is not None:
            try:
                delay_minutes = int(delay_minutes)
                assert delay_minutes > 0
            except (ValueError, TypeError, AssertionError):
                return {"error": "delay_minutes must be a positive integer"}
            if not _app:
                return {"error": "Scheduler not available"}
            db_log_reminder(chat_id, None, message, reason, "once", None)
            async def _once_job(ctx: ContextTypes.DEFAULT_TYPE, _cid=chat_id, _msg=message, _reason=reason):
                await _run_reminder(ctx, _cid, {"message": _msg, "reason": _reason}, simulate=False)
            _app.job_queue.run_once(_once_job, when=delay_minutes * 60,
                                    name=f"once_{chat_id}_{uuid.uuid4()}")
            return {"success": True, "once_in_minutes": delay_minutes, "message": message}
        # ── absolute time ──
        gate = _timezone_gate(user)
        if gate:
            return gate
        time_str = (args.get("time") or "").strip()
        try:
            h, m = time_str.split(":")
            assert 0 <= int(h) <= 23 and 0 <= int(m) <= 59
        except (ValueError, AssertionError):
            return {"error": "time is required (HH:MM) when delay_minutes is not set"}
        if once:
            # one-shot at a specific clock time
            if not _app:
                return {"error": "Scheduler not available"}
            user_tz = user.get("timezone", "UTC")
            delay = _parse_once_delay(time_str, user_tz)
            if delay is None or delay <= 0:
                return {"error": f"Time {time_str} is in the past or invalid"}
            # Persisted (unlike the delay_minutes branch above) so restore_all_jobs
            # can re-arm it: a one-shot scheduled for tonight used to vanish on any
            # restart between now and then, silently, after the bot had confirmed it.
            reminder = {
                "id": str(uuid.uuid4()), "time": time_str, "message": message,
                "once": True, "reason": reason,
                "fire_at": (datetime.utcnow() + timedelta(seconds=delay)).isoformat(),
            }
            user.setdefault("reminders", []).append(reminder)
            save_state(state)
            db_log_reminder(chat_id, reminder["id"], message, reason, "once", time_str)
            schedule_user_reminder(_app, chat_id, reminder)
            return {"success": True, "once_at": time_str, "message": message}
        # ── daily recurring ──
        reminder = {"id": str(uuid.uuid4()), "time": time_str, "message": message, "once": False, "reason": reason}
        user.setdefault("reminders", []).append(reminder)
        save_state(state)
        db_log_reminder(chat_id, reminder["id"], message, reason, "daily", time_str)
        if _app:
            schedule_user_reminder(_app, chat_id, reminder)
        return {"success": True, "daily_at": time_str, "message": message}

    if name == "get_reminders":
        reminders = user.get("reminders", [])
        result = []
        for i, r in enumerate(reminders):
            if r.get("annual"):
                kind = f"annual {r.get('date', '')}"
            elif r.get("once"):
                kind = "once"
            else:
                kind = "daily"
            result.append({
                "number": i + 1, "time": r["time"], "kind": kind,
                "message": r["message"], "reason": r.get("reason"),
            })
        response = {"reminders": result, "count": len(result)}
        if args.get("include_history"):
            history = []
            for r in db_get_reminder_history(chat_id):
                history.append({
                    "message": r["message"], "reason": r["reason"], "kind": r["kind"],
                    "time": r["time"], "created_at": r["created_at"][:10],
                    "status": "removed" if r["removed_at"] else "active",
                })
            response["history"] = history
        return response

    if name == "remove_reminder":
        n = int(args.get("reminder_number", 0))
        reminders = user.get("reminders", [])
        if n < 1 or n > len(reminders):
            return {"error": f"Reminder {n} not found. There are {len(reminders)} reminders. Call get_reminders first."}
        removed = reminders.pop(n - 1)
        save_state(state)
        db_mark_reminder_removed(chat_id, removed["id"])
        if _app:
            job_name = f"reminder_{chat_id}_{removed['id']}"
            for job in _app.job_queue.get_jobs_by_name(job_name):
                job.schedule_removal()
        return {"success": True, "removed": removed["message"], "time": removed["time"]}

    if name == "create_tracker":
        tname = (args.get("name") or "").lower().strip()
        unit = (args.get("unit") or "").strip()
        if not tname or not tname.isalpha():
            return {"error": "Tracker name must be letters only (e.g. steps, weight, mood)."}
        if tname in RESERVED_COMMANDS:
            return {"error": f"'{tname}' is a reserved command name. Choose a different name."}
        trackers = user.setdefault("trackers", {})
        if tname in trackers:
            return {"already_exists": True, "name": tname, "unit": trackers[tname].get("unit", "")}
        trackers[tname] = {"unit": unit, "log": []}
        save_state(state)
        return {"success": True, "name": tname, "unit": unit,
                "tip": f"Tracker created. Now use log_tracker('{tname}', value) to log values."}

    if name == "add_journal_entry":
        text = (args.get("text") or "").strip()
        if not text:
            return {"error": "Journal text is required"}
        db_add_journal(chat_id, text, auto=False)
        return {"success": True, "date": _local_date(datetime.utcnow().isoformat(), user.get("timezone", "UTC"))}

    if name == "save_memory":
        text = (args.get("text") or "").strip()
        if not text:
            return {"error": "Text is required"}
        kind = (args.get("type") or "note").lower()
        if kind == "journal":
            db_add_journal(chat_id, text, auto=True)
        elif kind == "profile":
            db_add_profile_memory(chat_id, text)
        elif kind == "episodic":
            db_add_episodic_memory(chat_id, text, ttl_days=30)
        else:  # "note" or unknown
            db_add_note(chat_id, text, auto=True)
        result = {"success": True, "saved_as": kind}
        if kind == "journal":
            result["date"] = _local_date(datetime.utcnow().isoformat(), user.get("timezone", "UTC"))
        return result

    if name == "get_journal":
        limit = int(args.get("limit") or 10)
        rows = db_get_journal(chat_id, limit=limit)
        tz_str = user.get("timezone", "UTC")
        return {
            "entries": [{"date": _local_date(r["ts"], tz_str), "text": r["entry"]} for r in rows],
            "count": len(rows),
        }

    if name == "remove_task":
        n = int(args.get("task_number", 0))
        tasks = user.get("tasks", [])
        if n < 1 or n > len(tasks):
            return {"error": f"Task {n} not found. There are {len(tasks)} tasks."}
        removed = _task_text(tasks.pop(n - 1))
        save_state(state)
        return {"success": True, "removed": removed}

    if name == "get_trackers":
        trackers = user.get("trackers", {})
        result = []
        for tname, data in trackers.items():
            log = data.get("log", [])
            unit = data.get("unit", "")
            result.append({
                "name": tname,
                "unit": unit,
                "last_value": log[-1]["value"] if log else None,
                "last_date": log[-1]["ts"][:10] if log else None,
                "total_entries": len(log),
            })
        return {"trackers": result, "count": len(result)}

    if name == "get_habits":
        habits = user.get("habits", {})
        today = _today(user=user).isoformat()
        result = []
        for hname, data in habits.items():
            completions = data.get("completions", [])
            result.append({
                "name": hname,
                "done_today": today in completions,
                "streak": _habit_streak(completions, user=user),
                "total_completions": len(completions),
            })
        return {"habits": result, "count": len(result)}

    if name == "add_habit":
        hname = (args.get("name") or "").lower().strip().replace(" ", "_")
        if not hname:
            return {"error": "Habit name is required."}
        habits = user.setdefault("habits", {})
        if hname in habits:
            return {"already_exists": True, "name": hname}
        habits[hname] = {"completions": [], "created": date.today().isoformat()}
        save_state(state)
        return {"success": True, "name": hname}

    if name == "complete_habit":
        hname = (args.get("name") or "").lower().strip()
        habits = user.get("habits", {})
        if hname not in habits:
            available = list(habits.keys())
            return {"error": f"Habit '{hname}' not found.", "available": available}
        today = date.today().isoformat()
        completions = habits[hname].setdefault("completions", [])
        if today in completions:
            return {"already_done": True, "name": hname, "streak": _habit_streak(completions, user=user)}
        completions.append(today)
        if len(completions) > 365:
            habits[hname]["completions"] = completions[-365:]
        save_state(state)
        return {"success": True, "name": hname, "streak": _habit_streak(completions, user=user)}

    if name == "remove_habit":
        hname = (args.get("name") or "").lower().strip()
        habits = user.get("habits", {})
        if hname not in habits:
            return {"error": f"Habit '{hname}' not found.", "available": list(habits.keys())}
        del habits[hname]
        save_state(state)
        return {"success": True, "removed": hname}

    if name == "get_notes":
        rows = db_get_notes(chat_id)
        return {"notes": [{"id": r["id"], "number": i + 1, "text": r["text"]} for i, r in enumerate(rows)], "count": len(rows)}

    if name == "add_note":
        text = (args.get("text") or "").strip()
        if not text:
            return {"error": "Note text is required."}
        row_id = db_add_note(chat_id, text, auto=False)
        total = len(db_get_notes(chat_id))
        return {"success": True, "note": text, "total_notes": total}

    if name == "remove_note":
        n = int(args.get("note_number", 0))
        rows = db_get_notes(chat_id)
        if n < 1 or n > len(rows):
            return {"error": f"Note {n} not found. There are {len(rows)} notes. Call get_notes first."}
        removed = rows[n - 1]
        db_remove_note(chat_id, removed["id"])
        return {"success": True, "removed": removed["text"]}

    if name == "set_today_focus":
        text = (args.get("text") or "").strip()
        if not text:
            return {"error": "Focus text is required."}
        user["today_focus"] = {"date": date.today().isoformat(), "text": text}
        save_state(state)
        return {"success": True, "focus": text}

    if name == "search":
        q = (args.get("query") or "").lower().strip()
        if not q:
            return {"error": "Query is required."}
        results = {"tasks": [], "notes": [], "journal": [], "reminders": []}
        for i, t in enumerate(user.get("tasks", []), 1):
            if q in _task_text(t).lower():
                results["tasks"].append({"number": i, "text": _task_text(t)})
        for r in db_search_notes(chat_id, q):
            results["notes"].append({"id": r["id"], "text": r["text"]})
        for r in db_search_journal(chat_id, q):
            results["journal"].append({"date": r["ts"][:10], "excerpt": r["entry"][:100]})
        for r in db_search_reminders(chat_id, q):
            results["reminders"].append({
                "message": r["message"], "reason": r["reason"], "kind": r["kind"],
                "time": r["time"], "created_at": r["created_at"][:10],
                "status": "removed" if r["removed_at"] else "active",
            })
        total = sum(len(v) for v in results.values())
        return {"query": q, "total_matches": total, **results}

    if name == "get_streak":
        streak = _get_streak(user)
        total = len(user.get("activity_days", []))
        return {"current_streak": streak, "total_active_days": total}

    return {"error": f"Unknown tool: {name}"}


# ─────────────────────── habit helpers ───────────────────────

def _habit_streak(completions: list, user=None) -> int:
    """Consecutive days ending today or yesterday (so streak survives the day)."""
    days = set(completions)
    if not days:
        return 0
    streak = 0
    d = _today(user=user)
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    while d.isoformat() in days:
        streak += 1
        d -= timedelta(days=1)
    return streak


def _habit_summary_lines(habits: dict, user=None) -> list[str]:
    today = _today(user=user).isoformat()
    lines = []
    for name, data in habits.items():
        done = today in data.get("completions", [])
        streak = _habit_streak(data.get("completions", []), user=user)
        mark = "✓" if done else "○"
        lines.append(f"  {mark} {name}  ({streak}d streak)")
    return lines


# ─────────────────────── quiet hours ───────────────────────

def _is_muted(user: dict, *, simulate: bool = True) -> bool:
    """Return True if the user has an active /mute in effect. `simulate`
    controls whether an active /debug clock override is honored: True (the
    default, used by /debug fire) evaluates against the simulated now;
    False (used by the real schedule_user_* job wrappers) always evaluates
    against the real wall clock, so a forgotten debug-clock override can
    never suppress or wrongly fire a real scheduled job (CR-01)."""
    until = user.get("muted_until", "")
    if not until:
        return False
    try:
        now = _utcnow(user) if simulate else datetime.utcnow()
        return now < datetime.fromisoformat(until)
    except ValueError:
        return False


def _is_quiet_now(user: dict, *, simulate: bool = True) -> bool:
    """Return True if current local time falls within the user's quiet
    window. See `_is_muted`'s docstring for what `simulate` controls."""
    qh = user.get("quiet_hours", {})
    start_str = qh.get("start")
    end_str = qh.get("end")
    if not start_str or not end_str:
        return False
    try:
        tz = ZoneInfo(user.get("timezone", "UTC"))
    except (ZoneInfoNotFoundError, KeyError):
        tz = ZoneInfo("UTC")
    now = (_now(tz, user=user) if simulate else datetime.now(tz)).time().replace(tzinfo=None)
    sh, sm = (int(x) for x in start_str.split(":"))
    eh, em = (int(x) for x in end_str.split(":"))
    start_t = dt_time(sh, sm)
    end_t = dt_time(eh, em)
    if start_t <= end_t:
        return start_t <= now < end_t
    # Spans midnight
    return now >= start_t or now < end_t


# ─────────────────────── debug clock (DEBUG-02) ───────────────────────
# A persistent, bounded, per-account simulated "now" (D-01). `_debug_now` is
# the single place the override is resolved; `_now`/`_today`/`_utcnow` are
# thin call-site wrappers that are exact no-ops when no override is active.

def _debug_now(user: dict) -> datetime | None:
    """Return the active simulated-now override for this user as an aware
    datetime, or None if no override is active. Swallows a missing,
    unparseable or expired override rather than raising, in the same shape
    `_is_muted` already uses for a bad stored value. The expiry is always
    judged against the real wall clock, never the override, so an override
    can never extend itself."""
    raw = user.get("debug_clock")
    if not raw:
        return None
    expires_raw = user.get("debug_clock_expires")
    if not expires_raw:
        return None
    try:
        expires = datetime.fromisoformat(expires_raw)
    except ValueError:
        return None
    if datetime.utcnow() >= expires:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        try:
            tz = ZoneInfo(user.get("timezone", "UTC"))
        except (ZoneInfoNotFoundError, KeyError):
            tz = ZoneInfo("UTC")
        dt = dt.replace(tzinfo=tz)
    return dt


def _now(tz, user: dict = None) -> datetime:
    """datetime.now(tz), or the active simulated-now override converted into
    tz when one is set for this user."""
    debug_dt = _debug_now(user) if user is not None else None
    if debug_dt is not None:
        return debug_dt.astimezone(tz)
    return datetime.now(tz)


def _today(user: dict = None) -> date:
    """date.today(), or the active simulated-now override's date when one is
    set for this user. Deliberately server-local, matching date.today()'s
    existing semantics with no override active (D-P6) -- not user-timezone
    aware, and this scope deliberately doesn't change that."""
    debug_dt = _debug_now(user) if user is not None else None
    if debug_dt is not None:
        return debug_dt.date()
    return date.today()


def _utcnow(user: dict = None) -> datetime:
    """datetime.utcnow(), or the active simulated-now override converted to
    UTC and stripped to naive when one is set for this user -- so a one-token
    swap keeps comparisons against naive stored UTC ISO strings (e.g.
    muted_until) safe."""
    debug_dt = _debug_now(user) if user is not None else None
    if debug_dt is not None:
        return debug_dt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return datetime.utcnow()


# ─────────────────────── task helpers ───────────────────────

def _task_text(task) -> str:
    return task["text"] if isinstance(task, dict) else str(task)


def _task_due(task) -> str | None:
    return task.get("due") if isinstance(task, dict) else None


def _task_tags(task) -> list:
    return re.findall(r"#(\w+)", _task_text(task).lower())


def _format_task_line(task, idx: int, user=None) -> str:
    text = _task_text(task)
    due = _task_due(task)
    if not due:
        return f"{idx}. {text}"
    try:
        due_date = date.fromisoformat(due)
        days_left = (due_date - _today(user=user)).days
        if days_left < 0:
            badge = f" ⚠️ overdue {-days_left}d"
        elif days_left == 0:
            badge = " 🔴 DUE TODAY"
        elif days_left <= 3:
            badge = f" 🟡 due in {days_left}d"
        else:
            badge = f" (due {due})"
    except ValueError:
        badge = f" (due {due})"
    return f"{idx}. {text}{badge}"


def _tasks_for_prompt(tasks: list) -> str:
    if not tasks:
        return "none set yet"
    parts = []
    for t in tasks:
        text = _task_text(t)
        due = _task_due(t)
        parts.append(f"{text} [due {due}]" if due else text)
    return ", ".join(parts)


# ─────────────────────── LLM helpers ───────────────────────

def get_llm_client(user: dict, chat_id: int = 0) -> AsyncOpenAI:
    user_key = db_get_key(chat_id) or user["llm"].get("api_key")  # DB first, state fallback
    if user_key:
        if user_key.startswith("gsk_"):
            return AsyncOpenAI(api_key=user_key, base_url=GROQ_BASE_URL)
        return AsyncOpenAI(api_key=user_key)
    if GROQ_API_KEY:
        return AsyncOpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)
    return AsyncOpenAI(api_key=DEFAULT_API_KEY)


def get_model(user: dict, chat_id: int = 0) -> str:
    user_key = db_get_key(chat_id) or user["llm"].get("api_key")
    user_model = user["llm"].get("model")
    if user_key:
        if user_key.startswith("gsk_") and not user_model:
            return GROQ_DEFAULT_MODEL
        return user_model or DEFAULT_MODEL
    if GROQ_API_KEY:
        return user_model or GROQ_DEFAULT_MODEL
    return user_model or DEFAULT_MODEL


def build_system_prompt(user: dict, chat_id: int = 0) -> str:
    context_section = f"\nAbout this user: {user['context']}\n" if user["context"] else ""

    tracker_lines = []
    for name, data in user.get("trackers", {}).items():
        log = data.get("log", [])
        if log:
            last = log[-1]
            unit = data.get("unit", "")
            tracker_lines.append(f"  {name}: {last['value']}{unit} ({last['ts'][:10]})")
    tracker_section = (
        "\nRecent tracker readings:\n" + "\n".join(tracker_lines) + "\n"
        if tracker_lines else ""
    )

    habit_lines = _habit_summary_lines(user.get("habits", {}), user=user)
    habit_section = (
        "\nToday's habits:\n" + "\n".join(habit_lines) + "\n"
        if habit_lines else ""
    )

    tasks_str = _tasks_for_prompt(user["tasks"])

    focus = user.get("today_focus", {})
    # Comparison only -- routed through the simulated clock (when active) for
    # consistency with habit_section right above (WR-03). The write side in
    # set_today_focus/today_cmd stays on real time; only reads compare here.
    today_str = _today(user=user).isoformat()
    focus_section = (
        f"\nToday's focus: {focus['text']}\n"
        if focus.get("date") == today_str and focus.get("text") else ""
    )

    notes = db_get_notes(str(chat_id))
    notes_section = (
        "\nUser's recent notes:\n" + "\n".join(f"  • {r['text']}" for r in notes[-10:]) + "\n"
        if notes else ""
    )

    # Profile memory: permanent facts always in context
    profile = db_get_profile_memory(str(chat_id))
    profile_section = (
        "\nPermanent user facts:\n" + "\n".join(f"  • {r['fact']}" for r in profile) + "\n"
        if profile else ""
    )

    # Episodic memory: recent (non-expired) observations
    episodic = db_get_episodic_memory(str(chat_id), limit=10)
    episodic_section = (
        "\nRecent observations (last 30 days):\n" + "\n".join(f"  – {r['event']}" for r in episodic) + "\n"
        if episodic else ""
    )

    # Include last few journal entries for context
    recent_journal = db_get_journal(str(chat_id), limit=3)
    journal_tz = user.get("timezone", "UTC")
    journal_section = (
        "\nRecent journal entries:\n" + "\n".join(
            f"  – [{_local_date(r['ts'], journal_tz)}] {r['entry'][:80]}" for r in recent_journal
        ) + "\n"
        if recent_journal else ""
    )

    lang = user.get("language", "").strip()
    lang_instruction = (
        f"8. Always respond exclusively in {lang}. "
        "Do not switch to English even if the user writes in English.\n"
        if lang else ""
    )

    _mcp_domain = os.environ.get("MCP_REMOTE_DOMAIN")
    if _mcp_domain:
        connect_claude_instruction = (
            "10b. When asked how to connect you to Claude (Claude Desktop, Claude Code, or claude.ai), "
            "explain both steps, in order:\n"
            "  1) In claude.ai: Settings → Connectors → Add custom connector, paste this URL exactly: "
            f"https://{_mcp_domain}/mcp — no client ID or secret needed, it registers itself. Click Connect.\n"
            "  2) That opens Google sign-in. Before or during that, send /link here in Telegram, tap the "
            "link it replies with, and sign in with the *same* Google account — that's what binds Google "
            "to this Telegram account (one-time, expires in 10 minutes). If they connect in claude.ai "
            "before ever running /link, they'll get a clear error telling them to /link first and retry.\n"
            "  After both are done, Claude can access their tasks/habits/notes/journal directly.\n"
        )
    else:
        connect_claude_instruction = (
            "10b. If asked how to connect you to Claude: the remote MCP server isn't configured on this "
            "deployment right now, so only Claude Desktop/Code (local, stdio) can connect, not claude.ai. "
            "Say so plainly rather than describing a link that won't work.\n"
        )

    tz_str = user.get("timezone", "UTC")
    try:
        from zoneinfo import ZoneInfo as _ZI
        _tz = _ZI(tz_str)
        from datetime import datetime as _dt
        _now_dt = _dt.now(_tz)
        _offset = _now_dt.strftime("%z")
        _utc_label = f"UTC{_offset[:3]}:{_offset[3:]}" if len(_offset) == 5 else tz_str
    except Exception:
        _utc_label = tz_str
    if user.get("timezone_confirmed"):
        tz_section = f"\nThe user's timezone is {tz_str} ({_utc_label}), confirmed by them.\n"
    else:
        tz_section = (
            f"\nThe user's timezone defaults to {tz_str} ({_utc_label}) but has NOT been "
            "confirmed by them yet.\n"
        )

    # Persona: Milestone A / Phase 4 concept, landed early and minimally as a
    # user request during Milestone B — see project memory. This is deliberately
    # NOT the full Phase 4 (no pressure dial, no never-do rules, no drift-testing
    # across 50+ turns) — just a character voice, always on, default Jeeves.
    # Set only via set_persona/set_honorific (talking to the bot), never a
    # settings screen or slash command, matching PROJECT.md's stated constraint.
    persona = (user.get("persona") or "Jeeves").strip()
    honorific = (user.get("honorific") or "").strip()
    if persona.lower() in ("plain", "none", "off"):
        persona_instruction = ""
    else:
        persona_instruction = (
            f"Adopt the voice of {persona} in *how* you phrase replies — word choice, tone, small "
            "mannerisms. This is decoration on your phrasing only.\n"
        )
    if honorific:
        honorific_instruction = (
            f'Address the user as "{honorific}" where it fits naturally, not forced into every sentence.\n'
        )
    elif _is_new_user(user):
        # /start's onboarding text asks this too, but plenty of users never
        # type /start — they just start talking. Without this, a brand-new
        # user chatting freely would never get asked at all.
        honorific_instruction = (
            "This user hasn't said how they'd like to be addressed yet. Early in this "
            "conversation — within your first reply or two, not necessarily the very first — "
            "ask naturally (e.g. 'and how should I address you?'), then call set_honorific once "
            "they answer. One question, not an interrogation; if they ignore it, drop it.\n"
        )
    else:
        honorific_instruction = ""

    return (
        "You are a personal secretary and accountability coach bot on Telegram.\n\n"
        "Your job:\n"
        "0. Literal requests always win over voice: an exact word, an exact number, an exact "
        "format, a direct factual answer — output exactly that, nothing added, nothing in "
        "character. Persona is for ordinary conversation, never for a literal request.\n"
        f"{persona_instruction}{honorific_instruction}"
        "1. Be a helpful, direct assistant.\n"
        "2. Proactively hold the user accountable for their goals and tasks.\n"
        "3. During check-ins, ask about specific tasks from their task list.\n"
        "4. Be warm but firm. Don't accept vague excuses without gentle pushback.\n"
        "5. Keep responses concise — this is a chat, not an essay.\n"
        "6. Don't offer hotlines or unsolicited emotional support suggestions.\n"
        "7. Correct mistakes naturally and briefly when they occur.\n"
        "8. Use your tools proactively. When a user says they want to do something, "
        "need a reminder, or mentions a goal, call the relevant tool immediately "
        "(add_task, add_reminder, log_tracker, create_tracker, etc.) without asking for confirmation first.\n"
        "9. If a user asks to track something new (steps, sleep, mood, etc.) and it doesn't exist yet, "
        "call create_tracker first, then log_tracker to log the initial value if provided.\n"
        "10. When asked what you can do or how to use you, explain concisely: tasks, trackers, "
        "reminders, habits, journal, check-ins, and that the user can speak naturally.\n"
        f"{connect_claude_instruction}"
        "11. Silently call save_memory whenever the user shares a personal fact, decision, plan, "
        "or reflection that is worth remembering for future conversations. "
        "Choose the right type: 'profile' for permanent facts about the user (name, job, allergies, preferences), "
        "'episodic' for recent events or observations that are relevant for ~30 days, "
        "'journal' for reflections or day summaries, 'note' for short reminders or plans. "
        "Do NOT tell the user you are saving it — just save it in the background.\n"
        "12. Exception to #8: if the user's timezone is not yet confirmed (see below) and they ask "
        "to schedule anything at a specific clock time — a reminder like \"remind me at 6am\", a "
        "meeting or task at a given hour, a check-in time, quiet hours — do NOT call the tool yet. "
        "Ask what city or timezone they're in first, call set_timezone with their answer, then "
        "complete the original request and confirm the resolved time naming the timezone THEY gave "
        "you. Never name, guess, or imply any timezone the user has not actually confirmed — not "
        "in the confirmation, not in passing. This does not apply to relative delays like \"in 30 "
        "minutes\", which don't need a timezone. If a scheduling tool call ever fails because the "
        "timezone isn't confirmed, treat that as the same signal: ask, call set_timezone, then "
        "retry the original request.\n"
        f"{lang_instruction}"
        f"{tz_section}{context_section}{tracker_section}{habit_section}{focus_section}{profile_section}{episodic_section}{notes_section}{journal_section}"
        f"\nThe user's tracked tasks: {tasks_str}\n"
    )


# ─────────────────────── tool selection ───────────────────────
# The full TOOLS schema is ~2400 tokens and is re-sent on every request in the
# tool-call loop, so a two-round turn pays for it twice. After round 1 the model
# has already chosen its tools, and the only ones it still plausibly needs are
# the ones it just called plus their natural follow-ups (get_reminders →
# remove_reminder, create_tracker → log_tracker). Narrowing rounds 2+ to that
# set leaves round 1 — the round that actually decides what gets called —
# completely untouched.

# Every tool that CHANGES something stays available in all rounds. An earlier
# attempt narrowed rounds 2+ to the caller's own domain group, which broke a
# real cross-domain request: "look at my reminders and create a task from them"
# called get_reminders in round 1, found add_task withheld in round 2, and
# duplicated the reminder instead — silently doing the wrong thing rather than
# failing. A round-2 request is by nature the "now act on it" half of a turn,
# so which action it needs cannot be predicted from what it read first.
_WRITE_TOOLS = frozenset({
    "add_task", "complete_task", "remove_task", "set_today_focus",
    "add_reminder", "remove_reminder",
    "log_tracker", "create_tracker",
    "add_habit", "complete_habit", "remove_habit",
    "add_note", "remove_note",
    "add_journal_entry", "save_memory",
    "set_timezone", "set_checkins", "set_persona", "set_honorific",
    "get_current_time",  # resolving relative dates is a prerequisite for many writes
})

# Read-only lookups, by contrast, are what a first round is FOR. Once the model
# has the data in the transcript it has no reason to re-fetch it, so these are
# the ones worth withholding later — with the group rule below keeping a
# read-then-read chain (get_reminders → get_reminders(include_history)) intact.
_TOOL_GROUPS = (
    {"get_tasks", "add_task", "complete_task", "remove_task", "set_today_focus"},
    {"get_reminders", "add_reminder", "remove_reminder"},
    {"get_trackers", "create_tracker", "log_tracker"},
    {"get_habits", "add_habit", "complete_habit", "remove_habit"},
    {"get_notes", "add_note", "remove_note"},
    {"get_journal", "add_journal_entry"},
)


def _tools_for_round(called: set[str]) -> list:
    """Return the tool schemas to send once the model has already called
    `called`. Keeps every write tool plus the read tools it has shown interest
    in; falls back to the full list when nothing is known, so a caller that
    somehow reaches a later round with no recorded calls is never left with
    fewer tools than it started with."""
    if not called:
        return TOOLS
    keep = set(called) | _WRITE_TOOLS
    for group in _TOOL_GROUPS:
        if called & group:
            keep |= group
    return [t for t in TOOLS if t["function"]["name"] in keep]


# ─────────────────────── chat ───────────────────────

async def chat(chat_id: int, user_message: str, system: str = None, touch_activity: bool = True) -> str:
    user = get_user(chat_id)
    if touch_activity:
        # Only genuine user-initiated turns count toward the activity streak and
        # clear a pending check-in — proactive bot-initiated messages (check-ins,
        # weekly digest, missed-checkin catch-up) pass touch_activity=False so
        # sending a nudge is never mistaken for the user having responded.
        _touch_activity(user)
        user["pending_checkin"] = None
    system = system or build_system_prompt(user, chat_id)
    client = get_llm_client(user, chat_id)
    model = get_model(user, chat_id)

    # Build the full message list (history + new user message)
    messages = (
        [{"role": "system", "content": system}]
        + user["history"]
        + [{"role": "user", "content": user_message}]
    )

    reply = None
    try:
        # Tool call loop — up to 5 rounds
        called_tools: set[str] = set()
        for _round in range(5):
            # Round 0 always offers the full schema so the model's initial
            # choice is never constrained; later rounds narrow to what it has
            # actually reached for, which is where the duplicate cost is.
            round_tools = TOOLS if _round == 0 else _tools_for_round(called_tools)
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=600,
                    temperature=0.7,
                    tools=round_tools,
                    tool_choice="auto",
                )
            except Exception as tool_err:
                # Model may not support tools (e.g. custom model); retry without
                err_str = str(tool_err).lower()
                if any(w in err_str for w in ("tool", "function", "unsupported")):
                    response = await client.chat.completions.create(
                        model=model,
                        messages=messages,
                        max_tokens=600,
                        temperature=0.7,
                    )
                else:
                    raise

            msg = response.choices[0].message
            if not msg.tool_calls:
                reply = (msg.content or "").strip()
                break

            # Execute each tool call and append results
            messages.append(msg)
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                called_tools.add(tc.function.name)
                result = await _execute_tool(chat_id, tc.function.name, args)
                logger.info("Tool %s(%s) → %s", tc.function.name, args, result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
        else:
            reply = "I got stuck in a loop. Please try again."

    except Exception as e:
        logger.error("LLM call failed for %s (%s): %s", chat_id, type(e).__name__, e)
        err = str(e).lower()
        if "auth" in err or "401" in err or "incorrect api key" in err:
            return "⚠️ API key rejected. Use /clearapikey to revert to the default model."
        if "rate" in err or "429" in err:
            return "⚠️ API rate limit hit. Try again in a moment."
        if "model" in err and "not found" in err:
            return "⚠️ Model not found. Use /setmodel to pick a valid model."
        return "⚠️ AI service temporarily unavailable. Please try again."

    # A model can legitimately return content=None with no tool calls (most
    # often right after a tool round). That used to become "" — which Telegram
    # rejects outright ("message text is empty"), and which poisoned history
    # with an empty assistant turn that degraded every later prompt.
    if not reply or not reply.strip():
        logger.warning("Empty model reply for %s; substituting fallback text", chat_id)
        reply = "⚠️ I didn't manage to put that into words. Could you say it again?"

    # Store only the user turn and final text reply in history
    user["history"].append({"role": "user", "content": user_message})
    user["history"].append({"role": "assistant", "content": reply})
    if len(user["history"]) > MAX_HISTORY:
        user["history"] = user["history"][-MAX_HISTORY:]
    save_state(state)
    return reply


# ─────────────────────── scheduling helpers ───────────────────────

def _parse_local_time(time_str: str, tz_str: str) -> dt_time:
    """Return timezone-aware dt_time from 'HH:MM' and IANA tz string."""
    try:
        tz = ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, KeyError):
        tz = ZoneInfo("UTC")
    h, m = (int(x) for x in time_str.split(":"))
    return dt_time(h, m, tzinfo=tz)


async def _run_checkin(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, label: str, *, simulate: bool = True
) -> str | None:
    """Send the morning or evening check-in message for chat_id, including the
    stale-tracker nudge and the unanswered-check-in acknowledgement. Shared by
    the per-user scheduled check-in job (via schedule_user_checkins's thin
    wrapper) and `/debug fire checkin_morning`/`checkin_evening` so the two
    paths can never diverge. Passes touch_activity=False to chat() so a
    proactive check-in is never mistaken for the user having responded.
    Returns a short reason string when quiet hours or mute suppress the
    check-in, or when something in the try block raised ("send failed" --
    WR-01); None once it sent one. `simulate` (default True, as used by
    /debug fire) controls whether the guards *and* this runner's own
    date/time reads (the stale-tracker check below) honor an active /debug
    clock override; the real scheduled wrapper always passes simulate=False
    so the real job is provably never affected by it (CR-01)."""
    user_now = get_user(chat_id)
    if _is_quiet_now(user_now, simulate=simulate):
        return "quiet hours"
    if _is_muted(user_now, simulate=simulate):
        return "muted"
    # Real-time-only user handle for this runner's own date reads below --
    # None makes `_today`/`_now` ignore any active /debug clock override
    # (see docstring, CR-01). `user_now` itself keeps flowing through the
    # rest of the function unchanged since it's still needed for real data
    # (trackers, pending_checkin, etc.), not just time.
    time_user = user_now if simulate else None
    is_morning = (label == "morning")
    variety_instruction = (
        "Open with a natural greeting that's noticeably different from how you've opened "
        "recent check-ins — check the conversation history and don't reuse the same phrasing "
        "or structure. Where it genuinely fits, weave in something specific and recent — a "
        "task, a journal entry, a note, or something you remember about the user — so it "
        "reads as personal rather than templated. Don't force a callback if nothing fits."
    )
    if is_morning:
        prompt = (
            f"It's morning check-in time. {variety_instruction} "
            "Then ask what they plan to work on today from their task list -- pick 1-2 "
            "specific tasks. Push for a concrete commitment, not a vague intention: get "
            "them to say what exactly they'll do and roughly when today they'll do it "
            "(implementation intentions work far better than 'I'll get to it')."
        )
    else:
        prompt = (
            f"It's evening check-in time. {variety_instruction} "
            "Ask how their day went and specifically about progress on their tasks. "
            "If they didn't do much, gently push back and encourage them to do "
            "at least one small thing. Then, separately, ask what they plan to do "
            "tomorrow -- again pushing for a specific action and a specific time, not "
            "just a vague plan."
        )
    try:
        dynamic_prompt = prompt
        if is_morning:
            # Append stale-tracker reminder if any tracker hasn't been logged in 2+ days
            stale = []
            for tname, tdata in user_now.get("trackers", {}).items():
                log = tdata.get("log", [])
                if log:
                    last_ts = log[-1]["ts"][:10]
                    try:
                        if (_today(user=time_user) - date.fromisoformat(last_ts)).days >= 2:
                            stale.append(tname)
                    except ValueError:
                        pass
            if stale:
                dynamic_prompt += (
                    f" Also mention that these trackers haven't been updated in 2+ days "
                    f"and nudge the user to log: {', '.join(stale)}."
                )
        if user_now.get("pending_checkin"):
            dynamic_prompt += (
                " Note: the user did not reply to your last check-in message. "
                "Briefly acknowledge the silence and vary your wording instead of "
                "repeating the same question."
            )
        reply = await chat(chat_id, dynamic_prompt, touch_activity=False)
        await context.bot.send_message(chat_id=chat_id, text=reply)
        user_now["pending_checkin"] = label
        save_state(state)
        db_log_job(str(chat_id), f"checkin_{'morning' if is_morning else 'evening'}")
    except Exception as e:
        logger.error("Check-in failed for %s: %s", chat_id, e)
        return "send failed"
    return None


def schedule_user_checkins(app: Application, chat_id: int) -> None:
    """Schedule (or reschedule) per-user morning/evening check-in jobs."""
    user = get_user(chat_id)
    tz_str = user.get("timezone", "UTC")
    enabled = user.get("checkin_enabled", False)
    times = user.get("checkin_times", {"morning": "08:00", "evening": "21:00"})

    for label in ["morning", "evening"]:
        job_name = f"checkin_{label}_{chat_id}"
        for job in app.job_queue.get_jobs_by_name(job_name):
            job.schedule_removal()
        if not enabled:
            continue

        t = _parse_local_time(times.get(label, "08:00" if label == "morning" else "21:00"), tz_str)

        async def _checkin_wrapper(context: ContextTypes.DEFAULT_TYPE, _cid=chat_id, _label=label):
            await _run_checkin(context, _cid, _label, simulate=False)

        app.job_queue.run_daily(_checkin_wrapper, time=t, name=job_name)


async def _run_reminder(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, reminder: dict, *, simulate: bool = True
) -> str | None:
    """Deliver a single reminder through the LLM (so wording varies and can
    surface *why* it matters from memory/context, instead of a frozen static
    string) and register a snooze token. Shared by the per-reminder scheduled
    job (via schedule_user_reminder's thin wrapper), the once/delay-based
    jobs created inline in add_reminder, the snooze re-fire, and `/debug fire
    reminder <n>` so none of these paths can diverge. Returns a short reason
    string when quiet hours or mute suppress the reminder, or when the send
    itself raised ("send failed" -- WR-01); None once it sent one. See
    `_run_checkin`'s docstring for what `simulate` controls (CR-01)."""
    u = get_user(chat_id)
    if _is_quiet_now(u, simulate=simulate):
        return "quiet hours"
    if _is_muted(u, simulate=simulate):
        return "muted"
    msg = reminder["message"]
    reason = reminder.get("reason")
    try:
        prompt = (
            f"A scheduled reminder is firing now: \"{msg}\"."
            + (f" Why it matters, as recorded: {reason}." if reason else
               " No reason was recorded for it -- if you know from memory or "
               "conversation history why this matters to the user, weave it in "
               "briefly; otherwise just deliver the reminder.")
            + " Deliver it as a short, natural nudge -- vary the phrasing and "
              "opening from your recent messages (check conversation history), "
              "don't reuse the same template every time. One to two sentences, "
              "not a lecture."
        )
        reply = await chat(chat_id, prompt, touch_activity=False)
        # uuid4, not a truncated monotonic clock: the old token wrapped every
        # ~2.8h, so two reminders that far apart collided and snoozing the older
        # button replayed the newer one's text. Stored in SQLite rather than a
        # process-local dict so a restart between delivery and the tap doesn't
        # turn the button into "Snooze expired" — restarts are routine now that
        # a bot.py commit triggers one.
        token = uuid.uuid4().hex
        db_save_snooze(token, chat_id, msg, reason)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔁 Snooze 30 min", callback_data=f"snooze_{token}_30"),
        ]])
        await context.bot.send_message(
            chat_id=chat_id, text=reply, reply_markup=keyboard
        )
    except Exception as e:
        logger.error("Reminder failed for %s: %s", chat_id, e)
        return "send failed"
    return None


def _drop_reminder(chat_id: int, reminder_id: str) -> None:
    """Remove a fired one-shot reminder from state so it doesn't linger in
    /remind list or get re-armed by restore_all_jobs on the next restart."""
    user = get_user(chat_id)
    before = user.get("reminders", [])
    user["reminders"] = [r for r in before if r.get("id") != reminder_id]
    if len(user["reminders"]) != len(before):
        save_state(state)
        db_mark_reminder_removed(chat_id, reminder_id)


def schedule_user_reminder(app: Application, chat_id: int, reminder: dict) -> None:
    user = get_user(chat_id)
    job_name = f"reminder_{chat_id}_{reminder['id']}"
    for job in app.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()

    # One-shot reminders carry an absolute fire_at (UTC ISO) and run once, then
    # delete themselves; daily ones repeat at a local wall-clock time.
    if reminder.get("once"):
        fire_at = reminder.get("fire_at")
        if not fire_at:
            return
        try:
            delay = (datetime.fromisoformat(fire_at) - datetime.utcnow()).total_seconds()
        except ValueError:
            return
        if delay <= 0:
            # Its moment passed while the bot was down — drop it rather than
            # firing a stale "remind me at 23:00" the next morning.
            _drop_reminder(chat_id, reminder["id"])
            return

        async def _once_wrapper(context: ContextTypes.DEFAULT_TYPE, _cid=chat_id, _reminder=reminder):
            try:
                await _run_reminder(context, _cid, _reminder, simulate=False)
            finally:
                _drop_reminder(_cid, _reminder["id"])

        app.job_queue.run_once(_once_wrapper, when=delay, name=job_name)
        return

    t = _parse_local_time(reminder["time"], user.get("timezone", "UTC"))

    async def _reminder_wrapper(context: ContextTypes.DEFAULT_TYPE, _cid=chat_id, _reminder=reminder):
        await _run_reminder(context, _cid, _reminder, simulate=False)

    app.job_queue.run_daily(_reminder_wrapper, time=t, name=job_name)


async def _run_deadline_alert(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, *, simulate: bool = True
) -> str | None:
    """Send the 09:00 deadline alert (overdue/due-today/due-soon tasks) plus any
    annual reminders due today for chat_id. Shared by the 09:00 scheduled job
    (via schedule_user_alerts's thin wrapper) and `/debug fire deadline_alert`
    so the two paths can never diverge. Returns a short reason string when
    quiet hours or mute suppress the alert, when nothing was due and no
    annual reminder matched today ("nothing due"), or when something was due
    but every send attempt raised ("send failed" -- WR-01); None once it sent
    at least one message — the same suppression-reason contract as the other
    five runners (_run_checkin, _run_habit_reminder, _run_idle_nudge,
    _run_weekly_digest, _run_reminder). See `_run_checkin`'s docstring for
    what `simulate` controls (CR-01)."""
    u = get_user(chat_id)
    if _is_quiet_now(u, simulate=simulate):
        return "quiet hours"
    if _is_muted(u, simulate=simulate):
        return "muted"
    # Real-time-only when simulate=False (real scheduled job) so overdue/
    # due-today badges are provably computed against the real date, not a
    # forgotten /debug clock override -- see `_run_checkin`'s docstring.
    today = _today(user=(u if simulate else None))
    alerts = []
    for task in u.get("tasks", []):
        due = _task_due(task)
        if not due:
            continue
        try:
            due_date = date.fromisoformat(due)
            days_left = (due_date - today).days
            text = _task_text(task)
            if days_left < 0:
                alerts.append(f"⚠️ Overdue {-days_left}d: {text}")
            elif days_left == 0:
                alerts.append(f"🔴 Due TODAY: {text}")
            elif days_left <= 3:
                alerts.append(f"🟡 Due in {days_left}d: {text}")
        except ValueError:
            pass
    sent = False
    attempted = False
    if alerts:
        attempted = True
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text="📅 Deadline alert:\n" + "\n".join(alerts)
            )
            sent = True
        except Exception as e:
            logger.error("Deadline alert failed for %s: %s", chat_id, e)

    # Fire annual reminders whose MM-DD matches today
    today_mmdd = today.strftime("%m-%d")
    for r in u.get("reminders", []):
        if r.get("annual") and r.get("date") == today_mmdd:
            attempted = True
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"📅 Annual reminder: {r['message']}"
                )
                sent = True
            except Exception as e:
                logger.error("Annual reminder failed for %s: %s", chat_id, e)

    if sent:
        return None
    return "send failed" if attempted else "nothing due"


async def _run_habit_reminder(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, *, simulate: bool = True
) -> str | None:
    """Send the list of not-yet-done-today habits at 20:00. Shared by the
    scheduled job (via schedule_user_alerts's thin wrapper) and
    `/debug fire habit_reminder` so the two paths can never diverge. Returns
    a short reason string when quiet hours or mute suppress the reminder, when
    every habit is already done today ("nothing undone"), or when the send
    itself raised ("send failed" -- WR-01); None once it sent one. See
    `_run_checkin`'s docstring for what `simulate` controls (CR-01)."""
    u = get_user(chat_id)
    if _is_quiet_now(u, simulate=simulate):
        return "quiet hours"
    if _is_muted(u, simulate=simulate):
        return "muted"
    habits = u.get("habits", {})
    # Real-time-only when simulate=False -- see `_run_deadline_alert`.
    today_str = _today(user=(u if simulate else None)).isoformat()
    undone = [n for n, d in habits.items() if today_str not in d.get("completions", [])]
    if not undone:
        return "nothing undone"
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🔔 Habit reminder — not yet done today:\n"
                + "\n".join(f"  • {n}" for n in undone)
                + "\nUse /habit done <name> to log them."
            )
        )
    except Exception as e:
        logger.error("Habit reminder failed for %s: %s", chat_id, e)
        return "send failed"
    return None


async def _run_idle_nudge(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, *, simulate: bool = True
) -> str | None:
    """Send an idle nudge at 11:00 if chat_id has been inactive 3+ days.
    Shared by the scheduled job (via schedule_user_alerts's thin wrapper) and
    `/debug fire idle_nudge` so the two paths can never diverge. Returns a
    short reason string when quiet hours or mute suppress the nudge, when the
    user has no tasks and no habits, when the last active day is under three
    days old, or when the send itself raised ("send failed" -- WR-01); None
    once it sent one. See `_run_checkin`'s docstring for what `simulate`
    controls (CR-01)."""
    u = get_user(chat_id)
    if _is_quiet_now(u, simulate=simulate):
        return "quiet hours"
    if _is_muted(u, simulate=simulate):
        return "muted"
    if not u.get("tasks") and not u.get("habits"):
        return "no tasks or habits"
    days = sorted(u.get("activity_days", []))
    if not days:
        return "no recent inactivity"
    last_active = date.fromisoformat(days[-1])
    # Real-time-only when simulate=False -- see `_run_deadline_alert`.
    if (_today(user=(u if simulate else None)) - last_active).days < 3:
        return "no recent inactivity"
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "👋 I haven't heard from you in a few days — just checking in!\n"
                "How are things going with your goals? "
                "Use /checkin to start a conversation, or just say hi."
            )
        )
    except Exception as e:
        logger.error("Idle nudge failed for %s: %s", chat_id, e)
        return "send failed"
    return None


async def _run_weekly_digest(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, *, simulate: bool = True
) -> str | None:
    """Send the weekly digest every Sunday at 10:00. Shared by the scheduled
    job (via schedule_user_alerts's thin wrapper) and
    `/debug fire weekly_digest` so the two paths can never diverge. Passes
    touch_activity=False to chat() for the same reason _run_checkin does.
    Returns a short reason string when quiet hours or mute suppress the
    digest, when today isn't Sunday, or when the send itself raised
    ("send failed" -- WR-01); None once it sent one. See `_run_checkin`'s
    docstring for what `simulate` controls (CR-01)."""
    u = get_user(chat_id)
    if _is_quiet_now(u, simulate=simulate):
        return "quiet hours"
    if _is_muted(u, simulate=simulate):
        return "muted"
    # Use user's local date for the Sunday check. Real-time-only when
    # simulate=False -- see `_run_deadline_alert`.
    tz = ZoneInfo(u.get("timezone", "UTC"))
    today = _now(tz, user=(u if simulate else None)).date()
    if today.weekday() != 6:
        return "not sunday"
    try:
        tasks_str = _tasks_for_prompt(u["tasks"])
        habits_str = "; ".join(
            f"{n}: {_habit_streak(h.get('completions',[]), user=u)}-day streak"
            for n, h in u.get("habits", {}).items()
        ) or "none"
        week_cutoff = (today - timedelta(days=7)).isoformat()
        n_done = sum(
            1 for t in u.get("archived_tasks", [])
            if (t.get("completed_at") or "")[:10] >= week_cutoff
        )
        journal_count = len([
            r for r in db_get_journal(str(chat_id))
            if r["ts"][:10] >= week_cutoff
        ])
        prompt = (
            f"Weekly digest for this user:\n"
            f"Tasks: {tasks_str}\n"
            f"Habits: {habits_str}\n"
            f"Tasks completed this week: {n_done} total\n"
            f"Journal entries this week: {journal_count}\n\n"
            "Write a brief, warm weekly digest (3-4 sentences): "
            "acknowledge their progress, highlight one strength, "
            "suggest one priority for the coming week. Be specific, not generic."
        )
        reply = await chat(
            chat_id, prompt,
            system="You are a supportive weekly accountability coach.",
            touch_activity=False,
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📅 Weekly digest:\n\n{reply}"
        )
    except Exception as e:
        logger.error("Weekly digest failed for %s: %s", chat_id, e)
        return "send failed"
    return None


def schedule_user_alerts(app: Application, chat_id: int) -> None:
    """Schedule daily deadline alert (09:00) and habit reminder (20:00) jobs."""
    user = get_user(chat_id)
    tz_str = user.get("timezone", "UTC")
    enabled = user.get("checkin_enabled", False)

    for job_name in [
        f"deadline_alert_{chat_id}", f"habit_reminder_{chat_id}",
        f"idle_nudge_{chat_id}", f"weekly_digest_{chat_id}",
    ]:
        for job in app.job_queue.get_jobs_by_name(job_name):
            job.schedule_removal()

    if not enabled:
        return

    # ── deadline alert at 09:00 ──
    async def _deadline_alert_wrapper(context: ContextTypes.DEFAULT_TYPE, _cid=chat_id):
        await _run_deadline_alert(context, _cid, simulate=False)

    app.job_queue.run_daily(
        _deadline_alert_wrapper,
        time=_parse_local_time("09:00", tz_str),
        name=f"deadline_alert_{chat_id}",
    )

    # ── habit reminder at 20:00 ──
    async def _habit_reminder_wrapper(context: ContextTypes.DEFAULT_TYPE, _cid=chat_id):
        await _run_habit_reminder(context, _cid, simulate=False)

    app.job_queue.run_daily(
        _habit_reminder_wrapper,
        time=_parse_local_time("20:00", tz_str),
        name=f"habit_reminder_{chat_id}",
    )

    # ── idle nudge at 11:00 — fires only if user has been inactive 3+ days ──
    async def _idle_nudge_wrapper(context: ContextTypes.DEFAULT_TYPE, _cid=chat_id):
        await _run_idle_nudge(context, _cid, simulate=False)

    app.job_queue.run_daily(
        _idle_nudge_wrapper,
        time=_parse_local_time("11:00", tz_str),
        name=f"idle_nudge_{chat_id}",
    )

    # ── Weekly digest every Sunday at 10:00 ──
    async def _weekly_digest_wrapper(context: ContextTypes.DEFAULT_TYPE, _cid=chat_id):
        await _run_weekly_digest(context, _cid, simulate=False)

    app.job_queue.run_daily(
        _weekly_digest_wrapper,
        time=_parse_local_time("10:00", tz_str),
        name=f"weekly_digest_{chat_id}",
    )


def restore_all_jobs(app: Application) -> None:
    """Recreate all scheduled jobs from persisted state on startup.
    Also detect and catch-up any check-ins missed while bot was down (within 2h window).
    """
    now_utc = datetime.utcnow()
    catchup_window = timedelta(hours=2)

    for cid_str, user in state["users"].items():
        cid = int(cid_str)
        schedule_user_checkins(app, cid)
        schedule_user_alerts(app, cid)
        # list() because schedule_user_reminder can drop an expired one-shot
        # from this same list while we're iterating it.
        for reminder in list(user.get("reminders", [])):
            # Annual reminders fire via the deadline-alert job's date check, not
            # as their own daily job — scheduling them here would make them fire
            # every day instead of once a year.
            if not reminder.get("annual"):
                schedule_user_reminder(app, cid, reminder)

        # Missed check-in catch-up
        if not user.get("checkin_enabled"):
            continue
        tz_str = user.get("timezone", "UTC")
        times = user.get("checkin_times", {"morning": "08:00", "evening": "21:00"})
        for label in ("morning", "evening"):
            job_type = f"checkin_{label}"
            last_fired = db_last_job_fired(cid_str, job_type)
            try:
                tz = ZoneInfo(tz_str)
            except Exception:
                tz = ZoneInfo("UTC")
            t_str = times.get(label, "08:00" if label == "morning" else "21:00")
            h, m = (int(x) for x in t_str.split(":"))
            scheduled_today = datetime.now(tz).replace(hour=h, minute=m, second=0, microsecond=0)
            scheduled_utc = scheduled_today.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
            if scheduled_utc < now_utc and (now_utc - scheduled_utc) < catchup_window:
                if not last_fired or last_fired[:16] < scheduled_utc.isoformat()[:16]:
                    # Schedule a one-shot catch-up in 10 seconds
                    async def _catchup(ctx, _cid=cid, _label=label):
                        u = get_user(_cid)
                        if _is_quiet_now(u) or _is_muted(u):
                            return
                        catchup_variety = (
                            "Vary your opening from recent check-ins instead of using a generic "
                            "template, and reference something specific and recent if it fits naturally."
                        )
                        prompt = (
                            f"Missed morning check-in catch-up: greet the user and ask about their plans. {catchup_variety}"
                            if _label == "morning" else
                            f"Missed evening check-in catch-up: ask briefly how their day went. {catchup_variety}"
                        )
                        reply = await chat(_cid, prompt, touch_activity=False)
                        await ctx.bot.send_message(chat_id=_cid, text=reply)
                        u["pending_checkin"] = _label
                        save_state(state)
                        db_log_job(str(_cid), f"checkin_{_label}")
                    app.job_queue.run_once(_catchup, when=10)

# ─────────────────────── tracker helpers ───────────────────────

def _days_ago_iso(n: int) -> str:
    return (datetime.utcnow() - timedelta(days=n)).isoformat()


def _tracker_stats(name: str, data: dict) -> str:
    log = data.get("log", [])
    unit = data.get("unit", "")
    if not log:
        return f"No data logged for {name} yet."
    values = [e["value"] for e in log if isinstance(e["value"], (int, float))]
    if not values:
        return f"No numeric data for {name}."
    avg = sum(values) / len(values)
    mn, mx = min(values), max(values)

    trend = ""
    recent = [e for e in log if e.get("ts", "") >= _days_ago_iso(7)]
    if len(recent) >= 2:
        delta = recent[-1]["value"] - recent[0]["value"]
        sign = "+" if delta >= 0 else ""
        trend = f"\n7-day change: {sign}{delta:.2f}{unit}"

    return (
        f"\U0001f4ca {name} stats ({len(values)} entries):\n"
        f"Latest: {values[-1]}{unit}\n"
        f"Average: {avg:.2f}{unit}\n"
        f"Min: {mn}{unit}  Max: {mx}{unit}{trend}"
    )


def _tracker_history(name: str, data: dict, n: int = 10) -> str:
    log = data.get("log", [])
    unit = data.get("unit", "")
    if not log:
        return f"No entries for {name}."
    recent = log[-n:]
    lines = [f"\U0001f4cb {name} history (last {len(recent)}):"]
    for e in reversed(recent):
        lines.append(f"  {e['ts'][:16].replace('T', ' ')}  →  {e['value']}{unit}")
    return "\n".join(lines)


_SPARK_BARS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list) -> str:
    """Return an ASCII sparkline string from a list of numbers."""
    if not values:
        return ""
    mn, mx = min(values), max(values)
    if mn == mx:
        return _SPARK_BARS[3] * len(values)
    return "".join(
        _SPARK_BARS[round((v - mn) / (mx - mn) * 7)] for v in values
    )


def _tracker_chart(name: str, data: dict, n: int = 30) -> str:
    log = data.get("log", [])
    unit = data.get("unit", "")
    if not log:
        return f"No data for {name} yet."
    recent = [e for e in log[-n:] if isinstance(e["value"], (int, float))]
    if not recent:
        return f"No numeric data for {name}."
    values = [e["value"] for e in recent]
    spark = _sparkline(values)
    mn, mx = min(values), max(values)
    first_date = recent[0]["ts"][:10]
    last_date = recent[-1]["ts"][:10]
    return (
        f"📈 {name} chart (last {len(recent)} entries):\n"
        f"{spark}\n"
        f"Min: {mn}{unit}  Max: {mx}{unit}\n"
        f"{first_date} → {last_date}"
    )


# ─────────────────────── handlers: core ───────────────────────

_HELP_TEXT = (
    "Secretary Bot — commands:\n\n"
    "Tasks:\n"
    "  /tasks  /addtask [due:YYYY-MM-DD]  /removetask <n>\n"
    "  /donetask <n>  — mark done & archive\n"
    "  /prioritize <n>  — move to top\n"
    "  /duedate <n> YYYY-MM-DD  — update due date\n"
    "  /extend <n> <days>  — extend due date by N days\n"
    "  /swap <n> <m>  — swap two tasks\n"
    "  /archive  — view completed tasks\n\n"
    "Focus & Notes:\n"
    "  /today [<focus>]  — set/view today's intention\n"
    "  /note <text>  — quick note\n"
    "  /notes  /removenote <n>\n"
    "  /search <query>  — search tasks, notes, journal\n\n"
    "Profile:\n"
    "  /setcontext <about you>  /context\n"
    "  /settimezone <IANA>  — e.g. Asia/Jerusalem\n"
    "  /mystats  /streak\n\n"
    "Check-ins:\n"
    "  /subscribe  /unsubscribe\n"
    "  /setcheckin HH:MM HH:MM  — custom times\n"
    "  /quiethours HH:MM HH:MM  — silence window\n"
    "  /checkin  — manual check-in\n\n"
    "Reminders:\n"
    "  /remind add HH:MM <msg>  — daily (snooze button included)\n"
    "  /remind once 30m|2h|HH:MM <msg>\n"
    "  /remind list  /remind remove <n>\n\n"
    "Habits:\n"
    "  /habit add|done|list|remove|stats <name>\n\n"
    "Trackers:\n"
    "  /addtracker <name> [unit]\n"
    "  /<name> <value> | stats | history | chart\n"
    "  /trackers  /removetracker <name>\n\n"
    "AI & LLM:\n"
    "  /journal <text>  /weekly  /insights  /reflect\n"
    "  /suggest  — AI suggests 3 personalised tasks/habits\n"
    "  /compress  — summarize & truncate history\n"
    "  /setapikey <key>  (sk-… OpenAI, gsk-… Groq/free)\n"
    "  /setmodel <model>  /clearapikey\n\n"
    "Language & Locale:\n"
    "  /setlanguage <name>  — e.g. Hebrew, Spanish\n"
    "  /clearlanguage  /time  — show local time\n\n"
    "Tools:\n"
    "  /focus [task_n] [min]  — pomodoro linked to a task\n"
    "  /pomodoro [min]  /export  /clear\n"
    "  /reset  — wipe all data\n"
    "  /feedback <text>  — send feedback to bot admin\n"
    "  /link  — connect this account to Claude via Google sign-in\n"
    "  (Send export JSON to import)\n"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    save_state(state)
    is_new = _is_new_user(user)
    if is_new:
        await update.message.reply_text(
            "Good day. Jeeves, at your service — your personal secretary and accountability "
            "coach, for as long as you'll have me. You may speak to me in plain language "
            "throughout; I shall understand and act accordingly.\n\n"
            "🧠 A brief accounting of my duties:\n"
            "  • Tasks — add, complete, set deadlines, tag with #hashtags\n"
            "  • Trackers — anything you'd care to log: weight, steps, mood, sleep…\n"
            "  • Reminders — daily or one-time, in any language\n"
            "  • Habits — tracked, with streaks\n"
            "  • Journal — reflections, with a considered word or two in reply\n"
            "  • Check-ins — a morning and evening word, if you subscribe\n"
            "  • …and rather more besides — simply ask\n\n"
            "Before we begin — how shall I address you? \"Sir\", \"Madam\", your given name, "
            "or something else entirely — your call. And should this manner of speech not suit, "
            "say so; I can just as easily be Yoda, or considerably less formal company. "
            "Either preference, once stated, holds until you say otherwise.\n\n"
            "Four small matters, whenever convenient:\n\n"
            "1️⃣ A word about yourself:\n"
            "   /setcontext I'm a developer working on fitness and learning Spanish\n\n"
            "2️⃣ Your timezone — simplest by 📍 sharing your location\n"
            "   (the 📎 attachment icon → Location)\n"
            "   or: /settimezone Asia/Jerusalem\n\n"
            "3️⃣ A first goal:\n"
            "   /addtask Exercise 3× per week\n\n"
            "4️⃣ Daily check-ins, should you want them:\n"
            "   /subscribe\n\n"
            "And should you wish Claude to see this account's data directly, /link will "
            "explain how.\n\n"
            "Or simply begin — try:\n"
            "  \"Add a tracker for my daily steps\"\n"
            "  \"Remind me to drink water every day at 10:00\"\n"
            "  \"What time is it?\""
        )
    else:
        await update.message.reply_text(
            "👋 Secretary Bot is active.\n\n"
            "You can talk to me naturally or use commands.\n"
            "Try: \"What can you do?\" or /help for the full command list."
        )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(_HELP_TEXT)


async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    tasks = user["tasks"]
    if not tasks:
        await update.message.reply_text("No tasks. Use /addtask to add one.")
        return

    # Optional tag filter: /tasks #work
    tag_filter = None
    if context.args and context.args[0].startswith("#"):
        tag_filter = context.args[0][1:].lower()
        filtered = [(i, t) for i, t in enumerate(tasks) if tag_filter in _task_tags(t)]
        if not filtered:
            await update.message.reply_text(f"No tasks tagged #{tag_filter}.")
            return
        lines = [
            _format_task_line(t, orig_i + 1, user=user) + (f"  ♻️ {t['recur']}" if isinstance(t, dict) and t.get("recur") else "")
            for orig_i, t in filtered
        ]
        header = f"Tasks tagged #{tag_filter}:"
    else:
        lines = [
            _format_task_line(t, i + 1, user=user) + (f"  ♻️ {t['recur']}" if isinstance(t, dict) and t.get("recur") else "")
            for i, t in enumerate(tasks)
        ]
        header = "Your tasks:"

    await update.message.reply_text(header + "\n" + "\n".join(lines))


async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = " ".join(context.args).strip()
    if not raw:
        await update.message.reply_text(
            "Usage: /addtask <description> [due:YYYY-MM-DD] [every:daily|weekly|monthly]\n"
            "Example: /addtask Morning run every:daily\n"
            "Example: /addtask Submit report due:2026-07-15"
        )
        return

    # Parse optional due date
    due = None
    due_match = re.search(r"\bdue:(\d{4}-\d{2}-\d{2})\b", raw)
    if due_match:
        try:
            date.fromisoformat(due_match.group(1))
            due = due_match.group(1)
            raw = raw[:due_match.start()] + raw[due_match.end():]
        except ValueError:
            await update.message.reply_text("Invalid date format. Use YYYY-MM-DD.")
            return

    # Parse optional recurrence
    recur = None
    recur_match = re.search(r"\bevery:(daily|weekly|monthly)\b", raw, re.IGNORECASE)
    if recur_match:
        recur = recur_match.group(1).lower()
        raw = raw[:recur_match.start()] + raw[recur_match.end():]

    task_text = raw.strip()
    if not task_text:
        await update.message.reply_text("Task text cannot be empty.")
        return

    if due or recur:
        task = {"text": task_text}
        if due:
            task["due"] = due
        elif recur:
            task["due"] = date.today().isoformat()  # first occurrence = today
        if recur:
            task["recur"] = recur
    else:
        task = task_text

    user = get_user(update.effective_chat.id)
    user["tasks"].append(task)
    save_state(state)

    parts = [f"Added: {task_text}"]
    if due:
        parts.append(f"due {due}")
    if recur:
        parts.append(f"repeats {recur}")
    await update.message.reply_text(" · ".join(parts) + ("  ♻️" if recur else ""))


async def remove_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    try:
        idx = int(context.args[0]) - 1
        removed = user["tasks"].pop(idx)
        save_state(state)
        await update.message.reply_text(f"Removed: {_task_text(removed)}")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /removetask <number>")


async def done_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Usage: /donetask <number>  (use /tasks to see numbers)")
        return
    try:
        idx = int(context.args[0]) - 1
        task = user["tasks"][idx]
        recur = task.get("recur") if isinstance(task, dict) else None

        # Always archive a completion record
        archived = user.setdefault("archived_tasks", [])
        archived.append({
            "text": _task_text(task),
            "due": _task_due(task),
            "completed_at": datetime.utcnow().isoformat(),
        })
        if len(archived) > 100:
            user["archived_tasks"] = archived[-100:]

        if recur:
            # Roll the due date forward instead of removing the task
            current_due = _task_due(task) or date.today().isoformat()
            try:
                base = date.fromisoformat(current_due)
            except ValueError:
                base = date.today()
            if recur == "daily":
                next_due = base + timedelta(days=1)
            elif recur == "weekly":
                next_due = base + timedelta(weeks=1)
            else:  # monthly
                # Same day next month (clamp to last day of month)
                m = base.month % 12 + 1
                y = base.year + (1 if base.month == 12 else 0)
                d = min(base.day, calendar.monthrange(y, m)[1])
                next_due = date(y, m, d)
            user["tasks"][idx] = {
                "text": _task_text(task),
                "due": next_due.isoformat(),
                "recur": recur,
            }
            save_state(state)
            await update.message.reply_text(
                f"✅ Done: {_task_text(task)}\n♻️ Repeats {recur} — next due {next_due.isoformat()}"
            )
        else:
            user["tasks"].pop(idx)
            save_state(state)
            await update.message.reply_text(f"✅ Done: {_task_text(task)}")

        await _check_milestones(update.effective_chat.id, context.application)
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /donetask <number>  (use /tasks to see numbers)")


async def show_archive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    archived = user.get("archived_tasks", [])
    if not archived:
        await update.message.reply_text("No completed tasks yet. Use /donetask <n> to mark one done.")
        return
    recent = archived[-20:]
    lines = []
    for entry in reversed(recent):
        ts = entry.get("completed_at", "")[:10]
        lines.append(f"✅ {entry['text']}  ({ts})")
    await update.message.reply_text(
        f"Completed tasks (last {len(recent)}):\n" + "\n".join(lines)
    )


async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wipe all user data and start fresh."""
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    # Cancel every per-reminder job (daily/annual/once) before the reminders list
    # that names them is wiped below — otherwise they become orphaned: still firing,
    # but invisible to /remind list and unrecoverable after a restart.
    prefixes = (f"reminder_{chat_id}_", f"once_{chat_id}_")
    for job in context.application.job_queue.jobs():
        if job.name and job.name.startswith(prefixes):
            job.schedule_removal()
    for r in user.get("reminders", []):
        db_mark_reminder_removed(chat_id, r["id"])
    # Keep LLM settings and timezone; wipe everything else
    user.update({
        "tasks": [],
        "history": [],
        "context": "",
        "checkin_enabled": False,
        "reminders": [],
        "trackers": {},
        "habits": {},
        "journal": [],
        "activity_days": [],
        "archived_tasks": [],
        "quiet_hours": {"start": None, "end": None},
        "debug_clock": "",
        "debug_clock_expires": "",
    })
    # A simulated clock is emphatically not something a wipe should keep
    # (T-1-15) -- unlike timezone, which is deliberately preserved above.
    db_delete_pref(str(chat_id), "debug_clock")
    db_delete_pref(str(chat_id), "debug_clock_expires")
    # Cancel all scheduled jobs
    schedule_user_checkins(context.application, chat_id)
    schedule_user_alerts(context.application, chat_id)
    save_state(state)
    await update.message.reply_text(
        "♻️ Account reset. All tasks, habits, trackers, and history cleared.\n"
        "Your timezone and API settings were kept.\n"
        "Use /start to set up again."
    )


async def prioritize_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Usage: /prioritize <number>  (moves task to top)")
        return
    try:
        idx = int(context.args[0]) - 1
        task = user["tasks"].pop(idx)
        user["tasks"].insert(0, task)
        save_state(state)
        await update.message.reply_text(f"⬆️ Moved to top: {_task_text(task)}")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /prioritize <number>  (use /tasks to see numbers)")


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    if not context.args:
        # Comparison only -- routed through the simulated clock (when active)
        # for consistency with the rest of the ambient scope (WR-03).
        focus = user.get("today_focus", {})
        if focus.get("date") == _today(user=user).isoformat() and focus.get("text"):
            await update.message.reply_text(f"🎯 Today's focus: {focus['text']}")
        else:
            await update.message.reply_text(
                "No focus set for today.\nUse /today <your focus for today> to set one."
            )
        return
    text = " ".join(context.args).strip()
    # Write side always stays on real time (durable write -- CR-02/D-P5).
    user["today_focus"] = {"date": date.today().isoformat(), "text": text}
    save_state(state)
    await update.message.reply_text(f"🎯 Today's focus set: {text}")


async def note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Usage: /note <text>")
        return
    text = " ".join(context.args).strip()
    db_add_note(str(update.effective_chat.id), text, auto=False)
    await update.message.reply_text("📝 Note saved.")


async def notes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db_get_notes(str(update.effective_chat.id))
    if not rows:
        await update.message.reply_text("No notes yet. Use /note <text> to add one.")
        return
    lines = [f"{i+1}. [{r['id']}] {r['text']}" for i, r in enumerate(rows)]
    await update.message.reply_text("📝 Notes:\n" + "\n".join(lines))


async def removenote_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /removenote <number>\n(use /notes to see numbers)")
        return
    try:
        n = int(context.args[0])
        rows = db_get_notes(str(update.effective_chat.id))
        if n < 1 or n > len(rows):
            raise IndexError
        row_id = rows[n - 1]["id"]
        db_remove_note(str(update.effective_chat.id), row_id)
        await update.message.reply_text(f"Removed: {rows[n-1]['text']}")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /removenote <number>  (use /notes to see numbers)")


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Usage: /search <query>")
        return
    q = " ".join(context.args).lower()
    results = []

    for i, t in enumerate(user.get("tasks", []), 1):
        if q in _task_text(t).lower():
            results.append(f"📋 Task {i}: {_task_text(t)}")

    for entry in user.get("archived_tasks", []):
        if q in entry["text"].lower():
            results.append(f"✅ Done ({entry.get('completed_at','')[:10]}): {entry['text']}")

    for r in db_search_notes(str(update.effective_chat.id), q):
        results.append(f"📝 Note: {r['text']}")

    for r in db_search_journal(str(update.effective_chat.id), q):
        snippet = r["entry"][:80].replace("\n", " ")
        results.append(f"📓 Journal ({r['ts'][:10]}): {snippet}…")

    if not results:
        await update.message.reply_text(f'No results for "{q}".')
    else:
        await update.message.reply_text(
            f'Search results for "{q}" ({len(results)} found):\n\n' + "\n".join(results[:20])
        )


async def duedate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Update a task's due date: /duedate <n> YYYY-MM-DD  (or 'none' to clear)."""
    user = get_user(update.effective_chat.id)
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /duedate <number> YYYY-MM-DD\nUse 'none' to clear the due date.")
        return
    try:
        idx = int(context.args[0]) - 1
        date_str = context.args[1].lower()
        task = user["tasks"][idx]
        if date_str == "none":
            new_due = None
        else:
            date.fromisoformat(date_str)  # validate
            new_due = date_str
        if isinstance(task, str):
            user["tasks"][idx] = {"text": task, "due": new_due}
        else:
            task["due"] = new_due
        save_state(state)
        text = _task_text(user["tasks"][idx])
        if new_due:
            await update.message.reply_text(f"📅 Due date updated: {text} → {new_due}")
        else:
            await update.message.reply_text(f"📅 Due date cleared: {text}")
    except (IndexError, ValueError):
        await update.message.reply_text("Invalid arguments. Use: /duedate <number> YYYY-MM-DD")


async def extend_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/extend <n> <days> — extend a task's due date by N days."""
    user = get_user(update.effective_chat.id)
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /extend <task number> <days>\nExample: /extend 2 7")
        return
    try:
        idx = int(context.args[0]) - 1
        days_n = int(context.args[1])
        task = user["tasks"][idx]
        current_due = _task_due(task)
        if current_due:
            base = date.fromisoformat(current_due)
        else:
            base = date.today()
        new_due = (base + timedelta(days=days_n)).isoformat()
        if isinstance(task, str):
            user["tasks"][idx] = {"text": task, "due": new_due}
        else:
            task["due"] = new_due
        save_state(state)
        text = _task_text(user["tasks"][idx])
        await update.message.reply_text(
            f"📅 Extended: {text}\n"
            f"{'Was: ' + current_due + '  →  ' if current_due else ''}Due: {new_due}"
        )
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /extend <task number> <days>")


async def swap_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Swap two tasks' positions: /swap <n> <m>."""
    user = get_user(update.effective_chat.id)
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /swap <n> <m>  — swap positions of two tasks")
        return
    try:
        i, j = int(context.args[0]) - 1, int(context.args[1]) - 1
        tasks = user["tasks"]
        tasks[i], tasks[j] = tasks[j], tasks[i]
        save_state(state)
        await update.message.reply_text(
            f"🔀 Swapped:\n  {i+1}. {_task_text(tasks[i])}\n  {j+1}. {_task_text(tasks[j])}"
        )
    except (IndexError, ValueError):
        await update.message.reply_text("Invalid numbers. Use /tasks to see task numbers.")


async def suggest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask AI to suggest new tasks or habits based on user profile."""
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    if is_rate_limited(chat_id):
        await update.message.reply_text("Rate limit reached. Try again later.")
        return
    ctx = user.get("context", "")
    tasks_str = _tasks_for_prompt(user["tasks"])
    habits = ", ".join(user.get("habits", {}).keys()) or "none"
    journal_recent = " | ".join(
        r["entry"][:60] for r in db_get_journal(str(chat_id), limit=3)
    ) or "no journal entries"
    prompt = (
        f"The user's profile: {ctx or 'not set'}\n"
        f"Current tasks: {tasks_str}\n"
        f"Current habits: {habits}\n"
        f"Recent journal: {journal_recent}\n\n"
        "Suggest exactly 3 specific, actionable new tasks or habits that would help this person "
        "make progress on their goals. For each, give one sentence of rationale. "
        "Format: numbered list. Be concrete — no generic advice."
    )
    await update.message.reply_text("Thinking of suggestions for you…")
    reply = await chat(
        chat_id, prompt,
        system="You are a life coach. Give specific, personalised suggestions based on the user's data."
    )
    await update.message.reply_text("💡 Suggestions:\n\n" + reply)


async def reflect_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deep personal reflection: patterns, what's working, what isn't."""
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    if is_rate_limited(chat_id):
        await update.message.reply_text("Rate limit reached. Try again later.")
        return
    streak = _get_streak(user)
    habits = user.get("habits", {})
    habit_lines = []
    for name, h in habits.items():
        s = _habit_streak(h.get("completions", []), user=user)
        habit_lines.append(f"{name}: {s}-day streak")
    reflect_journal = [r["entry"][:100] for r in db_get_journal(str(chat_id), limit=5)]
    n_done = len(user.get("archived_tasks", []))
    prompt = (
        f"User data for reflection:\n"
        f"Activity streak: {streak} days\n"
        f"Tasks completed total: {n_done}\n"
        f"Active tasks: {_tasks_for_prompt(user['tasks'])}\n"
        f"Habits: {'; '.join(habit_lines) or 'none'}\n"
        f"Recent journal (last 5): {' | '.join(reflect_journal) or 'none'}\n\n"
        "Write a short personal reflection (3-5 sentences) covering: "
        "1) What patterns do you see? "
        "2) What's clearly working? "
        "3) What's the one thing to focus on next week? "
        "Be honest, warm, and specific. Don't be generic."
    )
    await update.message.reply_text("Reflecting on your progress…")
    reply = await chat(
        chat_id, prompt,
        system="You are a thoughtful personal coach writing a sincere reflection."
    )
    await update.message.reply_text("🪞 Reflection:\n\n" + reply)


async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    if not context.args:
        lang = user.get("language", "")
        if lang:
            await update.message.reply_text(f"🌐 Current language: {lang}. Use /clearlanguage to reset.")
        else:
            await update.message.reply_text(
                "Usage: /setlanguage <language>\nExample: /setlanguage Hebrew\n"
                "I'll always respond in that language."
            )
        return
    lang = " ".join(context.args).strip()
    user["language"] = lang
    save_state(state)
    await update.message.reply_text(f"🌐 Language set to {lang}. I'll respond in {lang} from now on.")


async def clear_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    user["language"] = ""
    save_state(state)
    await update.message.reply_text("🌐 Language preference cleared. I'll follow your message language.")


async def compress_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Summarize conversation history and replace it with a compact version."""
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    if len(user["history"]) < 4:
        await update.message.reply_text("Not enough history to compress yet.")
        return
    await update.message.reply_text("Compressing history…")
    history_text = "\n".join(
        f"{m['role'].upper()}: {m['content'][:200]}" for m in user["history"]
    )
    summary_prompt = (
        "Summarize the following conversation in 3-5 sentences. "
        "Focus on: tasks discussed, commitments made, progress reported, key topics. "
        "Be factual and concise.\n\n" + history_text
    )
    try:
        summary = await chat(chat_id, summary_prompt, system="You are a concise summarizer.")
    except Exception:
        await update.message.reply_text("⚠️ Compression failed. Try again.")
        return
    user["history"] = [
        {"role": "user", "content": f"[Conversation summary] {summary}"},
        {"role": "assistant", "content": "Understood. I'll use this as context going forward."},
    ]
    save_state(state)
    await update.message.reply_text(
        f"✅ History compressed to 2 messages.\n\n📄 Summary:\n{summary}"
    )


async def time_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    tz_str = user.get("timezone", "UTC")
    try:
        tz = ZoneInfo(tz_str)
    except Exception:
        tz = ZoneInfo("UTC")
    now = _now(tz, user=user)
    await update.message.reply_text(
        f"🕐 Your local time: {now.strftime('%H:%M')} on {now.strftime('%A, %d %b %Y')}\n"
        f"Timezone: {tz_str}"
    )


async def feedback_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /feedback <your message>")
        return
    if not MY_CHAT_ID:
        await update.message.reply_text("Feedback is not configured for this bot.")
        return
    text = " ".join(context.args)
    sender = update.effective_chat.id
    try:
        await context.bot.send_message(
            chat_id=int(MY_CHAT_ID),
            text=f"📬 Feedback from {sender}:\n\n{text}"
        )
        await update.message.reply_text("✉️ Feedback sent. Thank you!")
    except Exception as e:
        logger.error("Feedback send failed: %s", e)
        await update.message.reply_text("⚠️ Could not deliver feedback right now.")


async def link_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/link — one-time code + URL to connect this Telegram account to the
    remote MCP server via Google sign-in (Milestone B / AUTH-01/02)."""
    chat_id = update.effective_chat.id
    code = db_create_link_code(chat_id)
    domain = os.environ.get("MCP_REMOTE_DOMAIN")
    if domain:
        url = f"https://{domain}/link/{code}"
        await update.message.reply_text(
            "🔗 Tap to connect Claude to your data:\n"
            f"{url}\n\n"
            "Signs you in with Google. One-time use, expires in 10 minutes."
        )
    else:
        await update.message.reply_text(
            f"🔗 Your one-time link code: {code}\n"
            "(MCP_REMOTE_DOMAIN isn't set for this bot, so no direct link — "
            "expires in 10 minutes.)"
        )


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not MY_CHAT_ID or str(chat_id) != MY_CHAT_ID:
        await update.message.reply_text("Admin only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    msg = " ".join(context.args)
    recipients = [uid for uid, u in state["users"].items() if u.get("checkin_enabled")]
    sent, failed = 0, 0
    for uid in recipients:
        try:
            await context.bot.send_message(chat_id=int(uid), text=f"📢 {msg}")
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(
        f"Broadcast done. Sent: {sent}, Failed: {failed} (of {len(recipients)} subscribed users)."
    )


async def mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/mute <Nh|Nd> — suppress all check-ins and reminders for N hours or days."""
    user = get_user(update.effective_chat.id)
    if not context.args:
        if _is_muted(user):
            until = user.get("muted_until", "")[:16].replace("T", " ")
            await update.message.reply_text(f"🔕 Muted until {until} UTC. Use /unmute to cancel.")
        else:
            await update.message.reply_text(
                "Usage: /mute <duration>\n"
                "Examples: /mute 4h  /mute 2d\n"
                "Suppresses all check-ins and reminders for that period."
            )
        return
    spec = context.args[0].lower()
    try:
        if spec.endswith("h"):
            delta = timedelta(hours=int(spec[:-1]))
        elif spec.endswith("d"):
            delta = timedelta(days=int(spec[:-1]))
        else:
            raise ValueError
        until = datetime.utcnow() + delta
        user["muted_until"] = until.isoformat()
        save_state(state)
        until_str = until.strftime("%Y-%m-%d %H:%M UTC")
        await update.message.reply_text(f"🔕 Muted until {until_str}. Use /unmute to cancel early.")
    except (ValueError, IndexError):
        await update.message.reply_text("Usage: /mute <Nh|Nd>  e.g. /mute 4h or /mute 2d")


async def unmute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    user["muted_until"] = ""
    save_state(state)
    await update.message.reply_text("🔔 Unmuted. Check-ins and reminders are active again.")


async def limit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current rate limit status."""
    key = str(update.effective_chat.id)
    now = time.monotonic()
    recent = [t for t in _rate_log[key] if now - t < RATE_WINDOW]
    used = len(recent)
    remaining = max(0, RATE_LIMIT - used)
    bar = "█" * min(used, RATE_LIMIT) + "░" * max(0, RATE_LIMIT - used)
    await update.message.reply_text(
        f"📊 Rate limit: {used}/{RATE_LIMIT} used this hour\n"
        f"[{bar}]\n"
        f"{remaining} messages remaining (resets rolling)"
    )


async def set_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Usage: /setcontext <about you and your goals>")
        return
    user = get_user(update.effective_chat.id)
    user["context"] = text
    save_state(state)
    await update.message.reply_text("Context saved.")


async def show_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    if not user["context"]:
        await update.message.reply_text("No context set. Use /setcontext.")
        return
    await update.message.reply_text(f"Your context:\n{user['context']}")


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    tz = user.get("timezone", "UTC")

    # Optional: /subscribe HH:MM HH:MM sets check-in times in one step
    if len(context.args) >= 2:
        def _valid(s):
            try:
                h, m = s.split(":")
                assert 0 <= int(h) <= 23 and 0 <= int(m) <= 59
                return True
            except (ValueError, AssertionError):
                return False
        morning_t, evening_t = context.args[0], context.args[1]
        if not _valid(morning_t) or not _valid(evening_t):
            await update.message.reply_text(
                "Invalid time format. Use: /subscribe HH:MM HH:MM (24h)\n"
                "Example: /subscribe 09:00 22:00"
            )
            return
        user["checkin_times"] = {"morning": morning_t, "evening": evening_t}

    user["checkin_enabled"] = True
    save_state(state)
    times = user.get("checkin_times", {"morning": "08:00", "evening": "21:00"})
    schedule_user_checkins(context.application, update.effective_chat.id)
    schedule_user_alerts(context.application, update.effective_chat.id)
    await update.message.reply_text(
        f"Daily check-ins enabled at {times['morning']} (morning) and {times['evening']} (evening) ({tz}).\n"
        "Also: deadline alerts at 09:00 and habit reminders at 20:00.\n"
        "Use /settimezone or /setcheckin to adjust."
    )


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    user["checkin_enabled"] = False
    save_state(state)
    schedule_user_checkins(context.application, update.effective_chat.id)
    schedule_user_alerts(context.application, update.effective_chat.id)
    await update.message.reply_text("Daily check-ins, deadline alerts, and habit reminders disabled.")


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-set timezone from a shared location."""
    loc = update.message.location
    tz_str = _tf.timezone_at(lat=loc.latitude, lng=loc.longitude)
    if not tz_str:
        await update.message.reply_text(
            "Couldn't determine timezone from that location. "
            "Please set it manually: /settimezone Europe/London"
        )
        return
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    _set_user_timezone(chat_id, user, tz_str)
    save_state(state)
    schedule_user_checkins(context.application, chat_id)
    schedule_user_alerts(context.application, chat_id)
    for reminder in list(user.get("reminders", [])):
        # Annual reminders fire via the deadline-alert date check; scheduling
        # one here would turn a once-a-year reminder into a daily one.
        if not reminder.get("annual"):
            schedule_user_reminder(context.application, chat_id, reminder)
    await update.message.reply_text(f"📍 Timezone set to {tz_str} from your location.")


def _normalize_tz(tz_str: str) -> str:
    """Convert UTC±N offsets to valid IANA Etc/GMT∓N names (POSIX sign is inverted)."""
    import re as _re
    m = _re.fullmatch(r"UTC([+-])(\d{1,2})", tz_str.strip(), _re.IGNORECASE)
    if m:
        sign, hours = m.group(1), int(m.group(2))
        if hours == 0:
            return "UTC"
        # POSIX Etc/GMT sign is opposite to UTC offset
        posix_sign = "-" if sign == "+" else "+"
        return f"Etc/GMT{posix_sign}{hours}"
    return tz_str


async def set_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tz_str = _normalize_tz(" ".join(context.args).strip())
    if not tz_str:
        await update.message.reply_text(
            "Usage: /settimezone <IANA timezone>\n"
            "Examples: UTC  UTC+3  UTC-5  Europe/London  America/New_York  Asia/Jerusalem"
        )
        return
    try:
        ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, KeyError):
        await update.message.reply_text(
            f"Unknown timezone: {tz_str}\n"
            "Use IANA names (e.g. Europe/London, Asia/Tokyo) or UTC offsets (UTC+3, UTC-5).\n"
            "Tip: share your 📍 location and I'll detect it automatically."
        )
        return
    user = get_user(update.effective_chat.id)
    _set_user_timezone(update.effective_chat.id, user, tz_str)
    save_state(state)
    schedule_user_checkins(context.application, update.effective_chat.id)
    schedule_user_alerts(context.application, update.effective_chat.id)
    for reminder in list(user.get("reminders", [])):
        if not reminder.get("annual"):
            schedule_user_reminder(context.application, update.effective_chat.id, reminder)
    # Show user-friendly offset for Etc/GMT zones (POSIX sign is inverted)
    display = tz_str
    import re as _re2
    m2 = _re2.fullmatch(r"Etc/GMT([+-])(\d+)", tz_str)
    if m2:
        friendly_sign = "+" if m2.group(1) == "-" else "-"
        display = f"UTC{friendly_sign}{m2.group(2)} ({tz_str})"
    await update.message.reply_text(f"Timezone set to {display}.")


async def manual_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    # Build a mini-dashboard header. Display-only comparisons (no writes
    # happen in this function) -- routed through the simulated clock (when
    # active) for consistency with the rest of the ambient scope (WR-03).
    today_str = _today(user=user).isoformat()
    lines = []

    focus = user.get("today_focus", {})
    if focus.get("date") == today_str and focus.get("text"):
        lines.append(f"🎯 Focus: {focus['text']}")

    tasks = user.get("tasks", [])
    if tasks:
        lines.append(f"📋 Tasks ({len(tasks)}): " + " · ".join(_task_text(t) for t in tasks[:3])
                     + ("…" if len(tasks) > 3 else ""))

    habits = user.get("habits", {})
    if habits:
        done_today = [n for n, h in habits.items() if today_str in h.get("completions", [])]
        pending = [n for n in habits if n not in done_today]
        if pending:
            lines.append(f"🔲 Habits pending: {', '.join(pending)}")
        elif done_today:
            lines.append(f"✅ All habits done today!")

    dashboard = "\n".join(lines)
    prompt = (
        "The user requested a manual check-in. "
        "Ask what's on their mind and how their tasks are going. "
        "Be warm and specific — pick one task or habit to ask about."
    )
    reply = await chat(chat_id, prompt)
    if dashboard:
        await update.message.reply_text(dashboard + "\n\n" + reply)
    else:
        await update.message.reply_text(reply)


async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    user["history"] = []
    save_state(state)
    await update.message.reply_text("Conversation history cleared.")


# ─────────────────────── handlers: reminders ───────────────────────

def _parse_once_delay(spec: str, tz_str: str) -> float | None:
    """Parse a one-time reminder time spec. Return seconds from now, or None if invalid."""
    # "30m" or "2h"
    m = re.fullmatch(r"(\d+)(m|h)", spec)
    if m:
        val = int(m.group(1))
        return val * 60 if m.group(2) == "m" else val * 3600
    # "HH:MM" — today (or tomorrow if time already passed) in user's timezone
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", spec)
    if m:
        try:
            tz = ZoneInfo(tz_str)
        except (ZoneInfoNotFoundError, KeyError):
            tz = ZoneInfo("UTC")
        h, mn = int(m.group(1)), int(m.group(2))
        now_local = datetime.now(tz)
        target = now_local.replace(hour=h, minute=mn, second=0, microsecond=0)
        if target <= now_local:
            target += timedelta(days=1)
        return (target - now_local).total_seconds()
    return None


async def remind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage:\n"
            "  /remind add HH:MM <message>       — daily recurring\n"
            "  /remind once 30m <message>         — once in 30 minutes\n"
            "  /remind once 2h <message>          — once in 2 hours\n"
            "  /remind once HH:MM <message>       — once at that time today\n"
            "  /remind list\n"
            "  /remind remove <number>"
        )
        return

    sub = args[0].lower()

    if sub == "list":
        reminders = user.get("reminders", [])
        if not reminders:
            await update.message.reply_text("No reminders. Add one with /remind add or /remind once.")
            return
        tz = user.get("timezone", "UTC")
        lines = []
        for i, r in enumerate(reminders):
            if r.get("annual"):
                kind = f"annual {r.get('date','??')}"
            elif r.get("once"):
                kind = "once"
            else:
                kind = "daily"
            line = f"{i+1}. [{kind}] {r['time']} {tz} — {r['message']}"
            if r.get("reason"):
                line += f" (💡 {r['reason']})"
            lines.append(line)
        await update.message.reply_text("Your reminders:\n" + "\n".join(lines))

    elif sub == "add":
        if len(args) < 3:
            await update.message.reply_text("Usage: /remind add HH:MM <message>")
            return
        if not user.get("timezone_confirmed"):
            await update.message.reply_text(_TZ_NOT_CONFIRMED_MSG)
            return
        time_str = args[1]
        try:
            h, m = time_str.split(":")
            assert 0 <= int(h) <= 23 and 0 <= int(m) <= 59
        except (ValueError, AssertionError):
            await update.message.reply_text("Invalid time. Use HH:MM (e.g. 09:30)")
            return
        message = " ".join(args[2:])
        reminder = {"id": str(uuid.uuid4()), "time": time_str, "message": message, "once": False}
        user.setdefault("reminders", []).append(reminder)
        save_state(state)
        db_log_reminder(update.effective_chat.id, reminder["id"], message, None, "daily", time_str)
        schedule_user_reminder(context.application, update.effective_chat.id, reminder)
        tz = user.get("timezone", "UTC")
        await update.message.reply_text(f"Daily reminder set: {time_str} ({tz}) — {message}")

    elif sub == "once":
        if len(args) < 3:
            await update.message.reply_text("Usage: /remind once <30m|2h|HH:MM> <message>")
            return
        spec = args[1]
        message = " ".join(args[2:])
        if ":" in spec and not user.get("timezone_confirmed"):
            # Only the absolute HH:MM form of /remind once depends on timezone;
            # the relative 30m/2h form fires N real-time minutes from now regardless.
            await update.message.reply_text(_TZ_NOT_CONFIRMED_MSG)
            return
        delay = _parse_once_delay(spec, user.get("timezone", "UTC"))
        if delay is None or delay <= 0:
            await update.message.reply_text("Invalid time spec. Use: 30m, 2h, or HH:MM")
            return

        reminder_id = str(uuid.uuid4())
        job_name = f"once_{update.effective_chat.id}_{reminder_id}"
        chat_id = update.effective_chat.id
        db_log_reminder(chat_id, reminder_id, message, None, "once", spec)

        async def _once_job(ctx: ContextTypes.DEFAULT_TYPE, _cid=chat_id, _msg=message):
            try:
                await ctx.bot.send_message(chat_id=_cid, text=f"⏰ Reminder: {_msg}")
            except Exception as e:
                logger.error("One-time reminder failed: %s", e)

        context.application.job_queue.run_once(_once_job, when=delay, name=job_name)
        mins = int(delay // 60)
        await update.message.reply_text(
            f"⏰ One-time reminder set in {mins} min: {message}"
        )

    elif sub == "remove":
        if len(args) < 2:
            await update.message.reply_text("Usage: /remind remove <number>")
            return
        try:
            n = int(args[1])
        except ValueError:
            await update.message.reply_text("Invalid number. Use /remind list to see numbers.")
            return
        reminders = user.get("reminders", [])
        # Explicit range check, not pop(n-1): "/remind remove 0" would otherwise
        # index -1 and silently delete the *last* reminder instead of erroring.
        if not (1 <= n <= len(reminders)):
            await update.message.reply_text("Invalid number. Use /remind list to see numbers.")
            return
        removed = reminders.pop(n - 1)
        job_name = f"reminder_{update.effective_chat.id}_{removed['id']}"
        for job in context.application.job_queue.get_jobs_by_name(job_name):
            job.schedule_removal()
        save_state(state)
        db_mark_reminder_removed(update.effective_chat.id, removed["id"])
        await update.message.reply_text(f"Removed: {removed['time']} — {removed['message']}")

    elif sub == "annual":
        # /remind annual MM-DD HH:MM <message>
        if len(args) < 4:
            await update.message.reply_text(
                "Usage: /remind annual MM-DD HH:MM <message>\n"
                "Example: /remind annual 12-25 09:00 Merry Christmas!"
            )
            return
        if not user.get("timezone_confirmed"):
            await update.message.reply_text(_TZ_NOT_CONFIRMED_MSG)
            return
        date_str = args[1]
        time_str = args[2]
        try:
            mo, dy = (int(x) for x in date_str.split("-"))
            assert 1 <= mo <= 12 and 1 <= dy <= 31
            h, mi = (int(x) for x in time_str.split(":"))
            assert 0 <= h <= 23 and 0 <= mi <= 59
        except (ValueError, AssertionError):
            await update.message.reply_text("Invalid format. Use MM-DD HH:MM (e.g. 12-25 09:00)")
            return
        message = " ".join(args[3:])
        reminder = {
            "id": str(uuid.uuid4()),
            "time": time_str,
            "date": date_str,
            "message": message,
            "once": False,
            "annual": True,
        }
        user.setdefault("reminders", []).append(reminder)
        save_state(state)
        db_log_reminder(update.effective_chat.id, reminder["id"], message, None, "annual", time_str)
        tz = user.get("timezone", "UTC")
        await update.message.reply_text(
            f"📅 Annual reminder set: every {date_str} at {time_str} ({tz}) — {message}"
        )

    else:
        await update.message.reply_text("Unknown subcommand. Use add, annual, once, list, or remove.")


# ─────────────────────── handlers: trackers ───────────────────────

async def add_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /addtracker <name> [unit]\nExample: /addtracker weight kg")
        return
    name = context.args[0].lower().strip()
    unit = context.args[1] if len(context.args) > 1 else ""
    if not name.isalpha():
        await update.message.reply_text("Tracker name must be letters only (e.g. weight, mood, steps)")
        return
    if name in RESERVED_COMMANDS:
        await update.message.reply_text(f"'{name}' is a reserved command name. Choose another.")
        return
    user = get_user(update.effective_chat.id)
    if name in user.get("trackers", {}):
        await update.message.reply_text(f"Tracker '{name}' already exists.")
        return
    user.setdefault("trackers", {})[name] = {"unit": unit, "log": []}
    save_state(state)
    await update.message.reply_text(
        f"Tracker created!\n"
        f"  /{name} <value>   — log a value\n"
        f"  /{name} stats     — show statistics\n"
        f"  /{name} history   — show history"
    )


async def list_trackers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    trackers = user.get("trackers", {})
    if not trackers:
        await update.message.reply_text("No trackers. Create one with /addtracker <name> [unit]")
        return
    lines = []
    for name, data in trackers.items():
        log = data.get("log", [])
        unit = data.get("unit", "")
        last = f"last: {log[-1]['value']}{unit}" if log else "no data"
        lines.append(f"/{name} ({last})")
    await update.message.reply_text("Your trackers:\n" + "\n".join(lines))


async def remove_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /removetracker <name>")
        return
    name = context.args[0].lower()
    user = get_user(update.effective_chat.id)
    if name not in user.get("trackers", {}):
        await update.message.reply_text(f"No tracker named '{name}'.")
        return
    del user["trackers"][name]
    save_state(state)
    await update.message.reply_text(f"Tracker '{name}' removed.")


async def handle_custom_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catch-all COMMAND handler — routes to user-defined trackers."""
    cmd = update.message.text.split()[0][1:].split("@")[0].lower()
    user = get_user(update.effective_chat.id)
    trackers = user.get("trackers", {})

    if cmd not in trackers:
        await update.message.reply_text(
            f"Unknown command /{cmd}.\n"
            "Use /trackers to see your custom trackers."
        )
        return

    tracker = trackers[cmd]
    args = update.message.text.split()[1:]

    if not args:
        log = tracker.get("log", [])
        unit = tracker.get("unit", "")
        if log:
            last = log[-1]
            await update.message.reply_text(
                f"{cmd}: {last['value']}{unit} at {last['ts'][:16].replace('T', ' ')}\n"
                f"  /{cmd} <value> | stats | history"
            )
        else:
            await update.message.reply_text(f"No data yet. Log with /{cmd} <value>")
        return

    sub = args[0].lower()
    if sub == "stats":
        await update.message.reply_text(_tracker_stats(cmd, tracker))
    elif sub == "history":
        n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
        await update.message.reply_text(_tracker_history(cmd, tracker, n))
    elif sub == "chart":
        n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 30
        await update.message.reply_text(_tracker_chart(cmd, tracker, n))
    else:
        try:
            raw = float(sub)
            value = int(raw) if raw == int(raw) else raw
        except ValueError:
            await update.message.reply_text(
                f"Usage: /{cmd} <number> | stats | history | chart"
            )
            return
        entry = {"ts": datetime.utcnow().isoformat(), "value": value}
        tracker.setdefault("log", []).append(entry)
        save_state(state)
        unit = tracker.get("unit", "")
        await update.message.reply_text(f"✓ Logged {cmd}: {value}{unit}")


# ─────────────────────── handlers: LLM settings ───────────────────────

async def set_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    model = " ".join(context.args).strip()
    if not model:
        await update.message.reply_text(
            "Usage: /setmodel <model>\nExamples: gpt-4o  gpt-4o-mini  gpt-3.5-turbo"
        )
        return
    user = get_user(update.effective_chat.id)
    user["llm"]["model"] = model
    save_state(state)
    await update.message.reply_text(f"Model set to: {model}")


async def set_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = " ".join(context.args).strip()
    if not key:
        await update.message.reply_text(
            "Usage: /setapikey <key>\n"
            "OpenAI key (sk-...): uses OpenAI models\n"
            "Groq key (gsk-...): uses Llama 3 for free\n"
            "Get a free Groq key at console.groq.com"
        )
        return
    chat_id = str(update.effective_chat.id)
    db_store_key(chat_id, key)
    # Clear plaintext from state.json if it was stored there before
    user = get_user(update.effective_chat.id)
    user["llm"]["api_key"] = None
    save_state(state)
    try:
        await update.message.delete()
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✓ API key saved (encrypted). (Original message deleted for security.)"
    )


async def clear_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_delete_key(str(update.effective_chat.id))
    user = get_user(update.effective_chat.id)
    user["llm"]["api_key"] = None
    save_state(state)
    await update.message.reply_text(f"API key cleared. Using default model ({DEFAULT_MODEL}).")


# ─────────────────────── handlers: journal & extras ───────────────────────

async def journal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Usage: /journal <your entry>")
        return
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    db_add_journal(str(chat_id), text, auto=False)
    entry_date = _local_date(datetime.utcnow().isoformat(), user.get("timezone", "UTC"))

    prompt = f'The user just wrote this journal entry: "{text}"\nOffer a brief, warm reflection in 2-3 sentences.'
    reply = await chat(chat_id, prompt)
    await update.message.reply_text(f"\U0001f4d4 Saved ({entry_date}).\n\n{reply}")


async def weekly_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    lines = []

    if user["tasks"]:
        lines.append(f"Tasks being tracked: {_tasks_for_prompt(user['tasks'])}")

    for name, data in user.get("trackers", {}).items():
        recent = [e for e in data.get("log", []) if e.get("ts", "") >= _days_ago_iso(7)]
        if recent:
            unit = data.get("unit", "")
            vals = ", ".join(str(e["value"]) + unit for e in recent)
            lines.append(f"{name} (last 7 days): {vals}")

    week_cutoff = _days_ago_iso(7)
    recent_journal = [
        r for r in db_get_journal(str(update.effective_chat.id))
        if r["ts"] >= week_cutoff
    ]
    if recent_journal:
        lines.append(f"Journal entries this week: {len(recent_journal)}")

    if not lines:
        await update.message.reply_text("Not enough data for a weekly summary yet.")
        return

    prompt = (
        f"Generate a concise weekly summary for the user based on this data:\n"
        + "\n".join(lines)
        + "\n\nNote trends, celebrate wins, and highlight one area to improve. Keep it to 5-7 lines."
    )
    reply = await chat(update.effective_chat.id, prompt)
    await update.message.reply_text(f"\U0001f4ca Weekly Summary\n\n{reply}")


async def export_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    export = {
        "exported_at": datetime.utcnow().isoformat(),
        "tasks": user["tasks"],
        "context": user["context"],
        "timezone": user["timezone"],
        "reminders": [{"time": r["time"], "message": r["message"]} for r in user.get("reminders", [])],
        "trackers": user["trackers"],
        "habits": user.get("habits", {}),
        "journal": [{"ts": r["ts"], "entry": r["entry"]} for r in db_get_journal(str(update.effective_chat.id))],
        "notes": [r["text"] for r in db_get_notes(str(update.effective_chat.id))],
    }
    data_bytes = json.dumps(export, ensure_ascii=False, indent=2).encode("utf-8")
    bio = BytesIO(data_bytes)
    bio.name = "secretary_export.json"
    await update.message.reply_document(
        document=bio,
        filename="secretary_export.json",
        caption="Your data export (conversation history not included)."
    )


async def streak_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    streak = _get_streak(user)
    total = len(user.get("activity_days", []))
    if streak == 0:
        await update.message.reply_text("No streak yet. Send me a message every day to build one!")
    else:
        await update.message.reply_text(
            f"🔥 Current streak: {streak} day{'s' if streak != 1 else ''}\n"
            f"Total active days: {total}"
        )


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != MY_CHAT_ID:
        return
    total = len(state["users"])
    subscribed = sum(1 for u in state["users"].values() if u.get("checkin_enabled"))
    with_key = sum(
        1 for cid in state["users"]
        if db_get_key(cid) or state["users"][cid].get("llm", {}).get("api_key")
    )
    with_trackers = sum(1 for u in state["users"].values() if u.get("trackers"))
    with_reminders = sum(1 for u in state["users"].values() if u.get("reminders"))
    groq_mode = "Groq (free tier)" if GROQ_API_KEY else "OpenAI gpt-4o-mini (bot-funded)"
    await update.message.reply_text(
        f"📊 Bot stats:\n"
        f"Total users: {total}\n"
        f"Subscribed to check-ins: {subscribed}\n"
        f"Using custom API key: {with_key}\n"
        f"Have trackers: {with_trackers}\n"
        f"Have reminders: {with_reminders}\n"
        f"Default model: {GROQ_DEFAULT_MODEL if GROQ_API_KEY else DEFAULT_MODEL}\n"
        f"Free tier: {groq_mode}"
    )


# ─────────────────────── handlers: debug (owner-only) ───────────────────────

# Deliberately not in _post_init's BotFather command list and not in
# _HELP_TEXT — this surface must stay undiscoverable to non-owners (T-1-01).

# Maps a fixed job name to the zero-extra-argument runner /debug fire invokes
# for it — the same function object schedule_user_checkins/_reminder/_alerts
# hand to run_daily, so the debug path and the scheduled path can never
# diverge (T-1-08). The check-in label is bound here at registry-construction
# time rather than branched on inside the dispatch. `reminder <n>` is handled
# separately in _debug_fire since it needs a second argument (the 1-based
# index from /remind list) to look up which reminder dict to pass.
DEBUG_JOBS = {
    "checkin_morning": lambda context, chat_id: _run_checkin(context, chat_id, "morning"),
    "checkin_evening": lambda context, chat_id: _run_checkin(context, chat_id, "evening"),
    "deadline_alert": _run_deadline_alert,
    "habit_reminder": _run_habit_reminder,
    "idle_nudge": _run_idle_nudge,
    "weekly_digest": _run_weekly_digest,
}


async def _debug_fire(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list) -> None:
    """`/debug fire <job_name>` — invoke a scheduled job's real body on demand,
    through the same extracted function the scheduler calls, with the real
    context and chat_id so side effects are identical. `/debug fire reminder
    <n>` targets a specific reminder by the same 1-based number /remind list
    shows (D-P3) — never a UUID, and never a nearest-match guess, since firing
    the wrong job has real side effects. No guard is bypassed: quiet hours,
    mute, the Sunday gate and the idle threshold all still apply, and the
    reply names the guard that suppressed the fire instead of returning
    silence."""
    chat_id = update.effective_chat.id
    known = ", ".join(sorted(DEBUG_JOBS.keys())) + ", reminder <n>"

    if not args:
        await update.message.reply_text(
            f"Usage: /debug fire <job_name>\nKnown jobs: {known}"
        )
        return

    job_name = args[0].lower()

    if job_name == "reminder":
        if len(args) < 2:
            await update.message.reply_text("Usage: /debug fire reminder <number>")
            return
        user = get_user(chat_id)
        try:
            n = int(args[1])
            if n < 1:
                raise ValueError
            reminder = user.get("reminders", [])[n - 1]
        except (IndexError, ValueError):
            await update.message.reply_text(
                "Invalid number. Use /remind list to see numbers."
            )
            return
        result = await _run_reminder(context, chat_id, reminder)
        if result is None:
            await update.message.reply_text(f"✅ Fired reminder {n}.")
        else:
            await update.message.reply_text(
                f"⏸️ Not fired — reminder {n} is suppressed right now: {result}."
            )
        return

    runner = DEBUG_JOBS.get(job_name)
    if runner is None:
        await update.message.reply_text(
            f"Unknown job '{job_name}'. Known jobs: {known}"
        )
        return

    result = await runner(context, chat_id)
    if result is None:
        await update.message.reply_text(f"✅ Fired {job_name}.")
    else:
        await update.message.reply_text(
            f"⏸️ Not fired — {job_name} is suppressed right now: {result}. "
            "This is the real guard, not a debug failure."
        )


async def _debug_clock(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list) -> None:
    """`/debug clock <ISO> | reset | (status)` — set, inspect or clear a
    persistent, bounded, per-account simulated "now" (DEBUG-02, D-01). Dual-
    writes exactly as the set_timezone tool does: the in-memory user-dict key
    so the current process sees it immediately, and the SQLite pref so it
    survives a restart. The expiry is real-wall-clock bound to twelve hours
    ahead of `datetime.utcnow()` so a forgotten override cannot silently skew
    this account's real deadline badges and quiet hours forever (T-1-03) —
    resolution against that expiry happens in `_debug_now`."""
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    if not args:
        # Resolve through _debug_now (not a raw field read) so an expired
        # override is reported as absent, matching every other code path
        # that honors the clock (WR-02).
        if _debug_now(user) is None:
            await update.message.reply_text(
                "No simulated clock is set. Real time is in effect."
            )
            return
        override = user.get("debug_clock")
        expires = user.get("debug_clock_expires", "")
        await update.message.reply_text(
            f"🕐 Simulated clock: {override}\n"
            f"Expires (UTC): {expires}\n"
            "Real scheduled jobs keep firing on the real wall clock while this is active."
        )
        return

    if args[0].lower() == "reset":
        db_delete_pref(str(chat_id), "debug_clock")
        db_delete_pref(str(chat_id), "debug_clock_expires")
        user["debug_clock"] = ""
        user["debug_clock_expires"] = ""
        save_state(state)
        await update.message.reply_text("🕐 Simulated clock cleared. Real time is in effect.")
        return

    raw = args[0].strip()
    try:
        datetime.fromisoformat(raw)  # validate (V5); matches duedate_cmd's convention
    except ValueError:
        await update.message.reply_text(
            "Usage: /debug clock <ISO instant>\n"
            "Accepted forms: YYYY-MM-DD or YYYY-MM-DDTHH:MM\n"
            "Or: /debug clock reset"
        )
        return

    expires_iso = (datetime.utcnow() + timedelta(hours=12)).isoformat()
    user["debug_clock"] = raw
    user["debug_clock_expires"] = expires_iso
    db_set_pref(str(chat_id), "debug_clock", raw)
    db_set_pref(str(chat_id), "debug_clock_expires", expires_iso)
    save_state(state)
    await update.message.reply_text(
        f"🕐 Simulated clock set to {raw}.\n"
        f"Expires (UTC): {expires_iso} — after this it auto-resets to real time.\n"
        "Real scheduled jobs keep firing on the real wall clock while this is active. "
        "Use /debug clock reset to clear it early."
    )


async def _debug_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/debug prompt` — dump build_system_prompt(user, chat_id) verbatim to the
    owner. This is a read, not a chat turn: no LLM client is constructed, no
    completion is requested, nothing is appended to history, and nothing is
    written to disk. The prompt carries the user's real journal entries, notes
    and profile memory (T-1-02), so delivery stays entirely in memory, never a
    filesystem handle, and this function contains no logging statement.

    Below the threshold the prompt is the entire message body verbatim; above
    it, follow export_data's in-memory document pattern exactly so a long
    prompt is delivered whole rather than truncated or split (T-1-06)."""
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    prompt = build_system_prompt(user, chat_id)
    if len(prompt) <= DEBUG_PROMPT_INLINE_MAX:
        await update.message.reply_text(prompt)
    else:
        data_bytes = prompt.encode("utf-8")
        bio = BytesIO(data_bytes)
        bio.name = "system_prompt.txt"
        await update.message.reply_document(
            document=bio,
            filename="system_prompt.txt",
            caption=f"System prompt ({len(prompt)} chars)."
        )


async def debug_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/debug fire|clock|prompt — owner-only debug surface (DEBUG-01/02/03).
    Fails closed: rejects every caller, including the developer, when
    MY_CHAT_ID is unset or empty (broadcast_cmd's owner-gate spelling)."""
    chat_id = update.effective_chat.id
    if not MY_CHAT_ID or str(chat_id) != MY_CHAT_ID:
        await update.message.reply_text("Admin only.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /debug <fire|clock|prompt> [args]"
        )
        return

    sub = args[0].lower()
    if sub == "fire":
        await _debug_fire(update, context, args[1:])
    elif sub == "clock":
        await _debug_clock(update, context, args[1:])
    elif sub == "prompt":
        await _debug_prompt(update, context)
    else:
        await update.message.reply_text(
            "Unknown subcommand. Use fire, clock, or prompt."
        )


# ─────────────────────── handlers: habits ───────────────────────

async def habit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    habits = user.setdefault("habits", {})
    args = context.args

    if not args or args[0].lower() in ("list", "ls"):
        if not habits:
            await update.message.reply_text(
                "No habits yet. Add one with /habit add <name>"
            )
            return
        lines = _habit_summary_lines(habits, user=user)
        await update.message.reply_text("Your habits:\n" + "\n".join(lines))
        return

    sub = args[0].lower()

    if sub == "add":
        if len(args) < 2:
            await update.message.reply_text("Usage: /habit add <name>")
            return
        name = args[1].lower()
        if name in habits:
            await update.message.reply_text(f"Habit '{name}' already exists.")
            return
        habits[name] = {"completions": [], "created": date.today().isoformat()}
        save_state(state)
        await update.message.reply_text(
            f"Habit '{name}' added!\nMark it done today with /habit done {name}"
        )

    elif sub == "done":
        if len(args) < 2:
            await update.message.reply_text("Usage: /habit done <name>")
            return
        name = args[1].lower()
        if name not in habits:
            await update.message.reply_text(f"No habit named '{name}'. Use /habit list.")
            return
        today = date.today().isoformat()
        completions = habits[name].setdefault("completions", [])
        if today in completions:
            await update.message.reply_text(f"'{name}' already marked done today.")
            return
        completions.append(today)
        if len(completions) > 365:
            habits[name]["completions"] = completions[-365:]
        save_state(state)
        streak = _habit_streak(completions, user=user)
        await update.message.reply_text(f"✓ '{name}' done! 🔥 Streak: {streak} day{'s' if streak != 1 else ''}")

    elif sub == "remove":
        if len(args) < 2:
            await update.message.reply_text("Usage: /habit remove <name>")
            return
        name = args[1].lower()
        if name not in habits:
            await update.message.reply_text(f"No habit named '{name}'.")
            return
        del habits[name]
        save_state(state)
        await update.message.reply_text(f"Habit '{name}' removed.")

    elif sub == "stats":
        if len(args) < 2:
            await update.message.reply_text("Usage: /habit stats <name>")
            return
        name = args[1].lower()
        if name not in habits:
            await update.message.reply_text(f"No habit named '{name}'. Use /habit list.")
            return
        completions = sorted(habits[name].get("completions", []))
        today = _today(user=user)
        last_7 = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
        dots = "".join("✅" if d in completions else "❌" for d in last_7)
        current = _habit_streak(completions, user=user)
        # Longest streak
        longest = 0
        run = 0
        prev = None
        for d in completions:
            if prev and (date.fromisoformat(d) - date.fromisoformat(prev)).days == 1:
                run += 1
            else:
                run = 1
            longest = max(longest, run)
            prev = d
        # 30-day rate
        cutoff = (today - timedelta(days=29)).isoformat()
        done_30 = sum(1 for d in completions if d >= cutoff)
        rate = round(done_30 / 30 * 100)
        last_missed = next(
            (d for d in reversed(last_7) if d not in completions), None
        )
        created = habits[name].get("created", "?")
        await update.message.reply_text(
            f"📊 Habit: {name}\n"
            f"Created: {created}\n"
            f"Last 7 days: {dots}\n"
            f"Current streak: {current} day{'s' if current != 1 else ''}\n"
            f"Longest streak: {longest} day{'s' if longest != 1 else ''}\n"
            f"30-day completion: {rate}% ({done_30}/30)\n"
            f"Total completions: {len(completions)}"
            + (f"\nLast missed: {last_missed}" if last_missed else "")
        )

    else:
        await update.message.reply_text("Usage: /habit add|done|list|remove|stats <name>")


# ─────────────────────── handlers: mystats & pomodoro ───────────────────────

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    streak = _get_streak(user)
    total_days = len(user.get("activity_days", []))
    activity = sorted(user.get("activity_days", []))
    first_seen = activity[0] if activity else "N/A"

    model = get_model(user)
    has_own_key = bool(user["llm"].get("api_key"))
    model_info = f"{model} (your key)" if has_own_key else f"{model} (default)"

    # Last-7-day activity chart
    days_set = set(user.get("activity_days", []))
    week_chart = "".join(
        "█" if (_today(user=user) - timedelta(days=i)).isoformat() in days_set else "░"
        for i in range(6, -1, -1)
    )

    mute_status = ""
    if _is_muted(user):
        until = user.get("muted_until", "")[:16].replace("T", " ")
        mute_status = f"🔕 Muted until {until} UTC"

    lines = [
        f"🔥 Streak: {streak} day{'s' if streak != 1 else ''}",
        f"📅 Last 7 days: {week_chart}",
        f"🗓 Active: {total_days} days (first: {first_seen})",
        f"✅ Tasks: {len(user['tasks'])} active  ·  {len(user.get('archived_tasks', []))} done",
        f"📋 Trackers: {', '.join(user.get('trackers', {}).keys()) or 'none'}",
        f"📓 Journal: {len(user.get('journal', []))} entries",
        f"⏰ Reminders: {len(user.get('reminders', []))}",
        f"🤖 Model: {model_info}",
    ]
    if mute_status:
        lines.append(mute_status)
    habit_lines = _habit_summary_lines(user.get("habits", {}), user=user)
    if habit_lines:
        lines.append("\nHabits today:")
        lines.extend(habit_lines)

    await update.message.reply_text("📊 Your stats:\n" + "\n".join(lines))


async def pomodoro_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    minutes = 25
    if context.args:
        try:
            minutes = int(context.args[0])
            assert 1 <= minutes <= 120
        except (ValueError, AssertionError):
            await update.message.reply_text("Usage: /pomodoro [minutes]  (1–120, default 25)")
            return

    chat_id = update.effective_chat.id

    async def _done(ctx: ContextTypes.DEFAULT_TYPE, _cid=chat_id, _min=minutes):
        await ctx.bot.send_message(
            chat_id=_cid,
            text=f"🍅 Pomodoro done! {_min} min complete. Take a short break, then keep going."
        )

    context.application.job_queue.run_once(_done, when=minutes * 60)
    await update.message.reply_text(f"🍅 Pomodoro started: {minutes} min. I'll ping you when it's done!")


async def focus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/focus [task_number] [minutes] — pomodoro linked to a specific task."""
    user = get_user(update.effective_chat.id)
    chat_id = update.effective_chat.id
    task_label = None
    minutes = 25

    # /focus [task_n] [minutes] — first numeric arg is task index, second is duration
    args_ints = []
    for arg in context.args:
        try:
            args_ints.append(int(arg))
        except ValueError:
            pass

    tasks_list = user.get("tasks", [])
    if len(args_ints) >= 2:
        # Both supplied: first = task index, second = minutes
        idx = args_ints[0] - 1
        if 0 <= idx < len(tasks_list):
            task_label = _task_text(tasks_list[idx])
        minutes = max(1, min(120, args_ints[1]))
    elif len(args_ints) == 1:
        n = args_ints[0]
        if 1 <= n <= len(tasks_list):
            task_label = _task_text(tasks_list[n - 1])
        else:
            minutes = max(1, min(120, n))

    async def _done(ctx, _cid=chat_id, _min=minutes, _task=task_label):
        if _task:
            msg = f"🍅 {_min} min focus done! How did it go with '{_task}'?"
        else:
            msg = f"🍅 {_min} min focus session complete. Take a break!"
        await ctx.bot.send_message(chat_id=_cid, text=msg)

    context.application.job_queue.run_once(_done, when=minutes * 60)
    if task_label:
        await update.message.reply_text(
            f"🎯 Focusing on: {task_label}\n🍅 {minutes} min session started. Good luck!"
        )
    else:
        await update.message.reply_text(f"🍅 Focus session started: {minutes} min.")


# ─────────────────────── handlers: import ───────────────────────

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_MMDD_RE = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


def _sanitize_import(imported: dict) -> tuple[dict, list[str]]:
    """Validate an /import payload into only the well-formed parts of it.

    The file comes from the user, and whatever lands in state is read on every
    later turn by build_system_prompt() and the schedulers — neither of which
    tolerates a wrong shape. An unvalidated import therefore doesn't just fail
    at import time, it bricks the account: build_system_prompt() raises on
    every subsequent message, so the user can't even talk to the bot to undo
    it. Drop what doesn't fit the shape in _new_user() and report it, rather
    than trusting the file or rejecting it wholesale over one bad entry.

    Returns (clean, skipped) — `clean` holds only keys that validated, and
    `skipped` holds human-readable notes about what was dropped.
    """
    clean: dict = {}
    skipped: list[str] = []

    if not isinstance(imported, dict):
        return clean, ["file is not a JSON object"]

    # tasks: list of plain strings or {"text": str, "due": ISO date, "recur": ...}
    if "tasks" in imported:
        raw = imported["tasks"]
        if isinstance(raw, list):
            tasks, bad = [], 0
            for t in raw:
                if isinstance(t, str):
                    tasks.append(t)
                elif isinstance(t, dict) and isinstance(t.get("text"), str):
                    task = {"text": t["text"]}
                    due = t.get("due")
                    if isinstance(due, str) and due:
                        try:
                            date.fromisoformat(due)
                            task["due"] = due
                        except ValueError:
                            bad += 1
                            continue
                    if t.get("recur") in ("daily", "weekly", "monthly"):
                        task["recur"] = t["recur"]
                    tasks.append(task)
                else:
                    bad += 1
            clean["tasks"] = tasks
            if bad:
                skipped.append(f"{bad} malformed task(s)")
        else:
            skipped.append("tasks (not a list)")

    if "context" in imported:
        if isinstance(imported["context"], str):
            clean["context"] = imported["context"]
        else:
            skipped.append("context (not text)")

    if "timezone" in imported:
        tz = imported["timezone"]
        if isinstance(tz, str):
            try:
                ZoneInfo(tz)
                clean["timezone"] = tz
            except (ZoneInfoNotFoundError, KeyError, ValueError):
                skipped.append(f"timezone '{tz}' (unknown)")
        else:
            skipped.append("timezone (not text)")

    # trackers: {name: {"unit": str, "log": [{"ts": iso, "value": number}]}}
    if "trackers" in imported:
        raw = imported["trackers"]
        if isinstance(raw, dict):
            trackers, bad = {}, 0
            for tname, data in raw.items():
                if not isinstance(tname, str) or not isinstance(data, dict):
                    bad += 1
                    continue
                log = []
                for entry in data.get("log", []) if isinstance(data.get("log"), list) else []:
                    if (isinstance(entry, dict) and isinstance(entry.get("ts"), str)
                            and isinstance(entry.get("value"), (int, float))
                            and not isinstance(entry.get("value"), bool)):
                        log.append({"ts": entry["ts"], "value": float(entry["value"])})
                unit = data.get("unit")
                trackers[tname.lower()] = {
                    "unit": unit if isinstance(unit, str) else "",
                    "log": log[-TRACKER_LOG_CAP:],
                }
            clean["trackers"] = trackers
            if bad:
                skipped.append(f"{bad} malformed tracker(s)")
        else:
            skipped.append("trackers (not an object)")

    # habits: {name: {"completions": [ISO date], "created": ISO date}}
    if "habits" in imported:
        raw = imported["habits"]
        if isinstance(raw, dict):
            habits, bad = {}, 0
            for hname, data in raw.items():
                if not isinstance(hname, str) or not isinstance(data, dict):
                    bad += 1
                    continue
                comps = data.get("completions")
                habits[hname] = {
                    "completions": [c for c in comps if isinstance(c, str)] if isinstance(comps, list) else [],
                    "created": data["created"] if isinstance(data.get("created"), str) else date.today().isoformat(),
                }
            clean["habits"] = habits
            if bad:
                skipped.append(f"{bad} malformed habit(s)")
        else:
            skipped.append("habits (not an object)")

    if "journal" in imported:
        raw = imported["journal"]
        if isinstance(raw, list):
            clean["journal"] = [
                e["entry"] if isinstance(e, dict) and isinstance(e.get("entry"), str)
                else e if isinstance(e, str) else None
                for e in raw
            ]
            dropped = clean["journal"].count(None)
            clean["journal"] = [e for e in clean["journal"] if e is not None]
            if dropped:
                skipped.append(f"{dropped} malformed journal entr(ies)")
        else:
            skipped.append("journal (not a list)")

    if "notes" in imported:
        raw = imported["notes"]
        if isinstance(raw, list):
            notes = []
            for n in raw:
                if isinstance(n, str):
                    notes.append(n)
                elif isinstance(n, dict) and isinstance(n.get("text"), str):
                    notes.append(n["text"])
            clean["notes"] = notes
            if len(notes) != len(raw):
                skipped.append(f"{len(raw) - len(notes)} malformed note(s)")
        else:
            skipped.append("notes (not a list)")

    # reminders: schedule_user_reminder() indexes r["time"] unconditionally and
    # run_daily needs a real HH:MM, so a missing/!HH:MM time is fatal downstream.
    if "reminders" in imported:
        raw = imported["reminders"]
        if isinstance(raw, list):
            reminders, bad = [], 0
            for r in raw:
                if not isinstance(r, dict) or not isinstance(r.get("time"), str) \
                        or not _HHMM_RE.match(r["time"]) or not isinstance(r.get("message"), str):
                    bad += 1
                    continue
                rem = {
                    "id": r["id"] if isinstance(r.get("id"), str) and r.get("id") else str(uuid.uuid4()),
                    "time": r["time"],
                    "message": r["message"],
                    "once": bool(r.get("once", False)),
                }
                if isinstance(r.get("reason"), str):
                    rem["reason"] = r["reason"]
                # An annual reminder needs a valid MM-DD or the deadline-alert
                # date check silently never matches it.
                if r.get("annual"):
                    if isinstance(r.get("date"), str) and _MMDD_RE.match(r["date"]):
                        rem["annual"] = True
                        rem["date"] = r["date"]
                    else:
                        bad += 1
                        continue
                reminders.append(rem)
            clean["reminders"] = reminders
            if bad:
                skipped.append(f"{bad} malformed reminder(s)")
        else:
            skipped.append("reminders (not a list)")

    return clean, skipped


async def handle_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc or not doc.file_name.endswith(".json"):
        await update.message.reply_text("Please send a secretary_export.json file from /export.")
        return

    tg_file = await context.bot.get_file(doc.file_id)
    raw = await tg_file.download_as_bytearray()
    try:
        imported = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        await update.message.reply_text("Invalid JSON file.")
        return

    clean, skipped = _sanitize_import(imported)
    if not clean:
        await update.message.reply_text(
            "Nothing importable in that file.\n"
            + ("Problems: " + ", ".join(skipped) if skipped else "")
        )
        return

    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    counts = {}

    if "tasks" in clean:
        user["tasks"] = clean["tasks"]
        counts["tasks"] = len(user["tasks"])
    if "context" in clean:
        user["context"] = clean["context"]
    if "timezone" in clean:
        # Route through the same helper /settimezone uses so the SQLite
        # override and the confirmed flag stay in sync with state.json.
        _set_user_timezone(chat_id, user, clean["timezone"])
    if "trackers" in clean:
        user["trackers"] = clean["trackers"]
        counts["trackers"] = len(user["trackers"])
    if "habits" in clean:
        user["habits"] = clean["habits"]
        counts["habits"] = len(user["habits"])
    if "journal" in clean:
        cid = str(chat_id)
        for entry in clean["journal"]:
            db_add_journal(cid, entry, auto=False)
        counts["journal"] = len(clean["journal"])
    if "notes" in clean:
        cid = str(chat_id)
        for text in clean["notes"]:
            db_add_note(cid, text, auto=False)
        counts["notes"] = len(clean["notes"])
    if "reminders" in clean:
        user["reminders"] = clean["reminders"]
        counts["reminders"] = len(user["reminders"])
        for reminder in list(user["reminders"]):
            if not reminder.get("annual"):
                schedule_user_reminder(context.application, chat_id, reminder)

    save_state(state)
    summary = "  " + "\n  ".join(f"{k}: {v}" for k, v in counts.items())
    msg = f"✓ Import successful!\n{summary}"
    if skipped:
        msg += "\n\n⚠️ Skipped: " + ", ".join(skipped)
    await update.message.reply_text(msg)


# ─────────────────────── handlers: quiet hours ───────────────────────

async def quiet_hours_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    args = context.args

    if not args or args[0].lower() == "off":
        user["quiet_hours"] = {"start": None, "end": None}
        save_state(state)
        await update.message.reply_text("Quiet hours disabled. Check-ins and reminders will fire at their scheduled times.")
        return

    if len(args) < 2:
        tz = user.get("timezone", "UTC")
        qh = user.get("quiet_hours", {})
        if qh.get("start"):
            await update.message.reply_text(
                f"Quiet hours: {qh['start']}–{qh['end']} ({tz})\n"
                "Use /quiethours off to disable."
            )
        else:
            await update.message.reply_text(
                "Usage: /quiethours HH:MM HH:MM\n"
                "Example: /quiethours 23:00 07:00  (no messages at night)\n"
                "Use /quiethours off to disable."
            )
        return

    def _valid_time(s):
        try:
            h, m = s.split(":")
            assert 0 <= int(h) <= 23 and 0 <= int(m) <= 59
            return True
        except (ValueError, AssertionError):
            return False

    start_str, end_str = args[0], args[1]
    if not _valid_time(start_str) or not _valid_time(end_str):
        await update.message.reply_text("Invalid time. Use HH:MM format.")
        return

    if not user.get("timezone_confirmed"):
        await update.message.reply_text(_TZ_NOT_CONFIRMED_MSG)
        return

    user["quiet_hours"] = {"start": start_str, "end": end_str}
    save_state(state)
    tz = user.get("timezone", "UTC")
    await update.message.reply_text(
        f"Quiet hours set: {start_str}–{end_str} ({tz})\n"
        "Check-ins and reminders will be silenced during this window."
    )


# ─────────────────────── handlers: custom check-in times ────────────────────

async def set_checkin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    tz = user.get("timezone", "UTC")
    times = user.get("checkin_times", {"morning": "08:00", "evening": "21:00"})

    if not context.args:
        await update.message.reply_text(
            f"Current check-in times: {times['morning']} (morning) and {times['evening']} (evening) ({tz})\n"
            "Usage: /setcheckin HH:MM HH:MM\n"
            "Example: /setcheckin 07:30 22:00"
        )
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /setcheckin <morning HH:MM> <evening HH:MM>")
        return

    if not user.get("timezone_confirmed"):
        await update.message.reply_text(_TZ_NOT_CONFIRMED_MSG)
        return

    def _valid(s):
        try:
            h, m = s.split(":")
            assert 0 <= int(h) <= 23 and 0 <= int(m) <= 59
            return True
        except (ValueError, AssertionError):
            return False

    morning_t, evening_t = context.args[0], context.args[1]
    if not _valid(morning_t) or not _valid(evening_t):
        await update.message.reply_text("Invalid time. Use HH:MM (24h format).")
        return

    user["checkin_times"] = {"morning": morning_t, "evening": evening_t}
    save_state(state)
    schedule_user_checkins(context.application, update.effective_chat.id)
    await update.message.reply_text(
        f"Check-in times updated:\n"
        f"  Morning: {morning_t} ({tz})\n"
        f"  Evening: {evening_t} ({tz})"
    )


# ─────────────────────── handlers: AI insights ───────────────────────

async def insights_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    parts = []

    if user["tasks"]:
        parts.append("Tasks: " + _tasks_for_prompt(user["tasks"]))

    for name, data in user.get("trackers", {}).items():
        log = data.get("log", [])
        if log:
            unit = data.get("unit", "")
            recent = log[-30:]
            vals = [str(e["value"]) + unit for e in recent]
            parts.append(f"{name} (last {len(recent)} entries): {', '.join(vals)}")

    for name, data in user.get("habits", {}).items():
        completions = data.get("completions", [])
        streak = _habit_streak(completions, user=user)
        total = len(completions)
        parts.append(f"Habit '{name}': {total} completions total, current streak {streak}d")

    recent_journal = db_get_journal(str(update.effective_chat.id), limit=10)
    if recent_journal:
        excerpts = " | ".join(r["entry"][:80] for r in recent_journal[:3])
        parts.append(f"Recent journal: {excerpts}")

    if not parts:
        await update.message.reply_text("Not enough data for insights yet. Start tracking!")
        return

    data_str = "\n".join(parts)
    prompt = (
        f"Analyze this user's data and provide personalized insights:\n\n{data_str}\n\n"
        "Identify 2-3 key observations: trends (positive or concerning), patterns, "
        "and one specific actionable recommendation. Be direct and specific, not generic. "
        "Keep it to 8-10 lines."
    )
    await update.message.reply_text("Analyzing your data…")
    reply = await chat(update.effective_chat.id, prompt)
    await update.message.reply_text(f"🔍 Insights:\n\n{reply}")


# ─────────────────────── handlers: callback query (inline buttons) ───────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.from_user.id

    # ── snooze button from reminder ──
    if query.data.startswith("snooze_"):
        parts = query.data.split("_")
        # format: snooze_{token}_{minutes}. The join keeps this tolerant of the
        # older multi-segment token format for any button still in flight from
        # before the switch to a uuid4 hex (which contains no underscores).
        minutes = int(parts[-1])
        token = "_".join(parts[1:-1])
        snoozed = db_take_snooze(token) or _snooze_cache.pop(token, None)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        if not snoozed:
            await context.bot.send_message(chat_id=chat_id, text="⚠️ Snooze expired.")
            return

        async def _snooze_fire(ctx: ContextTypes.DEFAULT_TYPE, _cid=chat_id, _r=snoozed):
            await _run_reminder(ctx, _cid, _r, simulate=False)

        context.application.job_queue.run_once(_snooze_fire, when=minutes * 60)
        await context.bot.send_message(
            chat_id=chat_id, text=f"⏱ Snoozed {minutes} min. I'll remind you again."
        )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global fallback for any exception a handler lets escape.

    Without one registered, python-telegram-bot logs "No error handlers are
    registered" and the user is left staring at an unanswered message — every
    crash looks exactly like the bot ignoring them. Log the traceback for the
    owner, and tell the user something went wrong so they can retry or report
    it.
    """
    logger.error("Unhandled exception while handling update", exc_info=context.error)
    chat = getattr(update, "effective_chat", None)
    if chat is None:
        return  # nothing to reply to (e.g. a job or callback-less error)
    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text="⚠️ Something went wrong handling that. It's been logged — please try again, "
                 "or rephrase if it keeps happening.",
        )
    except Exception:
        logger.exception("Failed to deliver the error notice to %s", chat.id)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_rate_limited(chat_id):
        await update.message.reply_text(
            "You've reached the hourly limit (30 messages). Please wait a bit before sending more."
        )
        return
    reply = await chat(chat_id, update.message.text)
    await update.message.reply_text(reply)
    await _check_milestones(chat_id, context.application)


# ─────────────────────── main ───────────────────────

async def _post_init(app) -> None:
    """Register commands with BotFather so users see the list in the Telegram UI."""
    commands = [
        BotCommand("start", "Welcome / onboarding"),
        BotCommand("help", "All commands"),
        BotCommand("tasks", "Show active tasks"),
        BotCommand("addtask", "Add a task (due:YYYY-MM-DD optional)"),
        BotCommand("donetask", "Mark task done & archive"),
        BotCommand("prioritize", "Move task to top"),
        BotCommand("extend", "Extend task due date by N days"),
        BotCommand("today", "Set/show today's focus"),
        BotCommand("checkin", "Manual check-in with dashboard"),
        BotCommand("subscribe", "Enable daily check-ins"),
        BotCommand("remind", "Manage reminders (add/list/remove)"),
        BotCommand("habit", "Track daily habits"),
        BotCommand("journal", "Add journal entry"),
        BotCommand("weekly", "7-day AI summary"),
        BotCommand("reflect", "Personal reflection"),
        BotCommand("suggest", "AI task suggestions"),
        BotCommand("mystats", "Your stats dashboard"),
        BotCommand("focus", "Pomodoro linked to a task"),
        BotCommand("note", "Save a quick note"),
        BotCommand("search", "Search tasks, notes, journal"),
    ]
    try:
        await app.bot.set_my_commands(commands)
        logger.info("BotFather commands registered (%d).", len(commands))
    except Exception as e:
        logger.warning("Could not register commands: %s", e)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(_post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("tasks", show_tasks))
    app.add_handler(CommandHandler("addtask", add_task))
    app.add_handler(CommandHandler("removetask", remove_task))
    app.add_handler(CommandHandler("donetask", done_task))
    app.add_handler(CommandHandler("archive", show_archive))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("prioritize", prioritize_task))
    app.add_handler(CommandHandler("today", today_cmd))
    app.add_handler(CommandHandler("note", note_cmd))
    app.add_handler(CommandHandler("notes", notes_cmd))
    app.add_handler(CommandHandler("removenote", removenote_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("setlanguage", set_language))
    app.add_handler(CommandHandler("clearlanguage", clear_language))
    app.add_handler(CommandHandler("compress", compress_history))
    app.add_handler(CommandHandler("time", time_cmd))
    app.add_handler(CommandHandler("feedback", feedback_cmd))
    app.add_handler(CommandHandler("link", link_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("duedate", duedate_cmd))
    app.add_handler(CommandHandler("extend", extend_cmd))
    app.add_handler(CommandHandler("swap", swap_cmd))
    app.add_handler(CommandHandler("focus", focus_cmd))
    app.add_handler(CommandHandler("mute", mute_cmd))
    app.add_handler(CommandHandler("unmute", unmute_cmd))
    app.add_handler(CommandHandler("limit", limit_cmd))
    app.add_handler(CommandHandler("suggest", suggest_cmd))
    app.add_handler(CommandHandler("reflect", reflect_cmd))
    app.add_handler(CommandHandler("setcontext", set_context))
    app.add_handler(CommandHandler("context", show_context))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CommandHandler("settimezone", set_timezone))
    app.add_handler(CommandHandler("remind", remind_cmd))
    app.add_handler(CommandHandler("addtracker", add_tracker))
    app.add_handler(CommandHandler("trackers", list_trackers))
    app.add_handler(CommandHandler("removetracker", remove_tracker))
    app.add_handler(CommandHandler("checkin", manual_checkin))
    app.add_handler(CommandHandler("clear", clear_history))
    app.add_handler(CommandHandler("setmodel", set_model))
    app.add_handler(CommandHandler("setapikey", set_api_key))
    app.add_handler(CommandHandler("clearapikey", clear_api_key))
    app.add_handler(CommandHandler("journal", journal_cmd))
    app.add_handler(CommandHandler("weekly", weekly_summary))
    app.add_handler(CommandHandler("export", export_data))
    app.add_handler(CommandHandler("streak", streak_cmd))
    app.add_handler(CommandHandler("adminstats", admin_stats))
    app.add_handler(CommandHandler("debug", debug_cmd))
    app.add_handler(CommandHandler("habit", habit_cmd))
    app.add_handler(CommandHandler("mystats", my_stats))
    app.add_handler(CommandHandler("pomodoro", pomodoro_cmd))
    app.add_handler(CommandHandler("quiethours", quiet_hours_cmd))
    app.add_handler(CommandHandler("setcheckin", set_checkin_cmd))
    app.add_handler(CommandHandler("insights", insights_cmd))
    # Inline keyboard callback for check-in buttons
    app.add_handler(CallbackQueryHandler(handle_callback))
    # Catch-all for user-defined tracker commands (must be last command handler)
    app.add_handler(MessageHandler(filters.COMMAND, handle_custom_command))
    # Location sharing → auto-detect timezone
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    # Document handler for /import (JSON file upload)
    app.add_handler(MessageHandler(filters.Document.ALL, handle_import))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(on_error)

    _init_db()
    restore_all_jobs(app)

    global _app
    _app = app  # allow _execute_tool(add_reminder) to schedule jobs

    logger.info("Bot started.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
