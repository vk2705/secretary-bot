# Secretary Bot — Feature Expansion Plan

## Status: Implementation in progress

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

### 4. Custom LLM API keys and models ✅ planned
- Default: bot owner's `OPENAI_API_KEY` with `gpt-4o-mini` (cheap, bot-funded tier).
- `/setapikey <key>` — store user's own OpenAI key; message deleted for security.
- `/setmodel <model>` — choose model (gpt-4o, gpt-4o-mini, etc.).
- `/clearapikey` — revert to default.

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

## Implementation order

1. [x] Write PLAN.md
2. [x] Implement full bot.py
3. [x] Syntax check (python3 -m py_compile)
4. [x] Smoke-test startup — state migration verified
5. [x] Update CLAUDE.md
6. [x] Commit
