import os
import re
import json
import time
import logging
import uuid
from collections import defaultdict
from datetime import datetime, date, timedelta, time as dt_time
from io import BytesIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from openai import AsyncOpenAI
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
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
MAX_HISTORY = 20
MAX_LOG_ENTRIES = 500
MAX_JOURNAL_ENTRIES = 200

# Rate limiting: max AI calls per hour per user
RATE_LIMIT = 30
RATE_WINDOW = 3600
_rate_log: dict[str, list] = defaultdict(list)

# Reserved command names that cannot be used as tracker names
RESERVED_COMMANDS = {
    "start", "tasks", "addtask", "removetask", "setcontext", "context",
    "subscribe", "unsubscribe", "settimezone", "remind", "addtracker",
    "trackers", "removetracker", "checkin", "clear", "setmodel",
    "setapikey", "clearapikey", "journal", "weekly", "export",
    "streak", "adminstats", "habit", "mystats", "pomodoro",
    "quiethours", "insights",
}

# ─────────────────────── state ───────────────────────

def _new_user(**overrides) -> dict:
    base = {
        "tasks": [],
        "history": [],
        "context": "",
        "checkin_enabled": False,
        "timezone": "UTC",
        "reminders": [],
        "trackers": {},
        "habits": {},
        "journal": [],
        "activity_days": [],
        "quiet_hours": {"start": None, "end": None},
        "llm": {"model": None, "api_key": None},
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
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


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
    return u


# ─────────────────────── rate limiting ───────────────────────

def is_rate_limited(chat_id: int) -> bool:
    """Return True if user has exceeded RATE_LIMIT AI calls in the last hour."""
    key = str(chat_id)
    now = time.monotonic()
    _rate_log[key] = [t for t in _rate_log[key] if now - t < RATE_WINDOW]
    if len(_rate_log[key]) >= RATE_LIMIT:
        return True
    _rate_log[key].append(now)
    return False


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
    d = date.today()
    while d.isoformat() in days:
        streak += 1
        d -= timedelta(days=1)
    return streak


# ─────────────────────── habit helpers ───────────────────────

def _habit_streak(completions: list) -> int:
    """Consecutive days ending today or yesterday (so streak survives the day)."""
    days = set(completions)
    if not days:
        return 0
    streak = 0
    d = date.today()
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    while d.isoformat() in days:
        streak += 1
        d -= timedelta(days=1)
    return streak


def _habit_summary_lines(habits: dict) -> list[str]:
    today = date.today().isoformat()
    lines = []
    for name, data in habits.items():
        done = today in data.get("completions", [])
        streak = _habit_streak(data.get("completions", []))
        mark = "✓" if done else "○"
        lines.append(f"  {mark} {name}  ({streak}d streak)")
    return lines


# ─────────────────────── quiet hours ───────────────────────

def _is_quiet_now(user: dict) -> bool:
    """Return True if current local time falls within the user's quiet window."""
    qh = user.get("quiet_hours", {})
    start_str = qh.get("start")
    end_str = qh.get("end")
    if not start_str or not end_str:
        return False
    try:
        tz = ZoneInfo(user.get("timezone", "UTC"))
    except (ZoneInfoNotFoundError, KeyError):
        tz = ZoneInfo("UTC")
    now = datetime.now(tz).time().replace(tzinfo=None)
    sh, sm = (int(x) for x in start_str.split(":"))
    eh, em = (int(x) for x in end_str.split(":"))
    start_t = dt_time(sh, sm)
    end_t = dt_time(eh, em)
    if start_t <= end_t:
        return start_t <= now < end_t
    # Spans midnight
    return now >= start_t or now < end_t


# ─────────────────────── task helpers ───────────────────────

def _task_text(task) -> str:
    return task["text"] if isinstance(task, dict) else str(task)


def _task_due(task) -> str | None:
    return task.get("due") if isinstance(task, dict) else None


def _format_task_line(task, idx: int) -> str:
    text = _task_text(task)
    due = _task_due(task)
    if not due:
        return f"{idx}. {text}"
    try:
        due_date = date.fromisoformat(due)
        days_left = (due_date - date.today()).days
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

def get_llm_client(user: dict) -> AsyncOpenAI:
    user_key = user["llm"].get("api_key")
    if user_key:
        # Groq keys start with gsk_
        if user_key.startswith("gsk_"):
            return AsyncOpenAI(api_key=user_key, base_url=GROQ_BASE_URL)
        return AsyncOpenAI(api_key=user_key)
    if GROQ_API_KEY:
        return AsyncOpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)
    return AsyncOpenAI(api_key=DEFAULT_API_KEY)


def get_model(user: dict) -> str:
    user_key = user["llm"].get("api_key")
    user_model = user["llm"].get("model")
    if user_key:
        if user_key.startswith("gsk_") and not user_model:
            return GROQ_DEFAULT_MODEL
        return user_model or DEFAULT_MODEL
    if GROQ_API_KEY:
        return user_model or GROQ_DEFAULT_MODEL
    return user_model or DEFAULT_MODEL


def build_system_prompt(user: dict) -> str:
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

    habit_lines = _habit_summary_lines(user.get("habits", {}))
    habit_section = (
        "\nToday's habits:\n" + "\n".join(habit_lines) + "\n"
        if habit_lines else ""
    )

    tasks_str = _tasks_for_prompt(user["tasks"])
    return (
        "You are a personal secretary and accountability coach bot on Telegram.\n\n"
        "Your job:\n"
        "1. Be a helpful, direct assistant.\n"
        "2. Proactively hold the user accountable for their goals and tasks.\n"
        "3. During check-ins, ask about specific tasks from their task list.\n"
        "4. Be warm but firm. Don't accept vague excuses without gentle pushback.\n"
        "5. Keep responses concise — this is a chat, not an essay.\n"
        "6. Don't offer hotlines or unsolicited emotional support suggestions.\n"
        "7. Correct English mistakes naturally and briefly when they occur.\n"
        f"{context_section}{tracker_section}{habit_section}"
        f"\nThe user's tracked tasks: {tasks_str}\n"
    )


# ─────────────────────── chat ───────────────────────

async def chat(chat_id: int, user_message: str, system: str = None) -> str:
    user = get_user(chat_id)
    _touch_activity(user)
    system = system or build_system_prompt(user)
    user["history"].append({"role": "user", "content": user_message})
    if len(user["history"]) > MAX_HISTORY:
        user["history"] = user["history"][-MAX_HISTORY:]

    response = await get_llm_client(user).chat.completions.create(
        model=get_model(user),
        messages=[{"role": "system", "content": system}] + user["history"],
        max_tokens=600,
        temperature=0.7,
    )
    reply = response.choices[0].message.content.strip()
    user["history"].append({"role": "assistant", "content": reply})
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


def schedule_user_checkins(app: Application, chat_id: int) -> None:
    """Schedule (or reschedule) per-user 08:00 / 21:00 check-in jobs."""
    user = get_user(chat_id)
    tz_str = user.get("timezone", "UTC")
    enabled = user.get("checkin_enabled", False)

    for label, hour in [("morning", 8), ("evening", 21)]:
        job_name = f"checkin_{label}_{chat_id}"
        for job in app.job_queue.get_jobs_by_name(job_name):
            job.schedule_removal()
        if not enabled:
            continue

        t = _parse_local_time(f"{hour:02d}:00", tz_str)
        if label == "morning":
            prompt = (
                "It's morning check-in time. Greet the user briefly, "
                "then ask what they plan to work on today from their task list. "
                "Pick 1-2 specific tasks to focus on."
            )
        else:
            prompt = (
                "It's evening check-in time. Ask the user how their day went. "
                "Ask specifically about what progress they made on their tasks. "
                "If they didn't do much, gently push back and encourage them to do "
                "at least one small thing."
            )

        async def _job(context: ContextTypes.DEFAULT_TYPE, _cid=chat_id, _prompt=prompt):
            user_now = get_user(_cid)
            if _is_quiet_now(user_now):
                return
            try:
                reply = await chat(_cid, _prompt)
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ Going well", callback_data="ci:well"),
                        InlineKeyboardButton("🔄 Partially", callback_data="ci:partial"),
                    ],
                    [
                        InlineKeyboardButton("❌ Not today", callback_data="ci:skip"),
                        InlineKeyboardButton("💬 Let's talk", callback_data="ci:chat"),
                    ],
                ])
                await context.bot.send_message(chat_id=_cid, text=reply, reply_markup=keyboard)
            except Exception as e:
                logger.error("Check-in failed for %s: %s", _cid, e)

        app.job_queue.run_daily(_job, time=t, name=job_name)


def schedule_user_reminder(app: Application, chat_id: int, reminder: dict) -> None:
    user = get_user(chat_id)
    job_name = f"reminder_{chat_id}_{reminder['id']}"
    for job in app.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()
    t = _parse_local_time(reminder["time"], user.get("timezone", "UTC"))

    async def _job(context: ContextTypes.DEFAULT_TYPE, _cid=chat_id, _msg=reminder["message"]):
        if _is_quiet_now(get_user(_cid)):
            return
        try:
            await context.bot.send_message(chat_id=_cid, text=f"⏰ Reminder: {_msg}")
        except Exception as e:
            logger.error("Reminder failed for %s: %s", _cid, e)

    app.job_queue.run_daily(_job, time=t, name=job_name)


def restore_all_jobs(app: Application) -> None:
    """Recreate all scheduled jobs from persisted state on startup."""
    for cid_str, user in state["users"].items():
        cid = int(cid_str)
        schedule_user_checkins(app, cid)
        for reminder in user.get("reminders", []):
            schedule_user_reminder(app, cid, reminder)


# ─────────────────────── tracker helpers ───────────────────────

def _days_ago_iso(n: int) -> str:
    from datetime import timedelta
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


# ─────────────────────── handlers: core ───────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_user(update.effective_chat.id)
    save_state(state)
    await update.message.reply_text(
        "Secretary bot is active.\n\n"
        "Core:\n"
        "  /tasks  /addtask  /removetask\n"
        "  /setcontext  /context\n"
        "  /subscribe  /unsubscribe\n"
        "  /settimezone <IANA>  — e.g. Asia/Jerusalem\n"
        "  /checkin  /clear\n\n"
        "Reminders:\n"
        "  /remind add HH:MM <message>\n"
        "  /remind list\n"
        "  /remind remove <n>\n\n"
        "Trackers:\n"
        "  /addtracker <name> [unit]\n"
        "  /<name> <value> | stats | history\n"
        "  /trackers  /removetracker <name>\n\n"
        "LLM:\n"
        "  /setapikey <key>   /clearapikey\n"
        "  /setmodel <model>  — e.g. gpt-4o\n"
        "  (Groq keys start with gsk_ and use Llama for free)\n\n"
        "More:\n"
        "  /journal <text>  /weekly  /insights  /export  /streak  /mystats\n"
        "  /pomodoro [min]  — focus timer\n"
        "  /habit add|done|list|remove <name>  — daily habits\n"
        "  /quiethours HH:MM HH:MM  — silence at night\n"
        "  /addtask <text> [due:YYYY-MM-DD]  — tasks with deadlines\n"
        "  (Send your export JSON to import data)"
    )


async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    if not user["tasks"]:
        await update.message.reply_text("No tasks set. Use /addtask to add one.")
        return
    lines = [_format_task_line(t, i + 1) for i, t in enumerate(user["tasks"])]
    await update.message.reply_text("Your tasks:\n" + "\n".join(lines))


async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = " ".join(context.args).strip()
    if not raw:
        await update.message.reply_text(
            "Usage: /addtask <description> [due:YYYY-MM-DD]\n"
            "Example: /addtask Submit report due:2026-07-15"
        )
        return
    # Parse optional due date
    due_match = re.search(r"\bdue:(\d{4}-\d{2}-\d{2})\b", raw)
    if due_match:
        due = due_match.group(1)
        try:
            date.fromisoformat(due)
        except ValueError:
            await update.message.reply_text("Invalid date format. Use YYYY-MM-DD.")
            return
        task_text = raw[:due_match.start()].strip()
        task = {"text": task_text, "due": due}
        confirm = f"Added: {task_text} (due {due})"
    else:
        task = raw
        confirm = f"Added: {raw}"
    user = get_user(update.effective_chat.id)
    user["tasks"].append(task)
    save_state(state)
    await update.message.reply_text(confirm)


async def remove_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    try:
        idx = int(context.args[0]) - 1
        removed = user["tasks"].pop(idx)
        save_state(state)
        await update.message.reply_text(f"Removed: {_task_text(removed)}")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /removetask <number>")


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
    user["checkin_enabled"] = True
    save_state(state)
    tz = user.get("timezone", "UTC")
    schedule_user_checkins(context.application, update.effective_chat.id)
    await update.message.reply_text(
        f"Daily check-ins enabled at 08:00 and 21:00 ({tz}).\n"
        "Use /settimezone to change your timezone."
    )


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    user["checkin_enabled"] = False
    save_state(state)
    schedule_user_checkins(context.application, update.effective_chat.id)
    await update.message.reply_text("Daily check-ins disabled.")


async def set_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tz_str = " ".join(context.args).strip()
    if not tz_str:
        await update.message.reply_text(
            "Usage: /settimezone <IANA timezone>\n"
            "Examples: UTC  Europe/London  America/New_York  Asia/Jerusalem"
        )
        return
    try:
        ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, KeyError):
        await update.message.reply_text(f"Unknown timezone: {tz_str}")
        return
    user = get_user(update.effective_chat.id)
    user["timezone"] = tz_str
    save_state(state)
    schedule_user_checkins(context.application, update.effective_chat.id)
    # Reschedule reminders with new timezone
    for reminder in user.get("reminders", []):
        schedule_user_reminder(context.application, update.effective_chat.id, reminder)
    await update.message.reply_text(f"Timezone set to {tz_str}.")


async def manual_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = "The user requested a manual check-in. Ask what's on their mind and how their tasks are going."
    reply = await chat(update.effective_chat.id, prompt)
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
            kind = "once" if r.get("once") else "daily"
            lines.append(f"{i+1}. [{kind}] {r['time']} {tz} — {r['message']}")
        await update.message.reply_text("Your reminders:\n" + "\n".join(lines))

    elif sub == "add":
        if len(args) < 3:
            await update.message.reply_text("Usage: /remind add HH:MM <message>")
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
        schedule_user_reminder(context.application, update.effective_chat.id, reminder)
        tz = user.get("timezone", "UTC")
        await update.message.reply_text(f"Daily reminder set: {time_str} ({tz}) — {message}")

    elif sub == "once":
        if len(args) < 3:
            await update.message.reply_text("Usage: /remind once <30m|2h|HH:MM> <message>")
            return
        spec = args[1]
        message = " ".join(args[2:])
        delay = _parse_once_delay(spec, user.get("timezone", "UTC"))
        if delay is None or delay <= 0:
            await update.message.reply_text("Invalid time spec. Use: 30m, 2h, or HH:MM")
            return

        reminder_id = str(uuid.uuid4())
        job_name = f"once_{update.effective_chat.id}_{reminder_id}"
        chat_id = update.effective_chat.id

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
            idx = int(args[1]) - 1
            removed = user.get("reminders", []).pop(idx)
            job_name = f"reminder_{update.effective_chat.id}_{removed['id']}"
            for job in context.application.job_queue.get_jobs_by_name(job_name):
                job.schedule_removal()
            save_state(state)
            await update.message.reply_text(f"Removed: {removed['time']} — {removed['message']}")
        except (IndexError, ValueError):
            await update.message.reply_text("Invalid number. Use /remind list to see numbers.")

    else:
        await update.message.reply_text("Unknown subcommand. Use add, once, list, or remove.")


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
    else:
        try:
            raw = float(sub)
            value = int(raw) if raw == int(raw) else raw
        except ValueError:
            await update.message.reply_text(
                f"Usage: /{cmd} <number> | stats | history"
            )
            return
        entry = {"ts": datetime.utcnow().isoformat(), "value": value}
        tracker.setdefault("log", []).append(entry)
        if len(tracker["log"]) > MAX_LOG_ENTRIES:
            tracker["log"] = tracker["log"][-MAX_LOG_ENTRIES:]
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
    user = get_user(update.effective_chat.id)
    user["llm"]["api_key"] = key
    save_state(state)
    try:
        await update.message.delete()
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✓ API key saved. (Original message deleted for security.)"
    )


async def clear_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    user = get_user(update.effective_chat.id)
    user.setdefault("journal", []).append({"ts": datetime.utcnow().isoformat(), "entry": text})
    if len(user["journal"]) > MAX_JOURNAL_ENTRIES:
        user["journal"] = user["journal"][-MAX_JOURNAL_ENTRIES:]
    save_state(state)

    prompt = f'The user just wrote this journal entry: "{text}"\nOffer a brief, warm reflection in 2-3 sentences.'
    reply = await chat(update.effective_chat.id, prompt)
    await update.message.reply_text(f"\U0001f4d4 Saved.\n\n{reply}")


async def weekly_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    lines = []

    if user["tasks"]:
        lines.append(f"Tasks being tracked: {', '.join(user['tasks'])}")

    for name, data in user.get("trackers", {}).items():
        recent = [e for e in data.get("log", []) if e.get("ts", "") >= _days_ago_iso(7)]
        if recent:
            unit = data.get("unit", "")
            vals = ", ".join(str(e["value"]) + unit for e in recent)
            lines.append(f"{name} (last 7 days): {vals}")

    journal = user.get("journal", [])
    recent_journal = [e for e in journal if e.get("ts", "") >= _days_ago_iso(7)]
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
        "journal": user["journal"],
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
    with_key = sum(1 for u in state["users"].values() if u.get("llm", {}).get("api_key"))
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
        lines = _habit_summary_lines(habits)
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
        streak = _habit_streak(completions)
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

    else:
        await update.message.reply_text("Usage: /habit add|done|list|remove <name>")


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

    lines = [
        f"🔥 Streak: {streak} day{'s' if streak != 1 else ''}",
        f"🗓 Active: {total_days} days (first: {first_seen})",
        f"✅ Tasks: {len(user['tasks'])}",
        f"📋 Trackers: {', '.join(user.get('trackers', {}).keys()) or 'none'}",
        f"📓 Journal: {len(user.get('journal', []))} entries",
        f"⏰ Reminders: {len(user.get('reminders', []))}",
        f"🤖 Model: {model_info}",
    ]
    habit_lines = _habit_summary_lines(user.get("habits", {}))
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


# ─────────────────────── handlers: import ───────────────────────

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

    user = get_user(update.effective_chat.id)
    counts = {}

    if "tasks" in imported:
        user["tasks"] = imported["tasks"]
        counts["tasks"] = len(user["tasks"])
    if "context" in imported:
        user["context"] = imported["context"]
    if "timezone" in imported:
        try:
            ZoneInfo(imported["timezone"])
            user["timezone"] = imported["timezone"]
        except (ZoneInfoNotFoundError, KeyError):
            pass
    if "trackers" in imported:
        user["trackers"] = imported["trackers"]
        counts["trackers"] = len(user["trackers"])
    if "habits" in imported:
        user["habits"] = imported["habits"]
        counts["habits"] = len(user["habits"])
    if "journal" in imported:
        user["journal"] = imported["journal"]
        counts["journal"] = len(user["journal"])
    if "reminders" in imported:
        for r in imported["reminders"]:
            r.setdefault("id", str(uuid.uuid4()))
            r.setdefault("once", False)
        user["reminders"] = imported["reminders"]
        counts["reminders"] = len(user["reminders"])
        for reminder in user["reminders"]:
            schedule_user_reminder(context.application, update.effective_chat.id, reminder)

    save_state(state)
    summary = "  " + "\n  ".join(f"{k}: {v}" for k, v in counts.items())
    await update.message.reply_text(f"✓ Import successful!\n{summary}")


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

    user["quiet_hours"] = {"start": start_str, "end": end_str}
    save_state(state)
    tz = user.get("timezone", "UTC")
    await update.message.reply_text(
        f"Quiet hours set: {start_str}–{end_str} ({tz})\n"
        "Check-ins and reminders will be silenced during this window."
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
        streak = _habit_streak(completions)
        total = len(completions)
        parts.append(f"Habit '{name}': {total} completions total, current streak {streak}d")

    journal = user.get("journal", [])
    if journal:
        recent_j = journal[-5:]
        entries_text = " | ".join(e["entry"][:60] for e in recent_j)
        parts.append(f"Recent journal entries: {entries_text}")

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

    if not query.data.startswith("ci:"):
        return

    mood = query.data.split(":")[1]
    prompts = {
        "well": (
            "The user tapped 'Going well' after a check-in message. "
            "Acknowledge briefly and ask what specific task they're tackling next."
        ),
        "partial": (
            "The user tapped 'Partially done' after a check-in. "
            "Acknowledge the progress warmly, then ask what got in the way and "
            "encourage one concrete next step."
        ),
        "skip": (
            "The user tapped 'Not today' after a check-in. "
            "Be understanding — don't lecture — but ask what got in the way "
            "and suggest the smallest possible step they could still do."
        ),
        "chat": (
            "The user wants to talk after a check-in. "
            "Open the conversation warmly — ask what's on their mind."
        ),
    }
    prompt = prompts.get(mood, "User responded to a check-in.")

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    if is_rate_limited(chat_id):
        await context.bot.send_message(chat_id=chat_id, text="Hourly limit reached. Talk to me later!")
        return

    reply = await chat(chat_id, prompt)
    await context.bot.send_message(chat_id=chat_id, text=reply)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_rate_limited(chat_id):
        await update.message.reply_text(
            "You've reached the hourly limit (30 messages). Please wait a bit before sending more."
        )
        return
    reply = await chat(chat_id, update.message.text)
    await update.message.reply_text(reply)


# ─────────────────────── main ───────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("tasks", show_tasks))
    app.add_handler(CommandHandler("addtask", add_task))
    app.add_handler(CommandHandler("removetask", remove_task))
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
    app.add_handler(CommandHandler("habit", habit_cmd))
    app.add_handler(CommandHandler("mystats", my_stats))
    app.add_handler(CommandHandler("pomodoro", pomodoro_cmd))
    app.add_handler(CommandHandler("quiethours", quiet_hours_cmd))
    app.add_handler(CommandHandler("insights", insights_cmd))
    # Inline keyboard callback for check-in buttons
    app.add_handler(CallbackQueryHandler(handle_callback))
    # Catch-all for user-defined tracker commands (must be last command handler)
    app.add_handler(MessageHandler(filters.COMMAND, handle_custom_command))
    # Document handler for /import (JSON file upload)
    app.add_handler(MessageHandler(filters.Document.ALL, handle_import))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    restore_all_jobs(app)

    logger.info("Bot started.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
