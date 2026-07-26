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
import json
import logging
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

STATE_FILE = Path(os.environ.get("BOT_STATE_FILE", Path(__file__).parent / "state.json"))
DB_FILE = Path(os.environ.get("BOT_DB_FILE", Path(__file__).parent / "bot_memory.db"))

# Public hostname for the remote HTTPS deployment (see "remote HTTPS mode"
# below). Only affects HTTP transports' DNS-rebinding Host-header check;
# irrelevant to the default stdio transport used by Claude Desktop/Code.
_REMOTE_DOMAIN = os.environ.get("MCP_REMOTE_DOMAIN", "")

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
)


def _db() -> sqlite3.Connection:
    """Thread-local SQLite connection, same schema/mode as bot.py's _db()."""
    con = sqlite3.connect(DB_FILE, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row
    return con


# ─────────────── helpers ───────────────

def _load() -> dict:
    if not STATE_FILE.exists():
        return {"users": {}}
    with open(STATE_FILE) as f:
        return json.load(f)


def _save(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


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

@mcp.tool()
def list_users() -> list[dict]:
    """List all registered bot users with basic info."""
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

@mcp.tool()
def get_tasks(chat_id: str) -> dict:
    """Get all active tasks for a user."""
    state = _load()
    u = _require_user(state, chat_id)
    return {
        "tasks": [_fmt_task(t, i + 1) for i, t in enumerate(u.get("tasks", []))],
        "count": len(u.get("tasks", [])),
    }


@mcp.tool()
def add_task(chat_id: str, text: str, due_date: str = "", recur: str = "") -> dict:
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


@mcp.tool()
def complete_task(chat_id: str, task_number: int) -> dict:
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


@mcp.tool()
def remove_task(chat_id: str, task_number: int) -> dict:
    """Permanently delete a task (use complete_task to archive instead)."""
    state = _load()
    u = _require_user(state, chat_id)
    tasks = u.get("tasks", [])
    if task_number < 1 or task_number > len(tasks):
        return {"error": f"Task {task_number} not found."}
    removed = _task_text(tasks.pop(task_number - 1))
    _save(state)
    return {"success": True, "removed": removed}


@mcp.tool()
def get_archived_tasks(chat_id: str, limit: int = 20) -> dict:
    """Get recently completed tasks (most recent first)."""
    state = _load()
    u = _require_user(state, chat_id)
    archived = list(reversed(u.get("archived_tasks", [])))[:limit]
    return {"archived": archived, "total": len(u.get("archived_tasks", []))}


# ─────────────── tools: habits ───────────────

@mcp.tool()
def get_habits(chat_id: str) -> list[dict]:
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


@mcp.tool()
def log_habit(chat_id: str, habit_name: str) -> dict:
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

@mcp.tool()
def get_trackers(chat_id: str) -> list[dict]:
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


@mcp.tool()
def log_tracker(chat_id: str, tracker_name: str, value: float) -> dict:
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

@mcp.tool()
def get_journal(chat_id: str, limit: int = 10) -> list[dict]:
    """Get recent journal entries from bot_memory.db (most recent first)."""
    with _db() as con:
        rows = con.execute(
            "SELECT id, entry, ts, auto FROM journal WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (str(chat_id), limit)
        ).fetchall()
    return [dict(r) for r in rows]


@mcp.tool()
def add_journal_entry(chat_id: str, text: str) -> dict:
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

@mcp.tool()
def get_notes(chat_id: str) -> dict:
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


@mcp.tool()
def add_note(chat_id: str, text: str) -> dict:
    """Save a quick note for a user into bot_memory.db."""
    ts = datetime.utcnow().isoformat()
    with _db() as con:
        cur = con.execute(
            "INSERT INTO notes(chat_id, text, ts, auto) VALUES(?,?,?,0)",
            (str(chat_id), text.strip(), ts)
        )
        row_id = cur.lastrowid
    return {"success": True, "id": row_id, "saved_at": ts}


@mcp.tool()
def remove_note(chat_id: str, note_number: int) -> dict:
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

@mcp.tool()
def get_memory(chat_id: str) -> dict:
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


# ─────────────── tools: stats & overview ───────────────

@mcp.tool()
def get_user_stats(chat_id: str) -> dict:
    """Get a full stats overview for a user."""
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
    }


@mcp.tool()
def get_reminders(chat_id: str) -> list[dict]:
    """List all scheduled reminders for a user."""
    state = _load()
    u = _require_user(state, chat_id)
    return u.get("reminders", [])


# ─────────────── MCP resources ───────────────

@mcp.resource("bot://users")
def resource_users() -> str:
    """All registered users (summary)."""
    users = list_users()
    return json.dumps(users, indent=2)


@mcp.resource("bot://users/{chat_id}/tasks")
def resource_tasks(chat_id: str) -> str:
    """Active task list for a user."""
    return json.dumps(get_tasks(chat_id), indent=2)


@mcp.resource("bot://users/{chat_id}/habits")
def resource_habits(chat_id: str) -> str:
    """Habit list for a user."""
    return json.dumps(get_habits(chat_id), indent=2)


@mcp.resource("bot://users/{chat_id}/journal")
def resource_journal(chat_id: str) -> str:
    """Last 10 journal entries for a user."""
    return json.dumps(get_journal(chat_id, limit=10), indent=2)


@mcp.resource("bot://users/{chat_id}/notes")
def resource_notes(chat_id: str) -> str:
    """All notes for a user."""
    return json.dumps(get_notes(chat_id), indent=2)


@mcp.resource("bot://users/{chat_id}/memory")
def resource_memory(chat_id: str) -> str:
    """Profile + episodic memory the bot has learned about a user."""
    return json.dumps(get_memory(chat_id), indent=2)


@mcp.resource("bot://users/{chat_id}/trackers")
def resource_trackers(chat_id: str) -> str:
    """Tracker data for a user."""
    return json.dumps(get_trackers(chat_id), indent=2)


@mcp.resource("bot://users/{chat_id}/stats")
def resource_stats(chat_id: str) -> str:
    """Full stats for a user."""
    return json.dumps(get_user_stats(chat_id), indent=2)


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
# claude.ai's "Add custom connector" dialog has no field for a bearer token
# or custom header -- just a URL (plus optional OAuth client id/secret). So
# instead of an Authorization header, the shared secret is a query parameter
# baked into the URL you paste into claude.ai: https://host/mcp?key=<token>.

_log = logging.getLogger("secretary_mcp.remote")


class _TokenAuthMiddleware:
    """Reject any request to the MCP endpoint whose ?key= doesn't match MCP_REMOTE_TOKEN.

    Only gates the actual /mcp route -- everything else (in particular the
    OAuth discovery paths claude.ai's connector probes, like
    /.well-known/oauth-protected-resource and /register) is passed through
    to the app, which has no auth configured and 404s them naturally. That
    404 is what tells the client "no OAuth here, just use the URL's token."

    Rejects with 403, not 401: per the MCP Authorization spec, a 401 is
    specifically the signal that tells a client "this resource requires
    OAuth" and makes it attempt discovery + dynamic client registration
    against this server (which doesn't implement either, so it always fails
    with a "couldn't register" error in the client). 403 ("forbidden, and
    auth won't fix it") carries no such meaning, so the shared-secret check
    still works but doesn't get misread as an OAuth challenge.

    Every request is logged (method, path, client, whether ?key= matched,
    and the eventual response status) for debugging connector issues via
    `journalctl -u secretary-mcp.service`. The token value itself is never
    logged, only whether it matched.
    """

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from urllib.parse import parse_qs
        query = parse_qs(scope.get("query_string", b"").decode())
        key_valid = query.get("key", [None])[0] == self.token
        client = scope.get("client") or ("?", 0)
        headers = dict(scope.get("headers") or [])
        user_agent = headers.get(b"user-agent", b"").decode(errors="replace")
        gated = scope.get("path", "").startswith("/mcp")

        status = {}

        async def logging_send(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        if gated and not key_valid:
            from starlette.responses import PlainTextResponse
            response = PlainTextResponse("Forbidden", status_code=403)
            await response(scope, receive, logging_send)
        else:
            await self.app(scope, receive, logging_send)

        _log.info(
            "%s %s client=%s:%s key_valid=%s ua=%r status=%s",
            scope.get("method"), scope.get("path"),
            client[0], client[1], key_valid, user_agent, status.get("code", "?"),
        )


def _run_remote() -> None:
    import uvicorn

    token = os.environ["MCP_REMOTE_TOKEN"]
    host = os.environ.get("MCP_REMOTE_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_REMOTE_PORT", "8545"))

    app = _TokenAuthMiddleware(mcp.streamable_http_app(), token)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    if os.environ.get("MCP_TRANSPORT") == "remote":
        _run_remote()
    else:
        mcp.run()
