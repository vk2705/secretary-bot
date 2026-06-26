import os
import json
import logging
import asyncio
from datetime import datetime
from openai import AsyncOpenAI
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
MY_CHAT_ID = int(os.environ["MY_CHAT_ID"])

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# ---------- persistent state (simple JSON file) ----------

STATE_FILE = "state.json"

DEFAULT_TASKS = [
    "Ship model (wooden)",
    "Japanese / Chinese study",
    "Weight and health",
    "Kubernetes / AI study",
    "Insomnia / sleep quality",
]

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "tasks": DEFAULT_TASKS,
        "history": [],   # last N conversation messages
    }

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

state = load_state()

# ---------- OpenAI chat ----------

SYSTEM_PROMPT = """You are Vitaly's personal secretary bot. Your job is:
1. Be a helpful, direct assistant — like Claude, but running inside Telegram.
2. Proactively hold Vitaly accountable for his goals and tasks.
3. When doing check-ins, ask about specific tasks from the task list.
4. Be warm but firm. Don't accept vague excuses without gentle pushback.
5. Keep responses concise — this is a chat, not an essay.
6. Vitaly is 50, lives alone in Israel, works as a C/C++ developer at Radware.
7. His tracked goals: {tasks}
8. Don't offer hotlines or unsolicited emotional support suggestions.
9. Correct his English mistakes naturally and briefly when they occur.
"""

MAX_HISTORY = 20  # messages to keep in memory

async def chat(user_message: str, system_override: str = None) -> str:
    global state
    system = (system_override or SYSTEM_PROMPT).format(
        tasks=", ".join(state["tasks"])
    )
    state["history"].append({"role": "user", "content": user_message})
    if len(state["history"]) > MAX_HISTORY:
        state["history"] = state["history"][-MAX_HISTORY:]

    response = await openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": system}] + state["history"],
        max_tokens=600,
        temperature=0.7,
    )
    reply = response.choices[0].message.content.strip()
    state["history"].append({"role": "assistant", "content": reply})
    save_state(state)
    return reply

# ---------- scheduled jobs ----------

async def morning_checkin(context: ContextTypes.DEFAULT_TYPE):
    prompt = (
        "It's morning check-in time. Greet Vitaly briefly, "
        "then ask what he plans to work on today from his task list. "
        "Pick 1-2 specific tasks to focus on."
    )
    reply = await chat(prompt, system_override=SYSTEM_PROMPT)
    await context.bot.send_message(chat_id=MY_CHAT_ID, text=reply)

async def evening_checkin(context: ContextTypes.DEFAULT_TYPE):
    prompt = (
        "It's evening check-in time. Ask Vitaly how his day went. "
        "Ask specifically about what progress he made on his tasks. "
        "If he didn't do much, gently push back and encourage him to do at least one small thing."
    )
    reply = await chat(prompt, system_override=SYSTEM_PROMPT)
    await context.bot.send_message(chat_id=MY_CHAT_ID, text=reply)

# ---------- command handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != MY_CHAT_ID:
        return
    await update.message.reply_text(
        "Secretary bot is active.\n\n"
        "Commands:\n"
        "/tasks — show tracked tasks\n"
        "/addtask <task> — add a task\n"
        "/removetask <number> — remove a task\n"
        "/checkin — trigger a manual check-in\n"
        "/clear — clear conversation history\n\n"
        "Or just talk to me normally."
    )

async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != MY_CHAT_ID:
        return
    tasks = state["tasks"]
    if not tasks:
        await update.message.reply_text("No tasks set.")
        return
    text = "Current tracked tasks:\n" + "\n".join(
        f"{i+1}. {t}" for i, t in enumerate(tasks)
    )
    await update.message.reply_text(text)

async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != MY_CHAT_ID:
        return
    task = " ".join(context.args).strip()
    if not task:
        await update.message.reply_text("Usage: /addtask <task description>")
        return
    state["tasks"].append(task)
    save_state(state)
    await update.message.reply_text(f"Added: {task}")

async def remove_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != MY_CHAT_ID:
        return
    try:
        idx = int(context.args[0]) - 1
        removed = state["tasks"].pop(idx)
        save_state(state)
        await update.message.reply_text(f"Removed: {removed}")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /removetask <number>  (use /tasks to see numbers)")

async def manual_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != MY_CHAT_ID:
        return
    prompt = (
        "Vitaly requested a manual check-in. "
        "Ask him what's on his mind and how his tasks are going."
    )
    reply = await chat(prompt, system_override=SYSTEM_PROMPT)
    await update.message.reply_text(reply)

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != MY_CHAT_ID:
        return
    state["history"] = []
    save_state(state)
    await update.message.reply_text("Conversation history cleared.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != MY_CHAT_ID:
        return
    user_text = update.message.text
    reply = await chat(user_text)
    await update.message.reply_text(reply)

# ---------- main ----------

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("tasks", show_tasks))
    app.add_handler(CommandHandler("addtask", add_task))
    app.add_handler(CommandHandler("removetask", remove_task))
    app.add_handler(CommandHandler("checkin", manual_checkin))
    app.add_handler(CommandHandler("clear", clear_history))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # scheduled check-ins (Israel time = UTC+3)
    # Morning: 08:00 Israel = 05:00 UTC
    # Evening: 21:00 Israel = 18:00 UTC
    job_queue = app.job_queue
    job_queue.run_daily(morning_checkin, time=datetime.strptime("05:00", "%H:%M").time())
    job_queue.run_daily(evening_checkin, time=datetime.strptime("18:00", "%H:%M").time())

    logger.info("Bot started.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
