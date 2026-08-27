# Secretary Bot — Feature Expansion Plan

## Status: Iteration 12 complete

---

## Features

### 1. Per-user timezone-aware check-ins ✅ planned
Users set their timezone with `/settimezone <IANA>`. Morning (08:00) and evening (21:00) check-ins are scheduled in their local timezone using per-user APScheduler jobs instead of a single global job.

### 2. User-defined reminders ✅ planned
`/remind add HH:MM <message>` — schedules a daily reminder at that local time.
`/remind list`, `/remind remove <n>` — manage reminders.
Stored in user state; recreated on bot restart.

### 3. Custom tracker commands ✅ planned
`/addtracker <name> [unit]` — creates a personal tracking command (e.g. `/weight kg`, `/mood`, `/steps`).
`/<name> <value>` — log a numeric value with timestamp.
`/<name> stats` — latest, average, min/max, 7-day trend.
`/<name> history [n]` — last N entries.
`/trackers` — list all trackers with last value.
`/removetracker <name>` — delete a tracker.

Implemented as a catch-all MessageHandler for COMMAND after all defined handlers.

### 4. Custom LLM API keys and models ✅ done
- Default: bot owner's `OPENAI_API_KEY` with `gpt-4o-mini` (cheap, bot-funded tier).
- If `GROQ_API_KEY` is set in env, users without their own key use Groq Llama 3 (free).
- `/setapikey <key>` — store OpenAI (sk-) or Groq (gsk-) key; auto-detected by prefix. Message auto-deleted.
- `/setmodel <model>` — choose model.
- `/clearapikey` — revert to default.

### 8. Groq free tier ✅ done
Bot owner sets `GROQ_API_KEY` in env. Users without their own API key get routed to Groq's Llama 3 automatically. Users with a `gsk_` prefixed key also get Groq routing.

### 9. Rate limiting ✅ done
30 AI calls per user per hour (in-memory rolling window). Prevents abuse.

### 10. One-time reminders ✅ done
`/remind once 30m <msg>` / `/remind once 2h <msg>` / `/remind once HH:MM <msg>` — fires once, not stored.

### 11. Streak tracking ✅ done
`/streak` — shows consecutive days the user was active. Activity recorded on every message.

### 12. Admin stats ✅ done
`/adminstats` — bot-owner only. Shows total users, subscribed count, custom key count, etc.

### 13. Habit tracking ✅ done (iteration 3)
`/habit add <name>` — create a daily habit.
`/habit done <name>` — mark today's completion; shows per-habit streak.
`/habit list` — shows all habits with today's status and streak.
`/habit remove <name>` — delete.
Habits appear in the AI system prompt so check-ins reference them.

### 14. /mystats dashboard ✅ done (iteration 3)
One-shot summary: streak, active days, task count, trackers, journal count, reminders, model, and today's habit statuses.

### 15. Pomodoro timer ✅ done (iteration 3)
`/pomodoro [minutes]` — fires a one-time job after N min (default 25) with a completion message.

### 16. /import from export ✅ done (iteration 3)
Send the JSON file from /export back to the bot to restore tasks, trackers, habits, journal, reminders, timezone, and context. Schedules restored reminders immediately.

### 17. Quiet hours ✅ done (iteration 4)
`/quiethours 23:00 07:00` — suppress all check-ins and reminders during a local-time window (handles midnight-spanning ranges). `/quiethours off` disables. Applies to both check-ins and user-defined daily reminders.

### 18. Task deadlines ✅ done (iteration 4)
`/addtask Submit report due:2026-07-15` — tasks stored as `{text, due}` objects. `/tasks` shows badges: ⚠️ overdue, 🔴 due today, 🟡 due in ≤3d. Due dates included in AI system prompt. Backward compatible with plain-string tasks.

### 19. Interactive check-in buttons ✅ done (iteration 4)
Check-in messages now include an inline keyboard: [✅ Going well] [🔄 Partially] [❌ Not today] [💬 Let's talk]. Tapping a button dismisses the keyboard and triggers a contextual AI follow-up response.

### 20. AI Insights ✅ done (iteration 4)
`/insights` — aggregates last 30 tracker entries, habit streaks, recent journal excerpts, and task list, then asks the AI for 2-3 specific observations and one actionable recommendation. More comprehensive than `/weekly`.
Send the JSON file from /export back to the bot to restore tasks, trackers, habits, journal, reminders, timezone, and context. Schedules restored reminders immediately.

### 5. Journal entries ✅ planned
`/journal <text>` — saves entry with timestamp, AI reflects briefly.
Used in weekly summaries.

### 6. Weekly summary ✅ planned
`/weekly` — AI-generated summary using task list, tracker data (last 7 days), and journal entry count.

### 7. Data export ✅ planned
`/export` — sends a JSON file with all user data (tasks, trackers, journal, reminders). Conversation history excluded.

---

## State schema (per user)

```json
{
  "tasks": [],
  "history": [],
  "context": "",
  "checkin_enabled": false,
  "timezone": "UTC",
  "reminders": [{"id": "uuid", "time": "HH:MM", "message": "..."}],
  "trackers": {
    "weight": {"unit": "kg", "log": [{"ts": "ISO8601", "value": 85.5}]}
  },
  "journal": [{"ts": "ISO8601", "entry": "..."}],
  "llm": {"model": null, "api_key": null}
}
```

---

### 21. Task completion and archiving ✅ done (iteration 6)
`/donetask <n>` — marks a task done, removes from active list, appends to `archived_tasks` with UTC timestamp. `/archive` shows last 20 completed tasks. `/mystats` now shows total completed count.

### 22. Idle-user nudge ✅ done (iteration 6)
Daily job at 11:00 local for each subscribed user. Fires only if user has been inactive for 3+ consecutive days (checks `activity_days`). Sends a gentle check-in message referencing their goals. Quiet-hours-aware.

### 23. Error handling in chat() ✅ done (iteration 6)
All LLM calls wrapped in try/except. Distinguishes auth failures (401), rate limits (429), model-not-found, and generic errors. Pops the user message from history on failure to prevent orphaned turns.

### 24. Onboarding flow ✅ done (iteration 6)
`/start` for a user with no context and no tasks shows a guided 4-step setup (setcontext → settimezone → addtask → subscribe) instead of a wall of commands.

### 25. `/help` command ✅ done (iteration 6)
`/help` — comprehensive command reference always accessible. `/start` for existing users now redirects to `/help`.

### 26. `requirements.txt` ✅ done (iteration 6)
Added `python-telegram-bot[job-queue]>=21.6`, `openai>=1.0.0`, `tzdata`.

### 27. `/reset` command ✅ done (iteration 6)
`/reset` — clears all user data (tasks, habits, trackers, journal, reminders, history) while keeping timezone and LLM settings. Cancels scheduled jobs and resets to onboarding state.

### 28. CLAUDE.md rewrite ✅ done (iteration 6)
Full rewrite with current architecture, state schema, scheduling table, handler order, LLM routing, and complete command reference.

### 29. Daily focus / intention ✅ done (iteration 7)
`/today <text>` — sets today's focus (date-scoped; auto-expires). `/today` with no args shows current focus. Focus injected into AI system prompt and visible in check-ins.

### 30. Quick notes scratchpad ✅ done (iteration 7)
`/note <text>` — appends to a persistent notes list (max 50). `/notes` — numbered list. `/removenote <n>` — delete by index. Notes appear in AI system prompt (last 10).

### 31. Task prioritization ✅ done (iteration 7)
`/prioritize <n>` — moves task n to position 1 without deleting it. Instant reordering for focus.

### 32. Reminder snooze button ✅ done (iteration 7)
Daily reminders now include a `🔁 Snooze 30 min` inline button. Tapping schedules a one-shot re-fire 30 minutes later using a `_snooze_cache` token map (no state persistence needed).

### 33. Cross-data search ✅ done (iteration 7)
`/search <query>` — case-insensitive substring search across active tasks, archived tasks, notes, and journal entries. Returns grouped results (up to 20).

### 34. Language preference ✅ done (iteration 8)
`/setlanguage Hebrew` — stores preferred response language; injected as an exclusive-language instruction into the system prompt. `/clearlanguage` resets. Works for any language the underlying LLM supports.

### 35. History compression ✅ done (iteration 8)
`/compress` — asks the AI to summarize the full conversation history in 3-5 sentences (tasks discussed, commitments, progress), then replaces history with that 2-message compact form. Reduces token usage for long-running conversations.

### 36. Admin broadcast ✅ done (iteration 8)
`/broadcast <message>` — admin-only (MY_CHAT_ID required). Sends a `📢 <message>` to all subscribed users. Reports sent/failed counts.

### 37. User feedback ✅ done (iteration 8)
`/feedback <text>` — forwards a message to the bot admin's chat_id with the sender's ID. Simple one-way channel for bug reports and feature requests.

### 38. Annual reminders ✅ done (iteration 8)
`/remind annual MM-DD HH:MM <message>` — e.g. `12-25 09:00 Merry Christmas!`. Stored with `"annual": true` and `"date": "MM-DD"`. Fires via the daily deadline alert job which checks if today's `%m-%d` matches any annual reminder.

### 39. `/time` command ✅ done (iteration 8)
Shows the user's current local time and day of week using their stored IANA timezone.

### 40. Enhanced manual /checkin ✅ done (iteration 8)
`/checkin` now prepends a mini-dashboard: today's focus (if set), active task count with first 3 names, and pending habits. The AI response follows immediately below. Makes check-in much more actionable.

### 41. Achievement milestones ✅ done (iteration 9)
`_check_milestones(chat_id, app)` fires after every message and after `/donetask`. Congratulates user on 7/14/30/60/100-day streaks and 5/10/25/50/100 completed tasks. Uses `milestones_sent` list to send each notification only once.

### 42. `/suggest` ✅ done (iteration 9)
AI reviews user's context, tasks, habits, and recent journal, then suggests 3 specific actionable tasks or habits with rationale. Completely personalized — no generic advice.

### 43. `/reflect` ✅ done (iteration 9)
Deeper personal reflection than `/weekly`: identifies patterns across habits/streaks/journal, names what's working, and suggests one focus area for next week.

### 44. `/duedate <n> YYYY-MM-DD` ✅ done (iteration 9)
Updates a task's due date in-place (or clears it with `none`). Converts plain-string tasks to dict form on first use. No need to delete and re-add.

### 45. `/swap <n> <m>` ✅ done (iteration 9)
Swaps two tasks' positions without deleting either. Useful for reordering the task list.

### 46. Gratitude in evening check-in ✅ done (iteration 9)
Evening check-in prompt now asks the user to share one thing they're grateful for today. Builds a positive close-of-day habit.

### 47. Stale-tracker reminder in morning check-in ✅ done (iteration 9)
Morning check-in dynamically detects trackers with no log in 2+ days and includes a nudge to log them, by name.

### 48. LLM function calling / tool use ✅ done (iteration 10)
The AI can now take direct actions mid-conversation using OpenAI-compatible function calling. `chat()` runs a tool loop (up to 5 rounds); tool results are fed back to the model before the final reply. Tool schemas not supported by a user's custom model trigger an automatic no-tools fallback. Only the final user message + text reply are stored in history (tool messages are ephemeral).

**7 tools implemented:**
- `get_current_time` — user's local datetime (timezone-aware)
- `get_tasks` — fresh task list with numbers and due dates
- `add_task(text, due_date?)` — add a task mid-conversation ("remind me to call the dentist next Friday")
- `complete_task(task_number)` — mark a task done and archive it
- `log_tracker(tracker_name, value)` — log a numeric value ("I weighed 74kg this morning")
- `add_reminder(time, message)` — schedule a daily reminder and register the job immediately
- `add_journal_entry(text)` — save a journal entry when the user describes their day

### 49. BotFather command registration ✅ done (iteration 11)
`_post_init(app)` async hook registered via `ApplicationBuilder().post_init(...)`. On every startup, registers 20 commands with BotFather so users see a tappable command list in the Telegram UI.

### 50. Scheduled weekly digest ✅ done (iteration 11)
Every Sunday at 10:00 local time, subscribed users get an AI-generated weekly digest: progress acknowledgement, one strength callout, and one priority suggestion for the week. Job uses `days=(6,)` via a weekday check inside the daily job.

### 51. `/extend <n> <days>` ✅ done (iteration 11)
Extends a task's due date by N calendar days. If the task has no due date, bases from today. Converts plain-string tasks to dict form. Companion to `/duedate`.

### 52. `/focus [task_n] [minutes]` ✅ done (iteration 11)
Pomodoro timer linked to a specific task. Start message shows the task name; end message asks how it went with that task. Falls back to plain pomodoro if no task number given.

### 53. `/habit stats <name>` ✅ done (iteration 11)
Detailed per-habit report: last 7 days as ✅/❌ grid, current streak, longest streak, 30-day completion rate, total completions, and last missed day.

### 54. Recurring tasks ✅ done (iteration 12)
`/addtask <text> every:daily|weekly|monthly` — creates a recurring task with an auto-set first due date. `/donetask` archives a completion record and rolls the due date forward (daily +1d, weekly +7d, monthly same-day next month). Tasks display `♻️ daily/weekly/monthly` badge in `/tasks`. Tag filter also shows recurrence badge.

### 55. Task tags ✅ done (iteration 12)
Add `#tag` anywhere in task text. `/tasks #work` filters to matching tasks. Tags are case-insensitive and extracted by regex; no schema change needed.

### 56. /mute and /unmute ✅ done (iteration 12)
`/mute 4h` or `/mute 2d` — suppresses all check-ins, reminders, alerts, and nudges for the specified period (stored as UTC ISO timestamp in `muted_until`). `/unmute` cancels early. `/mute` with no args shows current status. `/mystats` displays mute status.

### 57. /limit ✅ done (iteration 12)
Shows real-time rate limit status: messages used this hour, messages remaining, visual `█░` bar.

### 58. 7-day activity chart in /mystats ✅ done (iteration 12)
`/mystats` now includes a `█░` bar for the last 7 days of activity, and the completed-task count is shown inline with the active count.

### Bug fixes (iteration 12) ✅
- `_execute_tool complete_task` now mirrors `/donetask`: rolls recurring tasks forward instead of deleting them
- `idle_nudge` and `weekly_digest` jobs now correctly cancelled on unsubscribe (moved removal before early return)
- `focus_cmd` arg parsing rewritten to be unambiguous: first int = task index, second int = minutes
- Weekly digest weekday check now uses user's local timezone date, not server date
- Weekly digest `n_done` now counts completions in the past 7 days, not all-time
- `_execute_tool complete_task` now calls `_check_milestones` for non-recurring completions
- Tag-filtered task display now shows ♻️ recurring indicator
- `_execute_tool add_task` returns a warning (not silent success) when `due_date` is invalid
- `mute_status` in `/mystats` no longer has a spurious leading newline

## Backlog (ideas — not yet implemented, pending discussion)

### 59. Capture user complaints about bot mistakes 🔲 proposed
When a user expresses dissatisfaction with something the bot did — e.g. they wrote in Russian but the bot answered in English, or gave wrong/ignored info — that complaint currently just sits in `history` and gets lost. It should be captured somewhere durable so recurring failure patterns (like language mismatches) can actually get reviewed and fixed instead of silently repeating.

Open questions to settle before implementing:
- How to detect "the user is complaining about the bot" — a dedicated tool the LLM calls when it recognizes dissatisfaction, vs. a keyword/heuristic pass over messages?
- What to store per complaint: chat_id, timestamp, the user's message, what the bot did (e.g. last assistant reply + language used), and a rough category (e.g. `wrong_language`, `wrong_info`, `ignored_instruction`).
- Where it lives — new SQLite table (e.g. `complaints`), similar to `notes`/`journal`.
- Whether/how it surfaces — an admin command to review recent complaints (e.g. `/complaints`), or just a raw table queried manually for now.

### 60. Reliable persistence of user-given LLM behavior preferences 🔲 proposed
Right now, if a user tells the bot a standing style/behavior preference — e.g. "always use emojis in reminders", "keep replies short", "never use my first name" — there's no dedicated place that reliably captures and enforces it. The only path today is the LLM voluntarily calling `save_memory` to write it into `profile_memory`, which then shows up in every system prompt (`build_system_prompt`) as a "permanent user fact" — but whether the model bothers to save it, and whether it then actually honors it every time (including in code paths that re-invoke the LLM, like `_run_reminder`'s paraphrasing step), is not guaranteed by anything in the code. This is the same root cause behind #59's example (Russian in, English out) and the emoji-reminder question: preferences are memory-dependent, not enforced.

Open questions to settle before implementing:
- Should there be a distinct, structured category from general `profile_memory` facts — e.g. a `preferences` table/list that's always surfaced verbatim near the top of the system prompt (higher priority than free-form facts), rather than mixed in with arbitrary permanent facts?
- Does the LLM need an explicit tool (e.g. `save_preference`) separate from `save_memory`, so the model is nudged to use it specifically when it detects a standing instruction about its own behavior (tone, formatting, language, emoji use, etc.) rather than a fact about the user's life?
- How to keep this bounded — a user could ask for many preferences over time; needs a cap/dedup strategy similar to `notes`/`profile_memory`.
- Should `/mystats` or a new command let the user see/edit their stored preferences directly, instead of only being set implicitly through conversation?

## Implementation order

1. [x] Write PLAN.md
2. [x] Implement full bot.py
3. [x] Syntax check (python3 -m py_compile)
4. [x] Smoke-test startup — state migration verified
5. [x] Update CLAUDE.md
6. [x] Commit
