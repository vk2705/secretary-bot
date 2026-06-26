import os
import json
import logging
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
# Optional: used only to migrate old single-user state.json to the new format.
MY_CHAT_ID = os.environ.get("MY_CHAT_ID")

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

STATE_FILE = "state.json"
MAX_HISTORY = 20

# ---------- persistent state ----------

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "users" not in data:
            # Migrate old single-user format
            if MY_CHAT_ID:
                return {
                    "users": {
                        MY_CHAT_ID: {
                            "tasks": data.get("tasks", []),
                            "history": data.get("history", []),
                            "context": "",
                            "checkin_enabled": True,
                        }
                    }
                }
            return {"users": {}}
        return data
    return {"users": {}}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

state = load_state()

def get_user(chat_id: int) -> dict:
    key = str(chat_id)
    if key not in state["users"]:
        state["users"][key] = {
            "tasks": [],
            "history": [],
            "context": "",
            "checkin_enabled": False,
        }
    return state["users"][key]

# ---------- OpenAI chat ----------

SYSTEM_PROMPT = """You are a personal secretary and accountability coach bot on Telegram.

Your job:
1. Be a helpful, direct assistant.
2. Proactively hold the user accountable for their goals and tasks.
3. During check-ins, ask about specific tasks from their task list.
4. Be warm but firm. Don't accept vague excuses without gentle pushback.
5. Keep responses concise — this is a chat, not an essay.
6. Don't offer hotlines or unsolicited emotional support suggestions.
7. Correct English mistakes naturally and briefly when they occur.
{context_section}
The user's tracked tasks: {tasks}
"""

async def chat(chat_id: int, user_message: str, system_override: str = None) -> str:
    user = get_user(chat_id)
    context_section = f"\nAbout this user: {user['context']}\n" if user["context"] else ""
    system = (system_override or SYSTEM_PROMPT).format(
        tasks=", ".join(user["tasks"]) if user["tasks"] else "none set yet",
        context_section=context_section,
    )
    user["history"].append({"role": "user", "content": user_message})
    if len(user["history"]) > MAX_HISTORY:
        user["history"] = user["history"][-MAX_HISTORY:]

    response = await openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": system}] + user["history"],
        max_tokens=600,
        temperature=0.7,
    )
    reply = response.choices[0].message.content.strip()
    user["history"].append({"role": "assistant", "content": reply})
    save_state(state)
    return reply

# ---------- scheduled jobs ----------

async def morning_checkin(context: ContextTypes.DEFAULT_TYPE):
    prompt = (
        "It's morning check-in time. Greet the user briefly, "
        "then ask what they plan to work on today from their task list. "
        "Pick 1-2 specific tasks to focus on."
    )
    for chat_id_str, user in list(state["users"].items()):
        if user.get("checkin_enabled"):
            try:
                reply = await chat(int(chat_id_str), prompt)
                await context.bot.send_message(chat_id=int(chat_id_str), text=reply)
            except Exception as e:
                logger.error("Morning check-in failed for %s: %s", chat_id_str, e)

async def evening_checkin(context: ContextTypes.DEFAULT_TYPE):
    prompt = (
        "It's evening check-in time. Ask the user how their day went. "
        "Ask specifically about what progress they made on their tasks. "
        "If they didn't do much, gently push back and encourage them to do at least one small thing."
    )
    for chat_id_str, user in list(state["users"].items()):
        if user.get("checkin_enabled"):
            try:
                reply = await chat(int(chat_id_str), prompt)
                await context.bot.send_message(chat_id=int(chat_id_str), text=reply)
            except Exception as e:
                logger.error("Evening check-in failed for %s: %s", chat_id_str, e)

# ---------- command handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_user(update.effective_chat.id)
    save_state(state)
    await update.message.reply_text(
        "Secretary bot is active.\n\n"
        "Commands:\n"
        "/tasks — show your tracked tasks\n"
        "/addtask <task> — add a task\n"
        "/removetask <number> — remove a task\n"
        "/setcontext <text> — tell me about yourself and your goals\n"
        "/context — show your current context\n"
        "/subscribe — enable daily check-ins (08:00 & 21:00 UTC+3)\n"
        "/unsubscribe — disable daily check-ins\n"
        "/checkin — trigger a manual check-in\n"
        "/clear — clear conversation history\n\n"
        "Or just talk to me."
    )

async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    if not user["tasks"]:
        await update.message.reply_text("No tasks set. Use /addtask to add one.")
        return
    text = "Your tracked tasks:\n" + "\n".join(
        f"{i+1}. {t}" for i, t in enumerate(user["tasks"])
    )
    await update.message.reply_text(text)

async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task = " ".join(context.args).strip()
    if not task:
        await update.message.reply_text("Usage: /addtask <task description>")
        return
    user = get_user(update.effective_chat.id)
    user["tasks"].append(task)
    save_state(state)
    await update.message.reply_text(f"Added: {task}")

async def remove_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    try:
        idx = int(context.args[0]) - 1
        removed = user["tasks"].pop(idx)
        save_state(state)
        await update.message.reply_text(f"Removed: {removed}")
    except (IndexError, ValueError):
        await update.message.reply_text(
            "Usage: /removetask <number>  (use /tasks to see numbers)"
        )

async def set_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(
            "Usage: /setcontext <description of yourself and your goals>"
        )
        return
    user = get_user(update.effective_chat.id)
    user["context"] = text
    save_state(state)
    await update.message.reply_text(
        "Context saved. I'll use this to personalize our conversations."
    )

async def show_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    if not user["context"]:
        await update.message.reply_text(
            "No context set. Use /setcontext to tell me about yourself."
        )
        return
    await update.message.reply_text(f"Your context:\n{user['context']}")

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    user["checkin_enabled"] = True
    save_state(state)
    await update.message.reply_text(
        "Daily check-ins enabled. You'll hear from me at 08:00 and 21:00 (UTC+3 / Israel time)."
    )

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    user["checkin_enabled"] = False
    save_state(state)
    await update.message.reply_text("Daily check-ins disabled.")

async def manual_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = (
        "The user requested a manual check-in. "
        "Ask what's on their mind and how their tasks are going."
    )
    reply = await chat(update.effective_chat.id, prompt)
    await update.message.reply_text(reply)

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    user["history"] = []
    save_state(state)
    await update.message.reply_text("Conversation history cleared.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = await chat(update.effective_chat.id, update.message.text)
    await update.message.reply_text(reply)

# ---------- main ----------

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("tasks", show_tasks))
    app.add_handler(CommandHandler("addtask", add_task))
    app.add_handler(CommandHandler("removetask", remove_task))
    app.add_handler(CommandHandler("setcontext", set_context))
    app.add_handler(CommandHandler("context", show_context))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CommandHandler("checkin", manual_checkin))
    app.add_handler(CommandHandler("clear", clear_history))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Scheduled check-ins (Israel time = UTC+3)
    # Morning: 08:00 Israel = 05:00 UTC
    # Evening: 21:00 Israel = 18:00 UTC
    job_queue = app.job_queue
    job_queue.run_daily(morning_checkin, time=datetime.strptime("05:00", "%H:%M").time())
    job_queue.run_daily(evening_checkin, time=datetime.strptime("18:00", "%H:%M").time())

    logger.info("Bot started.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
