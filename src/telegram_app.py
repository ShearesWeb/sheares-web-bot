import logging
import os

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

LOG = logging.getLogger(__name__)

REPLY_TEXT = "\n\n".join(
    [
        "🚀📊 Want to learn how to broadcast your food polls to all your favourite groups and channels on Telegram?",
        "Join SHeares Web at our Telegram Bot WorkSHop next Friday and discover how to level up your game! 🤖✨",
        "🔥 Learn how to create your own Telegram Bot customized to your use case (type /help to explore!)",
        "🎁 FREE BBT for the first 15 sign-ups!",
        "Don’t miss out — secure your spot now 👇",
        "🔗 https://nus.syd1.qualtrics.com/jfe/form/SV_5jURdLp4j5Yxeaq",
    ]
)
NUMBER_STICKER_FILE_ID = (
    "CAACAgIAAyEFAASb_-ZOAAIBoGmEsbMw7p38xCOcv2n7n4l10JkYAALDjQACT0-gSn3s_kVfc7_AOAQ"
)
FOOD_POLL_OPTIONS = [
    "McDonald's",
    "Fatty Bom Bom",
    "Fong Hup",
    "Stuff'd",
    "Jollibee",
    "Wingstop",
]
SLURP_POLL_OPTIONS = ["Each A Cup", "Acai", "Chagee", "Playmade"]
HELP_TEXT = "\n".join(
    [
        "Available commands:",
        "/ff - Food poll",
        "/slurp - Dessert / BBT poll",
        "/help - Show this help",
        "",
        "Auto responses:",
        "Send any message with 6 or 7 to get the sticker.",
    ]
)


async def handle_ff(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    await message.reply_poll(
        question="What food should we get?",
        options=FOOD_POLL_OPTIONS,
        is_anonymous=False,
    )


async def handle_slurp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    await message.reply_poll(
        question="What dessert / BBT should we get?",
        options=SLURP_POLL_OPTIONS,
        is_anonymous=False,
    )


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    await message.reply_text(HELP_TEXT, parse_mode=None)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    text = message.text or message.caption or ""
    lower_text = text.lower()

    if "6" in text or "7" in text:
        try:
            await context.bot.send_sticker(
                chat_id=message.chat_id, sticker=NUMBER_STICKER_FILE_ID
            )
        except Exception as exc:
            LOG.error("Failed to send sticker: %s", exc)


def get_bot_token() -> str:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("Missing required environment variable: TELEGRAM_BOT_TOKEN")
    return bot_token


def build_application(bot_token: str):
    application = ApplicationBuilder().token(bot_token).build()
    application.add_handler(CommandHandler("ff", handle_ff))
    application.add_handler(CommandHandler("slurp", handle_slurp))
    application.add_handler(CommandHandler("help", handle_help))
    application.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, handle_message))
    return application
