#!/usr/bin/env python3
"""
console.py — a local, Telegram-free back door into the bot, for debugging.

Runs bot.py's real handlers against a throwaway sandbox copy of the state,
so you can walk through a brand-new user's first session (/start, onboarding,
free-text conversation) as many times as you like without touching the
production state.json / bot_memory.db and without a Telegram client.

Usage:
    export $(grep -v '^#' env | xargs) && python3 console.py

    # keep the sandbox between runs instead of starting fresh
    export $(grep -v '^#' env | xargs) && python3 console.py --keep

    # pretend to be a different chat_id (default 999001, a fresh user)
    python3 console.py --chat-id 424242

    # seed the sandbox from the real data instead of starting empty
    python3 console.py --seed-from-live

Inside the console:
    anything not starting with "/"   → free text, goes through chat() to the LLM
    /start, /addtask foo, ...        → the real command handlers
    :help                            → console meta-commands
    :quit                            → exit

Everything lives under a sandbox directory (printed at startup); delete it and
you are back to a virgin user.
"""

import argparse
import ast
import asyncio
import os
import shutil
import sys
import tempfile
import types
from unittest.mock import AsyncMock, MagicMock

REPO = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SANDBOX = os.path.join(tempfile.gettempdir(), "secretary-bot-console")
DEFAULT_CHAT_ID = 999001
VENV_PYTHON = os.path.join(REPO, "venv", "bin", "python3")


def _reexec_in_venv() -> None:
    """The dependencies live in ./venv, but a plain `python3 console.py` picks
    up whichever interpreter is first on PATH -- typically /usr/bin/python3,
    which has no `openai` and dies at bot.py's import line. start_bot.sh solves
    this by spelling out venv/bin/python3; do the same here, but transparently,
    so the documented `python3 console.py` works from any shell."""
    if sys.prefix != sys.base_prefix:
        return  # already inside a virtualenv
    if os.environ.get("SECRETARY_CONSOLE_REEXEC"):
        return  # re-exec already attempted; don't loop
    if not os.access(VENV_PYTHON, os.X_OK):
        return  # no venv to fall back to; let the real ImportError surface
    if os.path.realpath(VENV_PYTHON) == os.path.realpath(sys.executable):
        return
    os.environ["SECRETARY_CONSOLE_REEXEC"] = "1"
    os.execv(VENV_PYTHON, [VENV_PYTHON, os.path.abspath(__file__), *sys.argv[1:]])


# ─────────────────────── telegram stubs ───────────────────────
# bot.py imports telegram at module level. We never talk to Telegram here, so
# stub the package before importing bot — same approach tests/test_bot.py uses,
# kept deliberately in that shape so the two stay recognisably the same trick.

def _install_stubs() -> None:
    for name in ("telegram", "telegram.ext", "telegram.ext._application",
                 "timezonefinder"):
        sys.modules.setdefault(name, types.ModuleType(name))

    tg = sys.modules["telegram"]
    for attr in ("Update", "BotCommand"):
        setattr(tg, attr, MagicMock)
    # Constructed as InlineKeyboardMarkup([[button]]); a bare MagicMock class
    # would bind that nested list to Mock's own `spec` kwarg and blow up on
    # "unhashable type: 'list'".
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

    os.environ.setdefault("TELEGRAM_TOKEN", "CONSOLE_TOKEN")


# ─────────────────────── fake PTB context ───────────────────────

class _FakeJobQueue:
    """Records scheduled jobs instead of running them. Handlers schedule
    check-ins, reminders and pomodoros through this; in a console session we
    only care that the scheduling happened and with what name."""

    def __init__(self, sink):
        self._sink = sink

    def run_daily(self, callback, time=None, name=None, **kw):
        self._sink.append(("daily", name, str(time)))
        return MagicMock(name=name)

    def run_once(self, callback, when=None, name=None, **kw):
        self._sink.append(("once", name, f"+{when}s"))
        return MagicMock(name=name)

    def get_jobs_by_name(self, name):
        return []

    def jobs(self):
        return []


class Console:
    def __init__(self, bot, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id
        self.scheduled: list[tuple] = []
        self.sent: list[str] = []
        self.commands = _discover_commands(bot)

        # bot._execute_tool(add_reminder) reaches for the module-level _app to
        # schedule jobs; give it the same fake application the handlers get.
        self.app = MagicMock()
        self.app.job_queue = _FakeJobQueue(self.scheduled)
        self.app.bot.send_message = AsyncMock(side_effect=self._record_send)
        bot._app = self.app

    async def _record_send(self, *a, **kw):
        """Captures both call shapes the handlers use: reply_text(text) passes
        the body positionally, bot.send_message(chat_id=..., text=...) by
        keyword."""
        if "text" in kw:
            text = kw["text"]
        elif a:
            text = a[0]
        else:
            text = ""
        self.sent.append(str(text))

    def _make_update(self):
        update = MagicMock()
        update.effective_chat.id = self.chat_id
        update.message.reply_text = AsyncMock(side_effect=self._record_send)
        update.message.reply_document = AsyncMock()
        return update

    def _make_context(self, args):
        ctx = MagicMock()
        ctx.args = list(args)
        ctx.application = self.app
        ctx.bot.send_message = AsyncMock(side_effect=self._record_send)
        return ctx

    async def dispatch(self, line: str) -> None:
        """Route one input line the way main()'s handler chain would."""
        if line.startswith("/"):
            parts = line[1:].split()
            name, args = parts[0].lower(), parts[1:]
            handler = self.commands.get(name)
            if handler is None:
                # main() registers handle_custom_command last, as the catch-all
                # that routes unknown commands to a user's custom tracker.
                handler = self.bot.handle_custom_command
                args = parts[1:]
                update = self._make_update()
                update.message.text = line
                await handler(update, self._make_context(args))
                return
            await handler(self._make_update(), self._make_context(args))
        else:
            update = self._make_update()
            update.message.text = line
            await self.bot.handle_message(update, self._make_context([]))

    def drain(self) -> list[str]:
        out, self.sent = self.sent, []
        return out


def _discover_commands(bot) -> dict:
    """Read main()'s CommandHandler registrations out of bot.py's source, so
    this console can never drift out of sync with the real command list the
    way a hand-maintained copy would."""
    with open(os.path.join(REPO, "bot.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    main_fn = next(
        (n for n in tree.body
         if isinstance(n, ast.FunctionDef) and n.name == "main"), None
    )
    if main_fn is None:
        raise RuntimeError("could not find main() in bot.py")

    found = {}
    for node in ast.walk(main_fn):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Name) and fn.id == "CommandHandler"):
            continue
        if len(node.args) < 2:
            continue
        name_node, handler_node = node.args[0], node.args[1]
        if not (isinstance(name_node, ast.Constant)
                and isinstance(name_node.value, str)):
            continue
        if not isinstance(handler_node, ast.Name):
            continue
        handler = getattr(bot, handler_node.id, None)
        if handler is not None:
            found[name_node.value] = handler
    return found


# ─────────────────────── sandbox ───────────────────────

def _prepare_sandbox(path: str, keep: bool, seed_from_live: bool) -> None:
    if not keep and os.path.isdir(path):
        shutil.rmtree(path)
    os.makedirs(path, exist_ok=True)

    if seed_from_live:
        for fname in ("state.json", "bot_memory.db"):
            src, dst = os.path.join(REPO, fname), os.path.join(path, fname)
            if os.path.exists(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)


META = """Console meta-commands:
  :help              this text
  :quit / :q         exit
  :chat <id>         switch to a different chat_id
  :whoami            show current chat_id and sandbox paths
  :state             dump this user's state.json entry
  :jobs              list jobs the session tried to schedule
  :commands          list every /command the console knows about
  :reset             wipe the sandbox user and start over as a new user
  :verbose           toggle INFO logging (tool calls, LLM requests) on/off

Anything else starting with "/" runs the real command handler.
Anything else at all goes to the LLM through chat()."""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chat-id", type=int, default=DEFAULT_CHAT_ID)
    ap.add_argument("--sandbox", default=DEFAULT_SANDBOX)
    ap.add_argument("--keep", action="store_true",
                    help="reuse an existing sandbox instead of wiping it")
    ap.add_argument("--seed-from-live", action="store_true",
                    help="copy the real state.json/bot_memory.db into a fresh sandbox")
    ap.add_argument("--owner", action="store_true",
                    help="treat this chat_id as MY_CHAT_ID so /debug and /adminstats work")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="show bot.py's INFO logging (tool calls, HTTP) instead of warnings only")
    args = ap.parse_args()

    _reexec_in_venv()

    _prepare_sandbox(args.sandbox, args.keep, args.seed_from_live)
    _install_stubs()

    # Point bot.py at the sandbox BEFORE importing it: STATE_FILE is read by
    # load_state() at import time, at the module's very bottom.
    state_path = os.path.join(args.sandbox, "state.json")
    db_path = os.path.join(args.sandbox, "bot_memory.db")

    import bot  # noqa: E402  (after stubs + env)

    # bot.py logs each tool call at INFO and httpx logs every LLM request;
    # useful when debugging the tool loop, noise the rest of the time.
    import logging
    if not args.verbose:
        logging.getLogger().setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        bot.logger.setLevel(logging.WARNING)

    bot.STATE_FILE = state_path
    bot.DB_FILE = db_path
    bot._init_db()
    bot.state = bot.load_state()
    if args.owner:
        bot.MY_CHAT_ID = str(args.chat_id)

    console = Console(bot, args.chat_id)

    print(f"secretary-bot console — sandbox: {args.sandbox}")
    print(f"chat_id: {args.chat_id}"
          + ("  (owner)" if args.owner else "")
          + f"   model: {bot.get_model(bot.get_user(args.chat_id))}")
    print(f"{len(console.commands)} commands available. :help for meta-commands, :quit to exit.\n")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    while True:
        try:
            line = input(f"[{console.chat_id}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue

        if line.startswith(":"):
            parts = line[1:].split()
            meta, margs = (parts[0].lower() if parts else ""), parts[1:]
            if meta in ("quit", "q", "exit"):
                break
            elif meta == "help":
                print(META)
            elif meta == "whoami":
                print(f"chat_id={console.chat_id}\nstate={state_path}\ndb={db_path}")
            elif meta == "chat":
                if margs:
                    console.chat_id = int(margs[0])
                    print(f"switched to chat_id {console.chat_id}")
                else:
                    print("usage: :chat <id>")
            elif meta == "state":
                import json
                u = bot.state.get("users", {}).get(str(console.chat_id))
                print(json.dumps(u, indent=2, ensure_ascii=False) if u
                      else "(no such user yet — send /start)")
            elif meta == "jobs":
                if not console.scheduled:
                    print("(no jobs scheduled this session)")
                for kind, name, when in console.scheduled:
                    print(f"  {kind:6} {name}  {when}")
            elif meta == "commands":
                print("  ".join(sorted(console.commands)))
            elif meta == "verbose":
                import logging
                on = bot.logger.level != logging.INFO
                lvl = logging.INFO if on else logging.WARNING
                logging.getLogger().setLevel(lvl)
                logging.getLogger("httpx").setLevel(lvl)
                bot.logger.setLevel(lvl)
                print(f"verbose logging {'on' if on else 'off'}")
            elif meta == "reset":
                bot.state.get("users", {}).pop(str(console.chat_id), None)
                bot.save_state(bot.state)
                print(f"user {console.chat_id} wiped from the sandbox")
            else:
                print(f"unknown meta-command :{meta} — try :help")
            continue

        try:
            loop.run_until_complete(console.dispatch(line))
        except Exception as e:
            print(f"!! handler raised {type(e).__name__}: {e}")
            continue

        for msg in console.drain():
            print(f"\n{msg}\n")

    loop.close()
    print("bye.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
