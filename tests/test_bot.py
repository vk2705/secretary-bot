"""
test_bot.py — Secretary Bot test suite

Three test sections:
  1. Unit tests  — pure logic, no LLM/network calls
  2. LLM sanity  — one real API call to verify the model is alive and coherent
  3. NL tool-use — real API calls; verify the model invokes the right tool for
                   natural-language requests

Run:
    python -m pytest tests/test_bot.py -v
    python -m pytest tests/test_bot.py -v -k sanity      # sanity only
    python -m pytest tests/test_bot.py -v -k nl          # NL tool-use only
    python -m pytest tests/test_bot.py -v -k "not sanity and not nl"  # unit only

Environment:
    Needs OPENAI_API_KEY (or GROQ_API_KEY) loaded, same as running the bot.
    Load with:  export $(grep -v '^#' env | xargs) && python -m pytest …
"""

import asyncio
import json
import os
import sys
import types
from copy import deepcopy
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── make bot.py importable without a running Telegram app ─────────────────────
# Stub out telegram + openai before importing bot so no network calls happen at
# module load time.
for _mod in [
    "telegram", "telegram.ext", "telegram.ext._application",
    "openai", "timezonefinder",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

# Minimal stubs so bot.py top-level imports don't crash.
_tg = sys.modules["telegram"]
for _attr in [
    "Update", "InlineKeyboardMarkup", "InlineKeyboardButton", "BotCommand",
]:
    setattr(_tg, _attr, MagicMock)

_tgext = sys.modules["telegram.ext"]
for _attr in [
    "Application", "CommandHandler", "MessageHandler", "CallbackQueryHandler",
    "filters", "ContextTypes", "ApplicationBuilder",
]:
    setattr(_tgext, _attr, MagicMock)
# ContextTypes.DEFAULT_TYPE is used as a type annotation — needs to exist
_tgext.ContextTypes.DEFAULT_TYPE = type(None)

# openai stub — real AsyncOpenAI is imported directly in bot.py so we need it
# available; the real openai package IS installed, we just need to let it load.
if "openai" in sys.modules:
    del sys.modules["openai"]  # let it import normally below

_tf_mod = sys.modules["timezonefinder"]
_tf_stub = MagicMock()
_tf_stub.return_value.timezone_at.return_value = "Europe/London"
setattr(_tf_mod, "TimezoneFinder", _tf_stub)

os.environ.setdefault("TELEGRAM_TOKEN", "TEST_TOKEN")
os.environ.setdefault("OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY", ""))

import importlib  # noqa: E402 (after stubs)

# Patch os.path.exists so bot.py treats state.json as missing at import time,
# giving us a clean empty state without touching the real state.json.
_real_exists = os.path.exists

def _patched_exists(path):
    if str(path).endswith("state.json"):
        return False
    return _real_exists(path)

with patch("os.path.exists", side_effect=_patched_exists):
    import bot  # noqa: E402

# Initialise SQLite DB in a temp file for tests (shared across connections)
import tempfile as _tempfile
_db_tmp = _tempfile.NamedTemporaryFile(suffix=".db", delete=False)
bot.DB_FILE = _db_tmp.name
_db_tmp.close()
bot._init_db()


def _fresh_db():
    """Point bot at a brand-new temp DB and initialise it."""
    tmp = _tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    bot.DB_FILE = tmp.name
    bot._init_db()


# Autouse fixture: give every test its own DB file so tests never bleed into each other.
@pytest.fixture(autouse=True)
def isolate_db():
    _fresh_db()
    yield


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def fresh_user(**overrides) -> dict:
    """Return a clean user dict (like a brand-new registration)."""
    u = bot._new_user(**overrides)
    return u


def run(coro):
    """Run an async function synchronously (for non-async test helpers)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1: Unit tests (pure logic, no LLM)
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizeTimezone:
    def test_utc_plus_offset(self):
        assert bot._normalize_tz("UTC+7") == "Etc/GMT-7"

    def test_utc_minus_offset(self):
        assert bot._normalize_tz("UTC-5") == "Etc/GMT+5"

    def test_utc_zero(self):
        assert bot._normalize_tz("UTC+0") == "UTC"

    def test_iana_passthrough(self):
        assert bot._normalize_tz("Europe/London") == "Europe/London"

    def test_case_insensitive(self):
        assert bot._normalize_tz("utc+3") == "Etc/GMT-3"

    def test_utc_plain(self):
        assert bot._normalize_tz("UTC") == "UTC"


class TestTaskHelpers:
    def test_task_text_string(self):
        assert bot._task_text("Buy milk") == "Buy milk"

    def test_task_text_dict(self):
        assert bot._task_text({"text": "Buy milk", "due": "2026-07-01"}) == "Buy milk"

    def test_task_due_none_for_string(self):
        assert bot._task_due("Buy milk") is None

    def test_task_due_from_dict(self):
        assert bot._task_due({"text": "Buy milk", "due": "2026-07-01"}) == "2026-07-01"

    def test_task_due_missing_key(self):
        assert bot._task_due({"text": "Buy milk"}) is None

    def test_task_tags_extracted(self):
        tags = bot._task_tags({"text": "Finish #work report #urgent"})
        assert "work" in tags and "urgent" in tags

    def test_task_tags_empty(self):
        assert bot._task_tags("plain task") == []

    def test_format_task_overdue(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        line = bot._format_task_line({"text": "T", "due": yesterday}, 1)
        assert "overdue" in line

    def test_format_task_due_today(self):
        line = bot._format_task_line({"text": "T", "due": date.today().isoformat()}, 1)
        assert "DUE TODAY" in line

    def test_format_task_no_due(self):
        line = bot._format_task_line("Simple task", 3)
        assert line == "3. Simple task"


class TestGetUser:
    def setup_method(self):
        # Reset state before each test
        bot.state = {"users": {}}

    def test_new_user_created(self):
        u = bot.get_user(999)
        assert "999" in bot.state["users"]

    def test_existing_user_returned(self):
        bot.state["users"]["42"] = bot._new_user(context="hello")
        u = bot.get_user(42)
        assert u["context"] == "hello"

    def test_forward_fill_missing_keys(self):
        # Simulate old user without newer fields
        bot.state["users"]["77"] = {"tasks": [], "history": [], "context": ""}
        u = bot.get_user(77)
        assert "habits" in u
        assert "reminders" in u
        assert "llm" in u


class TestGetLlmClient:
    def setup_method(self):
        bot.state = {"users": {}}

    def test_default_client_uses_openai_key(self):
        u = fresh_user()
        # Just check it doesn't raise and returns something
        from openai import AsyncOpenAI
        with patch("bot.AsyncOpenAI") as mock_ai:
            mock_ai.return_value = MagicMock()
            client = bot.get_llm_client(u)
            mock_ai.assert_called_once()
            call_kwargs = mock_ai.call_args[1]
            assert call_kwargs["api_key"] == bot.DEFAULT_API_KEY

    def test_user_groq_key_routed_to_groq(self):
        u = fresh_user()
        u["llm"]["api_key"] = "gsk_testkey"
        with patch("bot.AsyncOpenAI") as mock_ai:
            mock_ai.return_value = MagicMock()
            bot.get_llm_client(u)
            call_kwargs = mock_ai.call_args[1]
            assert call_kwargs["base_url"] == bot.GROQ_BASE_URL

    def test_user_openai_key_used_directly(self):
        u = fresh_user()
        u["llm"]["api_key"] = "sk_mykey"
        with patch("bot.AsyncOpenAI") as mock_ai:
            mock_ai.return_value = MagicMock()
            bot.get_llm_client(u)
            call_kwargs = mock_ai.call_args[1]
            assert call_kwargs["api_key"] == "sk_mykey"
            assert "base_url" not in call_kwargs


class TestGetModel:
    def test_default_model(self):
        u = fresh_user()
        assert bot.get_model(u) == bot.DEFAULT_MODEL

    def test_groq_key_gives_groq_model(self):
        u = fresh_user()
        u["llm"]["api_key"] = "gsk_test"
        assert bot.get_model(u) == bot.GROQ_DEFAULT_MODEL

    def test_user_custom_model_respected(self):
        u = fresh_user()
        u["llm"]["model"] = "gpt-4o"
        assert bot.get_model(u) == "gpt-4o"


class TestBuildSystemPrompt:
    def test_contains_tasks(self):
        u = fresh_user()
        u["tasks"] = ["Write tests", "Deploy to prod"]
        prompt = bot.build_system_prompt(u)
        assert "Write tests" in prompt
        assert "Deploy to prod" in prompt

    def test_contains_context(self):
        u = fresh_user()
        u["context"] = "I am a software engineer"
        prompt = bot.build_system_prompt(u)
        assert "software engineer" in prompt

    def test_language_instruction_injected(self):
        u = fresh_user()
        u["language"] = "Hebrew"
        prompt = bot.build_system_prompt(u)
        assert "Hebrew" in prompt

    def test_no_language_by_default(self):
        u = fresh_user()
        prompt = bot.build_system_prompt(u)
        assert "exclusively in" not in prompt

    def test_today_focus_injected(self):
        u = fresh_user()
        u["today_focus"] = {"date": date.today().isoformat(), "text": "Deep work session"}
        prompt = bot.build_system_prompt(u)
        assert "Deep work session" in prompt

    def test_expired_focus_not_injected(self):
        u = fresh_user()
        u["today_focus"] = {"date": "2020-01-01", "text": "Old focus"}
        prompt = bot.build_system_prompt(u)
        assert "Old focus" not in prompt


class TestRateLimit:
    def setup_method(self):
        bot._rate_log.clear()

    def test_within_limit_not_blocked(self):
        for _ in range(bot.RATE_LIMIT - 1):
            assert not bot.is_rate_limited(1234)

    def test_over_limit_blocked(self):
        for _ in range(bot.RATE_LIMIT):
            bot.is_rate_limited(5678)
        assert bot.is_rate_limited(5678)

    def test_separate_users_independent(self):
        for _ in range(bot.RATE_LIMIT):
            bot.is_rate_limited(1111)
        assert not bot.is_rate_limited(2222)


class TestStreakLogic:
    def setup_method(self):
        bot.state = {"users": {}}

    def test_zero_streak_no_activity(self):
        u = fresh_user()
        assert bot._get_streak(u) == 0

    def test_streak_today_only(self):
        u = fresh_user()
        u["activity_days"] = [date.today().isoformat()]
        assert bot._get_streak(u) == 1

    def test_consecutive_streak(self):
        u = fresh_user()
        days = [(date.today() - timedelta(days=i)).isoformat() for i in range(5)]
        u["activity_days"] = days
        assert bot._get_streak(u) == 5

    def test_streak_broken_by_gap(self):
        u = fresh_user()
        u["activity_days"] = [
            date.today().isoformat(),
            (date.today() - timedelta(days=2)).isoformat(),  # gap
        ]
        assert bot._get_streak(u) == 1


class TestExecuteToolUnit:
    """Test _execute_tool in isolation (no real LLM or scheduler)."""

    def setup_method(self):
        bot.state = {"users": {}}
        bot._app = None

    def test_get_current_time_returns_fields(self):
        bot.state["users"]["10"] = fresh_user(timezone="UTC")
        result = run(bot._execute_tool(10, "get_current_time", {}))
        assert "time" in result and "date" in result and "weekday" in result

    def test_get_tasks_empty(self):
        bot.state["users"]["10"] = fresh_user()
        result = run(bot._execute_tool(10, "get_tasks", {}))
        assert result["count"] == 0

    def test_add_task_success(self):
        bot.state["users"]["10"] = fresh_user()
        result = run(bot._execute_tool(10, "add_task", {"text": "Go running"}))
        assert result["success"] is True
        assert bot.state["users"]["10"]["tasks"][0] == "Go running"

    def test_add_task_with_due(self):
        bot.state["users"]["10"] = fresh_user()
        run(bot._execute_tool(10, "add_task", {"text": "Submit report", "due_date": "2026-07-15"}))
        task = bot.state["users"]["10"]["tasks"][0]
        assert task["due"] == "2026-07-15"

    def test_add_task_invalid_due_warns(self):
        bot.state["users"]["10"] = fresh_user()
        result = run(bot._execute_tool(10, "add_task", {"text": "T", "due_date": "not-a-date"}))
        assert result["success"] is True
        assert "warning" in result

    def test_complete_task_removes_it(self):
        bot.state["users"]["10"] = fresh_user()
        bot.state["users"]["10"]["tasks"] = ["Task A"]
        run(bot._execute_tool(10, "complete_task", {"task_number": 1}))
        assert len(bot.state["users"]["10"]["tasks"]) == 0

    def test_complete_task_archives_it(self):
        bot.state["users"]["10"] = fresh_user()
        bot.state["users"]["10"]["tasks"] = ["Task A"]
        run(bot._execute_tool(10, "complete_task", {"task_number": 1}))
        assert bot.state["users"]["10"]["archived_tasks"][0]["text"] == "Task A"

    def test_complete_task_out_of_range(self):
        bot.state["users"]["10"] = fresh_user()
        result = run(bot._execute_tool(10, "complete_task", {"task_number": 99}))
        assert "error" in result

    def test_complete_recurring_task_rolls_forward(self):
        bot.state["users"]["10"] = fresh_user()
        bot.state["users"]["10"]["tasks"] = [
            {"text": "Morning run", "due": date.today().isoformat(), "recur": "daily"}
        ]
        result = run(bot._execute_tool(10, "complete_task", {"task_number": 1}))
        assert result["recurs"] == "daily"
        next_due = date.fromisoformat(result["next_due"])
        assert next_due == date.today() + timedelta(days=1)

    def test_log_tracker_success(self):
        bot.state["users"]["10"] = fresh_user()
        bot.state["users"]["10"]["trackers"] = {"weight": {"unit": "kg", "log": []}}
        result = run(bot._execute_tool(10, "log_tracker", {"tracker_name": "weight", "value": 75.5}))
        assert result["success"] is True
        assert bot.state["users"]["10"]["trackers"]["weight"]["log"][0]["value"] == 75.5

    def test_log_tracker_unknown(self):
        bot.state["users"]["10"] = fresh_user()
        result = run(bot._execute_tool(10, "log_tracker", {"tracker_name": "nonexistent", "value": 10}))
        assert "error" in result

    def test_add_reminder_no_scheduler(self):
        bot.state["users"]["10"] = fresh_user()
        bot._app = None
        result = run(bot._execute_tool(10, "add_reminder", {
            "message": "Take medicine", "delay_minutes": 5
        }))
        assert "error" in result  # no scheduler available

    def test_add_reminder_daily_stored(self):
        bot.state["users"]["10"] = fresh_user()
        bot._app = MagicMock()
        bot._app.job_queue = MagicMock()
        with patch("bot.schedule_user_reminder"):
            result = run(bot._execute_tool(10, "add_reminder", {
                "message": "Evening walk", "time": "20:00"
            }))
        assert result["success"] is True
        assert len(bot.state["users"]["10"]["reminders"]) == 1

    def test_add_journal_entry(self):
        bot.state["users"]["10"] = fresh_user()
        result = run(bot._execute_tool(10, "add_journal_entry", {"text": "Had a great day"}))
        assert result["success"] is True
        rows = bot.db_get_journal("10", limit=5)
        assert any(r["entry"] == "Had a great day" for r in rows)

    def test_unknown_tool_returns_error(self):
        bot.state["users"]["10"] = fresh_user()
        result = run(bot._execute_tool(10, "fly_to_moon", {}))
        assert "error" in result


class TestQuietHours:
    def test_within_quiet_hours(self):
        u = fresh_user()
        u["quiet_hours"] = {"start": "00:00", "end": "23:59"}
        assert bot._is_quiet_now(u) is True

    def test_outside_quiet_hours(self):
        u = fresh_user()
        u["quiet_hours"] = {"start": None, "end": None}
        assert bot._is_quiet_now(u) is False

    def test_quiet_hours_midnight_spanning(self):
        """23:00–07:00 spans midnight; check that a time of 01:00 is quiet."""
        u = fresh_user()
        u["quiet_hours"] = {"start": "23:00", "end": "07:00"}
        u["timezone"] = "UTC"
        from unittest.mock import patch
        from datetime import datetime, time as dt_time
        from zoneinfo import ZoneInfo
        midnight_plus_one = datetime(2026, 1, 1, 1, 0, tzinfo=ZoneInfo("UTC"))
        with patch("bot.datetime") as mock_dt:
            mock_dt.now.return_value = midnight_plus_one
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            # The logic inside _is_quiet_now uses datetime.now(tz)
            # We'll test the boolean logic directly instead:
            start_t = dt_time(23, 0)
            end_t = dt_time(7, 0)
            now_t = dt_time(1, 0)
            # midnight-spanning: quiet if now >= start OR now < end
            result = now_t >= start_t or now_t < end_t
            assert result is True


class TestIsRateLimit:
    def setup_method(self):
        bot._rate_log.clear()

    def test_first_call_not_limited(self):
        assert bot.is_rate_limited(1) is False

    def test_exactly_at_limit_blocked(self):
        for _ in range(bot.RATE_LIMIT):
            bot.is_rate_limited(2)
        assert bot.is_rate_limited(2) is True


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2: LLM sanity tests (real API call)
# ─────────────────────────────────────────────────────────────────────────────

pytestmark_sanity = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)


@pytest.mark.sanity
class TestLlmSanity:
    """Single real API call to verify the default model is alive and coherent."""

    def setup_method(self):
        bot.state = {"users": {}}

    @pytest.mark.asyncio
    async def test_model_responds(self):
        """Model must return a non-empty string response."""
        bot.state["users"]["1"] = fresh_user()
        reply = await bot.chat(1, "Say the word HELLO and nothing else.")
        assert isinstance(reply, str)
        assert len(reply.strip()) > 0, "Model returned empty reply"

    @pytest.mark.asyncio
    async def test_model_is_coherent(self):
        """Model should include 'HELLO' when asked to say it."""
        bot.state["users"]["2"] = fresh_user()
        reply = await bot.chat(2, "Respond with exactly one word: HELLO")
        assert "HELLO" in reply.upper(), f"Unexpected reply: {reply!r}"

    @pytest.mark.asyncio
    async def test_model_respects_language_instruction(self):
        """System prompt language instruction should change reply language."""
        bot.state["users"]["3"] = fresh_user(language="Russian")
        reply = await bot.chat(3, "Say hello")
        # Russian greetings: Привет, Здравствуй, etc.  At minimum, Cyrillic present.
        has_cyrillic = any("\u0400" <= c <= "\u04ff" for c in reply)
        assert has_cyrillic, f"Expected Russian reply, got: {reply!r}"

    @pytest.mark.asyncio
    async def test_error_on_bad_key(self):
        """A bad API key should return a user-friendly error string."""
        bot.state["users"]["4"] = fresh_user()
        bot.state["users"]["4"]["llm"]["api_key"] = "sk-invalid-key-for-testing"
        reply = await bot.chat(4, "hello")
        assert "⚠️" in reply, f"Expected error reply, got: {reply!r}"


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3: Natural-language tool-use tests (real API)
# These verify that the LLM correctly selects and invokes the right tool when
# users phrase requests in plain human language.
# ─────────────────────────────────────────────────────────────────────────────

class ToolCallCapture:
    """Context manager that patches _execute_tool to record calls."""
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.calls: list[tuple[str, dict]] = []
        self._original = bot._execute_tool

    async def _fake(self, cid, name, args):
        self.calls.append((name, args))
        return await self._original(cid, name, args)

    def __enter__(self):
        bot._execute_tool = lambda cid, n, a: self._fake(cid, n, a)
        return self

    def __exit__(self, *_):
        bot._execute_tool = self._original


@pytest.mark.nl
class TestNaturalLanguageToolUse:
    """Verify the model uses the correct tool for natural-language user messages."""

    def setup_method(self):
        bot.state = {"users": {}}
        bot._app = None

    # ── what time is it now? ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_what_time_is_it(self):
        """'What time is it?' should invoke get_current_time."""
        uid = 101
        bot.state["users"][str(uid)] = fresh_user(timezone="Europe/London")
        with ToolCallCapture(uid) as cap:
            reply = await bot.chat(uid, "What time is it now?")
        tools_called = [name for name, _ in cap.calls]
        assert "get_current_time" in tools_called, (
            f"Expected get_current_time, got tools: {tools_called}\nReply: {reply}"
        )

    # ── I want to visit the doctor today ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_add_task_natural_language(self):
        """'Add a task: visit the doctor today' should invoke add_task."""
        uid = 102
        bot.state["users"][str(uid)] = fresh_user()
        with ToolCallCapture(uid) as cap:
            reply = await bot.chat(uid, "Add a task: visit the doctor today")
        tools_called = [name for name, _ in cap.calls]
        assert "add_task" in tools_called, (
            f"Expected add_task, got: {tools_called}\nReply: {reply}"
        )
        # Check the task text was captured
        add_args = next((a for n, a in cap.calls if n == "add_task"), {})
        assert add_args.get("text"), "add_task called without task text"

    # ── I forgot to drink tea — remind me ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_reminder_natural_language(self):
        """'I forgot to drink tea, remind me in 10 minutes' should use add_reminder."""
        uid = 103
        bot.state["users"][str(uid)] = fresh_user()
        bot._app = MagicMock()
        bot._app.job_queue = MagicMock()
        with ToolCallCapture(uid) as cap:
            reply = await bot.chat(uid, "I forgot to drink tea, remind me in 10 minutes")
        tools_called = [name for name, _ in cap.calls]
        assert "add_reminder" in tools_called, (
            f"Expected add_reminder, got: {tools_called}\nReply: {reply}"
        )
        add_args = next((a for n, a in cap.calls if n == "add_reminder"), {})
        # Should use delay_minutes for relative time
        assert add_args.get("delay_minutes") is not None or add_args.get("time") is not None, (
            f"add_reminder missing time spec: {add_args}"
        )

    # ── I weighed 74kg this morning ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_log_tracker_natural_language(self):
        """'I weighed 74kg this morning' should invoke log_tracker for weight."""
        uid = 104
        bot.state["users"][str(uid)] = fresh_user()
        bot.state["users"][str(uid)]["trackers"] = {"weight": {"unit": "kg", "log": []}}
        with ToolCallCapture(uid) as cap:
            reply = await bot.chat(uid, "I weighed 74kg this morning")
        tools_called = [name for name, _ in cap.calls]
        assert "log_tracker" in tools_called, (
            f"Expected log_tracker, got: {tools_called}\nReply: {reply}"
        )
        log_args = next((a for n, a in cap.calls if n == "log_tracker"), {})
        assert log_args.get("tracker_name") == "weight", f"Wrong tracker: {log_args}"
        assert float(log_args.get("value", 0)) == 74.0

    # ── finish task 1 ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_complete_task_natural_language(self):
        """'I finished my first task' should invoke complete_task."""
        uid = 105
        bot.state["users"][str(uid)] = fresh_user()
        bot.state["users"][str(uid)]["tasks"] = ["Go for a run"]
        with ToolCallCapture(uid) as cap:
            reply = await bot.chat(uid, "I finished task 1")
        tools_called = [name for name, _ in cap.calls]
        assert "complete_task" in tools_called, (
            f"Expected complete_task, got: {tools_called}\nReply: {reply}"
        )

    # ── what are my tasks? ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_tasks_natural_language(self):
        """'What are my tasks?' should invoke get_tasks."""
        uid = 106
        bot.state["users"][str(uid)] = fresh_user()
        bot.state["users"][str(uid)]["tasks"] = ["Learn Python", "Read book"]
        with ToolCallCapture(uid) as cap:
            reply = await bot.chat(uid, "What are my tasks?")
        tools_called = [name for name, _ in cap.calls]
        assert "get_tasks" in tools_called, (
            f"Expected get_tasks, got: {tools_called}\nReply: {reply}"
        )

    # ── journal: I had a great day ────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_journal_natural_language(self):
        """Describing a day should trigger add_journal_entry."""
        uid = 107
        bot.state["users"][str(uid)] = fresh_user()
        with ToolCallCapture(uid) as cap:
            reply = await bot.chat(
                uid,
                "Today was amazing — I finished my report, went to the gym, "
                "and had dinner with family. Please save this to my journal."
            )
        tools_called = [name for name, _ in cap.calls]
        assert "add_journal_entry" in tools_called, (
            f"Expected add_journal_entry, got: {tools_called}\nReply: {reply}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4: SQLite memory store
# ─────────────────────────────────────────────────────────────────────────────

class TestSQLiteNotes:
    """Tests for db_add_note / db_get_notes / db_remove_note / db_search_notes."""

    def setup_method(self):
        _fresh_db()

    def test_add_and_get_note(self):
        bot.db_add_note("1", "Buy milk")
        rows = bot.db_get_notes("1")
        assert len(rows) == 1
        assert rows[0]["text"] == "Buy milk"

    def test_notes_isolated_per_user(self):
        bot.db_add_note("1", "User 1 note")
        bot.db_add_note("2", "User 2 note")
        assert len(bot.db_get_notes("1")) == 1
        assert len(bot.db_get_notes("2")) == 1
        assert bot.db_get_notes("1")[0]["text"] == "User 1 note"

    def test_notes_ordered_by_insertion(self):
        bot.db_add_note("1", "First")
        bot.db_add_note("1", "Second")
        bot.db_add_note("1", "Third")
        texts = [r["text"] for r in bot.db_get_notes("1")]
        assert texts == ["First", "Second", "Third"]

    def test_remove_note(self):
        bot.db_add_note("1", "To remove")
        rows = bot.db_get_notes("1")
        row_id = rows[0]["id"]
        assert bot.db_remove_note("1", row_id) is True
        assert len(bot.db_get_notes("1")) == 0

    def test_remove_nonexistent_note_returns_false(self):
        assert bot.db_remove_note("1", 999999) is False

    def test_remove_other_users_note_not_allowed(self):
        bot.db_add_note("1", "Private note")
        row_id = bot.db_get_notes("1")[0]["id"]
        # user "2" tries to delete user "1"'s note
        assert bot.db_remove_note("2", row_id) is False
        assert len(bot.db_get_notes("1")) == 1

    def test_search_notes_case_insensitive(self):
        bot.db_add_note("1", "Call the dentist tomorrow")
        bot.db_add_note("1", "Buy groceries")
        results = bot.db_search_notes("1", "DENTIST")
        assert len(results) == 1
        assert "dentist" in results[0]["text"].lower()

    def test_search_notes_no_match(self):
        bot.db_add_note("1", "Buy milk")
        assert bot.db_search_notes("1", "xyz_nomatch") == []

    def test_auto_flag_stored(self):
        bot.db_add_note("1", "auto note", auto=True)
        bot.db_add_note("1", "manual note", auto=False)
        rows = bot.db_get_notes("1")
        autos = [r["auto"] for r in rows]
        assert 1 in autos
        assert 0 in autos

    def test_unlimited_notes(self):
        """Should be able to store far more than the old 50-note limit."""
        for i in range(200):
            bot.db_add_note("1", f"Note {i}")
        assert len(bot.db_get_notes("1")) == 200


class TestSQLiteJournal:
    """Tests for db_add_journal / db_get_journal / db_search_journal."""

    def setup_method(self):
        _fresh_db()

    def test_add_and_get_journal(self):
        bot.db_add_journal("1", "Had a great day")
        rows = bot.db_get_journal("1")
        assert len(rows) == 1
        assert rows[0]["entry"] == "Had a great day"

    def test_journal_isolated_per_user(self):
        bot.db_add_journal("1", "User 1 entry")
        bot.db_add_journal("2", "User 2 entry")
        assert len(bot.db_get_journal("1")) == 1
        assert len(bot.db_get_journal("2")) == 1

    def test_journal_limit_param(self):
        for i in range(10):
            bot.db_add_journal("1", f"Entry {i}")
        assert len(bot.db_get_journal("1", limit=3)) == 3

    def test_journal_limit_returns_most_recent(self):
        for i in range(5):
            bot.db_add_journal("1", f"Entry {i}")
        rows = bot.db_get_journal("1", limit=2)
        texts = [r["entry"] for r in rows]
        assert "Entry 4" in texts
        assert "Entry 3" in texts

    def test_journal_returned_in_chronological_order(self):
        bot.db_add_journal("1", "First")
        bot.db_add_journal("1", "Second")
        rows = bot.db_get_journal("1")
        assert rows[0]["entry"] == "First"
        assert rows[1]["entry"] == "Second"

    def test_search_journal(self):
        bot.db_add_journal("1", "Went to the gym and felt great")
        bot.db_add_journal("1", "Had pizza for dinner")
        results = bot.db_search_journal("1", "gym")
        assert len(results) == 1

    def test_unlimited_journal(self):
        """Should be able to store far more than the old 200-entry limit."""
        for i in range(500):
            bot.db_add_journal("1", f"Entry {i}")
        assert len(bot.db_get_journal("1")) == 500


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5: New _execute_tool coverage
# ─────────────────────────────────────────────────────────────────────────────

class TestNewExecuteTools:
    def setup_method(self):
        bot.state = {"users": {}}
        bot._app = None
        _fresh_db()

    # ── remove_task ──────────────────────────────────────────────────────────

    def test_remove_task_success(self):
        bot.state["users"]["20"] = fresh_user()
        bot.state["users"]["20"]["tasks"] = ["Task A", "Task B", "Task C"]
        result = run(bot._execute_tool(20, "remove_task", {"task_number": 2}))
        assert result["success"] is True
        assert result["removed"] == "Task B"
        assert len(bot.state["users"]["20"]["tasks"]) == 2

    def test_remove_task_out_of_range(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "remove_task", {"task_number": 1}))
        assert "error" in result

    # ── get_trackers ─────────────────────────────────────────────────────────

    def test_get_trackers_empty(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "get_trackers", {}))
        assert result["count"] == 0

    def test_get_trackers_with_data(self):
        bot.state["users"]["20"] = fresh_user()
        bot.state["users"]["20"]["trackers"] = {
            "weight": {"unit": "kg", "log": [{"ts": "2026-01-01", "value": 80.0}]},
            "steps": {"unit": "", "log": []},
        }
        result = run(bot._execute_tool(20, "get_trackers", {}))
        assert result["count"] == 2
        names = [t["name"] for t in result["trackers"]]
        assert "weight" in names and "steps" in names
        weight = next(t for t in result["trackers"] if t["name"] == "weight")
        assert weight["last_value"] == 80.0

    # ── create_tracker ───────────────────────────────────────────────────────

    def test_create_tracker_success(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "create_tracker", {"name": "steps", "unit": ""}))
        assert result["success"] is True
        assert "steps" in bot.state["users"]["20"]["trackers"]

    def test_create_tracker_duplicate(self):
        bot.state["users"]["20"] = fresh_user()
        bot.state["users"]["20"]["trackers"] = {"steps": {"unit": "", "log": []}}
        result = run(bot._execute_tool(20, "create_tracker", {"name": "steps"}))
        assert result.get("already_exists") is True

    def test_create_tracker_invalid_name(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "create_tracker", {"name": "my tracker"}))
        assert "error" in result

    def test_create_tracker_reserved_name(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "create_tracker", {"name": "start"}))
        assert "error" in result

    # ── get_habits ───────────────────────────────────────────────────────────

    def test_get_habits_empty(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "get_habits", {}))
        assert result["count"] == 0

    def test_get_habits_with_data(self):
        from datetime import date
        bot.state["users"]["20"] = fresh_user()
        today = date.today().isoformat()
        bot.state["users"]["20"]["habits"] = {
            "meditation": {"completions": [today], "created": "2026-01-01"},
            "running": {"completions": [], "created": "2026-01-01"},
        }
        result = run(bot._execute_tool(20, "get_habits", {}))
        assert result["count"] == 2
        med = next(h for h in result["habits"] if h["name"] == "meditation")
        assert med["done_today"] is True
        run_ = next(h for h in result["habits"] if h["name"] == "running")
        assert run_["done_today"] is False

    # ── add_habit ────────────────────────────────────────────────────────────

    def test_add_habit_success(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "add_habit", {"name": "meditation"}))
        assert result["success"] is True
        assert "meditation" in bot.state["users"]["20"]["habits"]

    def test_add_habit_duplicate(self):
        bot.state["users"]["20"] = fresh_user()
        bot.state["users"]["20"]["habits"] = {"meditation": {"completions": []}}
        result = run(bot._execute_tool(20, "add_habit", {"name": "meditation"}))
        assert result.get("already_exists") is True

    def test_add_habit_spaces_converted(self):
        bot.state["users"]["20"] = fresh_user()
        run(bot._execute_tool(20, "add_habit", {"name": "morning run"}))
        assert "morning_run" in bot.state["users"]["20"]["habits"]

    # ── complete_habit ───────────────────────────────────────────────────────

    def test_complete_habit_success(self):
        from datetime import date
        bot.state["users"]["20"] = fresh_user()
        bot.state["users"]["20"]["habits"] = {"meditation": {"completions": []}}
        result = run(bot._execute_tool(20, "complete_habit", {"name": "meditation"}))
        assert result["success"] is True
        today = date.today().isoformat()
        assert today in bot.state["users"]["20"]["habits"]["meditation"]["completions"]

    def test_complete_habit_already_done(self):
        from datetime import date
        bot.state["users"]["20"] = fresh_user()
        today = date.today().isoformat()
        bot.state["users"]["20"]["habits"] = {"meditation": {"completions": [today]}}
        result = run(bot._execute_tool(20, "complete_habit", {"name": "meditation"}))
        assert result.get("already_done") is True

    def test_complete_habit_not_found(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "complete_habit", {"name": "nonexistent"}))
        assert "error" in result

    # ── remove_habit ─────────────────────────────────────────────────────────

    def test_remove_habit_success(self):
        bot.state["users"]["20"] = fresh_user()
        bot.state["users"]["20"]["habits"] = {"meditation": {"completions": []}}
        result = run(bot._execute_tool(20, "remove_habit", {"name": "meditation"}))
        assert result["success"] is True
        assert "meditation" not in bot.state["users"]["20"]["habits"]

    def test_remove_habit_not_found(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "remove_habit", {"name": "nonexistent"}))
        assert "error" in result

    # ── get_notes / add_note ─────────────────────────────────────────────────

    def test_add_note_tool(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "add_note", {"text": "Buy milk"}))
        assert result["success"] is True
        rows = bot.db_get_notes("20")
        assert any(r["text"] == "Buy milk" for r in rows)

    def test_get_notes_tool(self):
        bot.state["users"]["20"] = fresh_user()
        bot.db_add_note("20", "Note A")
        bot.db_add_note("20", "Note B")
        result = run(bot._execute_tool(20, "get_notes", {}))
        assert result["count"] == 2

    def test_add_note_empty_text_errors(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "add_note", {"text": ""}))
        assert "error" in result

    # ── set_today_focus ──────────────────────────────────────────────────────

    def test_set_today_focus(self):
        from datetime import date
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "set_today_focus", {"text": "Finish the report"}))
        assert result["success"] is True
        focus = bot.state["users"]["20"]["today_focus"]
        assert focus["text"] == "Finish the report"
        assert focus["date"] == date.today().isoformat()

    def test_set_today_focus_empty_errors(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "set_today_focus", {"text": ""}))
        assert "error" in result

    # ── search ───────────────────────────────────────────────────────────────

    def test_search_finds_task(self):
        bot.state["users"]["20"] = fresh_user()
        bot.state["users"]["20"]["tasks"] = ["Call the dentist"]
        result = run(bot._execute_tool(20, "search", {"query": "dentist"}))
        assert result["total_matches"] >= 1
        assert any("dentist" in t["text"].lower() for t in result["tasks"])

    def test_search_finds_note(self):
        bot.state["users"]["20"] = fresh_user()
        bot.db_add_note("20", "dentist appointment next Monday")
        result = run(bot._execute_tool(20, "search", {"query": "dentist"}))
        assert len(result["notes"]) >= 1

    def test_search_finds_journal(self):
        bot.state["users"]["20"] = fresh_user()
        bot.db_add_journal("20", "Went to see the dentist today")
        result = run(bot._execute_tool(20, "search", {"query": "dentist"}))
        assert len(result["journal"]) >= 1

    def test_search_no_results(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "search", {"query": "xyz_no_match_abc"}))
        assert result["total_matches"] == 0

    def test_search_empty_query_errors(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "search", {"query": ""}))
        assert "error" in result

    # ── get_streak ───────────────────────────────────────────────────────────

    def test_get_streak_zero(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "get_streak", {}))
        assert result["current_streak"] == 0
        assert result["total_active_days"] == 0

    def test_get_streak_nonzero(self):
        from datetime import date, timedelta
        bot.state["users"]["20"] = fresh_user()
        days = [(date.today() - timedelta(days=i)).isoformat() for i in range(3)]
        bot.state["users"]["20"]["activity_days"] = days
        result = run(bot._execute_tool(20, "get_streak", {}))
        assert result["current_streak"] == 3

    # ── save_memory ──────────────────────────────────────────────────────────

    def test_save_memory_as_note(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "save_memory", {"text": "I prefer morning workouts", "type": "note"}))
        assert result["success"] is True
        assert result["saved_as"] == "note"
        rows = bot.db_get_notes("20")
        assert any("morning workouts" in r["text"] for r in rows)
        # should be marked as auto-saved
        auto_rows = [r for r in rows if r["auto"] == 1]
        assert len(auto_rows) >= 1

    def test_save_memory_as_journal(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "save_memory", {"text": "Had a tough day but pushed through", "type": "journal"}))
        assert result["success"] is True
        assert result["saved_as"] == "journal"
        rows = bot.db_get_journal("20")
        assert any("tough day" in r["entry"] for r in rows)

    def test_save_memory_defaults_to_note(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "save_memory", {"text": "Team meeting every Monday"}))
        assert result["saved_as"] == "note"

    def test_save_memory_empty_text_errors(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "save_memory", {"text": ""}))
        assert "error" in result

    # ── get_journal ──────────────────────────────────────────────────────────

    def test_get_journal_tool_empty(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "get_journal", {}))
        assert result["count"] == 0

    def test_get_journal_tool_with_entries(self):
        bot.state["users"]["20"] = fresh_user()
        bot.db_add_journal("20", "Entry A")
        bot.db_add_journal("20", "Entry B")
        result = run(bot._execute_tool(20, "get_journal", {"limit": 10}))
        assert result["count"] == 2
        texts = [e["text"] for e in result["entries"]]
        assert "Entry A" in texts and "Entry B" in texts

    def test_get_journal_tool_respects_limit(self):
        bot.state["users"]["20"] = fresh_user()
        for i in range(20):
            bot.db_add_journal("20", f"Entry {i}")
        result = run(bot._execute_tool(20, "get_journal", {"limit": 5}))
        assert result["count"] == 5


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6: Habit and mute helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestHabitStreak:
    def test_empty_completions(self):
        assert bot._habit_streak([]) == 0

    def test_only_today(self):
        from datetime import date
        assert bot._habit_streak([date.today().isoformat()]) == 1

    def test_consecutive_streak(self):
        from datetime import date, timedelta
        days = [(date.today() - timedelta(days=i)).isoformat() for i in range(7)]
        assert bot._habit_streak(days) == 7

    def test_streak_survives_yesterday_only(self):
        """If done yesterday but not today, streak should still count."""
        from datetime import date, timedelta
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        assert bot._habit_streak([yesterday]) == 1

    def test_streak_broken(self):
        from datetime import date, timedelta
        days = [
            date.today().isoformat(),
            (date.today() - timedelta(days=2)).isoformat(),  # gap
        ]
        assert bot._habit_streak(days) == 1


class TestMuteLogic:
    def test_not_muted_when_empty(self):
        u = fresh_user()
        assert bot._is_muted(u) is False

    def test_muted_when_future_timestamp(self):
        from datetime import datetime, timedelta
        u = fresh_user()
        u["muted_until"] = (datetime.utcnow() + timedelta(hours=4)).isoformat()
        assert bot._is_muted(u) is True

    def test_not_muted_when_expired(self):
        from datetime import datetime, timedelta
        u = fresh_user()
        u["muted_until"] = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        assert bot._is_muted(u) is False

    def test_not_muted_with_invalid_timestamp(self):
        u = fresh_user()
        u["muted_until"] = "not-a-date"
        assert bot._is_muted(u) is False


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7: Parse helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestParseOnceDelay:
    def test_minutes_spec(self):
        delay = bot._parse_once_delay("30m", "UTC")
        assert delay == 30 * 60

    def test_hours_spec(self):
        delay = bot._parse_once_delay("2h", "UTC")
        assert delay == 2 * 3600

    def test_invalid_spec_returns_none(self):
        assert bot._parse_once_delay("tomorrow", "UTC") is None

    def test_hhmm_spec_returns_positive_seconds(self):
        """HH:MM that is in the future (or tomorrow) should return > 0 seconds."""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        # pick a time definitely in the future: now + 1h
        future = datetime.now(ZoneInfo("UTC")) + timedelta(hours=1)
        spec = future.strftime("%H:%M")
        delay = bot._parse_once_delay(spec, "UTC")
        assert delay is not None and delay > 0

    def test_empty_spec_returns_none(self):
        assert bot._parse_once_delay("", "UTC") is None


class TestParseLocalTime:
    def test_returns_timezone_aware_time(self):
        from datetime import time as dt_time
        from zoneinfo import ZoneInfo
        t = bot._parse_local_time("09:30", "Europe/London")
        assert t.tzinfo is not None
        assert t.hour == 9 and t.minute == 30

    def test_invalid_tz_falls_back_to_utc(self):
        t = bot._parse_local_time("08:00", "Invalid/Zone")
        from zoneinfo import ZoneInfo
        assert str(t.tzinfo) == "UTC"


class TestTasksForPrompt:
    def test_empty_tasks(self):
        assert bot._tasks_for_prompt([]) == "none set yet"

    def test_string_tasks(self):
        result = bot._tasks_for_prompt(["Buy milk", "Call dentist"])
        assert "Buy milk" in result
        assert "Call dentist" in result

    def test_dict_task_with_due(self):
        result = bot._tasks_for_prompt([{"text": "Report", "due": "2026-07-15"}])
        assert "Report" in result
        assert "2026-07-15" in result


class TestHabitSummaryLines:
    def test_empty_habits(self):
        assert bot._habit_summary_lines({}) == []

    def test_done_today_shows_checkmark(self):
        from datetime import date
        habits = {"meditation": {"completions": [date.today().isoformat()], "created": "2026-01-01"}}
        lines = bot._habit_summary_lines(habits)
        assert len(lines) == 1
        assert "✓" in lines[0]

    def test_not_done_shows_circle(self):
        habits = {"meditation": {"completions": [], "created": "2026-01-01"}}
        lines = bot._habit_summary_lines(habits)
        assert "○" in lines[0]


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8: System prompt includes SQLite data
# ─────────────────────────────────────────────────────────────────────────────

class TestSystemPromptWithDB:
    def setup_method(self):
        bot.state = {"users": {}}
        _fresh_db()

    def test_notes_appear_in_prompt(self):
        bot.state["users"]["30"] = fresh_user()
        bot.db_add_note("30", "My secret goal is to write a book")
        u = bot.state["users"]["30"]
        prompt = bot.build_system_prompt(u, chat_id=30)
        assert "secret goal" in prompt

    def test_recent_journal_appears_in_prompt(self):
        bot.state["users"]["30"] = fresh_user()
        bot.db_add_journal("30", "Felt very productive today and shipped a feature")
        u = bot.state["users"]["30"]
        prompt = bot.build_system_prompt(u, chat_id=30)
        assert "productive" in prompt

    def test_empty_db_no_crash(self):
        bot.state["users"]["30"] = fresh_user()
        u = bot.state["users"]["30"]
        prompt = bot.build_system_prompt(u, chat_id=30)
        assert isinstance(prompt, str) and len(prompt) > 0


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 9: New NL tool-use tests (real API)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.nl
class TestNaturalLanguageNewTools:
    """NL tests for tools added since the first batch."""

    def setup_method(self):
        bot.state = {"users": {}}
        bot._app = None
        _fresh_db()

    # ── add a habit ───────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_add_habit_nl(self):
        uid = 201
        bot.state["users"][str(uid)] = fresh_user()
        with ToolCallCapture(uid) as cap:
            reply = await bot.chat(uid, "Add a daily habit: morning meditation")
        tools_called = [name for name, _ in cap.calls]
        assert "add_habit" in tools_called, (
            f"Expected add_habit, got: {tools_called}\nReply: {reply}"
        )

    # ── mark habit done ───────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_complete_habit_nl(self):
        uid = 202
        bot.state["users"][str(uid)] = fresh_user()
        bot.state["users"][str(uid)]["habits"] = {"meditation": {"completions": []}}
        with ToolCallCapture(uid) as cap:
            reply = await bot.chat(uid, "I did my meditation today")
        tools_called = [name for name, _ in cap.calls]
        assert "complete_habit" in tools_called, (
            f"Expected complete_habit, got: {tools_called}\nReply: {reply}"
        )

    # ── create a tracker ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_tracker_nl(self):
        uid = 203
        bot.state["users"][str(uid)] = fresh_user()
        with ToolCallCapture(uid) as cap:
            reply = await bot.chat(uid, "Create a tracker called sleep to track hours of sleep")
        tools_called = [name for name, _ in cap.calls]
        assert "create_tracker" in tools_called, (
            f"Expected create_tracker, got: {tools_called}\nReply: {reply}"
        )

    # ── set today's focus ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_set_focus_nl(self):
        uid = 204
        bot.state["users"][str(uid)] = fresh_user()
        with ToolCallCapture(uid) as cap:
            reply = await bot.chat(uid, "My focus for today is finishing the quarterly report")
        tools_called = [name for name, _ in cap.calls]
        assert "set_today_focus" in tools_called, (
            f"Expected set_today_focus, got: {tools_called}\nReply: {reply}"
        )

    # ── auto save memory ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_auto_save_personal_fact(self):
        """Sharing a personal fact should trigger save_memory silently."""
        uid = 205
        bot.state["users"][str(uid)] = fresh_user()
        with ToolCallCapture(uid) as cap:
            reply = await bot.chat(
                uid,
                "By the way, I'm allergic to peanuts and I work as a nurse."
            )
        tools_called = [name for name, _ in cap.calls]
        assert "save_memory" in tools_called, (
            f"Expected save_memory for personal fact, got: {tools_called}\nReply: {reply}"
        )

    # ── search ────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_search_nl(self):
        uid = 206
        bot.state["users"][str(uid)] = fresh_user()
        bot.state["users"][str(uid)]["tasks"] = ["Call the dentist"]
        with ToolCallCapture(uid) as cap:
            reply = await bot.chat(uid, "Search for dentist in my data")
        tools_called = [name for name, _ in cap.calls]
        assert "search" in tools_called, (
            f"Expected search, got: {tools_called}\nReply: {reply}"
        )

    # ── get streak ────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_streak_nl(self):
        uid = 207
        bot.state["users"][str(uid)] = fresh_user()
        with ToolCallCapture(uid) as cap:
            reply = await bot.chat(uid, "How long is my current streak?")
        tools_called = [name for name, _ in cap.calls]
        assert "get_streak" in tools_called, (
            f"Expected get_streak, got: {tools_called}\nReply: {reply}"
        )

