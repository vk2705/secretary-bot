---
phase: 1
slug: debug-and-dry-run
# status lifecycle: draft (seeded by plan-phase) → validated (set by validate-phase §6)
# audit-milestone §5.5 distinguishes NOT-VALIDATED (draft) from PARTIAL (validated + nyquist_compliant: false) (#2117)
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-08-02
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (`@pytest.mark.asyncio` used throughout `tests/test_bot.py`) |
| **Config file** | none — no `pytest.ini`/`pyproject.toml` in repo root |
| **Quick run command** | `python -m pytest tests/test_bot.py -v -k "not sanity and not nl"` |
| **Full suite command** | `python -m pytest tests/test_bot.py -v` |
| **Estimated runtime** | Not measured this session — excludes the `sanity`/`nl` suites, which require live API keys and real LLM calls (per CLAUDE.md's documented invocation) |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_bot.py -v -k "not sanity and not nl"`
- **After every plan wave:** Run `python -m pytest tests/test_bot.py -v -k "not sanity and not nl"` (the `sanity`/`nl` suites are for pre-ship spot checks, not per-commit, per existing project convention)
- **Before `/gsd-verify-work`:** Full non-LLM suite must be green, plus a manual UAT pass against the real bot (owner account) — see Manual-Only Verifications below
- **Max feedback latency:** Not measured this session

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| TBD | TBD | TBD | DEBUG-01 | — | N/A | unit | `pytest tests/test_bot.py -k debug_fire -x` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DEBUG-01 | T-1-01 | Owner gate blocks non-owner `chat_id` on every `/debug` subcommand | unit | `pytest tests/test_bot.py -k debug_owner_gate -x` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DEBUG-02 | — | N/A | unit | `pytest tests/test_bot.py -k test_now_helper -x` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DEBUG-02 | — | N/A | unit | `pytest tests/test_bot.py -k debug_clock_ambient -x` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DEBUG-02 | — | N/A | unit | `pytest tests/test_bot.py -k debug_clock_reset -x` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DEBUG-03 | T-1-02 | No LLM call made when dumping the prompt (`get_llm_client(...).chat.completions.create.assert_not_called()`) | unit | `pytest tests/test_bot.py -k debug_prompt_no_llm_call -x` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DEBUG-03 | — | Prompt >4096 chars delivered as a document, never truncated, never written to a tracked/untracked file path | unit | `pytest tests/test_bot.py -k debug_prompt_long -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*
*Task ID / Plan / Wave columns are TBD until `gsd-planner` assigns tasks — this file is seeded in `draft` status.*

**Threat refs** (from RESEARCH.md Security Domain):
- **T-1-01** — Non-owner Telegram user discovers and invokes `/debug` commands (Elevation of Privilege)
- **T-1-02** — Debug output (prompt dump containing journal/notes/profile facts) leaked via forward or logging (Information Disclosure)
- **T-1-03** — Debug clock silently active for weeks, corrupting the owner's own real deadline/quiet-hours view (Tampering of the owner's own trusted data view) — no dedicated automated test identified this session; covered by the restart/expiry design decision the planner must make, not purely by a unit test

---

## Wave 0 Requirements

- [ ] `tests/test_bot.py` — new fixtures for setting `bot.MY_CHAT_ID` per-test and mocking `context.bot.send_message` / `context.application.job_queue`, needed for DEBUG-01 owner-gate and job-fire tests
- [ ] `tests/test_bot.py` — a `db_set_pref`/`db_get_pref` round-trip test for the new debug-clock pref key, using the existing `isolate_db` autouse fixture (already provides a fresh SQLite temp file per test — no new fixture needed, just a new test)
- [ ] `tests/test_bot.py` — a mock of `get_llm_client()`'s return value (`AsyncMock`) to assert `.chat.completions.create.assert_not_called()`, needed for DEBUG-03's "no LLM call made" criterion

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|--------------------|
| Real job fire produces the actual Telegram message with real side effects | DEBUG-01 | Telegram message delivery and real job-firing side effects (state writes, job_log entries) are only partially mockable; the roadmap success criterion requires the *real* message, not a simulated one | On the real bot (owner account), run `/debug fire morning_checkin` and confirm the message matches what the scheduled job would send, and that `job_log`/state are updated identically to a real scheduled fire |
| Ambient simulated "now" affects ordinary commands | DEBUG-02 | End-to-end behavioral confirmation across multiple unrelated commands (`/tasks`, `/quiethours`) responding to one shared override is best confirmed by hand against the real bot, not simulated in unit tests alone | Set `/debug clock <future-date>`, then check `/tasks` shows an updated deadline badge and quiet-hours reads as expected; `/debug clock reset` and confirm real time returns |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < N/A — not measured
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
