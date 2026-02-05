import asyncio
import base64
import json
import logging
import os
import re
from typing import Any, Dict, Optional

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

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
NUMBER_STICKER_FILE_ID = (
    "CAACAgIAAyEFAASb_-ZOAAIBoGmEsbMw7p38xCOcv2n7n4l10JkYAALDjQACT0-gSn3s_kVfc7_AOAQ"
)
COMMAND_67_RE = re.compile(r"^/67(?:@\\w+)?(?:\\s+(\\d+))?", re.IGNORECASE)

async def handle_poll(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.poll:
        return

    try:
        await message.reply_text(REPLY_TEXT, parse_mode=None)
    except Exception as exc:
        LOG.warning("Reply failed, sending fallback message: %s", exc)
        try:
            await context.bot.send_message(
                chat_id=message.chat_id, text=REPLY_TEXT, parse_mode=None
            )
        except Exception as fallback_exc:
            LOG.error("Fallback send failed: %s", fallback_exc)


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
        LOG.error("Failed to send sticker: %s", exc)


def _get_bot_token() -> str:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("Missing required environment variable: TELEGRAM_BOT_TOKEN")
    return bot_token


def _extract_update_payload(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(event, dict):
        return None

    body = event.get("body")
    if body is None:
        if "update_id" in event:
            return event
        return None

    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")

    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8")

    if isinstance(body, str):
        body = body.strip()
        if not body:
            return None
        return json.loads(body)

    return None


async def _process_update(payload: Dict[str, Any]) -> None:
    bot_token = _get_bot_token()
    application = ApplicationBuilder().token(bot_token).build()
    application.add_handler(MessageHandler(filters.POLL, handle_poll))
    application.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, handle_number_message))

    await application.initialize()
    try:
        update = Update.de_json(payload, application.bot)
        if update is None:
            return
        await application.process_update(update)
    finally:
        await application.shutdown()


def handler(event, context):
    payload = _extract_update_payload(event or {})
    if payload is None:
        LOG.info("No update payload received.")
        return {"statusCode": 200, "body": json.dumps("No update")}

    asyncio.run(_process_update(payload))
    return {"statusCode": 200, "body": json.dumps("OK")}


if __name__ == "__main__":
    handler({}, None)
