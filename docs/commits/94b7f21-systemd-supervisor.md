# 94b7f21 — feat(ops): run the bot under systemd instead of a manual nohup process

**Date:** 2026-08-29
**Files:** `admin.py`, `githooks/post-commit`, plus a new
`/etc/systemd/system/secretary-bot.service` unit (not tracked in this repo,
same as `secretary-mcp.service`)

## What changed

There was no process supervisor for `bot.py`: a crash meant no service
until someone noticed and manually `kill`+`nohup`'d it back. This was a
review finding earlier in the session ("nothing requiring reliable
background execution" was an explicit constraint the project accepted, but
the bot itself had no supervisor at all) and became the first concrete
DevOps exercise the user asked for — real process supervision, not a
learning-project stand-in.

`secretary-mcp.service` already existed as the pattern to copy. The new
`secretary-bot.service` (`Type=simple`, `Restart=on-failure`,
`RestartSec=5`, `StartLimitBurst=5`/`StartLimitIntervalSec=300`) uses
`start_bot.sh` as `ExecStart` — that script already existed for exactly
this (sources `env`'s `export KEY=value` lines, which systemd's own
`EnvironmentFile=` directive can't parse), so no new script was needed.

`admin.py`'s `_stop_bot()`/`_start_bot()` moved from `pgrep` + `SIGTERM`/
`SIGKILL` + `nohup` to `systemctl stop/start/show` against the unit. Same
public shape, so `cmd_restart_bot()` and `githooks/post-commit` needed no
logic changes.

## Why this, not a hand-rolled restart

The pgrep-based version is exactly what caused a real `409 Conflict` crash
loop earlier in the same day's work — two `getUpdates` pollers briefly
overlapping because a liveness check raced a spawn. `systemctl stop` blocks
until the unit is confirmed stopped before `systemctl start` can run, which
avoids that overlap by construction rather than by careful polling.

## Verification

Live, not just read:
- `systemctl enable --now`: clean startup, all jobs restored, users intact
  across the cutover from the old manual process.
- `sudo kill -9` against the live `MainPID` (a real crash, not a graceful
  stop): systemd restarted it within ~1s, no `409 Conflict`, all jobs
  re-registered, data intact — the actual capability this exists for.
- `admin.py list-users` (read-only) and `restart-bot` (full stop/start)
  both exercised against the new path.

`journalctl -u secretary-bot.service` replaces `nohup.out` as the log
surface going forward.
