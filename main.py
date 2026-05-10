"""Telegram bot entry point.

Polling in development, webhook in production. Every handler is gated by a
single-user authorization check so the bot only responds to Mikey.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import time

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import calendar_tools
import config
import database as db
from agent import run_agent


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# --- Authorization ---------------------------------------------------------

async def check_allowed_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if user is None or user.id != config.TELEGRAM_ALLOWED_USER_ID:
        if update.message:
            await update.message.reply_text("Unauthorized.")
        logger.warning(
            "Rejected message from unauthorized user_id=%s",
            user.id if user else "<none>",
        )
        return False
    return True


# --- Command handlers ------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_allowed_user(update, context):
        return
    await update.message.reply_text(
        f"{config.AGENT_NAME} here.\n\n"
        "Send me a task or meeting in plain English and I'll find a slot.\n\n"
        f"You'll get a morning briefing at 8am EST and an evening review at 8pm EST.\n\n"
        "Commands:\n"
        "/today - today's schedule\n"
        "/week - this week's schedule\n"
        "/lessons - what I've learned about your preferences\n"
        "/clear - reset our short-term conversation context\n"
        "/help - this menu"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_allowed_user(update, context):
        return
    await update.message.reply_text(
        "Commands:\n"
        "/start - welcome message\n"
        "/today - today's schedule\n"
        "/week - this week's schedule (Mon-Sun)\n"
        "/lessons - long-term scheduling rules I've learned\n"
        "/clear - reset our short-term conversation context (lessons preserved)\n"
        "/help - this menu\n\n"
        "Or just message me normally:\n"
        "  - 'New task: review proposal, 45 min'\n"
        "  - 'Block 9am tomorrow for deep work, 2 hours'\n"
        "  - 'Move my 3pm meeting to 4pm'\n"
        "  - 'I'm in office Tuesday, block it'\n"
        "  - 'What's on Thursday?'\n\n"
        "Office/OOO days: Mark them as ALL-DAY events on your Google Calendar with "
        "titles like 'Office', 'OOO', or 'W2 onsite'. I won't schedule on those days "
        "unless you explicitly tell me to."
    )


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_allowed_user(update, context):
        return
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, calendar_tools.get_todays_schedule)
    if result["success"]:
        await update.message.reply_text(result["summary"])
    else:
        await update.message.reply_text(f"Couldn't load today's schedule: {result['error']}")


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_allowed_user(update, context):
        return
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, calendar_tools.get_weeks_schedule)
    if not result["success"]:
        await update.message.reply_text(f"Couldn't load this week: {result['error']}")
        return

    days = result["days"]
    if not any(days.values()):
        await update.message.reply_text("Nothing scheduled this week.")
        return

    lines = []
    for day_iso, events in days.items():
        if not events:
            continue
        lines.append(f"{day_iso}:")
        for ev in events:
            if ev["all_day"]:
                lines.append(f"  - All day: {ev['title']}")
            else:
                # Strip the date prefix from the formatted "YYYY-MM-DD HH:MM TZ" times.
                start_time = ev["start"][11:16] if len(ev["start"]) >= 16 else ev["start"]
                end_time = ev["end"][11:16] if len(ev["end"]) >= 16 else ev["end"]
                lines.append(f"  - {start_time}-{end_time}: {ev['title']}")
        lines.append("")
    await update.message.reply_text("\n".join(lines).strip())


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_allowed_user(update, context):
        return
    user_id = str(update.effective_user.id)
    db.clear_all_history(user_id)
    db.clear_pending_action(user_id)
    await update.message.reply_text(
        "Conversation cleared. (Your saved scheduling preferences are kept - "
        "use the agent to remove them if needed.)"
    )


async def cmd_lessons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_allowed_user(update, context):
        return
    user_id = str(update.effective_user.id)
    learnings = db.get_learnings(user_id, limit=50)
    if not learnings:
        await update.message.reply_text(
            "No saved preferences yet. I'll learn as we go - tell me when I "
            "schedule something wrong and I'll remember next time."
        )
        return
    lines = ["Saved preferences:"]
    for row in learnings:
        lines.append(f"  - {row['content']}")
    await update.message.reply_text("\n".join(lines))


# --- Message handler -------------------------------------------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_allowed_user(update, context):
        return
    if not update.message or not update.message.text:
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )

    user_id = str(update.effective_user.id)
    text = update.message.text

    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(None, run_agent, user_id, text)

    await update.message.reply_text(response)


# --- Scheduled briefings ---------------------------------------------------
#
# Fired via JobQueue at fixed wall-clock times in EST (UTC-5, no DST shift).
# The chat_id is the user's Telegram user_id - direct-message chats use the
# user_id as the chat_id.

async def morning_briefing(context: ContextTypes.DEFAULT_TYPE) -> None:
    """8am EST: send today's schedule and ask for adjustments."""
    chat_id = config.TELEGRAM_ALLOWED_USER_ID

    loop = asyncio.get_running_loop()
    schedule = await loop.run_in_executor(None, calendar_tools.get_todays_schedule)

    if schedule["success"]:
        message = (
            "Morning. Here's today:\n\n"
            f"{schedule['summary']}\n\n"
            "Anything to add or adjust?"
        )
    else:
        message = (
            "Morning. I couldn't load your calendar - "
            f"{schedule.get('error', 'unknown error')}.\n\n"
            "Anything to schedule today?"
        )

    try:
        await context.bot.send_message(chat_id=chat_id, text=message)
    except TelegramError as exc:
        logger.exception("Failed to send morning briefing: %s", exc)
        return

    # Save to conversation history so the agent has context when the user replies.
    db.save_message(str(chat_id), "assistant", message)
    db.clear_old_history(str(chat_id))


async def evening_review(context: ContextTypes.DEFAULT_TYPE) -> None:
    """8pm EST: prompt for issues (to learn from) and tomorrow's adds."""
    chat_id = config.TELEGRAM_ALLOWED_USER_ID
    message = (
        "Evening check-in.\n\n"
        "Any issues with how I scheduled today? Tell me and I'll save it as a "
        "rule so it doesn't happen again.\n\n"
        "Also - anything last-minute or for tomorrow?"
    )
    try:
        await context.bot.send_message(chat_id=chat_id, text=message)
    except TelegramError as exc:
        logger.exception("Failed to send evening review: %s", exc)
        return

    db.save_message(str(chat_id), "assistant", message)
    db.clear_old_history(str(chat_id))


def schedule_daily_briefings(application: Application) -> None:
    """Register the 8am EST morning briefing and 8pm EST evening review."""
    job_queue = application.job_queue
    if job_queue is None:
        # Should never happen with python-telegram-bot[job-queue]==20.7, but
        # surface clearly if APScheduler isn't installed.
        raise RuntimeError(
            "JobQueue is unavailable. Install python-telegram-bot[job-queue]."
        )

    job_queue.run_daily(
        morning_briefing,
        time=time(hour=8, minute=0, tzinfo=config.EST_FIXED),
        name="kiki_morning_briefing",
    )
    job_queue.run_daily(
        evening_review,
        time=time(hour=20, minute=0, tzinfo=config.EST_FIXED),
        name="kiki_evening_review",
    )
    logger.info("Scheduled daily briefings: 08:00 EST and 20:00 EST")


# --- Bootstrap -------------------------------------------------------------

def build_application() -> Application:
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("today", cmd_today))
    application.add_handler(CommandHandler("week", cmd_week))
    application.add_handler(CommandHandler("clear", cmd_clear))
    application.add_handler(CommandHandler("lessons", cmd_lessons))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return application


def main() -> None:
    db.init_db()
    application = build_application()
    schedule_daily_briefings(application)

    if config.ENVIRONMENT == "production":
        logger.info("Starting in production mode (webhook on port %s)", config.PORT)
        application.run_webhook(
            listen="0.0.0.0",
            port=config.PORT,
            url_path="webhook",
            webhook_url=f"{config.WEBHOOK_URL}/webhook",
        )
    else:
        logger.info("Starting in development mode (long polling)")
        application.run_polling()


if __name__ == "__main__":
    main()
