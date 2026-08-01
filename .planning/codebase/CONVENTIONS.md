# Coding Conventions

**Analysis Date:** 2026-08-01

## Naming Patterns

**Files:**
- Python modules use lowercase with underscores: `bot.py`, `mcp_server.py`, `test_bot.py`
- Test files follow pattern: `tests/test_*.py`

**Functions:**
- Public functions: `snake_case` (e.g., `get_user()`, `save_state()`, `build_system_prompt()`)
- Private/internal functions: prefixed with single underscore (e.g., `_new_user()`, `_task_text()`, `_db()`)
- Database helpers: `db_*` prefix for SQLite operations (e.g., `db_add_note()`, `db_get_journal()`, `db_store_key()`)
- Tool executor and handler functions: named descriptively without prefixes (e.g., `chat()`, `_execute_tool()`)

**Variables:**
- Snake_case throughout (e.g., `state`, `user`, `chat_id`, `task_number`, `tracker_name`)
- Constants: UPPERCASE with underscores (e.g., `RATE_LIMIT`, `DEFAULT_MODEL`, `GROQ_BASE_URL`, `STATE_FILE`)
- Private module-level variables: prefixed with underscore (e.g., `_fernet`, `_app`, `_tf`)
- Dictionary keys: lowercase snake_case (e.g., `chat_id`, `api_key`, `is_rate_limited`)

**Types:**
- Type hints used throughout for function parameters and return types
- Union types: `str | None` syntax (Python 3.10+)
- SQLite Row objects: typed as `sqlite3.Row` for database returns

## Code Style

**Formatting:**
- No linter or formatter configured (code relies on developer discipline)
- 4-space indentation throughout
- Line length: not strictly enforced but generally kept reasonable (80-100 chars observed)
- Docstrings: used for functions, describing purpose and key parameters
- Comments: explain **why** not **what**; sparse and meaningful

**Example patterns observed:**

```python
# WHY comment — good
# Atomic write: dump to a temp file in the same dir, then replace.
def save_state(state: dict) -> None:
    d = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".state-", suffix=".tmp")
```

**Linting:**
- Not enforced; no `.flake8`, `pyproject.toml`, or `.pylintrc` present
- Code quality relies on manual review and tests

## Import Organization

**Order:**
1. Standard library (`os`, `json`, `sqlite3`, `asyncio`, `datetime`, etc.)
2. Third-party packages (`openai`, `telegram`, `cryptography`, `mcp`, `timezonefinder`)
3. Internal/local imports (rarely used; single-file architecture in `bot.py`)

**Examples from `bot.py`:**
```python
import os
import re
import sys
import json
import time
import logging
import sqlite3
import uuid
# ... more stdlib

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from cryptography.fernet import Fernet, InvalidToken
from timezonefinder import TimezoneFinder
from openai import AsyncOpenAI
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
```

**Path aliases:**
- None observed; single-file architecture minimizes need for aliases

## Error Handling

**Patterns:**
- Try/except blocks used for expected errors (API calls, date parsing, timezone validation)
- All exceptions logged with `logger.error()` or `logger.warning()`
- User-facing errors: return error dict from tool functions (e.g., `{"error": "..."}`)
- Chat responses: transform exceptions into friendly messages prefixed with ⚠️

**Example from `chat()` function:**
```python
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
```

**Tool function error returns:**
```python
# Tool functions return error dicts, never raise
if not start_str or not end_str:
    return {"error": "quiet_hours not set"}
if task_number < 1 or task_number > len(tasks):
    return {"error": f"Task {task_number} not found."}
```

## Logging

**Framework:** Python's built-in `logging` module

**Setup:**
```python
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
```

**Patterns:**
- `logger.info()`: important state transitions and tool calls
- `logger.warning()`: recoverable issues and configuration problems
- `logger.error()`: unrecoverable issues that affect user experience

**Example:**
```python
logger.info("Tool %s(%s) → %s", tc.function.name, args, result)
logger.error("Milestone message failed for %s: %s", chat_id, e)
logger.warning("MASTER_KEY not set in env — generated a temporary key...")
```

## Comments

**When to Comment:**
- Comment the **why**, not the **what** (code shows what; comments explain purpose)
- Use section headers with dashes: `# ─────────────── rate limiting ───────────────`
- Single-line comments for assumptions and non-obvious logic
- Avoid redundant comments (e.g., don't comment `x = x + 1`)

**Example of good comments:**
```python
# Atomic write: dump to a temp file in the same dir, then replace.
# This ensures state.json is never partially written.

# Only genuine user-initiated turns count toward the activity streak and
# clear a pending check-in — proactive bot-initiated messages (check-ins,
# weekly digest, missed-checkin catch-up) pass touch_activity=False so
# sending a nudge is never mistaken for the user having responded.

# Quiet hours span midnight (23:00–07:00); check if time is quiet.
# If start > end, then now is quiet if now >= start OR now < end
```

**Docstrings:**
- Used for public functions and helpers
- Describe purpose, key parameters, return value
- Single-line docstrings for simple functions

```python
def get_user(chat_id: int) -> dict:
    """Load or initialize a user record, forward-filling missing keys."""
    ...

def _task_text(task) -> str:
    return task["text"] if isinstance(task, dict) else str(task)
    # No docstring needed — purpose is obvious
```

## Function Design

**Size:**
- Functions are generally small and focused on one responsibility
- Longest functions are async task handlers and tool dispatchers (necessary for complexity)
- Helper functions are short (5-20 lines typical)

**Parameters:**
- Use type hints for all parameters and return values
- Avoid large parameter lists; pass dicts when needed
- Named parameters used liberally (e.g., `limit=50`, `include_history=True`)

**Return Values:**
- Functions return dicts from tool handlers (containing success/error/data)
- Async functions return strings (for chat responses) or None (for side effects)
- SQLite functions return list[Row] or Row or bool
- Parse/validation functions return value or None

**Example:**
```python
# Simple, focused helper
def _task_text(task) -> str:
    return task["text"] if isinstance(task, dict) else str(task)

# Tool handler with structured return
def log_tracker(chat_id: int, name: str, value: float) -> dict:
    # ... validation ...
    if success:
        return {"success": True, "logged": value}
    return {"error": "tracker not found"}

# Async chat function
async def chat(chat_id: int, user_message: str, system: str = None, 
               touch_activity: bool = True) -> str:
    # ... tool loop ...
    return reply  # str, never dict
```

## Module Design

**Exports:**
- No explicit `__all__` defined; all public (non-underscore-prefixed) functions are importable
- Convention: underscore prefix indicates internal use only

**Barrel Files:**
- Not used; single-file architecture (`bot.py`)
- `mcp_server.py` is a separate entry point, not imported by `bot.py`

**Structure of `bot.py`:**
1. Imports and logging setup
2. Environment variables and constants
3. Encryption and cryptography helpers
4. SQLite connection and database initialization
5. Database operation helpers (db_* functions grouped by table)
6. State management (load_state, save_state, get_user)
7. Utility functions (task, date, streak helpers)
8. LLM tool definitions (TOOLS list)
9. Tool executor (_execute_tool)
10. Habit/streak/quiet hours helpers
11. LLM helpers (get_llm_client, get_model, build_system_prompt)
12. Main chat loop (chat function)
13. Scheduling and job functions
14. Telegram handlers (command, message, callback handlers)
15. Main entry point (main function)

**Persistence:**
- `state.json`: user prefs, tasks, history, habits, reminders (written atomically)
- `bot_memory.db`: SQLite (WAL mode, thread-safe) for notes, journal, profile/episodic memory, rate-limit log, job-fire log, encrypted API keys, user-prefs overrides

**No Classes:**
- Single-file architecture uses functions and dicts, not classes
- User state: dict with known schema (defined in `_new_user()`)
- No ORM or data models; direct SQLite and JSON manipulation

---

*Convention analysis: 2026-08-01*
