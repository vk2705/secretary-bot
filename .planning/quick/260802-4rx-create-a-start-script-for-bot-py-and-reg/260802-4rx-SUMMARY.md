---
phase: quick-260802-4rx
plan: 01
subsystem: infra
tags: [systemd, deployment, process-supervision, bash]

requires: []
provides:
  - "start_bot.sh wrapper that loads env (export KEY=value format) and execs the venv python3 interpreter"
  - "secretary-bot.service systemd unit (enabled, active) supervising bot.py"
  - "Live cutover from manual nohup process to systemd-managed process, zero downtime overlap avoided by ordered stop/start"
affects: [deployment, ops]

actuals:
  tokens: 400
  tasks: 3
  commits: 1

tech-stack:
  added: []
  patterns:
    - "systemd unit with ExecStart pointing at a wrapper script (not directly at the interpreter) when the credential file uses `export KEY=value` syntax incompatible with EnvironmentFile="

key-files:
  created:
    - /home/ec2-user/secretary-bot/start_bot.sh
    - /etc/systemd/system/secretary-bot.service (outside repo, not git-tracked)
  modified: []

key-decisions:
  - "Wrapper sources env via `set -a; . ./env; set +a` rather than the `export $(grep ... | xargs)` form used elsewhere in CLAUDE.md docs, because xargs word-splits and mangles values containing spaces"
  - "Systemd unit omits EnvironmentFile= entirely; the wrapper owns credential loading"
  - "Unit installed root-owned mode 644 via `sudo install`; service body still runs as User=ec2-user/Group=ec2-user"

requirements-completed: [OPS-SUPERVISOR-01]

coverage:
  - id: D1
    description: "start_bot.sh wrapper loads credentials from env and execs the venv python3 interpreter running bot.py"
    requirement: "OPS-SUPERVISOR-01"
    verification:
      - kind: other
        ref: "bash -n + mode/exec checks + env -i sourcing test (Task 1 <verify><automated>)"
        status: pass
    human_judgment: false
  - id: D2
    description: "secretary-bot.service systemd unit installed, enabled for boot, points ExecStart at the wrapper, no EnvironmentFile"
    requirement: "OPS-SUPERVISOR-01"
    verification:
      - kind: other
        ref: "systemd-analyze verify + systemctl is-enabled/is-active checks (Task 2 <verify><automated>)"
        status: pass
    human_judgment: false
  - id: D3
    description: "Live cutover completed: manual nohup process terminated, systemd service started, exactly one bot.py process running under systemd's MainPID, no crash loop, no Telegram getUpdates collision"
    requirement: "OPS-SUPERVISOR-01"
    verification:
      - kind: other
        ref: "systemctl is-active/NRestarts + ps/journalctl checks (Task 3 <verify><automated>)"
        status: pass
    human_judgment: false
  - id: D4
    description: "End-to-end Telegram round trip: a message sent to the bot receives a reply, proving credentials reached the systemd-managed process"
    verification: []
    human_judgment: true
    rationale: "Requires an actual Telegram message to be sent by a human; the executing agent has no Telegram account to send from. Task 3's <human-check>."

duration: ~5min
completed: 2026-08-02
status: complete
---

# Quick Task 260802-4rx: Systemd Supervision for bot.py Summary

**Added `start_bot.sh` wrapper + `secretary-bot.service` systemd unit, then cut over live from the unsupervised `nohup` process to systemd — bot.py now restarts automatically on crash or reboot.**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-08-02T03:29:00Z (approx)
- **Completed:** 2026-08-02T03:32:50Z
- **Tasks:** 3/3
- **Files modified:** 1 git-tracked (`start_bot.sh`), 1 outside-repo (`/etc/systemd/system/secretary-bot.service`)

## Accomplishments
- `start_bot.sh` created (mode 755, owner-write only) — sources `env`'s `export KEY=value` lines via `set -a`/`set +a` (systemd's `EnvironmentFile=` cannot parse the `export` keyword) and `exec`s the venv's `python3 bot.py`, making the Python process the unit's `MainPID`.
- `secretary-bot.service` installed at `/etc/systemd/system/` (root-owned, mode 644), mirroring the existing `secretary-mcp.service` precedent (`Type=simple`, `User=ec2-user`, `Restart=on-failure`, `RestartSec=2`, journal logging), enabled for boot.
- Live cutover performed in the mandated order: located the sole manual `bot.py` process via the `comm`-field `ps`+`awk` filter (PID 8552), sent SIGTERM, confirmed exit within 5s (no `kill -9` needed), started `secretary-bot.service`, and confirmed via journal + PID comparison that exactly one healthy `bot.py` process now runs, owned by systemd, with `NRestarts=0` and no Telegram single-consumer collision.

## Task Commits

1. **Task 1: Create the start_bot.sh wrapper** - `11eb9f5` (feat)
2. **Task 2: Install and enable the secretary-bot systemd unit** - no repo commit (unit file lives at `/etc/systemd/system/secretary-bot.service`, outside the git working tree, per plan constraints)
3. **Task 3: Cut over to the managed service** - no repo commit (process/service state change only; no git-trackable files modified)

**Plan metadata:** (docs commit handled separately by orchestrator, not by this executor per task instructions)

## Files Created/Modified
- `start_bot.sh` - Wrapper script: guards on missing `env`, sources credentials, execs the venv interpreter on `bot.py`
- `/etc/systemd/system/secretary-bot.service` (not git-tracked) - systemd unit supervising `bot.py`

## Decisions Made
- Used `set -a; . ./env; set +a` to source credentials rather than the `export $(grep -v '^#' env | xargs)` pattern documented elsewhere in CLAUDE.md, per the plan's explicit instruction — `xargs` word-splits on whitespace and would silently mangle any future credential value containing a space.
- Omitted `EnvironmentFile=` from the unit entirely; `start_bot.sh` is the single owner of credential loading, avoiding any duplication of secrets into a second file.
- Installed the unit root-owned mode 644 (`sudo install -m 0644 -o root -g root`) while the service body still runs as `User=ec2-user`/`Group=ec2-user` — the bot process itself never runs as root.

## Deviations from Plan

None - plan executed exactly as written. All three tasks completed in order with their automated `<verify>` blocks passing (`TASK1_OK`, `TASK2_OK`, `TASK3_OK`).

## Issues Encountered

None blocking. One observation (not a deviation, out of scope to fix): `bot.py`'s existing `logging.basicConfig(level=logging.INFO)` causes `httpx` to log full Telegram API request URLs — which embed the bot token in the URL path per Telegram's API design — at INFO level. This was already true before this task (same behavior previously flowed into `nohup.out`); it is now visible via `journalctl -u secretary-bot` instead. No change to `bot.py` was in scope for this quick task, so it was not touched. Flagging for awareness only.

## User Setup Required

**Human verification still needed (Task 3's `<human-check>`, cannot be performed by this agent):** Send any message to the bot on Telegram and confirm it replies. This is the only check that proves credentials reached the systemd-managed process end-to-end and the Telegram round trip works. All automated checks (`TASK3_OK`) already confirm the service is active, single-process, crash-loop-free, and polling started successfully — this manual step is purely confirmatory of the full round trip.

## Next Phase Readiness
- STATE.md's "Deploy fragility" blocker is closed for `bot.py` — the process now survives reboots (`enabled` + `WantedBy=multi-user.target`) and crashes (`Restart=on-failure`).
- `secretary-mcp.service` was left untouched and remained `active` throughout.
- `nohup.out` was preserved intact (not truncated/deleted) as historical pre-cutover log; all new logs go to `journalctl -u secretary-bot` going forward.
- No outstanding blockers for this quick task beyond the human Telegram-reply check noted above.

---
*Phase: quick-260802-4rx*
*Completed: 2026-08-02*

## Self-Check: PASSED

- FOUND: /home/ec2-user/secretary-bot/start_bot.sh
- FOUND: /etc/systemd/system/secretary-bot.service
- FOUND: commit 11eb9f5
- systemctl is-active secretary-bot: active
- systemctl is-enabled secretary-bot: enabled
