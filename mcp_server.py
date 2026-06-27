#!/usr/bin/env python3
"""
MCP server for secretary-bot.

Exposes tasks, habits, journal, and trackers from state.json as MCP tools
so Claude Desktop / Claude Code can read and write any user's data directly.

Run with:
    python3 mcp_server.py          # stdio transport (for Claude Desktop)
    mcp run mcp_server.py          # same, via mcp CLI
"""

import calendar
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from mcp.server.fastmcp import FastMCP

STATE_FILE = Path(os.environ.get("BOT_STATE_FILE", Path(__file__).parent / "state.json"))

mcp = FastMCP(
    "secretary-bot",
    instructions=(
        "Access a user's secretary-bot data: tasks, habits, journal entries, and trackers. "
        "Always identify the user by their numeric Telegram chat_id. "
        "Use list_users() first if you don't know the chat_id."
    ),
)


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
            "timezone": u.get("timezone", "UTC"),
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


# ─────────────── tools: journal ───────────────

@mcp.tool()
def get_journal(chat_id: str, limit: int = 10) -> list[dict]:
    """Get recent journal entries (most recent first)."""
    state = _load()
    u = _require_user(state, chat_id)
    entries = list(reversed(u.get("journal", [])))[:limit]
    return entries


@mcp.tool()
def add_journal_entry(chat_id: str, text: str) -> dict:
    """Save a journal entry for a user."""
    state = _load()
    u = _require_user(state, chat_id)
    entry = {"ts": datetime.utcnow().isoformat(), "entry": text.strip()}
    u.setdefault("journal", []).append(entry)
    if len(u["journal"]) > 200:
        u["journal"] = u["journal"][-200:]
    _save(state)
    return {"success": True, "saved_at": entry["ts"]}


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
    return {
        "chat_id": chat_id,
        "timezone": u.get("timezone", "UTC"),
        "streak": streak,
        "active_days": len(activity),
        "first_seen": activity[0] if activity else None,
        "last_active": activity[-1] if activity else None,
        "active_tasks": len(u.get("tasks", [])),
        "completed_tasks": len(u.get("archived_tasks", [])),
        "habits": habits_today,
        "trackers": list(u.get("trackers", {}).keys()),
        "journal_entries": len(u.get("journal", [])),
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


@mcp.resource("bot://users/{chat_id}/trackers")
def resource_trackers(chat_id: str) -> str:
    """Tracker data for a user."""
    return json.dumps(get_trackers(chat_id), indent=2)


@mcp.resource("bot://users/{chat_id}/stats")
def resource_stats(chat_id: str) -> str:
    """Full stats for a user."""
    return json.dumps(get_user_stats(chat_id), indent=2)


if __name__ == "__main__":
    mcp.run()
