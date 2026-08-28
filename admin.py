#!/usr/bin/env python3
"""
admin.py — CLI for one-off edits to the live state.json / bot_memory.db,
going through bot.py's own functions instead of hand-editing JSON/SQLite.

Why this exists: an ad-hoc timezone fix for one user (2026-08-28) was done
by stopping the bot, calling bot._set_user_timezone() + bot.save_state()
directly in a one-off python -c snippet, then restarting. That's the right
way to touch production data — bot.py's helpers keep state.json and SQLite
in sync the same way a real command handler would — but a one-off snippet
isn't repeatable or auditable. This wraps the same approach as a real tool.

Every write command stops the live bot first (so it isn't concurrently
writing state.json out from under this process), edits through bot.py's
real functions, then restarts it via start_bot.sh — the same restart path
used manually throughout this project's history. Read-only commands don't
touch the running process at all.

Usage:
    export $(grep -v '^#' env | xargs) && python3 admin.py <command> [args]

    list-users                                  Table of chat_id/tz/tasks/reminders
    show-user <chat_id>                         Full get_user() dump for one user
    set-timezone <chat_id> <iana_tz>             Set + confirm a user's timezone
    list-reminders <chat_id>                     Numbered reminders for one user
    remove-reminder <chat_id> <n>                Delete reminder n (1-based)
    restart-bot                                  Stop + start the bot, no data edit

Every command re-execs into venv/bin/python3 the same way console.py does,
so a bare `python3 admin.py` works from any shell.
"""

import argparse
import os
import subprocess
import sys
import time
import types
from unittest.mock import MagicMock

REPO = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = os.path.join(REPO, "venv", "bin", "python3")
START_SCRIPT = os.path.join(REPO, "start_bot.sh")


def _reexec_in_venv() -> None:
    """Same trick as console.py: dependencies live in ./venv, but a bare
    `python3 admin.py` picks up whichever interpreter is first on PATH."""
    if sys.prefix != sys.base_prefix:
        return
    if os.environ.get("SECRETARY_ADMIN_REEXEC"):
        return
    if not os.access(VENV_PYTHON, os.X_OK):
        return
    if os.path.realpath(VENV_PYTHON) == os.path.realpath(sys.executable):
        return
    os.environ["SECRETARY_ADMIN_REEXEC"] = "1"
    os.execv(VENV_PYTHON, [VENV_PYTHON, os.path.abspath(__file__), *sys.argv[1:]])


def _install_stubs() -> None:
    """bot.py imports telegram/timezonefinder at module level. We never talk
    to Telegram here, so stub them before importing bot — same approach
    console.py and tests/test_bot.py use, kept deliberately identical."""
    for name in ("telegram", "telegram.ext", "telegram.ext._application",
                 "timezonefinder"):
        sys.modules.setdefault(name, types.ModuleType(name))

    tg = sys.modules["telegram"]
    for attr in ("Update", "BotCommand"):
        setattr(tg, attr, MagicMock)
    for attr in ("InlineKeyboardMarkup", "InlineKeyboardButton"):
        setattr(tg, attr, MagicMock(side_effect=lambda *a, **kw: MagicMock()))

    tgext = sys.modules["telegram.ext"]
    for attr in ("Application", "CommandHandler", "MessageHandler",
                 "CallbackQueryHandler", "filters", "ContextTypes",
                 "ApplicationBuilder"):
        setattr(tgext, attr, MagicMock)
    tgext.ContextTypes.DEFAULT_TYPE = type(None)

    tf = sys.modules["timezonefinder"]
    stub = MagicMock()
    stub.return_value.timezone_at.return_value = "Europe/London"
    setattr(tf, "TimezoneFinder", stub)

    os.environ.setdefault("TELEGRAM_TOKEN", "ADMIN_TOKEN")


def _find_bot_pid() -> int | None:
    # The bot has been started both ways across this project's history: via
    # start_bot.sh (absolute paths: REPO/venv/bin/python3 REPO/bot.py) and
    # by hand from a `cd`'d shell (relative: venv/bin/python3 bot.py). Match
    # on the interpreter+script pair regardless of path style, but scope it
    # to processes whose cwd is actually this repo, so a bot.py belonging to
    # some other checkout is never mistaken for this one.
    out = subprocess.run(
        ["pgrep", "-f", r"venv/bin/python3 .*bot\.py"],
        capture_output=True, text=True,
    ).stdout.strip()
    for pid_str in out.splitlines():
        pid_str = pid_str.strip()
        if not pid_str:
            continue
        pid = int(pid_str)
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            continue
        if cwd == REPO:
            return pid
    return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _stop_bot() -> int | None:
    pid = _find_bot_pid()
    if pid is None:
        return None
    print(f"Stopping bot (pid {pid})...")
    os.kill(pid, 15)  # SIGTERM
    for _ in range(20):
        time.sleep(0.5)
        if not _pid_alive(pid):
            break
    else:
        print(f"WARNING: pid {pid} did not exit after 10s — sending SIGKILL", file=sys.stderr)
        try:
            os.kill(pid, 9)
        except OSError:
            pass
        time.sleep(1)
    if _pid_alive(pid):
        print(f"ERROR: pid {pid} still alive, refusing to start a second instance "
              f"(Telegram allows only one getUpdates poller — a second process "
              f"causes both to crash with 409 Conflict).", file=sys.stderr)
        sys.exit(1)
    return pid


def _start_bot() -> None:
    # Guard against ever ending up with two pollers: if something is still
    # answering on the bot.py pattern right before we spawn, refuse instead
    # of risking the 409 Conflict crash loop both instances hit otherwise.
    existing = _find_bot_pid()
    if existing is not None:
        print(f"ERROR: pid {existing} is already running bot.py — not starting "
              f"a second instance.", file=sys.stderr)
        sys.exit(1)
    print("Restarting bot...")
    nohup_path = os.path.join(REPO, "nohup.out")
    with open(nohup_path, "a") as log:
        subprocess.Popen(
            ["nohup", START_SCRIPT],
            cwd=REPO, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
    time.sleep(3)
    pid = _find_bot_pid()
    print(f"Bot back up (pid {pid})." if pid else "WARNING: bot did not come back up — check nohup.out")


def _with_bot_stopped(fn):
    """Run fn() (a write) with the live bot process stopped, then restart
    it — mirrors the manual stop/edit/restart sequence used throughout this
    project's history, so admin.py doesn't race the live process's own
    in-memory state dict."""
    was_running = _stop_bot() is not None
    try:
        fn()
    finally:
        if was_running:
            _start_bot()
        else:
            print("(bot wasn't running — leaving it stopped)")


def cmd_list_users(bot, args) -> None:
    state = bot.load_state()
    users = state.get("users", {})
    print(f"{'chat_id':<14} {'timezone':<22} {'confirmed':<10} {'tasks':<6} {'reminders':<10}")
    for cid, u in sorted(users.items()):
        print(f"{cid:<14} {u.get('timezone', 'UTC'):<22} "
              f"{str(u.get('timezone_confirmed', False)):<10} "
              f"{len(u.get('tasks', [])):<6} {len(u.get('reminders', [])):<10}")


def cmd_show_user(bot, args) -> None:
    import json
    user = bot.get_user(int(args.chat_id))
    print(json.dumps(user, indent=2, ensure_ascii=False, default=str))


def cmd_list_reminders(bot, args) -> None:
    user = bot.get_user(int(args.chat_id))
    reminders = user.get("reminders", [])
    if not reminders:
        print("No reminders.")
        return
    for i, r in enumerate(reminders, 1):
        once = " (once)" if r.get("once") else ""
        print(f"{i}. {r['time']}{once} — {r['message']}")


def cmd_set_timezone(bot, args) -> None:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo(args.iana_tz)
    except ZoneInfoNotFoundError:
        print(f"ERROR: '{args.iana_tz}' is not a valid IANA timezone name.", file=sys.stderr)
        sys.exit(1)

    def _do():
        chat_id = int(args.chat_id)
        user = bot.get_user(chat_id)
        before = user.get("timezone"), user.get("timezone_confirmed")
        bot._set_user_timezone(chat_id, user, args.iana_tz)
        bot.save_state(bot.state)
        print(f"chat_id {chat_id}: timezone {before[0]!r} (confirmed={before[1]}) "
              f"-> {user['timezone']!r} (confirmed={user['timezone_confirmed']})")

    _with_bot_stopped(_do)


def cmd_remove_reminder(bot, args) -> None:
    def _do():
        chat_id = int(args.chat_id)
        user = bot.get_user(chat_id)
        reminders = user.get("reminders", [])
        n = int(args.n)
        if not (1 <= n <= len(reminders)):
            print(f"ERROR: reminder {n} out of range (user has {len(reminders)}).", file=sys.stderr)
            sys.exit(1)
        removed = reminders.pop(n - 1)
        bot.save_state(bot.state)
        print(f"chat_id {chat_id}: removed reminder {n} — {removed['time']} {removed['message']!r}")

    _with_bot_stopped(_do)


def cmd_restart_bot(bot, args) -> None:
    """Stop + start the live bot with no data edit in between — for picking
    up a code change (bot.py/requirements.txt) without touching state.json
    or bot_memory.db at all. Used by .git/hooks/post-commit."""
    was_running = _stop_bot() is not None
    _start_bot()
    if not was_running:
        print("(bot wasn't running before this — started fresh)")


def main() -> None:
    _reexec_in_venv()
    _install_stubs()

    parser = argparse.ArgumentParser(description="Admin CLI for secretary-bot's live data.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-users", help="Table of all users").set_defaults(fn=cmd_list_users)

    p = sub.add_parser("show-user", help="Full get_user() dump for one user")
    p.add_argument("chat_id")
    p.set_defaults(fn=cmd_show_user)

    p = sub.add_parser("set-timezone", help="Set + confirm a user's timezone")
    p.add_argument("chat_id")
    p.add_argument("iana_tz", help="e.g. Asia/Yekaterinburg")
    p.set_defaults(fn=cmd_set_timezone)

    p = sub.add_parser("list-reminders", help="Numbered reminders for one user")
    p.add_argument("chat_id")
    p.set_defaults(fn=cmd_list_reminders)

    p = sub.add_parser("remove-reminder", help="Delete reminder n (1-based, as shown by list-reminders)")
    p.add_argument("chat_id")
    p.add_argument("n")
    p.set_defaults(fn=cmd_remove_reminder)

    sub.add_parser(
        "restart-bot",
        help="Stop + start the live bot (no data edit) — for picking up a code change",
    ).set_defaults(fn=cmd_restart_bot)

    args = parser.parse_args()

    import bot  # noqa: E402 — must come after stubs are installed
    args.fn(bot, args)


if __name__ == "__main__":
    main()
