# Secretary Bot — Feature Expansion Plan

## Status: Iteration 7 complete

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

## Implementation order

1. [x] Write PLAN.md
2. [x] Implement full bot.py
3. [x] Syntax check (python3 -m py_compile)
4. [x] Smoke-test startup — state migration verified
5. [x] Update CLAUDE.md
6. [x] Commit
