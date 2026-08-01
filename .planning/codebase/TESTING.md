# Testing Patterns

**Analysis Date:** 2026-08-01

## Test Framework

**Runner:**
- pytest 7.x+ (installed via requirements.txt indirectly via python-telegram-bot)
- pytest-asyncio for async test support

**Assertion Library:**
- Python's built-in `assert` statements with pytest's enhanced output

**Configuration:**
- No explicit pytest configuration file (`pytest.ini`, `pyproject.toml`, or `setup.cfg`)
- Tests are auto-discovered from `tests/test_bot.py`

**Run Commands:**
```bash
# All tests
export $(grep -v '^#' env | xargs) && python -m pytest tests/test_bot.py -v

# Unit tests only (pure logic, no LLM)
python -m pytest tests/test_bot.py -v -k "not sanity and not nl"

# LLM sanity check (one real API call)
export $(grep -v '^#' env | xargs) && python -m pytest tests/test_bot.py -v -k sanity

# NL tool-use tests (real API calls)
export $(grep -v '^#' env | xargs) && python -m pytest tests/test_bot.py -v -k nl
```

## Test File Organization

**Location:**
- `tests/test_bot.py` — single test file containing all test sections

**Test Structure:**
- Organized into 9 logical sections marked with comment headers
- Each section focuses on a specific area of functionality
- Test classes group related test methods

## Test Sections

**SECTION 1: Unit Tests (pure logic, no LLM)**
- Classes: `TestNormalizeTimezone`, `TestTaskHelpers`, `TestGetUser`, `TestGetLlmClient`, `TestGetModel`, `TestBuildSystemPrompt`, `TestRateLimit`, `TestStreakLogic`, `TestExecuteToolUnit`, `TestQuietHours`, `TestIsRateLimit`
- No API calls; all external dependencies mocked

**SECTION 2: LLM Sanity Tests (real API call)**
- Class: `TestLlmSanity` with `@pytest.mark.sanity`
- Single real API call to verify model is alive and coherent
- Skipped if `OPENAI_API_KEY` not set
- Tests:
  - `test_model_responds()` — model returns non-empty string
  - `test_model_is_coherent()` — model includes expected word in response
  - `test_model_respects_language_instruction()` — language instruction is applied
  - `test_error_on_bad_key()` — bad API key returns user-friendly error

**SECTION 3: Natural Language Tool-Use Tests (real API)**
- Class: `TestNaturalLanguageToolUse` with `@pytest.mark.nl`
- Tests that LLM correctly selects and invokes the right tool for natural-language requests
- Uses `ToolCallCapture` context manager to record tool calls without mocking
- Tests: "What time is it?", "Add a task", "Remind me in 10 minutes", "I weighed 74kg", "Finished task 1", "What are my tasks?", "Journal entry", etc.

**SECTION 4: SQLite Memory Store**
- Classes: `TestSQLiteNotes`, `TestSQLiteJournal`
- Tests database operations for notes and journal entries
- Tests isolation between users, ordering, removal, search

**SECTION 5: New _execute_tool Coverage**
- Class: `TestNewExecuteTools`
- Tests individual tool implementations (remove_task, get_trackers, create_tracker, get_habits, add_habit, complete_habit, remove_habit, get_notes, add_note, get_reminders, remove_reminder, set_today_focus, search, get_streak, save_memory, get_journal)

**SECTION 6: Habit and Mute Helpers**
- Classes: `TestHabitStreak`, `TestMuteLogic`
- Tests streak calculation logic and mute state

**SECTION 7: Parse Helpers**
- Classes: `TestParseOnceDelay`, `TestParseLocalTime`, `TestTasksForPrompt`, `TestHabitSummaryLines`
- Tests parsing and formatting helper functions

**SECTION 8: System Prompt with Database**
- Class: `TestSystemPromptWithDB`
- Tests that system prompt correctly includes SQLite-backed data (notes, journal)

**SECTION 9: New NL Tool-Use Tests (real API)**
- Class: `TestNaturalLanguageNewTools` with `@pytest.mark.nl`
- NL tests for newer tools: add_habit, complete_habit, create_tracker, set_today_focus, auto_save_memory, search, get_streak

## Test Structure

**Suite Organization:**
```python
class TestClassName:
    def setup_method(self):
        # Reset state before each test
        bot.state = {"users": {}}
        bot._app = None
        _fresh_db()

    def test_descriptive_name(self):
        # Arrange
        bot.state["users"]["1"] = fresh_user()
        
        # Act
        result = run(bot._execute_tool(1, "add_task", {"text": "Go running"}))
        
        # Assert
        assert result["success"] is True
```

**Patterns:**

### Setup and Isolation
```python
@pytest.fixture(autouse=True)
def isolate_db():
    """Each test gets a fresh DB to prevent test pollution."""
    _fresh_db()
    yield

def _fresh_db():
    """Create a new temp database file for this test."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    bot.DB_FILE = tmp.name
    bot._init_db()
```

### Mocking
```python
from unittest.mock import AsyncMock, MagicMock, patch

# Example: mock the LLM client
with patch("bot.AsyncOpenAI") as mock_ai:
    mock_ai.return_value = MagicMock()
    client = bot.get_llm_client(u)
    call_kwargs = mock_ai.call_args[1]
    assert call_kwargs["api_key"] == bot.DEFAULT_API_KEY
```

### Async Testing
```python
@pytest.mark.asyncio
async def test_model_responds(self):
    """Async tests use @pytest.mark.asyncio."""
    bot.state["users"]["1"] = fresh_user()
    reply = await bot.chat(1, "Say hello")
    assert isinstance(reply, str)

# Synchronous wrapper for async helpers
def run(coro):
    """Run an async function synchronously (for test helpers)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
```

### Tool Call Capture
```python
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

# Usage
with ToolCallCapture(uid) as cap:
    reply = await bot.chat(uid, "Add a task: visit the doctor today")
tools_called = [name for name, _ in cap.calls]
assert "add_task" in tools_called
```

## Mocking

**Framework:** `unittest.mock` (built-in)

**What to Mock:**
- Telegram-related classes (`Update`, `BotCommand`, `Application`, handlers)
- TimezoneFinder (returns "Europe/London" for all lookups)
- AsyncOpenAI client for API key tests

**What NOT to Mock:**
- Database operations (use real temp SQLite file instead)
- JSON state file operations (use temp file)
- Core bot logic (test with real implementation)

**Mocking Strategy in Test Module:**
```python
# At import time, stub out Telegram + timezonefinder before importing bot.py
for _mod in ["telegram", "telegram.ext", "telegram.ext._application", "openai", "timezonefinder"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

# Patch os.path.exists to hide real state.json at import time
def _patched_exists(path):
    if str(path).endswith("state.json"):
        return False
    return _real_exists(path)

with patch("os.path.exists", side_effect=_patched_exists):
    import bot
```

## Fixtures and Factories

**Test Data:**
```python
def fresh_user(**overrides) -> dict:
    """Return a clean user dict (like a brand-new registration)."""
    u = bot._new_user(**overrides)
    return u

# Usage
bot.state["users"]["10"] = fresh_user(timezone="Europe/London")
bot.state["users"]["30"] = fresh_user(language="Russian")
```

**Database Isolation:**
```python
@pytest.fixture(autouse=True)
def isolate_db():
    """Runs before each test; gives it a fresh temp DB."""
    _fresh_db()
    yield
    # Cleanup happens automatically when temp file goes out of scope
```

## Coverage

**Requirements:**
- No explicit coverage target or configuration
- Coverage not enforced (no CI check)

**View Coverage (manual):**
```bash
python -m pytest tests/test_bot.py --cov=bot --cov-report=html
# Then open htmlcov/index.html
```

**Current coverage gaps (documented in code):**
- Some job scheduling functions are not unit-tested (require APScheduler integration)
- Telegram handler functions not tested (require full APScheduler/Telegram app)

## Test Types

**Unit Tests:**
- Scope: Pure logic functions (helpers, parsers, state management)
- No API calls, no network, no external services
- Dependencies: mocked or stubbed
- Speed: Fast (< 1s total for all unit tests)
- Run command: `pytest -k "not sanity and not nl"`
- Examples: `TestNormalizeTimezone`, `TestTaskHelpers`, `TestRateLimit`, `TestStreakLogic`

**Integration Tests (LLM Sanity):**
- Scope: Single real API call to verify LLM is responsive
- Makes one real call to OpenAI or Groq
- Tests basic coherence (model says HELLO when asked)
- Skipped if API key not available
- Run command: `pytest -k sanity`
- Speed: ~2-5s per test

**End-to-End Tests (NL Tool-Use):**
- Scope: Full natural-language processing; model must understand intent and invoke correct tool
- Makes real API calls for each test
- Verifies tool selection accuracy (not just that model is alive)
- Examples: "Remind me in 10 minutes" → add_reminder tool, "I weighed 74kg" → log_tracker
- Run command: `pytest -k nl`
- Speed: ~3-5s per test (many tests, slow overall)

## Common Patterns

**Async Testing:**
```python
@pytest.mark.asyncio
async def test_chat_returns_string(self):
    bot.state["users"]["1"] = fresh_user()
    reply = await bot.chat(1, "Hello")
    assert isinstance(reply, str) and len(reply) > 0
```

**Error Testing:**
```python
def test_complete_task_out_of_range(self):
    bot.state["users"]["10"] = fresh_user()
    result = run(bot._execute_tool(10, "complete_task", {"task_number": 99}))
    assert "error" in result
```

**Database Testing:**
```python
def test_add_and_get_note(self):
    bot.db_add_note("1", "Buy milk")
    rows = bot.db_get_notes("1")
    assert len(rows) == 1
    assert rows[0]["text"] == "Buy milk"
```

**State Isolation:**
```python
def test_notes_isolated_per_user(self):
    bot.db_add_note("1", "User 1 note")
    bot.db_add_note("2", "User 2 note")
    assert len(bot.db_get_notes("1")) == 1
    assert len(bot.db_get_notes("2")) == 1
    # Users cannot see each other's notes
```

**Tool Implementation Testing:**
```python
def test_add_task_with_due(self):
    bot.state["users"]["10"] = fresh_user()
    run(bot._execute_tool(10, "add_task", {
        "text": "Submit report", 
        "due_date": "2026-07-15"
    }))
    task = bot.state["users"]["10"]["tasks"][0]
    assert task["due"] == "2026-07-15"
```

## Test Dependencies and Assumptions

**Required Environment:**
- `OPENAI_API_KEY` or `GROQ_API_KEY` for sanity/NL tests
- Unit tests work without any API keys
- Pytest automatically discovers and runs tests in `tests/` directory

**Temp File Cleanup:**
- State files (`state.json`) redirected to temp files during import
- Database files created fresh for each test via `isolate_db` fixture
- OS automatically cleans up temp files (set `delete=False` for debugging)

**Test Isolation:**
- Each test gets a fresh database (via autouse fixture)
- Each test sets `bot.state = {"users": {}}` to reset state
- No test can affect another test's data

---

*Testing analysis: 2026-08-01*
