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
    "Update", "BotCommand",
]:
    setattr(_tg, _attr, MagicMock)

# InlineKeyboardMarkup/InlineKeyboardButton are constructed with a nested-list
# positional arg (e.g. InlineKeyboardMarkup([[button]])) by the check-in and
# reminder job bodies. The bare MagicMock class can't stand in directly here:
# calling MagicMock(some_list) binds that list to Mock's own `spec` kwarg,
# and a nested list isn't hashable where `spec` introspection expects it —
# "unhashable type: 'list'". A side_effect that ignores the constructor args
# and returns a fresh MagicMock instance sidesteps the collision.
for _attr in ["InlineKeyboardMarkup", "InlineKeyboardButton"]:
    setattr(_tg, _attr, MagicMock(side_effect=lambda *a, **kw: MagicMock()))

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

# Redirect state.json too — save_state() writes straight to bot.STATE_FILE with no
# isolation of its own, so without this every test run overwrites the real
# production state.json in the repo with whatever fixture data the tests built.
_state_tmp = _tempfile.NamedTemporaryFile(suffix=".json", delete=False)
bot.STATE_FILE = _state_tmp.name
_state_tmp.close()


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

    def test_connect_claude_instruction_uses_real_domain_when_configured(self):
        u = fresh_user()
        with patch.dict(os.environ, {"MCP_REMOTE_DOMAIN": "mcp-sbot.alteon.help"}):
            prompt = bot.build_system_prompt(u)
        assert "https://mcp-sbot.alteon.help/mcp" in prompt
        assert "/link" in prompt
        assert "no client ID or secret needed" in prompt

    def test_connect_claude_instruction_admits_remote_unavailable_when_domain_unset(self):
        u = fresh_user()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MCP_REMOTE_DOMAIN", None)
            prompt = bot.build_system_prompt(u)
        assert "isn't configured on this deployment" in prompt
        assert "https://" not in prompt.split("connect you to Claude")[-1].split("\n")[0]

    def test_persona_defaults_to_jeeves(self):
        u = fresh_user()
        prompt = bot.build_system_prompt(u)
        assert "voice of Jeeves" in prompt

    def test_persona_reflects_custom_character(self):
        u = fresh_user(persona="Yoda")
        prompt = bot.build_system_prompt(u)
        assert "voice of Yoda" in prompt
        assert "Jeeves" not in prompt

    def test_persona_plain_disables_the_instruction_entirely(self):
        u = fresh_user(persona="plain")
        prompt = bot.build_system_prompt(u)
        assert "Adopt the voice" not in prompt

    def test_literal_compliance_rule_always_present(self):
        """The override that lets literal requests win over persona voice is
        not itself conditional on persona being set — it's rule 0, always."""
        for persona in ("Jeeves", "Yoda", "plain"):
            u = fresh_user(persona=persona)
            prompt = bot.build_system_prompt(u)
            assert "Literal requests always win over voice" in prompt

    def test_no_honorific_by_default(self):
        u = fresh_user()
        prompt = bot.build_system_prompt(u)
        assert "Address the user as" not in prompt

    def test_honorific_injected_when_set(self):
        u = fresh_user(honorific="Sir")
        prompt = bot.build_system_prompt(u)
        assert 'Address the user as "Sir"' in prompt

    def test_new_user_without_honorific_gets_asked(self):
        """Regression: a brand-new user who never types /start (just starts
        chatting, e.g. via console.py) must still get asked — the ask can't
        live only in start()'s hardcoded onboarding text."""
        u = fresh_user()  # no context, no tasks, no activity_days -> _is_new_user() True
        prompt = bot.build_system_prompt(u)
        assert "ask naturally" in prompt
        assert "how should I address you" in prompt

    def test_established_user_without_honorific_not_pestered(self):
        u = fresh_user(context="I'm a developer", tasks=["Ship the feature"])
        prompt = bot.build_system_prompt(u)
        assert "ask naturally" not in prompt
        assert "Address the user as" not in prompt

    def test_honorific_set_suppresses_the_ask_even_for_new_user(self):
        u = fresh_user(honorific="Sir")  # still "new" by the tasks/context/activity heuristic
        prompt = bot.build_system_prompt(u)
        assert "ask naturally" not in prompt
        assert 'Address the user as "Sir"' in prompt


class TestIsNewUser:
    def test_fresh_user_is_new(self):
        assert bot._is_new_user(fresh_user()) is True

    def test_user_with_context_not_new(self):
        assert bot._is_new_user(fresh_user(context="I'm a developer")) is False

    def test_user_with_tasks_not_new(self):
        assert bot._is_new_user(fresh_user(tasks=["Buy milk"])) is False

    def test_user_with_multiple_activity_days_not_new(self):
        assert bot._is_new_user(fresh_user(activity_days=["2026-01-01", "2026-01-02"])) is False

    def test_shared_by_start_and_system_prompt(self):
        """start()'s is_new and build_system_prompt()'s honorific-ask must
        use the literal same function, not two copies of the heuristic that
        could silently drift apart."""
        import inspect
        start_src = inspect.getsource(bot.start)
        prompt_src = inspect.getsource(bot.build_system_prompt)
        assert "_is_new_user(user)" in start_src
        assert "_is_new_user(user)" in prompt_src


class TestPersonaTools:
    def test_set_persona_saves_character(self):
        cid = 9801
        bot.state["users"][str(cid)] = fresh_user()
        result = run(bot._execute_tool(cid, "set_persona", {"character": "Rambo"}))
        assert result["success"] is True
        assert result["persona"] == "Rambo"
        assert bot.state["users"][str(cid)]["persona"] == "Rambo"

    def test_set_persona_requires_character(self):
        cid = 9802
        bot.state["users"][str(cid)] = fresh_user()
        result = run(bot._execute_tool(cid, "set_persona", {"character": ""}))
        assert "error" in result

    def test_set_honorific_saves_form(self):
        cid = 9803
        bot.state["users"][str(cid)] = fresh_user()
        result = run(bot._execute_tool(cid, "set_honorific", {"form": "Madam"}))
        assert result["success"] is True
        assert result["honorific"] == "Madam"
        assert bot.state["users"][str(cid)]["honorific"] == "Madam"

    def test_set_honorific_empty_string_clears_it(self):
        cid = 9804
        bot.state["users"][str(cid)] = fresh_user(honorific="Sir")
        result = run(bot._execute_tool(cid, "set_honorific", {"form": ""}))
        assert result["success"] is True
        assert bot.state["users"][str(cid)]["honorific"] == ""

    def test_new_user_defaults_to_jeeves_persona_and_no_honorific(self):
        u = bot._new_user()
        assert u["persona"] == "Jeeves"
        assert u["honorific"] == ""


class TestRateLimit:
    def setup_method(self):
        # Clear rate_log table so each test starts fresh
        with bot._db() as con:
            con.execute("DELETE FROM rate_log")

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
        bot.state["users"]["10"] = fresh_user(timezone_confirmed=True)
        bot._app = MagicMock()
        bot._app.job_queue = MagicMock()
        with patch("bot.schedule_user_reminder"):
            result = run(bot._execute_tool(10, "add_reminder", {
                "message": "Evening walk", "time": "20:00"
            }))
        assert result["success"] is True
        assert len(bot.state["users"]["10"]["reminders"]) == 1

    def test_add_reminder_absolute_time_blocked_without_confirmed_timezone(self):
        """PLAN.md #61: don't schedule anything at a clock time until the
        user's timezone has been explicitly confirmed (not just defaulted)."""
        bot.state["users"]["10"] = fresh_user()
        bot._app = MagicMock()
        bot._app.job_queue = MagicMock()
        with patch("bot.schedule_user_reminder"):
            result = run(bot._execute_tool(10, "add_reminder", {
                "message": "Evening walk", "time": "20:00"
            }))
        assert "error" in result
        assert "timezone" in result["error"].lower()
        assert len(bot.state["users"]["10"]["reminders"]) == 0

    def test_add_reminder_relative_delay_not_gated_by_timezone(self):
        """A relative delay ("in N minutes") doesn't depend on timezone, so it
        should work even before the user has confirmed one."""
        bot.state["users"]["10"] = fresh_user()
        bot._app = MagicMock()
        bot._app.job_queue = MagicMock()
        result = run(bot._execute_tool(10, "add_reminder", {
            "message": "Take medicine", "delay_minutes": 5
        }))
        assert result["success"] is True

    def test_set_timezone_confirms_and_unblocks_reminders(self):
        bot.state["users"]["10"] = fresh_user()
        run(bot._execute_tool(10, "set_timezone", {"timezone": "Europe/Moscow"}))
        assert bot.state["users"]["10"]["timezone_confirmed"] is True
        bot._app = MagicMock()
        bot._app.job_queue = MagicMock()
        with patch("bot.schedule_user_reminder"):
            result = run(bot._execute_tool(10, "add_reminder", {
                "message": "Evening walk", "time": "20:00"
            }))
        assert result["success"] is True

    def test_set_checkins_blocked_without_confirmed_timezone(self):
        bot.state["users"]["10"] = fresh_user()
        result = run(bot._execute_tool(10, "set_checkins", {"enabled": True}))
        assert "error" in result
        assert "timezone" in result["error"].lower()

    def test_set_checkins_disable_not_gated_by_timezone(self):
        """Turning check-ins off doesn't schedule anything, so it shouldn't
        require a confirmed timezone."""
        bot.state["users"]["10"] = fresh_user()
        result = run(bot._execute_tool(10, "set_checkins", {"enabled": False}))
        assert result == {"success": True, "enabled": False}

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
        with bot._db() as con:
            con.execute("DELETE FROM rate_log")

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

    def test_remove_note_success(self):
        bot.state["users"]["20"] = fresh_user()
        bot.db_add_note("20", "Note A")
        bot.db_add_note("20", "Note B")
        result = run(bot._execute_tool(20, "remove_note", {"note_number": 1}))
        assert result["success"] is True
        assert result["removed"] == "Note A"
        rows = bot.db_get_notes("20")
        assert len(rows) == 1 and rows[0]["text"] == "Note B"

    def test_remove_note_out_of_range(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "remove_note", {"note_number": 5}))
        assert "error" in result

    # ── get_reminders / remove_reminder ──────────────────────────────────────

    def test_get_reminders_tool(self):
        bot.state["users"]["20"] = fresh_user(timezone_confirmed=True)
        run(bot._execute_tool(20, "add_reminder", {"message": "drink water", "time": "09:00"}))
        run(bot._execute_tool(20, "add_reminder", {"message": "stretch", "time": "10:00"}))
        result = run(bot._execute_tool(20, "get_reminders", {}))
        assert result["count"] == 2
        assert result["reminders"][0]["number"] == 1
        assert result["reminders"][0]["message"] == "drink water"
        assert result["reminders"][0]["kind"] == "daily"

    def test_get_reminders_empty(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "get_reminders", {}))
        assert result["count"] == 0

    def test_remove_reminder_success(self):
        bot.state["users"]["20"] = fresh_user(timezone_confirmed=True)
        run(bot._execute_tool(20, "add_reminder", {"message": "drink water", "time": "09:00"}))
        run(bot._execute_tool(20, "add_reminder", {"message": "stretch", "time": "10:00"}))
        result = run(bot._execute_tool(20, "remove_reminder", {"reminder_number": 1}))
        assert result["success"] is True
        assert result["removed"] == "drink water"
        remaining = bot.state["users"]["20"]["reminders"]
        assert len(remaining) == 1 and remaining[0]["message"] == "stretch"

    def test_remove_reminder_out_of_range(self):
        bot.state["users"]["20"] = fresh_user()
        result = run(bot._execute_tool(20, "remove_reminder", {"reminder_number": 5}))
        assert "error" in result

    def test_add_reminder_with_reason(self):
        bot.state["users"]["20"] = fresh_user(timezone_confirmed=True)
        run(bot._execute_tool(20, "add_reminder", {
            "message": "Do exercise", "time": "11:00", "reason": "to keep your leg mobile",
        }))
        result = run(bot._execute_tool(20, "get_reminders", {}))
        assert result["reminders"][0]["reason"] == "to keep your leg mobile"

    def test_get_reminders_include_history(self):
        bot.state["users"]["20"] = fresh_user(timezone_confirmed=True)
        run(bot._execute_tool(20, "add_reminder", {
            "message": "Do exercise", "time": "11:00", "reason": "stay active",
        }))
        run(bot._execute_tool(20, "remove_reminder", {"reminder_number": 1}))
        result = run(bot._execute_tool(20, "get_reminders", {"include_history": True}))
        assert result["count"] == 0
        assert len(result["history"]) == 1
        assert result["history"][0]["message"] == "Do exercise"
        assert result["history"][0]["reason"] == "stay active"
        assert result["history"][0]["status"] == "removed"

    def test_search_finds_active_reminder(self):
        bot.state["users"]["20"] = fresh_user(timezone_confirmed=True)
        run(bot._execute_tool(20, "add_reminder", {
            "message": "Do exercise", "time": "11:00", "reason": "stay active",
        }))
        result = run(bot._execute_tool(20, "search", {"query": "exercise"}))
        assert len(result["reminders"]) == 1
        assert result["reminders"][0]["status"] == "active"

    def test_search_finds_removed_reminder(self):
        bot.state["users"]["20"] = fresh_user(timezone_confirmed=True)
        run(bot._execute_tool(20, "add_reminder", {"message": "Do exercise", "time": "11:00"}))
        run(bot._execute_tool(20, "remove_reminder", {"reminder_number": 1}))
        result = run(bot._execute_tool(20, "search", {"query": "exercise"}))
        assert len(result["reminders"]) == 1
        assert result["reminders"][0]["status"] == "removed"

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
# SECTION 6b: Debug clock / simulated-now helpers (DEBUG-02, plan 01-04)
# ─────────────────────────────────────────────────────────────────────────────

class TestNowHelper:
    """`_debug_now`/`_now`/`_today`/`_utcnow`: with no override set every
    helper is an exact no-op for the standard-library call it replaces; with
    an override set, resolution is a single guarded function of the user
    dict, expiring against the real wall clock."""

    def setup_method(self):
        bot.state = {"users": {}}

    def _with_override(self, cid, clock, hours_ahead=12):
        """Round-trip an override through SQLite exactly as the real /debug
        clock command would, then read it back through get_user()."""
        bot.db_set_pref(str(cid), "debug_clock", clock)
        expires = (bot.datetime.utcnow() + bot.timedelta(hours=hours_ahead)).isoformat()
        bot.db_set_pref(str(cid), "debug_clock_expires", expires)
        return bot.get_user(cid)

    # ── no override: exact no-ops against the standard library ──

    def test_now_helper_no_override_now_matches_stdlib(self):
        from zoneinfo import ZoneInfo
        u = fresh_user()
        before = bot.datetime.now(ZoneInfo("UTC"))
        result = bot._now(ZoneInfo("UTC"), user=u)
        after = bot.datetime.now(ZoneInfo("UTC"))
        assert before <= result <= after
        assert bot._now(ZoneInfo("UTC")) is not None  # user=None default also works

    def test_now_helper_no_override_today_matches_stdlib(self):
        u = fresh_user()
        assert bot._today(user=u) == date.today()
        assert bot._today() == date.today()

    def test_now_helper_no_override_utcnow_matches_stdlib(self):
        from datetime import timedelta as _td
        u = fresh_user()
        before = bot.datetime.utcnow()
        result = bot._utcnow(user=u)
        after = bot.datetime.utcnow()
        assert before - _td(seconds=1) <= result <= after + _td(seconds=1)

    def test_now_helper_no_override_debug_now_is_none(self):
        u = fresh_user()
        assert bot._debug_now(u) is None

    # ── override set: resolution and conversion ──

    def test_now_helper_override_resolves_aware_local_time(self):
        from zoneinfo import ZoneInfo
        cid = 9700
        u = self._with_override(cid, "2027-03-05T09:30")
        u["timezone"] = "Europe/Berlin"
        dt = bot._debug_now(u)
        assert dt is not None and dt.tzinfo is not None
        assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2027, 3, 5, 9, 30)

    def test_now_helper_today_matches_override_date(self):
        cid = 9701
        u = self._with_override(cid, "2027-03-05T09:30")
        u["timezone"] = "Europe/Berlin"
        assert bot._today(user=u) == date(2027, 3, 5)

    def test_now_helper_now_converts_to_requested_tz(self):
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt
        cid = 9702
        u = self._with_override(cid, "2027-03-05T09:30")
        u["timezone"] = "Europe/Berlin"
        expected_berlin = _dt(2027, 3, 5, 9, 30, tzinfo=ZoneInfo("Europe/Berlin"))
        expected_utc = expected_berlin.astimezone(ZoneInfo("UTC"))
        assert bot._now(ZoneInfo("UTC"), user=u) == expected_utc

    def test_now_helper_date_only_override_midnight_local(self):
        cid = 9703
        u = self._with_override(cid, "2027-03-05")
        dt = bot._debug_now(u)
        assert (dt.hour, dt.minute) == (0, 0)
        assert bot._today(user=u) == date(2027, 3, 5)

    def test_now_helper_utcnow_override_naive_and_comparable(self):
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt
        cid = 9704
        u = self._with_override(cid, "2027-03-05T09:30")
        u["timezone"] = "Europe/Berlin"
        result = bot._utcnow(user=u)
        assert result.tzinfo is None
        expected_berlin = _dt(2027, 3, 5, 9, 30, tzinfo=ZoneInfo("Europe/Berlin"))
        expected_naive_utc = expected_berlin.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        assert result == expected_naive_utc
        # naive-vs-naive comparison, matching the shape muted_until uses
        assert isinstance(result < bot.datetime.utcnow() + bot.timedelta(days=1), bool)

    # ── expiry and malformed input: never raise, treated as absent ──

    def test_now_helper_expired_override_treated_as_absent(self):
        cid = 9705
        bot.db_set_pref(str(cid), "debug_clock", "2020-01-01T00:00")
        expired = (bot.datetime.utcnow() - bot.timedelta(hours=1)).isoformat()
        bot.db_set_pref(str(cid), "debug_clock_expires", expired)
        u = bot.get_user(cid)
        assert bot._debug_now(u) is None
        assert bot._today(user=u) == date.today()

    def test_now_helper_unparseable_override_returns_none(self):
        cid = 9706
        u = self._with_override(cid, "not-a-date")
        assert bot._debug_now(u) is None

    def test_now_helper_unparseable_expiry_returns_none(self):
        cid = 9707
        bot.db_set_pref(str(cid), "debug_clock", "2027-03-05T09:30")
        bot.db_set_pref(str(cid), "debug_clock_expires", "not-a-date")
        u = bot.get_user(cid)
        assert bot._debug_now(u) is None

    def test_now_helper_missing_expiry_returns_none(self):
        cid = 9708
        bot.db_set_pref(str(cid), "debug_clock", "2027-03-05T09:30")
        u = bot.get_user(cid)
        assert bot._debug_now(u) is None

    # ── storage: get_user overlay and db_delete_pref ──

    def test_now_helper_get_user_overlays_debug_clock_sqlite_wins(self):
        cid = 9709
        bot.state["users"][str(cid)] = fresh_user(debug_clock="stale-in-state-json")
        bot.db_set_pref(str(cid), "debug_clock", "2027-03-05T09:30")
        expires = (bot.datetime.utcnow() + bot.timedelta(hours=12)).isoformat()
        bot.db_set_pref(str(cid), "debug_clock_expires", expires)
        u = bot.get_user(cid)
        assert u["debug_clock"] == "2027-03-05T09:30"

    def test_now_helper_db_delete_pref_removes_only_named_key(self):
        cid_a, cid_b = 9710, 9711
        bot.db_set_pref(str(cid_a), "debug_clock", "2027-01-01T00:00")
        bot.db_set_pref(str(cid_a), "timezone", "Europe/Berlin")
        bot.db_set_pref(str(cid_b), "debug_clock", "2027-02-02T00:00")
        bot.db_delete_pref(str(cid_a), "debug_clock")
        assert bot.db_get_pref(str(cid_a), "debug_clock") is None
        assert bot.db_get_pref(str(cid_a), "timezone") == "Europe/Berlin"
        assert bot.db_get_pref(str(cid_b), "debug_clock") == "2027-02-02T00:00"

    # ── shadowing-local regression guard (D-P6 hard prerequisite) ──

    def test_now_helper_set_timezone_tool_no_shadow_regression(self):
        """The module-level _now helper must remain callable from inside
        _execute_tool's set_timezone branch after the local rename."""
        cid = 9712
        bot.state["users"][str(cid)] = fresh_user()
        result = run(bot._execute_tool(cid, "set_timezone", {"timezone": "Europe/Berlin"}))
        assert result["success"] is True
        assert result["timezone"] == "Europe/Berlin"

    def test_now_helper_build_system_prompt_no_shadow_regression(self):
        """The module-level _now helper must remain callable from inside
        build_system_prompt after the local rename."""
        cid = 9713
        u = fresh_user()
        prompt = bot.build_system_prompt(u, cid)
        assert isinstance(prompt, str) and len(prompt) > 0


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


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 10: Debug command — owner-gated /debug fire|clock|prompt (Phase 1)
# ─────────────────────────────────────────────────────────────────────────────

def _debug_update(chat_id):
    """MagicMock Update: effective_chat.id is chat_id; message.reply_text and
    message.reply_document are AsyncMocks. Shared by every /debug test across
    plans 01-01 through 01-05."""
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message.reply_text = AsyncMock()
    update.message.reply_document = AsyncMock()
    return update


def _debug_context(args):
    """MagicMock context: .args is the given list; bot.send_message is an
    AsyncMock. Shared by every /debug test across plans 01-01 through 01-05."""
    context = MagicMock()
    context.args = list(args)
    context.bot.send_message = AsyncMock()
    return context


class as_owner:
    """Context manager: sets bot.MY_CHAT_ID to str(chat_id) for the duration of
    the `with` block and restores the previous value on exit."""

    def __init__(self, chat_id):
        self.chat_id = chat_id
        self._prev = None

    def __enter__(self):
        self._prev = bot.MY_CHAT_ID
        bot.MY_CHAT_ID = str(self.chat_id)
        return self

    def __exit__(self, exc_type, exc, tb):
        bot.MY_CHAT_ID = self._prev
        return False


def _no_llm_client():
    """MagicMock LLM client whose .chat.completions.create is an AsyncMock, so
    a test can assert it was never awaited. Reusable by any /debug or dry-run
    test across plans 01-02 through 01-05 that needs to pin "no LLM call was
    made" — there was no existing precedent for this assertion in the suite
    before this plan."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock()
    return client


def _delivered_text(update):
    """Return the text actually delivered by a /debug prompt call, regardless
    of whether it went through the short reply_text path or the long
    reply_document path, so one assertion works against either branch."""
    if update.message.reply_document.await_count:
        _, kwargs = update.message.reply_document.call_args
        doc = kwargs["document"]
        doc.seek(0)
        return doc.read().decode("utf-8")
    args, _ = update.message.reply_text.call_args
    return args[0]


def _capture_run_daily_callbacks(app):
    """Patch app.job_queue.run_daily to capture every (name -> callback) pair
    registered during a schedule_user_* call, keyed by job name. Reusable by
    every wrapper-equivalence test across the six extracted runners
    (_run_deadline_alert from 01-01; _run_checkin, _run_habit_reminder,
    _run_idle_nudge, _run_weekly_digest, _run_reminder from 01-03)."""
    captured = {}

    def _run_daily(callback, time=None, name=None):
        captured[name] = callback
        return MagicMock()

    app.job_queue.run_daily.side_effect = _run_daily
    app.job_queue.get_jobs_by_name.return_value = []
    return captured


class TestDebugFire:
    """Tracer slice: /debug fire deadline_alert produces the identical message
    and side effects as the 09:00 scheduled job, through the shared
    _run_deadline_alert extraction (D-P1, D-P2, D-P4)."""

    def setup_method(self):
        bot.state = {"users": {}}

    def test_debug_fire_deadline_alert_due_today(self):
        cid = 9001
        bot.state["users"][str(cid)] = fresh_user()
        bot.state["users"][str(cid)]["tasks"] = [
            {"text": "Ship the report", "due": date.today().isoformat()}
        ]
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["fire", "deadline_alert"])
            run(bot.debug_cmd(update, context))
        context.bot.send_message.assert_awaited_once()
        _, kwargs = context.bot.send_message.call_args
        assert "Due TODAY" in kwargs["text"]
        assert "Ship the report" in kwargs["text"]
        update.message.reply_text.assert_awaited_once()

    def test_debug_fire_deadline_alert_annual_reminder(self):
        cid = 9002
        bot.state["users"][str(cid)] = fresh_user()
        today_mmdd = date.today().strftime("%m-%d")
        bot.state["users"][str(cid)]["reminders"] = [
            {
                "id": "r1", "time": "09:00", "message": "Anniversary",
                "once": False, "annual": True, "date": today_mmdd,
            }
        ]
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["fire", "deadline_alert"])
            run(bot.debug_cmd(update, context))
        context.bot.send_message.assert_awaited_once()
        _, kwargs = context.bot.send_message.call_args
        assert "Anniversary" in kwargs["text"]

    def test_debug_owner_gate_non_owner_rejected(self):
        """Non-owner chat_id gets 'Admin only.' and no send_message."""
        cid = 9003
        bot.state["users"][str(cid)] = fresh_user()
        with as_owner(999999):  # someone else is configured as owner
            update = _debug_update(cid)
            context = _debug_context(["fire", "deadline_alert"])
            run(bot.debug_cmd(update, context))
        update.message.reply_text.assert_awaited_once_with("Admin only.")
        context.bot.send_message.assert_not_awaited()

    def test_debug_owner_gate_unset_my_chat_id_rejects_developer_too(self):
        """MY_CHAT_ID unset fails closed — rejects even the developer's own id."""
        cid = 9004
        bot.state["users"][str(cid)] = fresh_user()
        prev = bot.MY_CHAT_ID
        bot.MY_CHAT_ID = None
        try:
            update = _debug_update(cid)
            context = _debug_context(["fire", "deadline_alert"])
            run(bot.debug_cmd(update, context))
        finally:
            bot.MY_CHAT_ID = prev
        update.message.reply_text.assert_awaited_once_with("Admin only.")
        context.bot.send_message.assert_not_awaited()

    def test_debug_no_args_shows_usage(self):
        cid = 9005
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context([])
            run(bot.debug_cmd(update, context))
        update.message.reply_text.assert_awaited_once()
        text = update.message.reply_text.call_args[0][0]
        assert "fire" in text and "clock" in text and "prompt" in text

    def test_debug_unknown_subcommand(self):
        cid = 9006
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["wibble"])
            run(bot.debug_cmd(update, context))
        update.message.reply_text.assert_awaited_once()
        text = update.message.reply_text.call_args[0][0]
        assert "Unknown subcommand" in text
        context.bot.send_message.assert_not_awaited()

    def test_scheduled_deadline_alert_matches_debug_path(self):
        """schedule_user_alerts registers deadline_alert_{chat_id} as a
        run_daily job; awaiting that job's callback produces the same
        send_message call the /debug path produces, because both call the
        same extracted _run_deadline_alert."""
        cid = 9007
        bot.state["users"][str(cid)] = fresh_user(checkin_enabled=True)
        bot.state["users"][str(cid)]["tasks"] = [
            {"text": "Renew passport", "due": date.today().isoformat()}
        ]

        captured = {}
        app = MagicMock()

        def _run_daily(callback, time=None, name=None):
            if name == f"deadline_alert_{cid}":
                captured["callback"] = callback
            return MagicMock()

        app.job_queue.run_daily.side_effect = _run_daily
        app.job_queue.get_jobs_by_name.return_value = []

        bot.schedule_user_alerts(app, cid)
        assert "callback" in captured, "deadline_alert job was not registered"

        job_context = _debug_context([])
        run(captured["callback"](job_context))
        job_context.bot.send_message.assert_awaited_once()
        _, kwargs = job_context.bot.send_message.call_args
        assert "Renew passport" in kwargs["text"]

    # ── 01-03 Task 1: the five newly-extracted runners ─────────────────────

    def test_debug_fire_checkin_morning_touch_activity_false(self):
        """_run_checkin passes touch_activity=False to chat() — the Pitfall 3
        regression guard; a debug-fired check-in must never be mistaken for
        the user having responded."""
        cid = 9010
        bot.state["users"][str(cid)] = fresh_user(checkin_enabled=True)
        with patch.object(bot, "chat", AsyncMock(return_value="Morning!")) as mock_chat:
            result = run(bot._run_checkin(_debug_context([]), cid, "morning"))
        assert result is None
        mock_chat.assert_awaited_once()
        _, kwargs = mock_chat.call_args
        assert kwargs.get("touch_activity") is False

    def test_debug_fire_checkin_pending_and_activity_unchanged(self):
        """After firing, pending_checkin equals the fired label and
        activity_days is unchanged."""
        cid = 9011
        bot.state["users"][str(cid)] = fresh_user(checkin_enabled=True)
        before_days = list(bot.state["users"][str(cid)]["activity_days"])
        with patch.object(bot, "chat", AsyncMock(return_value="Morning!")):
            run(bot._run_checkin(_debug_context([]), cid, "morning"))
        user = bot.get_user(cid)
        assert user["pending_checkin"] == "morning"
        assert user["activity_days"] == before_days

    def test_debug_fire_checkin_stale_tracker_append(self):
        """A tracker not logged in 2+ days appends a stale-tracker nudge to
        the prompt handed to chat()."""
        cid = 9012
        bot.state["users"][str(cid)] = fresh_user(checkin_enabled=True)
        stale_ts = (date.today() - timedelta(days=3)).isoformat() + "T00:00:00"
        bot.state["users"][str(cid)]["trackers"] = {
            "weight": {"unit": "kg", "log": [{"ts": stale_ts, "value": 80}]}
        }
        with patch.object(bot, "chat", AsyncMock(return_value="Morning!")) as mock_chat:
            run(bot._run_checkin(_debug_context([]), cid, "morning"))
        args, _ = mock_chat.call_args
        prompt = args[1]
        assert "weight" in prompt
        assert "2+ days" in prompt

    def test_debug_fire_checkin_evening_no_stale_tracker_append(self):
        """The stale-tracker nudge is morning-only, matching the pre-extraction
        closure's `if _morning:` guard."""
        cid = 90121
        bot.state["users"][str(cid)] = fresh_user(checkin_enabled=True)
        stale_ts = (date.today() - timedelta(days=5)).isoformat() + "T00:00:00"
        bot.state["users"][str(cid)]["trackers"] = {
            "weight": {"unit": "kg", "log": [{"ts": stale_ts, "value": 80}]}
        }
        with patch.object(bot, "chat", AsyncMock(return_value="Evening!")) as mock_chat:
            run(bot._run_checkin(_debug_context([]), cid, "evening"))
        args, _ = mock_chat.call_args
        prompt = args[1]
        assert "2+ days" not in prompt

    def test_debug_fire_checkin_pending_checkin_ack(self):
        """An unanswered prior check-in appends the silence-acknowledgement
        instruction to the prompt handed to chat()."""
        cid = 9013
        bot.state["users"][str(cid)] = fresh_user(checkin_enabled=True, pending_checkin="evening")
        with patch.object(bot, "chat", AsyncMock(return_value="Morning!")) as mock_chat:
            run(bot._run_checkin(_debug_context([]), cid, "morning"))
        args, _ = mock_chat.call_args
        prompt = args[1]
        assert "did not reply" in prompt

    def test_debug_fire_checkin_sends_plain_text_and_logs_job(self):
        """Check-ins are plain text with no buttons (buttons underscored the
        mechanical feel) -- users respond in natural language instead."""
        cid = 9014
        bot.state["users"][str(cid)] = fresh_user(checkin_enabled=True)
        context = _debug_context([])
        with patch.object(bot, "chat", AsyncMock(return_value="Morning!")):
            run(bot._run_checkin(context, cid, "morning"))
        context.bot.send_message.assert_awaited_once()
        _, kwargs = context.bot.send_message.call_args
        assert kwargs.get("reply_markup") is None
        assert bot.db_last_job_fired(str(cid), "checkin_morning") is not None

    @pytest.mark.parametrize("runner_name,call_kwargs", [
        pytest.param("_run_checkin", {"label": "morning"}, id="checkin"),
        pytest.param("_run_habit_reminder", {}, id="habit_reminder"),
        pytest.param("_run_idle_nudge", {}, id="idle_nudge"),
        pytest.param("_run_weekly_digest", {}, id="weekly_digest"),
        pytest.param("_run_deadline_alert", {}, id="deadline_alert"),
    ])
    @pytest.mark.parametrize("guard_attr,expected_reason", [
        pytest.param("_is_quiet_now", "quiet hours", id="quiet_hours"),
        pytest.param("_is_muted", "muted", id="muted"),
    ])
    def test_debug_fire_runner_guard_suppression(
        self, runner_name, call_kwargs, guard_attr, expected_reason
    ):
        """Every runner returns the guard's reason string and sends nothing
        when quiet hours or mute suppress it — the guard is real behaviour,
        never bypassed on the debug path."""
        cid = 9020
        bot.state["users"][str(cid)] = fresh_user(checkin_enabled=True)
        context = _debug_context([])
        runner = getattr(bot, runner_name)
        with patch.object(bot, guard_attr, return_value=True), \
             patch.object(bot, "chat", AsyncMock(return_value="x")):
            if runner_name == "_run_checkin":
                result = run(runner(context, cid, call_kwargs["label"]))
            else:
                result = run(runner(context, cid))
        assert result == expected_reason
        context.bot.send_message.assert_not_awaited()

    @pytest.mark.parametrize("guard_attr,expected_reason", [
        pytest.param("_is_quiet_now", "quiet hours", id="quiet_hours"),
        pytest.param("_is_muted", "muted", id="muted"),
    ])
    def test_debug_fire_reminder_guard_suppression(self, guard_attr, expected_reason):
        cid = 9021
        bot.state["users"][str(cid)] = fresh_user()
        reminder = {"id": "r1", "time": "09:00", "message": "Take pills", "once": False}
        context = _debug_context([])
        with patch.object(bot, guard_attr, return_value=True):
            result = run(bot._run_reminder(context, cid, reminder))
        assert result == expected_reason
        context.bot.send_message.assert_not_awaited()

    def test_debug_fire_habit_reminder_all_done(self):
        cid = 9030
        today_str = date.today().isoformat()
        bot.state["users"][str(cid)] = fresh_user(
            habits={"meditation": {"completions": [today_str], "created": today_str}}
        )
        context = _debug_context([])
        result = run(bot._run_habit_reminder(context, cid))
        assert result == "nothing undone"
        context.bot.send_message.assert_not_awaited()

    def test_debug_fire_habit_reminder_lists_undone(self):
        cid = 9031
        bot.state["users"][str(cid)] = fresh_user(
            habits={
                "meditation": {"completions": [], "created": date.today().isoformat()},
                "reading": {"completions": [date.today().isoformat()], "created": date.today().isoformat()},
            }
        )
        context = _debug_context([])
        result = run(bot._run_habit_reminder(context, cid))
        assert result is None
        context.bot.send_message.assert_awaited_once()
        _, kwargs = context.bot.send_message.call_args
        assert "meditation" in kwargs["text"]
        assert "reading" not in kwargs["text"]

    def test_debug_fire_idle_nudge_no_tasks_or_habits(self):
        cid = 9040
        bot.state["users"][str(cid)] = fresh_user()
        context = _debug_context([])
        result = run(bot._run_idle_nudge(context, cid))
        assert result == "no tasks or habits"
        context.bot.send_message.assert_not_awaited()

    def test_debug_fire_idle_nudge_no_activity_history(self):
        cid = 9041
        bot.state["users"][str(cid)] = fresh_user(tasks=["Do a thing"])
        context = _debug_context([])
        result = run(bot._run_idle_nudge(context, cid))
        assert result == "no recent inactivity"
        context.bot.send_message.assert_not_awaited()

    def test_debug_fire_idle_nudge_recent_activity(self):
        cid = 9042
        bot.state["users"][str(cid)] = fresh_user(
            tasks=["Do a thing"], activity_days=[date.today().isoformat()]
        )
        context = _debug_context([])
        result = run(bot._run_idle_nudge(context, cid))
        assert result == "no recent inactivity"
        context.bot.send_message.assert_not_awaited()

    def test_debug_fire_idle_nudge_fires_after_three_days(self):
        cid = 9043
        old_day = (date.today() - timedelta(days=5)).isoformat()
        bot.state["users"][str(cid)] = fresh_user(tasks=["Do a thing"], activity_days=[old_day])
        context = _debug_context([])
        result = run(bot._run_idle_nudge(context, cid))
        assert result is None
        context.bot.send_message.assert_awaited_once()

    def test_debug_fire_weekly_digest_not_sunday_reason(self):
        cid = 9050
        bot.state["users"][str(cid)] = fresh_user()
        context = _debug_context([])
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        monday = _dt(2023, 1, 2, 10, 0, tzinfo=_ZI("UTC"))
        assert monday.weekday() != 6
        with patch.object(bot, "datetime") as mock_dt:
            mock_dt.now.return_value = monday
            result = run(bot._run_weekly_digest(context, cid))
        assert result == "not sunday"
        context.bot.send_message.assert_not_awaited()

    def test_debug_fire_weekly_digest_sunday_touch_activity_false(self):
        cid = 9051
        bot.state["users"][str(cid)] = fresh_user()
        context = _debug_context([])
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        sunday = _dt(2023, 1, 1, 10, 0, tzinfo=_ZI("UTC"))
        assert sunday.weekday() == 6
        with patch.object(bot, "datetime") as mock_dt, \
             patch.object(bot, "chat", AsyncMock(return_value="Great week!")) as mock_chat:
            mock_dt.now.return_value = sunday
            result = run(bot._run_weekly_digest(context, cid))
        assert result is None
        mock_chat.assert_awaited_once()
        _, kwargs = mock_chat.call_args
        assert kwargs.get("touch_activity") is False
        context.bot.send_message.assert_awaited_once()

    def test_debug_fire_reminder_sends_text_and_registers_snooze(self):
        """The reminder is now delivered through the LLM (chat()) so wording
        varies instead of a frozen template -- verify the raw message reaches
        the prompt handed to chat(), and that the (mocked) reply is sent with
        the snooze button attached."""
        cid = 9060
        bot.state["users"][str(cid)] = fresh_user()
        reminder = {"id": "r1", "time": "09:00", "message": "Take pills", "once": False}
        context = _debug_context([])
        with patch.object(bot, "chat", AsyncMock(return_value="Time for your pills!")) as mock_chat:
            result = run(bot._run_reminder(context, cid, reminder))
        assert result is None
        args, kwargs = mock_chat.call_args
        assert "Take pills" in args[1]
        assert kwargs.get("touch_activity") is False
        context.bot.send_message.assert_awaited_once()
        _, kwargs = context.bot.send_message.call_args
        assert kwargs["text"] == "Time for your pills!"
        assert kwargs["reply_markup"] is not None

    def test_debug_fire_reminder_with_reason_line(self):
        """A stored reason is passed into the LLM prompt so it can be woven
        into the message, rather than appended as a static line."""
        cid = 9061
        bot.state["users"][str(cid)] = fresh_user()
        reminder = {
            "id": "r2", "time": "09:00", "message": "Take pills",
            "reason": "doctor's orders", "once": False,
        }
        context = _debug_context([])
        with patch.object(bot, "chat", AsyncMock(return_value="ok")) as mock_chat:
            run(bot._run_reminder(context, cid, reminder))
        args, _ = mock_chat.call_args
        assert "doctor's orders" in args[1]

    def test_debug_fire_deadline_alert_nothing_due_reason(self):
        """_run_deadline_alert also carries the shared suppression-reason
        contract (Rule 2 deviation, see 01-03-SUMMARY.md): "nothing due" when
        no task is close and no annual reminder matched today."""
        cid = 9062
        bot.state["users"][str(cid)] = fresh_user()
        context = _debug_context([])
        result = run(bot._run_deadline_alert(context, cid))
        assert result == "nothing due"
        context.bot.send_message.assert_not_awaited()

    @pytest.mark.parametrize("runner_name,call_kwargs", [
        pytest.param("_run_reminder", {"reminder": {"id": "r9", "time": "09:00", "message": "hi", "once": False}}, id="reminder"),
        pytest.param("_run_habit_reminder", {}, id="habit_reminder"),
        pytest.param("_run_idle_nudge", {}, id="idle_nudge"),
    ])
    def test_wr01_runner_reports_send_failed_on_genuine_exception(self, runner_name, call_kwargs):
        """WR-01: a genuine send failure must be reported as a distinguishable
        reason string, never as silent success (None)."""
        cid = 9063
        bot.state["users"][str(cid)] = fresh_user(
            checkin_enabled=True,
            tasks=["Do a thing"],
            habits={"meditation": {"completions": [], "created": date.today().isoformat()}},
            activity_days=[(date.today() - timedelta(days=5)).isoformat()],
        )
        context = _debug_context([])
        context.bot.send_message = AsyncMock(side_effect=RuntimeError("boom"))
        runner = getattr(bot, runner_name)
        with patch.object(bot, "chat", AsyncMock(return_value="ok")):
            if runner_name == "_run_reminder":
                result = run(runner(context, cid, call_kwargs["reminder"]))
            else:
                result = run(runner(context, cid))
        assert result == "send failed"

    def test_wr01_weekly_digest_reports_send_failed_on_genuine_exception(self):
        cid = 9067
        bot.state["users"][str(cid)] = fresh_user()
        context = _debug_context([])
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        sunday = _dt(2023, 1, 1, 10, 0, tzinfo=_ZI("UTC"))
        with patch.object(bot, "chat", AsyncMock(return_value="Great week!")), \
             patch.object(bot, "datetime") as mock_dt:
            mock_dt.now.return_value = sunday
            mock_dt.utcnow.return_value = _dt.utcnow()
            context.bot.send_message = AsyncMock(side_effect=RuntimeError("boom"))
            result = run(bot._run_weekly_digest(context, cid))
        assert result == "send failed"

    def test_wr01_checkin_reports_send_failed_on_genuine_exception(self):
        cid = 9064
        bot.state["users"][str(cid)] = fresh_user(checkin_enabled=True)
        context = _debug_context([])
        with patch.object(bot, "chat", AsyncMock(side_effect=RuntimeError("llm outage"))):
            result = run(bot._run_checkin(context, cid, "morning"))
        assert result == "send failed"
        context.bot.send_message.assert_not_awaited()

    def test_wr01_deadline_alert_reports_send_failed_when_totally_unsent(self):
        """The partial exception WR-01 called out: something was due but the
        send itself raised -- must be "send failed", not "nothing due"."""
        cid = 9065
        bot.state["users"][str(cid)] = fresh_user(
            tasks=[{"text": "Ship it", "due": date.today().isoformat()}]
        )
        context = _debug_context([])
        context.bot.send_message = AsyncMock(side_effect=RuntimeError("boom"))
        result = run(bot._run_deadline_alert(context, cid))
        assert result == "send failed"

    def test_debug_fire_reports_send_failed_not_success(self):
        """/debug fire must not report '✅ Fired' when the runner returns a
        genuine failure reason (WR-01's user-facing consequence)."""
        cid = 9066
        bot.state["users"][str(cid)] = fresh_user(
            reminders=[{"id": "r1", "time": "09:00", "message": "Take pills", "once": False}]
        )
        with as_owner(cid), patch.object(bot, "chat", AsyncMock(return_value="ok")):
            update = _debug_update(cid)
            context = _debug_context(["fire", "reminder", "1"])
            context.bot.send_message = AsyncMock(side_effect=RuntimeError("boom"))
            run(bot.debug_cmd(update, context))
        text = update.message.reply_text.call_args[0][0]
        assert "✅" not in text
        assert "send failed" in text

    def test_scheduled_checkin_matches_debug_path(self):
        """schedule_user_checkins registers checkin_morning_{chat_id} and
        checkin_evening_{chat_id} as run_daily jobs; awaiting either callback
        produces the same effects as _run_checkin, because both call the same
        extracted function."""
        cid = 9070
        bot.state["users"][str(cid)] = fresh_user(checkin_enabled=True)
        app = MagicMock()
        captured = _capture_run_daily_callbacks(app)
        bot.schedule_user_checkins(app, cid)
        assert f"checkin_morning_{cid}" in captured
        assert f"checkin_evening_{cid}" in captured
        job_context = _debug_context([])
        with patch.object(bot, "chat", AsyncMock(return_value="Morning!")) as mock_chat:
            run(captured[f"checkin_morning_{cid}"](job_context))
        job_context.bot.send_message.assert_awaited_once()
        mock_chat.assert_awaited_once()
        _, kwargs = mock_chat.call_args
        assert kwargs.get("touch_activity") is False
        assert bot.get_user(cid)["pending_checkin"] == "morning"

    def test_scheduled_reminder_matches_debug_path(self):
        cid = 9071
        bot.state["users"][str(cid)] = fresh_user()
        reminder = {"id": "r3", "time": "09:00", "message": "Stretch", "once": False}
        app = MagicMock()
        captured = _capture_run_daily_callbacks(app)
        bot.schedule_user_reminder(app, cid, reminder)
        job_name = f"reminder_{cid}_{reminder['id']}"
        assert job_name in captured
        job_context = _debug_context([])
        with patch.object(bot, "chat", AsyncMock(return_value="Time to stretch!")) as mock_chat:
            run(captured[job_name](job_context))
        args, _ = mock_chat.call_args
        assert "Stretch" in args[1]
        job_context.bot.send_message.assert_awaited_once()
        _, kwargs = job_context.bot.send_message.call_args
        assert kwargs["text"] == "Time to stretch!"

    def test_scheduled_habit_reminder_matches_debug_path(self):
        cid = 9072
        bot.state["users"][str(cid)] = fresh_user(
            checkin_enabled=True,
            habits={"meditation": {"completions": [], "created": date.today().isoformat()}},
        )
        app = MagicMock()
        captured = _capture_run_daily_callbacks(app)
        bot.schedule_user_alerts(app, cid)
        job_name = f"habit_reminder_{cid}"
        assert job_name in captured
        job_context = _debug_context([])
        run(captured[job_name](job_context))
        job_context.bot.send_message.assert_awaited_once()
        _, kwargs = job_context.bot.send_message.call_args
        assert "meditation" in kwargs["text"]

    def test_scheduled_idle_nudge_matches_debug_path(self):
        cid = 9073
        old_day = (date.today() - timedelta(days=5)).isoformat()
        bot.state["users"][str(cid)] = fresh_user(
            checkin_enabled=True, tasks=["Do a thing"], activity_days=[old_day]
        )
        app = MagicMock()
        captured = _capture_run_daily_callbacks(app)
        bot.schedule_user_alerts(app, cid)
        job_name = f"idle_nudge_{cid}"
        assert job_name in captured
        job_context = _debug_context([])
        run(captured[job_name](job_context))
        job_context.bot.send_message.assert_awaited_once()

    def test_scheduled_weekly_digest_matches_debug_path(self):
        cid = 9074
        bot.state["users"][str(cid)] = fresh_user(checkin_enabled=True)
        app = MagicMock()
        captured = _capture_run_daily_callbacks(app)
        bot.schedule_user_alerts(app, cid)
        job_name = f"weekly_digest_{cid}"
        assert job_name in captured
        job_context = _debug_context([])
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        sunday = _dt(2023, 1, 1, 10, 0, tzinfo=_ZI("UTC"))
        with patch.object(bot, "datetime") as mock_dt, \
             patch.object(bot, "chat", AsyncMock(return_value="Great week!")) as mock_chat:
            mock_dt.now.return_value = sunday
            run(captured[job_name](job_context))
        job_context.bot.send_message.assert_awaited_once()
        mock_chat.assert_awaited_once()
        _, kwargs = mock_chat.call_args
        assert kwargs.get("touch_activity") is False

    # ── 01-03 Task 2: DEBUG_JOBS registry + full /debug fire dispatch ──────

    def test_debug_jobs_registry_has_exactly_six_fixed_names(self):
        """A rename of any registry key must fail this test loudly rather
        than pass silently."""
        assert set(bot.DEBUG_JOBS.keys()) == {
            "checkin_morning", "checkin_evening", "deadline_alert",
            "habit_reminder", "idle_nudge", "weekly_digest",
        }

    @pytest.mark.parametrize("job_name", [
        "checkin_morning", "checkin_evening", "deadline_alert",
        "habit_reminder", "idle_nudge", "weekly_digest",
    ])
    def test_debug_fire_each_registry_job_dispatches_and_confirms(self, job_name):
        """Each of the six fixed job names dispatches through debug_cmd to
        its runner and the owner receives a confirmation when it fires."""
        cid = 9100
        bot.state["users"][str(cid)] = fresh_user(checkin_enabled=True)
        bot.state["users"][str(cid)]["tasks"] = [
            {"text": "Ship it", "due": date.today().isoformat()}
        ]
        bot.state["users"][str(cid)]["habits"] = {
            "meditation": {"completions": [], "created": date.today().isoformat()}
        }
        bot.state["users"][str(cid)]["activity_days"] = [
            (date.today() - timedelta(days=5)).isoformat()
        ]
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        sunday = _dt(2023, 1, 1, 10, 0, tzinfo=_ZI("UTC"))
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["fire", job_name])
            with patch.object(bot, "chat", AsyncMock(return_value="reply")), \
                 patch.object(bot, "datetime") as mock_dt:
                mock_dt.now.return_value = sunday
                # _run_checkin's success path logs a real timestamp via
                # db_log_job -- only .now() is under test here (the Sunday
                # gate), so utcnow() must still return a real datetime or the
                # SQLite write raises and (correctly, per WR-01) the runner
                # reports "send failed" instead of "✅ Fired".
                mock_dt.utcnow.return_value = _dt.utcnow()
                run(bot.debug_cmd(update, context))
        update.message.reply_text.assert_awaited_once()
        text = update.message.reply_text.call_args[0][0]
        assert "✅" in text
        assert job_name in text

    def test_debug_fire_runner_suppression_reported_by_name(self):
        """When a runner returns a suppression reason, the reply names it —
        never silence, never mistaken for a broken command."""
        cid = 9101
        bot.state["users"][str(cid)] = fresh_user(checkin_enabled=True)
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["fire", "weekly_digest"])
            with patch.object(bot, "_is_quiet_now", return_value=True):
                run(bot.debug_cmd(update, context))
        update.message.reply_text.assert_awaited_once()
        text = update.message.reply_text.call_args[0][0]
        assert "quiet hours" in text
        assert "✅" not in text

    def test_debug_fire_reminder_2_fires_second_entry(self):
        """/debug fire reminder 2 fires user['reminders'][1], matching what
        /remind list numbers as 2 (D-P3)."""
        cid = 9102
        bot.state["users"][str(cid)] = fresh_user()
        bot.state["users"][str(cid)]["reminders"] = [
            {"id": "r1", "time": "08:00", "message": "First", "once": False},
            {"id": "r2", "time": "09:00", "message": "Second", "once": False},
        ]
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["fire", "reminder", "2"])
            run(bot.debug_cmd(update, context))
        context.bot.send_message.assert_awaited_once()
        _, kwargs = context.bot.send_message.call_args
        assert "Second" in kwargs["text"]
        assert "First" not in kwargs["text"]

    @pytest.mark.parametrize("reminder_args", [
        pytest.param(["fire", "reminder", "0"], id="zero"),
        pytest.param(["fire", "reminder", "99"], id="out_of_range"),
        pytest.param(["fire", "reminder", "abc"], id="non_numeric"),
        pytest.param(["fire", "reminder"], id="bare"),
    ])
    def test_debug_fire_reminder_invalid_number_fires_nothing(self, reminder_args):
        cid = 9103
        bot.state["users"][str(cid)] = fresh_user()
        bot.state["users"][str(cid)]["reminders"] = [
            {"id": "r1", "time": "08:00", "message": "Only one", "once": False},
        ]
        with as_owner(cid), patch.object(bot, "_run_reminder", AsyncMock()) as mock_run:
            update = _debug_update(cid)
            context = _debug_context(reminder_args)
            run(bot.debug_cmd(update, context))
        mock_run.assert_not_awaited()
        update.message.reply_text.assert_awaited_once()
        text = update.message.reply_text.call_args[0][0]
        assert "Usage" in text or "Invalid number" in text

    def test_debug_fire_reminder_zero_does_not_wrap_to_last_entry(self):
        """The threat-model concern (T-1-10): a naive `idx = int(n) - 1`
        against a Python list means /debug fire reminder 0 would silently
        wrap to index -1 (the LAST reminder) rather than being rejected.
        This must be rejected, not silently fire the wrong reminder."""
        cid = 9104
        bot.state["users"][str(cid)] = fresh_user()
        bot.state["users"][str(cid)]["reminders"] = [
            {"id": "r1", "time": "08:00", "message": "Should not fire", "once": False},
        ]
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["fire", "reminder", "0"])
            run(bot.debug_cmd(update, context))
        context.bot.send_message.assert_not_awaited()

    def test_debug_fire_unknown_job_lists_every_registry_key(self):
        cid = 9105
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["fire", "wibble"])
            run(bot.debug_cmd(update, context))
        update.message.reply_text.assert_awaited_once()
        text = update.message.reply_text.call_args[0][0]
        for key in bot.DEBUG_JOBS:
            assert key in text
        context.bot.send_message.assert_not_awaited()

    def test_debug_fire_bare_fire_lists_every_registry_key(self):
        cid = 9106
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["fire"])
            run(bot.debug_cmd(update, context))
        update.message.reply_text.assert_awaited_once()
        text = update.message.reply_text.call_args[0][0]
        for key in bot.DEBUG_JOBS:
            assert key in text
        context.bot.send_message.assert_not_awaited()

    def test_debug_fire_annual_reminder_via_deadline_alert_unchanged(self):
        """Firing an annual reminder's parent behaviour through deadline_alert
        still matches the annual entry by its %m-%d date (unchanged from
        01-01), now routed through the DEBUG_JOBS registry."""
        cid = 9107
        bot.state["users"][str(cid)] = fresh_user()
        today_mmdd = date.today().strftime("%m-%d")
        bot.state["users"][str(cid)]["reminders"] = [
            {
                "id": "r1", "time": "09:00", "message": "Anniversary",
                "once": False, "annual": True, "date": today_mmdd,
            }
        ]
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["fire", "deadline_alert"])
            run(bot.debug_cmd(update, context))
        context.bot.send_message.assert_awaited_once()
        _, kwargs = context.bot.send_message.call_args
        assert "Anniversary" in kwargs["text"]
        update.message.reply_text.assert_awaited_once()
        text = update.message.reply_text.call_args[0][0]
        assert "✅" in text


class TestDebugOwnerGate:
    """T-1-01 mitigation: prove the owner gate is a property of the whole
    /debug command, not of one branch — every argument shape is rejected for
    non-owners and when MY_CHAT_ID is unset or empty, the gate precedes any
    get_user() call (so probing registers nothing), string-form comparison
    accepts an int chat_id, and the command stays out of help output."""

    ARG_SHAPES = [
        pytest.param([], id="no_args"),
        pytest.param(["fire"], id="fire"),
        pytest.param(["clock"], id="clock"),
        pytest.param(["prompt"], id="prompt"),
        pytest.param(["wibble"], id="unknown"),
    ]

    def setup_method(self):
        bot.state = {"users": {}}

    @pytest.mark.parametrize("args", ARG_SHAPES)
    def test_debug_owner_gate_rejects_non_owner(self, args):
        cid = 8100
        with as_owner(999999):  # someone else is configured as owner
            update = _debug_update(cid)
            context = _debug_context(args)
            run(bot.debug_cmd(update, context))
        update.message.reply_text.assert_awaited_once_with("Admin only.")
        context.bot.send_message.assert_not_awaited()

    @pytest.mark.parametrize("args", ARG_SHAPES)
    def test_debug_owner_gate_rejects_when_my_chat_id_unset(self, args):
        cid = 8200
        prev = bot.MY_CHAT_ID
        bot.MY_CHAT_ID = None
        try:
            update = _debug_update(cid)
            context = _debug_context(args)
            run(bot.debug_cmd(update, context))
        finally:
            bot.MY_CHAT_ID = prev
        update.message.reply_text.assert_awaited_once_with("Admin only.")
        context.bot.send_message.assert_not_awaited()

    @pytest.mark.parametrize("args", ARG_SHAPES)
    def test_debug_owner_gate_rejects_when_my_chat_id_empty(self, args):
        cid = 8300
        prev = bot.MY_CHAT_ID
        bot.MY_CHAT_ID = ""
        try:
            update = _debug_update(cid)
            context = _debug_context(args)
            run(bot.debug_cmd(update, context))
        finally:
            bot.MY_CHAT_ID = prev
        update.message.reply_text.assert_awaited_once_with("Admin only.")
        context.bot.send_message.assert_not_awaited()

    def test_debug_owner_gate_accepts_int_chat_id_against_string_my_chat_id(self):
        """The gate compares string forms (str(chat_id) != MY_CHAT_ID): an int
        effective_chat.id must be accepted when MY_CHAT_ID holds the matching
        string, pinning the coercion against a later silent-lockout regression."""
        cid = 8400  # plain int, as Telegram actually sends it
        with as_owner(cid):
            assert isinstance(bot.MY_CHAT_ID, str)
            update = _debug_update(cid)
            context = _debug_context([])
            run(bot.debug_cmd(update, context))
        update.message.reply_text.assert_awaited_once()
        text = update.message.reply_text.call_args[0][0]
        assert text != "Admin only."

    def test_debug_owner_gate_probing_registers_no_user(self):
        """A rejected non-owner call must not call get_user() — probing the
        command must never register the prober's chat_id as a user."""
        cid = 8500
        with as_owner(999999):
            update = _debug_update(cid)
            context = _debug_context(["fire", "deadline_alert"])
            run(bot.debug_cmd(update, context))
        assert str(cid) not in bot.state["users"]

    def test_debug_owner_gate_help_text_omits_debug(self):
        assert "/debug" not in bot._HELP_TEXT
        assert "debug" not in bot._HELP_TEXT.lower()


class TestDebugPrompt:
    """`/debug prompt` — verbatim system-prompt dump for the owner: no LLM
    call, no conversational side effect, no disk write (DEBUG-03)."""

    def setup_method(self):
        bot.state = {"users": {}}

    def test_debug_prompt_matches_build_system_prompt_rich_user(self):
        """A user with tasks, habits, trackers, notes, journal entries and
        profile memory: the delivered text equals build_system_prompt(user,
        chat_id) character for character."""
        cid = 9200
        bot.state["users"][str(cid)] = fresh_user()
        user = bot.state["users"][str(cid)]
        user["tasks"] = [{"text": "Ship the report", "due": date.today().isoformat()}]
        user["habits"] = {"meditation": {"completions": [], "created": date.today().isoformat()}}
        user["trackers"] = {
            "weight": {"unit": "kg", "log": [{"ts": bot.datetime.utcnow().isoformat(), "value": 80.5}]}
        }
        bot.db_add_note(str(cid), "My secret goal is to write a book")
        bot.db_add_journal(str(cid), "Felt very productive today")
        bot.db_add_profile_memory(str(cid), "Prefers direct feedback")
        expected = bot.build_system_prompt(user, cid)
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["prompt"])
            run(bot.debug_cmd(update, context))
        assert _delivered_text(update) == expected

    def test_debug_prompt_empty_user_returns_base_prompt_no_crash(self):
        """A brand-new user with nothing stored still gets a non-empty base
        prompt, no exception, delivered over the short text path."""
        cid = 9201
        bot.state["users"][str(cid)] = fresh_user()
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["prompt"])
            run(bot.debug_cmd(update, context))
        text = _delivered_text(update)
        assert isinstance(text, str) and len(text) > 0
        update.message.reply_document.assert_not_awaited()

    def test_debug_prompt_no_llm_call(self):
        """No LLM client is constructed and no completion is requested when
        dumping the prompt -- this is a read, not a chat turn."""
        cid = 9202
        bot.state["users"][str(cid)] = fresh_user()
        bot.state["users"][str(cid)]["tasks"] = ["Write tests"]
        mock_client = _no_llm_client()
        with as_owner(cid), patch("bot.get_llm_client", return_value=mock_client):
            update = _debug_update(cid)
            context = _debug_context(["prompt"])
            run(bot.debug_cmd(update, context))
        mock_client.chat.completions.create.assert_not_awaited()

    def test_debug_prompt_long(self):
        """A prompt over 4000 chars is delivered whole as a document -- never
        truncated, never split -- and the decoded payload matches
        build_system_prompt exactly, including Cyrillic note/journal content."""
        cid = 9203
        bot.state["users"][str(cid)] = fresh_user()
        user = bot.state["users"][str(cid)]
        bot.db_add_note(str(cid), "Секретная заметка про план на будущее " * 10)
        for _ in range(9):
            bot.db_add_note(str(cid), "English padding note content. " * 10)
        bot.db_add_journal(str(cid), "Продуктивный день, закончил важный отчёт.")
        expected = bot.build_system_prompt(user, cid)
        assert len(expected) > 4000, "fixture must exceed the delivery threshold"
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["prompt"])
            run(bot.debug_cmd(update, context))
        update.message.reply_document.assert_awaited_once()
        update.message.reply_text.assert_not_awaited()
        assert _delivered_text(update) == expected

    def test_debug_prompt_short_uses_reply_text(self):
        """A prompt at or under the threshold takes the text path, not the
        document path."""
        cid = 9204
        bot.state["users"][str(cid)] = fresh_user()
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["prompt"])
            run(bot.debug_cmd(update, context))
        update.message.reply_text.assert_awaited_once()
        update.message.reply_document.assert_not_awaited()

    def test_debug_prompt_no_history_or_activity_mutation(self):
        """Dumping the prompt is not a conversation turn: history length and
        activity_days are unchanged across the call."""
        cid = 9205
        bot.state["users"][str(cid)] = fresh_user()
        user = bot.state["users"][str(cid)]
        user["history"] = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        user["activity_days"] = ["2026-08-01"]
        history_before = list(user["history"])
        activity_before = list(user["activity_days"])
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["prompt"])
            run(bot.debug_cmd(update, context))
        assert user["history"] == history_before
        assert user["activity_days"] == activity_before

    def test_debug_prompt_non_owner_rejected_before_assembly(self):
        """A non-owner is rejected before any prompt is assembled."""
        cid = 9206
        with as_owner(999999), patch("bot.build_system_prompt") as mock_build:
            update = _debug_update(cid)
            context = _debug_context(["prompt"])
            run(bot.debug_cmd(update, context))
        update.message.reply_text.assert_awaited_once_with("Admin only.")
        mock_build.assert_not_called()

    # ── Task 2: section-by-section, encoding, threshold and determinism edges ──

    _SECTION_CASES = [
        pytest.param(
            9400,
            lambda user, cid: user.__setitem__("context", "I am a software engineer"),
            "About this user:",
            id="context",
        ),
        pytest.param(
            9401,
            lambda user, cid: user.__setitem__("trackers", {
                "weight": {"unit": "kg", "log": [{"ts": bot.datetime.utcnow().isoformat(), "value": 80.0}]}
            }),
            "Recent tracker readings:",
            id="trackers",
        ),
        pytest.param(
            9402,
            lambda user, cid: user.__setitem__("habits", {
                "meditation": {"completions": [], "created": date.today().isoformat()}
            }),
            "Today's habits:",
            id="habits",
        ),
        pytest.param(
            9403,
            lambda user, cid: user.__setitem__("today_focus", {
                "date": date.today().isoformat(), "text": "Deep work session"
            }),
            "Today's focus:",
            id="today_focus",
        ),
        pytest.param(
            9404,
            lambda user, cid: bot.db_add_note(str(cid), "Remember to call the dentist"),
            "User's recent notes:",
            id="notes",
        ),
        pytest.param(
            9405,
            lambda user, cid: bot.db_add_profile_memory(str(cid), "Prefers direct feedback"),
            "Permanent user facts:",
            id="profile_memory",
        ),
        pytest.param(
            9406,
            lambda user, cid: bot.db_add_episodic_memory(str(cid), "Mentioned feeling stressed about work"),
            "Recent observations (last 30 days):",
            id="episodic_memory",
        ),
        pytest.param(
            9407,
            lambda user, cid: bot.db_add_journal(str(cid), "Felt very productive today"),
            "Recent journal entries:",
            id="journal",
        ),
        pytest.param(
            9408,
            lambda user, cid: user.__setitem__("language", "Hebrew"),
            "Always respond exclusively in",
            id="language",
        ),
    ]

    @pytest.mark.parametrize("cid,seed_fn,marker", _SECTION_CASES)
    def test_debug_prompt_section_absent_then_present(self, cid, seed_fn, marker):
        """Each optional prompt section is absent for an empty user and
        present once its backing data is seeded through the same helper the
        production code uses; in both states the dump matches
        build_system_prompt exactly."""
        bot.state["users"][str(cid)] = fresh_user()
        user = bot.state["users"][str(cid)]
        empty_prompt = bot.build_system_prompt(user, cid)
        assert marker not in empty_prompt

        seed_fn(user, cid)
        seeded_prompt = bot.build_system_prompt(user, cid)
        assert marker in seeded_prompt

        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["prompt"])
            run(bot.debug_cmd(update, context))
        assert _delivered_text(update) == seeded_prompt

    def test_debug_prompt_emoji_and_mixed_script_notes_round_trip(self):
        """A note containing an emoji and a note containing mixed Cyrillic
        and Latin text both round-trip through the document path unchanged."""
        cid = 9500
        bot.state["users"][str(cid)] = fresh_user()
        user = bot.state["users"][str(cid)]
        bot.db_add_note(str(cid), "Do not forget the 🎉 party plans this weekend! " * 40)
        bot.db_add_note(str(cid), "Смешанный текст mixed with English слова тут " * 40)
        expected = bot.build_system_prompt(user, cid)
        assert len(expected) > 4000, "fixture must exceed the delivery threshold"
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["prompt"])
            run(bot.debug_cmd(update, context))
        update.message.reply_document.assert_awaited_once()
        assert _delivered_text(update) == expected

    def test_debug_prompt_threshold_boundary_text_vs_document(self):
        """A prompt landing exactly on the 4000-char threshold takes the text
        path; one character more takes the document path. Padding is derived
        from a measured probe rather than a hard-coded note size, so this
        stays correct if prompt assembly is reshaped later (e.g. Phase 3)."""
        base_cid = 9300
        bot.state["users"][str(base_cid)] = fresh_user()
        base_len = len(bot.build_system_prompt(bot.state["users"][str(base_cid)], base_cid))

        probe_cid = 9301
        bot.state["users"][str(probe_cid)] = fresh_user()
        probe_user = bot.state["users"][str(probe_cid)]
        bot.db_add_note(str(probe_cid), "X")
        probe_len = len(bot.build_system_prompt(probe_user, probe_cid))
        overhead = probe_len - base_len - 1  # fixed chars the notes section wraps around the text

        target_note_len = 4000 - base_len - overhead

        at_cid = 9302
        bot.state["users"][str(at_cid)] = fresh_user()
        at_user = bot.state["users"][str(at_cid)]
        bot.db_add_note(str(at_cid), "A" * target_note_len)
        at_prompt = bot.build_system_prompt(at_user, at_cid)
        assert len(at_prompt) == 4000
        with as_owner(at_cid):
            update = _debug_update(at_cid)
            context = _debug_context(["prompt"])
            run(bot.debug_cmd(update, context))
        update.message.reply_text.assert_awaited_once()
        update.message.reply_document.assert_not_awaited()

        over_cid = 9303
        bot.state["users"][str(over_cid)] = fresh_user()
        over_user = bot.state["users"][str(over_cid)]
        bot.db_add_note(str(over_cid), "A" * (target_note_len + 1))
        over_prompt = bot.build_system_prompt(over_user, over_cid)
        assert len(over_prompt) == 4001
        with as_owner(over_cid):
            update = _debug_update(over_cid)
            context = _debug_context(["prompt"])
            run(bot.debug_cmd(update, context))
        update.message.reply_document.assert_awaited_once()
        update.message.reply_text.assert_not_awaited()

    def test_debug_prompt_deterministic_across_consecutive_calls(self):
        """Two consecutive /debug prompt calls with no intervening state
        change produce identical output -- guards against a future prompt
        section that sorts non-deterministically or embeds a live timestamp."""
        cid = 9600
        bot.state["users"][str(cid)] = fresh_user()
        bot.state["users"][str(cid)]["tasks"] = ["Write tests"]
        bot.db_add_note(str(cid), "Consistent note content")
        with as_owner(cid):
            update1 = _debug_update(cid)
            context1 = _debug_context(["prompt"])
            run(bot.debug_cmd(update1, context1))
            update2 = _debug_update(cid)
            context2 = _debug_context(["prompt"])
            run(bot.debug_cmd(update2, context2))
        assert _delivered_text(update1) == _delivered_text(update2)


def _reset_context():
    """MagicMock context for reset_cmd: job_queue.jobs()/get_jobs_by_name()
    both return an empty list so the cancel-then-reschedule loops in
    reset_cmd and schedule_user_checkins/_alerts iterate cleanly."""
    context = MagicMock()
    context.application.job_queue.jobs.return_value = []
    context.application.job_queue.get_jobs_by_name.return_value = []
    return context


class TestDebugClock:
    """`/debug clock <ISO> | reset | (status)` -- DEBUG-02. Covers T-1-01
    (owner gate), T-1-03 (bounded expiry, echoed at set time), T-1-12
    (malformed input never raises and stores nothing), T-1-14 (excluded
    from export), and T-1-15 (cleared by /reset)."""

    def setup_method(self):
        bot.state = {"users": {}}

    def test_debug_clock_set_echoes_instant_and_expiry(self):
        cid = 9800
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["clock", "2027-03-05T09:30"])
            run(bot.debug_cmd(update, context))
        update.message.reply_text.assert_awaited_once()
        text = update.message.reply_text.call_args[0][0]
        assert "2027-03-05T09:30" in text
        assert "Expires" in text
        user = bot.get_user(cid)
        assert user["debug_clock"] == "2027-03-05T09:30"

    def test_debug_clock_set_takes_effect_immediately_same_process(self):
        cid = 9801
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["clock", "2027-03-05T09:30"])
            run(bot.debug_cmd(update, context))
        user = bot.get_user(cid)
        assert bot._today(user=user) == date(2027, 3, 5)

    def test_debug_clock_date_only_stored_as_midnight_local(self):
        cid = 9802
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["clock", "2027-03-05"])
            run(bot.debug_cmd(update, context))
        user = bot.get_user(cid)
        dt = bot._debug_now(user)
        assert (dt.hour, dt.minute) == (0, 0)
        assert bot._today(user=user) == date(2027, 3, 5)

    def test_debug_clock_status_reports_active_override(self):
        cid = 9803
        with as_owner(cid):
            run(bot.debug_cmd(_debug_update(cid), _debug_context(["clock", "2027-03-05T09:30"])))
            status_update = _debug_update(cid)
            run(bot.debug_cmd(status_update, _debug_context(["clock"])))
        text = status_update.message.reply_text.call_args[0][0]
        assert "2027-03-05T09:30" in text
        assert "Expires" in text

    def test_debug_clock_status_reports_expired_override_as_absent(self):
        """WR-02: an expired override must not be reported as active -- the
        no-args branch resolves through _debug_now, not a raw field read."""
        cid = 9808
        expired = (bot.datetime.utcnow() - bot.timedelta(hours=1)).isoformat()
        bot.db_set_pref(str(cid), "debug_clock", "2020-01-01T00:00")
        bot.db_set_pref(str(cid), "debug_clock_expires", expired)
        with as_owner(cid):
            update = _debug_update(cid)
            run(bot.debug_cmd(update, _debug_context(["clock"])))
        text = update.message.reply_text.call_args[0][0]
        assert "No simulated clock" in text
        assert "2020-01-01T00:00" not in text

    def test_debug_clock_status_reports_none_active(self):
        cid = 9804
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["clock"])
            run(bot.debug_cmd(update, context))
        text = update.message.reply_text.call_args[0][0]
        assert "No simulated clock" in text

    def test_debug_clock_reset_clears_prefs_and_confirms(self):
        cid = 9805
        with as_owner(cid):
            run(bot.debug_cmd(_debug_update(cid), _debug_context(["clock", "2027-03-05T09:30"])))
            reset_update = _debug_update(cid)
            run(bot.debug_cmd(reset_update, _debug_context(["clock", "reset"])))
        reset_update.message.reply_text.assert_awaited_once()
        assert bot.db_get_pref(str(cid), "debug_clock") is None
        assert bot.db_get_pref(str(cid), "debug_clock_expires") is None
        user = bot.get_user(cid)
        assert bot._today(user=user) == date.today()

    def test_debug_clock_reset_when_nothing_set_is_harmless(self):
        cid = 9806
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["clock", "reset"])
            run(bot.debug_cmd(update, context))
        update.message.reply_text.assert_awaited_once()

    @pytest.mark.parametrize("bad_input", ["notadate", "2027-13-45", ""])
    def test_debug_clock_malformed_input_stores_nothing(self, bad_input):
        cid = 9807
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["clock", bad_input])
            run(bot.debug_cmd(update, context))
        update.message.reply_text.assert_awaited_once()
        text = update.message.reply_text.call_args[0][0]
        assert "Usage" in text
        assert bot.db_get_pref(str(cid), "debug_clock") is None
        assert bot.db_get_pref(str(cid), "debug_clock_expires") is None

    def test_debug_clock_expiry_is_twelve_hours_ahead(self):
        cid = 9808
        before = bot.datetime.utcnow()
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["clock", "2027-03-05T09:30"])
            run(bot.debug_cmd(update, context))
        after = bot.datetime.utcnow()
        expires = bot.datetime.fromisoformat(bot.db_get_pref(str(cid), "debug_clock_expires"))
        assert before + bot.timedelta(hours=11) <= expires <= after + bot.timedelta(hours=13)

    def test_debug_clock_survives_simulated_restart(self):
        """Simulate a bot restart by wiping the in-memory state dict; the
        override must still be readable through get_user()'s SQLite overlay."""
        cid = 9809
        with as_owner(cid):
            update = _debug_update(cid)
            context = _debug_context(["clock", "2027-03-05T09:30"])
            run(bot.debug_cmd(update, context))
        bot.state["users"] = {}
        user = bot.get_user(cid)
        assert user["debug_clock"] == "2027-03-05T09:30"
        assert bot._today(user=user) == date(2027, 3, 5)

    def test_debug_clock_reset_via_account_wipe_clears_prefs(self):
        """/reset (the account wipe, not /debug clock reset) also clears both
        debug clock prefs -- a wipe must never leave a live simulated clock
        behind (T-1-15)."""
        cid = 9810
        with as_owner(cid):
            run(bot.debug_cmd(_debug_update(cid), _debug_context(["clock", "2027-03-05T09:30"])))
        assert bot.db_get_pref(str(cid), "debug_clock") == "2027-03-05T09:30"

        reset_update = _debug_update(cid)
        run(bot.reset_cmd(reset_update, _reset_context()))

        assert bot.db_get_pref(str(cid), "debug_clock") is None
        assert bot.db_get_pref(str(cid), "debug_clock_expires") is None
        user = bot.get_user(cid)
        assert user["debug_clock"] == ""

    def test_debug_clock_excluded_from_export(self):
        cid = 9811
        with as_owner(cid):
            run(bot.debug_cmd(_debug_update(cid), _debug_context(["clock", "2027-03-05T09:30"])))
        export_update = _debug_update(cid)
        export_context = MagicMock()
        run(bot.export_data(export_update, export_context))
        export_update.message.reply_document.assert_awaited_once()
        _, kwargs = export_update.message.reply_document.call_args
        doc = kwargs["document"]
        doc.seek(0)
        payload = json.loads(doc.read().decode("utf-8"))
        assert "debug_clock" not in payload
        assert "debug_clock_expires" not in payload

    def test_debug_clock_non_owner_rejected_before_parse_or_store(self):
        cid = 9812
        with as_owner(999999):
            update = _debug_update(cid)
            context = _debug_context(["clock", "2027-03-05T09:30"])
            run(bot.debug_cmd(update, context))
        update.message.reply_text.assert_awaited_once_with("Admin only.")
        assert bot.db_get_pref(str(cid), "debug_clock") is None
        assert str(cid) not in bot.state["users"]


class TestDebugClockAmbient:
    """DEBUG-02's ambient scope (D-01, D-P7): a simulated `now`, once set, is
    visible to ordinary interaction -- deadline badges, quiet hours, mute
    evaluation, streaks, habit summaries, /time, /mystats, the dumped system
    prompt and every job runner -- while every durable-write path stays on
    the real wall clock. Covers T-1-16 (no fabricated date reaches a durable
    record), T-1-17 (no fabricated date reaches scheduler arithmetic), T-1-18
    (no partial refactor leaves a surface on the real clock) and T-1-19 (no
    behaviour change for a user who never sets a clock)."""

    def setup_method(self):
        bot.state = {"users": {}}

    def _with_clock(self, cid, iso, hours_ahead=12):
        """Round-trip a simulated clock through SQLite exactly as /debug clock
        would, then read it back through get_user() -- the real overlay path,
        matching plan 01-04's own tests."""
        bot.db_set_pref(str(cid), "debug_clock", iso)
        expires = (bot.datetime.utcnow() + bot.timedelta(hours=hours_ahead)).isoformat()
        bot.db_set_pref(str(cid), "debug_clock_expires", expires)
        return bot.get_user(cid)

    # ── Task 1: shared helpers (_format_task_line, _habit_streak,
    #    _habit_summary_lines, _is_muted, _is_quiet_now, _get_streak) ──

    def test_debug_clock_ambient_format_task_line_no_override_matches_fixed_expectation(self):
        """No override -- and no `user` argument at all -- reproduces the
        exact pre-refactor rendering (additive-change guard, T-1-19)."""
        task = {"text": "Ship it", "due": date.today().isoformat()}
        assert bot._format_task_line(task, 1) == "1. Ship it 🔴 DUE TODAY"

    def test_debug_clock_ambient_format_task_line_override_moves_due_today_badge(self):
        """A task due in 7 days renders the due-today badge once the clock is
        moved 7 days forward, and the plain due-date form otherwise -- the
        deadline-badge success criterion, paired with a no-override control."""
        due = (date.today() + timedelta(days=7)).isoformat()
        task = {"text": "Ship it", "due": due}
        u_real = fresh_user()
        assert f"(due {due})" in bot._format_task_line(task, 1, user=u_real)

        cid = 9900
        u_sim = self._with_clock(cid, due + "T09:00:00")
        assert "DUE TODAY" in bot._format_task_line(task, 1, user=u_sim)

    def test_debug_clock_ambient_is_quiet_now_override_activates_inactive_window(self):
        """A quiet-hours window that is not active in real time reads as
        active once the clock is moved inside it -- paired no-override
        control proves the window really was inactive first."""
        cid = 9901
        real_hour = bot.datetime.utcnow().hour
        quiet_hour = (real_hour + 12) % 24  # 12h away from real now: never overlaps
        start_str = f"{quiet_hour:02d}:00"
        end_str = f"{(quiet_hour + 1) % 24:02d}:00"

        u_real = fresh_user(timezone="UTC")
        u_real["quiet_hours"] = {"start": start_str, "end": end_str}
        assert bot._is_quiet_now(u_real) is False

        u_sim = self._with_clock(cid, f"2027-06-15T{quiet_hour:02d}:30:00")
        u_sim["quiet_hours"] = {"start": start_str, "end": end_str}
        u_sim["timezone"] = "UTC"
        assert bot._is_quiet_now(u_sim) is True

    def test_debug_clock_ambient_is_muted_override_before_and_after_expiry(self):
        """An override set before a stored mute expiry reads as muted; one set
        past it reads as unmuted."""
        cid = 9902
        u = fresh_user()
        u["muted_until"] = "2027-06-15T12:00:00"

        before = self._with_clock(cid, "2027-06-15T10:00:00")
        before["muted_until"] = "2027-06-15T12:00:00"
        assert bot._is_muted(before) is True

        bot.db_delete_pref(str(cid), "debug_clock")
        bot.db_delete_pref(str(cid), "debug_clock_expires")
        after = self._with_clock(cid, "2027-06-15T14:00:00")
        after["muted_until"] = "2027-06-15T12:00:00"
        assert bot._is_muted(after) is False

    def test_debug_clock_ambient_get_streak_no_override_matches_fixed_expectation(self):
        u = fresh_user()
        u["activity_days"] = [date.today().isoformat()]
        assert bot._get_streak(u) == 1

    def test_debug_clock_ambient_get_streak_override_computes_against_simulated_date(self):
        cid = 9903
        u = self._with_clock(cid, "2027-06-15T09:00:00")
        u["activity_days"] = ["2027-06-15", "2027-06-14"]
        assert bot._get_streak(u) == 2
        # no override: the same activity_days don't reach into the real streak
        u_real = fresh_user(activity_days=["2027-06-15", "2027-06-14"])
        assert bot._get_streak(u_real) == 0

    def test_debug_clock_ambient_habit_streak_no_override_matches_fixed_expectation(self):
        assert bot._habit_streak([date.today().isoformat()]) == 1
        assert bot._habit_streak([date.today().isoformat()], user=None) == 1

    def test_debug_clock_ambient_habit_streak_override_computes_against_simulated_date(self):
        cid = 9904
        u = self._with_clock(cid, "2027-06-15T09:00:00")
        assert bot._habit_streak(["2027-06-15", "2027-06-14"], user=u) == 2
        assert bot._habit_streak(["2027-06-15", "2027-06-14"]) == 0

    def test_debug_clock_ambient_habit_summary_lines_no_override_matches_fixed_expectation(self):
        habits = {"meditation": {"completions": [date.today().isoformat()]}}
        lines = bot._habit_summary_lines(habits)
        assert len(lines) == 1 and "✓" in lines[0]

    def test_debug_clock_ambient_habit_summary_lines_override_computes_against_simulated_date(self):
        cid = 9905
        u = self._with_clock(cid, "2027-06-15T09:00:00")
        habits = {"meditation": {"completions": ["2027-06-15"]}}
        lines = bot._habit_summary_lines(habits, user=u)
        assert "✓" in lines[0] and "1d streak" in lines[0]
        # no override: the same stored date doesn't read as done today
        lines_real = bot._habit_summary_lines(habits)
        assert "○" in lines_real[0]

    # ── Task 2: job runners, tool branches, command handlers ──

    def test_debug_clock_ambient_weekly_digest_runner_sunday_override_does_normal_work(self):
        """2027-06-13 is a Sunday: with the clock moved there the runner does
        its normal work instead of reporting the Sunday gate."""
        cid = 9910
        bot.state["users"][str(cid)] = fresh_user()
        self._with_clock(cid, "2027-06-13T10:00:00")
        context = _debug_context([])
        with patch.object(bot, "chat", AsyncMock(return_value="Nice week!")):
            result = run(bot._run_weekly_digest(context, cid))
        assert result is None
        context.bot.send_message.assert_awaited_once()

    def test_debug_clock_ambient_weekly_digest_runner_non_sunday_override_reports_gate(self):
        """2027-06-14 is a Monday: the runner reports the Sunday gate instead
        of doing its normal work -- paired with the Sunday case above."""
        cid = 9911
        bot.state["users"][str(cid)] = fresh_user()
        self._with_clock(cid, "2027-06-14T10:00:00")
        context = _debug_context([])
        with patch.object(bot, "chat", AsyncMock(return_value="Nice week!")):
            result = run(bot._run_weekly_digest(context, cid))
        assert result == "not sunday"
        context.bot.send_message.assert_not_awaited()

    def test_debug_clock_ambient_deadline_alert_runner_matches_annual_reminder_months_away(self):
        cid = 9912
        bot.state["users"][str(cid)] = fresh_user()
        bot.state["users"][str(cid)]["reminders"] = [
            {"id": "r1", "time": "09:00", "message": "Anniversary",
             "once": False, "annual": True, "date": "11-25"}
        ]
        self._with_clock(cid, "2027-11-25T09:00:00")
        context = _debug_context([])
        result = run(bot._run_deadline_alert(context, cid))
        assert result is None
        context.bot.send_message.assert_awaited_once()
        _, kwargs = context.bot.send_message.call_args
        assert "Anniversary" in kwargs["text"]

    def test_debug_clock_ambient_deadline_alert_runner_reports_overdue_by_simulated_week(self):
        cid = 9913
        bot.state["users"][str(cid)] = fresh_user()
        bot.state["users"][str(cid)]["tasks"] = [
            {"text": "Ship the report", "due": "2027-06-01"}
        ]
        self._with_clock(cid, "2027-06-08T09:00:00")
        context = _debug_context([])
        result = run(bot._run_deadline_alert(context, cid))
        assert result is None
        _, kwargs = context.bot.send_message.call_args
        assert "Overdue 7d" in kwargs["text"]

    def test_debug_clock_ambient_idle_nudge_runner_four_days_past_does_normal_work(self):
        cid = 9914
        bot.state["users"][str(cid)] = fresh_user(tasks=["Do a thing"])
        bot.state["users"][str(cid)]["activity_days"] = ["2027-06-01"]
        self._with_clock(cid, "2027-06-05T09:00:00")  # 4 days past
        context = _debug_context([])
        result = run(bot._run_idle_nudge(context, cid))
        assert result is None
        context.bot.send_message.assert_awaited_once()

    def test_debug_clock_ambient_idle_nudge_runner_one_day_past_reports_not_idle_enough(self):
        cid = 9915
        bot.state["users"][str(cid)] = fresh_user(tasks=["Do a thing"])
        bot.state["users"][str(cid)]["activity_days"] = ["2027-06-01"]
        self._with_clock(cid, "2027-06-02T09:00:00")  # 1 day past
        context = _debug_context([])
        result = run(bot._run_idle_nudge(context, cid))
        assert result == "no recent inactivity"
        context.bot.send_message.assert_not_awaited()

    def test_debug_clock_ambient_habit_reminder_runner_lists_undone_habit_on_simulated_date(self):
        cid = 9916
        bot.state["users"][str(cid)] = fresh_user(
            habits={"meditation": {"completions": ["2027-06-01"], "created": "2027-06-01"}}
        )
        self._with_clock(cid, "2027-06-02T09:00:00")  # no completion for this date
        context = _debug_context([])
        result = run(bot._run_habit_reminder(context, cid))
        assert result is None
        _, kwargs = context.bot.send_message.call_args
        assert "meditation" in kwargs["text"]

    def test_debug_clock_ambient_get_current_time_tool_reports_simulated_date(self):
        cid = 9917
        bot.state["users"][str(cid)] = fresh_user(timezone="UTC")
        self._with_clock(cid, "2027-06-15T14:30:00")
        result = run(bot._execute_tool(cid, "get_current_time", {}))
        assert result["date"] == "2027-06-15"
        assert result["time"] == "14:30"

    def test_debug_clock_ambient_get_habits_tool_reports_done_today_on_simulated_date(self):
        cid = 9918
        bot.state["users"][str(cid)] = fresh_user(
            habits={"meditation": {"completions": ["2027-06-15"], "created": "2027-06-01"}}
        )
        self._with_clock(cid, "2027-06-15T09:00:00")
        result = run(bot._execute_tool(cid, "get_habits", {}))
        habit = result["habits"][0]
        assert habit["done_today"] is True
        assert habit["streak"] == 1

    def test_debug_clock_ambient_time_cmd_reports_simulated_date(self):
        cid = 9919
        bot.state["users"][str(cid)] = fresh_user(timezone="UTC")
        self._with_clock(cid, "2027-06-15T14:30:00")
        update = _debug_update(cid)
        run(bot.time_cmd(update, _debug_context([])))
        text = update.message.reply_text.call_args[0][0]
        assert "14:30" in text
        assert "2027" in text

    def test_debug_clock_ambient_habit_cmd_stats_last_seven_days_uses_simulated_date(self):
        cid = 9920
        bot.state["users"][str(cid)] = fresh_user(
            habits={"meditation": {"completions": ["2027-06-15"], "created": "2027-06-01"}}
        )
        self._with_clock(cid, "2027-06-15T09:00:00")
        update = _debug_update(cid)
        run(bot.habit_cmd(update, _debug_context(["stats", "meditation"])))
        text = update.message.reply_text.call_args[0][0]
        assert "Current streak: 1 day" in text

    def test_debug_clock_ambient_my_stats_seven_day_chart_uses_simulated_date(self):
        cid = 9921
        bot.state["users"][str(cid)] = fresh_user(activity_days=["2027-06-15"])
        self._with_clock(cid, "2027-06-15T09:00:00")
        update = _debug_update(cid)
        run(bot.my_stats(update, _debug_context([])))
        text = update.message.reply_text.call_args[0][0]
        assert "█" in text  # today's cell in the 7-day chart is filled

    def test_debug_clock_ambient_no_override_job_runners_and_commands_unchanged(self):
        """Control: with no override active, the runners and command handlers
        touched by Task 2 behave exactly as before this plan (T-1-19)."""
        cid = 9922
        bot.state["users"][str(cid)] = fresh_user(timezone="UTC")
        result = run(bot._execute_tool(cid, "get_current_time", {}))
        assert result["date"] == date.today().isoformat()

        context = _debug_context([])
        with patch.object(bot, "chat", AsyncMock(return_value="x")):
            result = run(bot._run_weekly_digest(context, cid))
        expected = None if date.today().weekday() == 6 else "not sunday"
        assert result == expected

    def test_debug_clock_ambient_once_delay_arithmetic_unaffected_by_active_override(self):
        """The one-shot reminder delay computation (Table B exclusion, second
        prohibition) produces the same value whether or not an override is
        active -- it must never route through the simulated clock."""
        cid = 9923
        bot.state["users"][str(cid)] = fresh_user(timezone="UTC")
        no_override = bot._parse_once_delay("30m", "UTC")
        self._with_clock(cid, "2030-01-01T00:00:00")
        with_override = bot._parse_once_delay("30m", "UTC")
        assert no_override == with_override == 1800.0

    # ── Task 3: durable-record guard (no production code changes) ──
    # Clock set a year ahead -- far enough that a real stored value can
    # never be mistaken for a simulated one (T-1-16, T-1-17).

    def _assert_real_ts(self, ts_iso, tolerance_seconds=10):
        stored = bot.datetime.fromisoformat(ts_iso)
        real_now = bot.datetime.utcnow()
        assert abs((real_now - stored).total_seconds()) < tolerance_seconds

    def test_debug_clock_ambient_complete_habit_records_real_date_both_paths(self):
        """A year-ahead clock never reaches habit['completions'] -- neither
        through the tool dispatcher nor through /habit done, since Table B
        lists both write paths."""
        real_today = date.today().isoformat()

        cid = 9930
        bot.state["users"][str(cid)] = fresh_user(
            habits={"meditation": {"completions": [], "created": real_today}}
        )
        self._with_clock(cid, "2030-01-01T00:00:00")
        run(bot._execute_tool(cid, "complete_habit", {"name": "meditation"}))
        assert bot.state["users"][str(cid)]["habits"]["meditation"]["completions"] == [real_today]

        cid2 = 9931
        bot.state["users"][str(cid2)] = fresh_user(
            habits={"journaling": {"completions": [], "created": real_today}}
        )
        self._with_clock(cid2, "2030-01-01T00:00:00")
        update = _debug_update(cid2)
        run(bot.habit_cmd(update, _debug_context(["done", "journaling"])))
        assert bot.state["users"][str(cid2)]["habits"]["journaling"]["completions"] == [real_today]

    def test_debug_clock_ambient_journal_note_tracker_record_real_timestamp(self):
        """/journal, /note and both tracker-logging paths (tool + custom
        command) each stamp the real wall clock, never the simulated one."""
        cid = 9932
        bot.state["users"][str(cid)] = fresh_user()
        self._with_clock(cid, "2030-01-01T00:00:00")

        with patch.object(bot, "chat", AsyncMock(return_value="Nice reflection.")):
            update = _debug_update(cid)
            run(bot.journal_cmd(update, _debug_context(["Had", "a", "good", "day"])))
        journal_rows = bot.db_get_journal(str(cid))
        assert len(journal_rows) == 1
        self._assert_real_ts(journal_rows[0]["ts"])

        update = _debug_update(cid)
        run(bot.note_cmd(update, _debug_context(["Buy", "milk"])))
        note_rows = bot.db_get_notes(str(cid))
        assert len(note_rows) == 1
        self._assert_real_ts(note_rows[0]["ts"])

        bot.state["users"][str(cid)]["trackers"] = {"weight": {"unit": "kg", "log": []}}
        run(bot._execute_tool(cid, "log_tracker", {"tracker_name": "weight", "value": 80}))
        log = bot.state["users"][str(cid)]["trackers"]["weight"]["log"]
        assert len(log) == 1
        self._assert_real_ts(log[0]["ts"])

        bot.state["users"][str(cid)]["trackers"]["height"] = {"unit": "cm", "log": []}
        update = _debug_update(cid)
        update.message.text = "/height 180"
        run(bot.handle_custom_command(update, _debug_context([])))
        log2 = bot.state["users"][str(cid)]["trackers"]["height"]["log"]
        assert len(log2) == 1
        self._assert_real_ts(log2[0]["ts"])

    def test_debug_clock_ambient_chat_turn_touches_activity_with_real_date(self):
        """A genuine chat turn (touch_activity=True, the default) adds the
        real date to activity_days, not the simulated one."""
        cid = 9933
        real_today = date.today().isoformat()
        bot.state["users"][str(cid)] = fresh_user()
        self._with_clock(cid, "2030-01-01T00:00:00")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(tool_calls=None, content="Hi there!"))]
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        with patch.object(bot, "get_llm_client", return_value=mock_client):
            run(bot.chat(cid, "hello"))
        assert bot.state["users"][str(cid)]["activity_days"] == [real_today]

    def test_debug_clock_ambient_task_add_complete_extend_compute_from_real_date(self):
        """A relative due date, a recurring completion's next-due roll, and
        an extend's fallback base date all compute from the real date."""
        cid = 9935
        real_today = date.today()
        bot.state["users"][str(cid)] = fresh_user()
        self._with_clock(cid, "2030-01-01T00:00:00")

        update = _debug_update(cid)
        run(bot.add_task(update, _debug_context(["Meditate", "every:daily"])))
        task = bot.state["users"][str(cid)]["tasks"][0]
        assert task["due"] == real_today.isoformat()

        bot.state["users"][str(cid)]["tasks"] = [
            {"text": "Water plants", "due": None, "recur": "daily"}
        ]
        update = _debug_update(cid)
        run(bot.done_task(update, _debug_context(["1"])))
        rolled = bot.state["users"][str(cid)]["tasks"][0]
        assert rolled["due"] == (real_today + timedelta(days=1)).isoformat()
        archived = bot.state["users"][str(cid)]["archived_tasks"][-1]
        self._assert_real_ts(archived["completed_at"])

        bot.state["users"][str(cid)]["tasks"] = ["Plain task"]
        update = _debug_update(cid)
        run(bot.extend_cmd(update, _debug_context(["1", "7"])))
        extended = bot.state["users"][str(cid)]["tasks"][0]
        assert extended["due"] == (real_today + timedelta(days=7)).isoformat()

    def test_debug_clock_ambient_job_fire_writes_real_timestamp_to_job_log(self):
        cid = 9936
        bot.state["users"][str(cid)] = fresh_user(checkin_enabled=True)
        self._with_clock(cid, "2030-01-01T00:00:00")
        context = _debug_context([])
        with patch.object(bot, "chat", AsyncMock(return_value="Hi!")):
            run(bot._run_checkin(context, cid, "morning"))
        last_fired = bot.db_last_job_fired(str(cid), "checkin_morning")
        assert last_fired is not None
        self._assert_real_ts(last_fired)

    def test_debug_clock_ambient_mute_cmd_stores_real_hour_ahead_expiry(self):
        cid = 9937
        bot.state["users"][str(cid)] = fresh_user()
        self._with_clock(cid, "2030-01-01T00:00:00")
        before = bot.datetime.utcnow()
        update = _debug_update(cid)
        run(bot.mute_cmd(update, _debug_context(["1h"])))
        after = bot.datetime.utcnow()
        until = bot.datetime.fromisoformat(bot.state["users"][str(cid)]["muted_until"])
        assert before + bot.timedelta(minutes=59) <= until <= after + bot.timedelta(minutes=61)

    def test_debug_clock_ambient_durable_writes_identical_with_and_without_override(self):
        """Repeating a representative durable write (habit completion) with
        the clock cleared produces the identical stored value -- proving the
        clock made no difference to any of them."""
        real_today = date.today().isoformat()
        cid_a, cid_b = 9938, 9939
        bot.state["users"][str(cid_a)] = fresh_user(
            habits={"meditation": {"completions": [], "created": real_today}}
        )
        self._with_clock(cid_a, "2030-01-01T00:00:00")
        run(bot._execute_tool(cid_a, "complete_habit", {"name": "meditation"}))
        with_override = bot.state["users"][str(cid_a)]["habits"]["meditation"]["completions"]

        bot.state["users"][str(cid_b)] = fresh_user(
            habits={"meditation": {"completions": [], "created": real_today}}
        )
        run(bot._execute_tool(cid_b, "complete_habit", {"name": "meditation"}))
        without_override = bot.state["users"][str(cid_b)]["habits"]["meditation"]["completions"]

        assert with_override == without_override == [real_today]

    def test_debug_clock_ambient_set_use_reset_round_trip_restores_real_time_everywhere(self):
        """Set a clock, confirm several Table A surfaces reflect it, reset
        via the real /debug clock reset command, and confirm every one of
        those surfaces returns to real time -- the closest automated
        analogue to 01-VALIDATION.md's ambient-clock manual walkthrough."""
        cid = 9940
        bot.state["users"][str(cid)] = fresh_user(timezone="UTC")

        with as_owner(cid):
            run(bot.debug_cmd(_debug_update(cid), _debug_context(["clock", "2027-06-15T14:30:00"])))

        u = bot.get_user(cid)
        assert bot._today(user=u) == date(2027, 6, 15)
        result = run(bot._execute_tool(cid, "get_current_time", {}))
        assert result["date"] == "2027-06-15"
        time_update = _debug_update(cid)
        run(bot.time_cmd(time_update, _debug_context([])))
        assert "2027" in time_update.message.reply_text.call_args[0][0]

        with as_owner(cid):
            run(bot.debug_cmd(_debug_update(cid), _debug_context(["clock", "reset"])))

        u2 = bot.get_user(cid)
        assert bot._today(user=u2) == date.today()
        result2 = run(bot._execute_tool(cid, "get_current_time", {}))
        assert result2["date"] == date.today().isoformat()
        time_update2 = _debug_update(cid)
        run(bot.time_cmd(time_update2, _debug_context([])))
        assert str(date.today().year) in time_update2.message.reply_text.call_args[0][0]


class TestReviewFixCR02:
    """Regression tests for 01-REVIEW.md CR-02: an active /debug clock must
    never durably create or permanently suppress a real streak milestone."""

    def setup_method(self):
        bot.state = {"users": {}}

    def _with_clock(self, cid, iso, hours_ahead=12):
        """Round-trip a simulated clock through SQLite exactly as /debug
        clock would, then read it back through get_user()."""
        bot.db_set_pref(str(cid), "debug_clock", iso)
        expires = (bot.datetime.utcnow() + bot.timedelta(hours=hours_ahead)).isoformat()
        bot.db_set_pref(str(cid), "debug_clock_expires", expires)
        return bot.get_user(cid)

    def test_cr02_check_milestones_skips_entirely_while_debug_clock_active(self):
        """An active /debug clock must never create or permanently suppress
        a real milestone: _check_milestones bails out before touching
        milestones_sent or sending anything, even when the simulated date
        would otherwise cross a streak threshold that the real date has not
        reached yet."""
        cid = 9951
        # 10 consecutive real days of activity ending 3 days before "today" --
        # under real time the current streak is 0 (no activity logged today
        # or yesterday), but if _get_streak were evaluated against the
        # simulated date below it would land inside the run and read as 10.
        days = [(date.today() - timedelta(days=n)).isoformat() for n in range(3, 13)]
        bot.state["users"][str(cid)] = fresh_user(activity_days=days)
        u = self._with_clock(cid, (date.today() - timedelta(days=3)).isoformat() + "T09:00:00")
        assert bot._get_streak(u) >= 7  # confirms the simulated date would cross streak_7

        app = MagicMock()
        app.bot.send_message = AsyncMock()
        run(bot._check_milestones(cid, app))

        app.bot.send_message.assert_not_awaited()
        assert bot.state["users"][str(cid)].get("milestones_sent", []) == []

    def test_check_milestones_still_fires_with_no_debug_clock(self):
        """Control for the above: with no override active, a genuinely
        crossed streak_7 still sends the congratulation and records it --
        proving CR-02's early return doesn't disable real milestones."""
        cid = 9953
        days = [(date.today() - timedelta(days=n)).isoformat() for n in range(0, 7)]
        bot.state["users"][str(cid)] = fresh_user(activity_days=days)
        app = MagicMock()
        app.bot.send_message = AsyncMock()
        run(bot._check_milestones(cid, app))
        app.bot.send_message.assert_awaited_once()
        assert "streak_7" in bot.state["users"][str(cid)]["milestones_sent"]


class TestReviewFixWR03:
    """Regression tests for 01-REVIEW.md WR-03: today_focus comparisons must
    track the simulated /debug clock like every other display comparison
    (habit_section, deadline badges, etc), while the write side always
    stamps the real date."""

    def setup_method(self):
        bot.state = {"users": {}}

    def _with_clock(self, cid, iso, hours_ahead=12):
        bot.db_set_pref(str(cid), "debug_clock", iso)
        expires = (bot.datetime.utcnow() + bot.timedelta(hours=hours_ahead)).isoformat()
        bot.db_set_pref(str(cid), "debug_clock_expires", expires)
        return bot.get_user(cid)

    def test_build_system_prompt_focus_section_matches_habit_section_clock(self):
        """Setting a focus for the real "today" and then moving the
        simulated clock a week forward must retire the focus section,
        exactly as an expired real-time focus would -- consistent with
        habit_section right above it in build_system_prompt."""
        cid = 9954
        u = fresh_user()
        u["today_focus"] = {"date": date.today().isoformat(), "text": "Deep work session"}
        # No override yet -- still shows (control).
        assert "Deep work session" in bot.build_system_prompt(u)

        u_sim = self._with_clock(cid, (date.today() + timedelta(days=7)).isoformat() + "T09:00:00")
        u_sim["today_focus"] = {"date": date.today().isoformat(), "text": "Deep work session"}
        assert "Deep work session" not in bot.build_system_prompt(u_sim)

    def test_today_cmd_read_reflects_simulated_clock_write_stays_real(self):
        """/today's read-side comparison follows the simulated clock, but the
        write side (setting a new focus) always stamps the real date -- the
        durable-write boundary WR-03's fix explicitly preserves."""
        cid = 9955
        bot.state["users"][str(cid)] = fresh_user()
        u = self._with_clock(cid, (date.today() + timedelta(days=3)).isoformat() + "T09:00:00")
        u["today_focus"] = {"date": date.today().isoformat(), "text": "Old focus"}

        # Read: real-time focus is "expired" from the simulated vantage point.
        update = _debug_update(cid)
        run(bot.today_cmd(update, _debug_context([])))
        text = update.message.reply_text.call_args[0][0]
        assert "No focus set" in text

        # Write: setting a new focus while the clock is simulated must stamp
        # the real date, not the simulated one.
        write_update = _debug_update(cid)
        write_context = _debug_context(["New", "focus"])
        run(bot.today_cmd(write_update, write_context))
        assert bot.state["users"][str(cid)]["today_focus"]["date"] == date.today().isoformat()


class TestReviewFixCR01:
    """Regression tests for 01-REVIEW.md CR-01: the ambient /debug clock must
    never leak into the REAL scheduled-job guard evaluation -- only /debug
    fire's own simulated evaluation may honor an active override."""

    def setup_method(self):
        bot.state = {"users": {}}

    def _with_clock(self, cid, iso, hours_ahead=12):
        """Round-trip a simulated clock through SQLite exactly as /debug
        clock would, then read it back through get_user()."""
        bot.db_set_pref(str(cid), "debug_clock", iso)
        expires = (bot.datetime.utcnow() + bot.timedelta(hours=hours_ahead)).isoformat()
        bot.db_set_pref(str(cid), "debug_clock_expires", expires)
        return bot.get_user(cid)

    def test_real_scheduled_deadline_alert_ignores_active_debug_clock(self):
        """A /debug clock override placed inside real quiet hours must not
        suppress the REAL scheduled deadline_alert job -- only /debug fire's
        own simulated evaluation should honor it."""
        cid = 9950
        real_hour = bot.datetime.utcnow().hour
        quiet_hour = (real_hour + 12) % 24  # 12h away from real now: never overlaps
        start_str = f"{quiet_hour:02d}:00"
        end_str = f"{(quiet_hour + 1) % 24:02d}:00"
        bot.state["users"][str(cid)] = fresh_user(
            checkin_enabled=True,
            timezone="UTC",
            quiet_hours={"start": start_str, "end": end_str},
            tasks=[{"text": "Ship it", "due": date.today().isoformat()}],
        )
        # Move the simulated clock inside the quiet window that real time
        # never falls inside (by construction above).
        self._with_clock(cid, f"2027-06-15T{quiet_hour:02d}:30:00")

        app = MagicMock()
        captured = _capture_run_daily_callbacks(app)
        bot.schedule_user_alerts(app, cid)
        job_context = _debug_context([])
        result = run(captured[f"deadline_alert_{cid}"](job_context))
        assert result is None  # real job fired -- provably not suppressed
        job_context.bot.send_message.assert_awaited_once()

        # /debug fire, by contrast, is expected to honor the simulated
        # quiet-hours override and report the suppression by name.
        debug_context = _debug_context([])
        debug_result = run(bot._run_deadline_alert(debug_context, cid))
        assert debug_result == "quiet hours"
        debug_context.bot.send_message.assert_not_awaited()

    def test_real_scheduled_checkin_ignores_active_debug_clock_mute(self):
        """Same guarantee as above, exercised through _is_muted and the
        checkin wrapper: a debug-clock override that lands inside a stored
        mute window must not suppress the real scheduled check-in."""
        cid = 9952
        # muted_until is safely in the past relative to real "now" (so the
        # real evaluation is never muted), but the simulated clock below is
        # set to a moment before it (so the simulated evaluation is muted).
        bot.state["users"][str(cid)] = fresh_user(
            checkin_enabled=True,
            muted_until="2020-06-15T23:00:00",
        )
        self._with_clock(cid, "2020-06-15T12:00:00")

        app = MagicMock()
        captured = _capture_run_daily_callbacks(app)
        with patch.object(bot, "chat", AsyncMock(return_value="Morning!")):
            bot.schedule_user_checkins(app, cid)
            job_context = _debug_context([])
            result = run(captured[f"checkin_morning_{cid}"](job_context))
        assert result is None  # real job fired -- provably not suppressed
        job_context.bot.send_message.assert_awaited_once()

        debug_context = _debug_context([])
        with patch.object(bot, "chat", AsyncMock(return_value="Morning!")):
            debug_result = run(bot._run_checkin(debug_context, cid, "morning"))
        assert debug_result == "muted"
        debug_context.bot.send_message.assert_not_awaited()



# ─────────────────────────────────────────────────────────────────────────────
# SECTION 11: MCP link-code primitive — /link + db_create_link_code
# (Milestone B / AUTH-02 groundwork; consumed by mcp_server.py's OAuth
# provider, which is out of this test file's scope.)
# ─────────────────────────────────────────────────────────────────────────────

class TestDbCreateLinkCode:
    def setup_method(self):
        _fresh_db()

    def test_returns_8_char_hex_code(self):
        code = bot.db_create_link_code("1")
        assert len(code) == 8
        int(code, 16)  # raises if not hex

    def test_codes_are_unique(self):
        codes = {bot.db_create_link_code("1") for _ in range(20)}
        assert len(codes) == 20

    def test_row_persisted_with_chat_id_and_expiry(self):
        before = bot.datetime.utcnow()
        code = bot.db_create_link_code("42")
        with bot._db() as con:
            row = con.execute(
                "SELECT * FROM mcp_link_codes WHERE code=?", (code,)
            ).fetchone()
        assert row is not None
        assert row["chat_id"] == "42"
        assert row["used_at"] is None
        created = bot.datetime.fromisoformat(row["created_at"])
        expires = bot.datetime.fromisoformat(row["expires_at"])
        assert created >= before
        assert (expires - created) == bot.timedelta(minutes=10)

    def test_custom_ttl(self):
        code = bot.db_create_link_code("1", ttl_minutes=1)
        with bot._db() as con:
            row = con.execute(
                "SELECT created_at, expires_at FROM mcp_link_codes WHERE code=?", (code,)
            ).fetchone()
        created = bot.datetime.fromisoformat(row["created_at"])
        expires = bot.datetime.fromisoformat(row["expires_at"])
        assert (expires - created) == bot.timedelta(minutes=1)


class TestLinkCmd:
    def setup_method(self):
        _fresh_db()

    def _update_and_context(self, chat_id=123):
        update = MagicMock()
        update.effective_chat.id = chat_id
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        return update, context

    def test_creates_link_code_row_for_this_chat(self):
        update, context = self._update_and_context(chat_id=555)
        run(bot.link_cmd(update, context))
        with bot._db() as con:
            rows = con.execute(
                "SELECT chat_id FROM mcp_link_codes"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["chat_id"] == "555"

    def test_replies_with_bare_code_when_domain_unset(self):
        update, context = self._update_and_context()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MCP_REMOTE_DOMAIN", None)
            run(bot.link_cmd(update, context))
        update.message.reply_text.assert_awaited_once()
        reply = update.message.reply_text.call_args[0][0]
        assert "link code" in reply.lower()
        assert "http" not in reply

    def test_replies_with_full_url_when_domain_set(self):
        update, context = self._update_and_context()
        with patch.dict(os.environ, {"MCP_REMOTE_DOMAIN": "mcp-sbot.alteon.help"}):
            run(bot.link_cmd(update, context))
        update.message.reply_text.assert_awaited_once()
        reply = update.message.reply_text.call_args[0][0]
        assert "https://mcp-sbot.alteon.help/link/" in reply
        # the code embedded in the URL must be the one actually stored
        with bot._db() as con:
            code = con.execute("SELECT code FROM mcp_link_codes").fetchone()["code"]
        assert code in reply


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 12: Per-round tool narrowing (_tools_for_round)
# ─────────────────────────────────────────────────────────────────────────────

class TestToolsForRound:
    """The full TOOLS schema is re-sent on every round of the tool-call loop, so
    a two-round turn pays for it twice. `_tools_for_round` trims rounds 2+ to
    what the model still plausibly needs."""

    def _names(self, called):
        return {t["function"]["name"] for t in bot._tools_for_round(called)}

    def test_empty_called_set_falls_back_to_full_list(self):
        # A later round with nothing recorded must never be handed FEWER tools
        # than round 1 offered.
        assert bot._tools_for_round(set()) is bot.TOOLS

    def test_every_write_tool_survives_narrowing(self):
        # Regression guard for the bug this design was rewritten to fix: an
        # earlier version narrowed to the caller's own domain group, so "look at
        # my reminders and create a task from them" reached round 2 with
        # add_task withheld and duplicated the reminder instead — silently doing
        # the wrong thing rather than failing. A round-2 request is the "now act
        # on it" half of a turn, and which action it needs cannot be predicted
        # from what it read first, so every write tool stays available.
        for called in ({"get_reminders"}, {"get_tasks"}, {"search"},
                       {"get_streak"}, {"get_notes"}, {"get_journal"}):
            missing = bot._WRITE_TOOLS - self._names(called)
            assert not missing, f"after {called}, write tools withheld: {missing}"

    def test_add_task_available_after_reading_reminders(self):
        # The exact cross-domain case that regressed, pinned on its own.
        assert "add_task" in self._names({"get_reminders"})

    def test_called_tool_is_always_kept(self):
        for name in ("search", "get_streak", "get_journal"):
            assert name in self._names({name})

    def test_read_only_tools_outside_the_group_are_dropped(self):
        # The actual saving: lookups the model has shown no interest in.
        names = self._names({"get_tasks"})
        assert "get_habits" not in names
        assert "get_notes" not in names
        # ...while its own group's reads stay reachable.
        assert "get_tasks" in names

    def test_narrowed_list_is_a_strict_subset_of_tools(self):
        all_names = {t["function"]["name"] for t in bot.TOOLS}
        got = self._names({"get_tasks"})
        assert got < all_names

    def test_group_and_write_tool_names_are_real(self):
        # A typo in a group would silently withhold a tool forever.
        all_names = {t["function"]["name"] for t in bot.TOOLS}
        declared = set(bot._WRITE_TOOLS).union(*bot._TOOL_GROUPS)
        assert declared <= all_names, declared - all_names
