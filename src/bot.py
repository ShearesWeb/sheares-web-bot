import logging
import os
import re

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters,
)

REPLY_TEXT = "\n\n".join(
    [
        "🚀📊 Want to learn how to broadcast your food polls to all your favourite groups and channels on Telegram?",
        "Join SHeares Web at our Telegram Bot WorkSHop next Friday and discover how to level up your game! 🤖✨",
        "🔥 Learn how to create your own Telegram Bot customized to your use case (type /67 to try!)",
        "🎁 FREE BBT for the first 15 sign-ups!",
        "Don’t miss out — secure your spot now 👇",
        "🔗 https://nus.syd1.qualtrics.com/jfe/form/SV_5jURdLp4j5Yxeaq",
    ]
)
NUMBER_STICKER_FILE_ID = "CAACAgIAAyEFAASb_-ZOAAIBoGmEsbMw7p38xCOcv2n7n4l10JkYAALDjQACT0-gSn3s_kVfc7_AOAQ"
COMMAND_67_RE = re.compile(r"^/67(?:@\\w+)?(?:\\s+(\\d+))?", re.IGNORECASE)


async def handle_poll(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.poll:
        return

    try:
        await message.reply_text(REPLY_TEXT, parse_mode=None)
    except Exception as exc:
        logging.warning("Reply failed, sending fallback message: %s", exc)
        try:
            await context.bot.send_message(chat_id=message.chat_id, text=REPLY_TEXT, parse_mode=None)
        except Exception as fallback_exc:
            logging.error("Fallback send failed: %s", fallback_exc)

async def handle_number_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    text = message.text or message.caption or ""
    if "6" not in text and "7" not in text:
        return

    try:
        await context.bot.send_sticker(chat_id=message.chat_id, sticker=NUMBER_STICKER_FILE_ID)
    except Exception as exc:
        logging.error("Failed to send sticker: %s", exc)

def main() -> None:
    load_dotenv()

    token = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Missing BOT_TOKEN/TELEGRAM_BOT_TOKEN in environment.")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = ApplicationBuilder().token(token).build()

    # Polls in groups, private chats, and channels
    app.add_handler(MessageHandler(filters.POLL, handle_poll))
    # Messages containing 6 or 7 (including captions)
    app.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, handle_number_message))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
