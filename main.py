"""Telegram bot entry point.

Polling in development, webhook in production. Every handler is gated by a
single-user authorization check so the bot only responds to Mikey.
"""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.constants import ChatAction
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
        "Ready.\n\n"
        "Send me a task or meeting in plain English and I'll find a slot.\n\n"
        "Commands:\n"
        "/today - today's schedule\n"
        "/week - this week's schedule\n"
        "/clear - reset our conversation context\n"
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
        "/clear - reset our conversation context\n"
        "/help - this menu\n\n"
        "Or just message me normally:\n"
        "  - 'New task: review proposal, 45 min'\n"
        "  - 'Block 9am tomorrow for deep work, 2 hours'\n"
        "  - 'Move my 3pm meeting to 4pm'\n"
        "  - 'What's on Thursday?'"
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
    await update.message.reply_text("Conversation cleared.")


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


# --- Bootstrap -------------------------------------------------------------

def build_application() -> Application:
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("today", cmd_today))
    application.add_handler(CommandHandler("week", cmd_week))
    application.add_handler(CommandHandler("clear", cmd_clear))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return application


def main() -> None:
    db.init_db()
    application = build_application()

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
