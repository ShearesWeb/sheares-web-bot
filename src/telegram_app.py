import logging
import os
import random
from io import BytesIO

from PIL import Image
from telegram import InputFile, Update
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
        "/rizz [person_a] [person_b (optional)] - Random rizz line",
        "/make_sticker - Add as image caption to make a sticker",
        "/help - Show this help",
        "",
        "Auto responses:",
        "Send any message with 6 or 7 to get the sticker.",
    ]
)

RIZZ_LINES = [
    "Hey {person_a}, {person_b} just want to let you know that even Grab can’t deliver someone as sweet as you.",
    
    "Hey {person_a}, {person_b} just want to let you know that if beauty gave hall points, you’d secure more points than me combined for 5 years.",
    
    "Hey {person_a}, {person_b} just want to let you know that you’re the reason our Sheares corridor lights glow brighter at night.",
    
    "Hey {person_a}, {person_b} just want to let you know that if you were a module, I’d overload just to take you twice.",
    
    "Hey {person_a}, {person_b} just want to let you know that I’d willingly tank 9am lectures just to walk you to supper at 2am.",
    
    "Hey {person_a}, {person_b} just want to let you know that even the portable aircon loses its cool when you walk past.",
        
    "Hey {person_a}, {person_b} just want to let you know that you’re the only red flag I’d frame and hang on my wall.",
        
    "Hey {person_a}, {person_b} just want to let you know that you turned my academic comeback into a romantic downfall.",

    "Hey {person_a}, {person_b} just want to let you know that even during reading week, you’re the only thing on my mind.",

    "Hey {person_a}, {person_b} just want to let you know that I’d queue 40 minutes for comm hall food, but I’d wait a lifetime for you.",
    
    "Hey {person_a}, {person_b} just want to let you know that I’d add extra reps at the gym just to impress you.",

    "Hey {person_a}, {person_b} just want to let you know that even the flowers in Block E garden would bloom if they saw us together.",

    "Hey {person_a}, {person_b} just want to let you know that the Block B pond may ripple at night, but my heart does it every time you walk by.",
]


async def handle_ff(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    LOG.info("Handling /ff for chat_id=%s", message.chat_id)

    await message.reply_poll(
        question="What food should we get?",
        options=FOOD_POLL_OPTIONS,
        allows_multiple_answers=True,
        is_anonymous=False,
    )


async def handle_slurp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    LOG.info("Handling /slurp for chat_id=%s", message.chat_id)

    await message.reply_poll(
        question="What dessert / BBT should we get?",
        options=SLURP_POLL_OPTIONS,
        allows_multiple_answers=True,
        is_anonymous=False,
    )

async def handle_rizz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    LOG.info("Handling /rizz for chat_id=%s", message.chat_id)

    user = update.effective_user
    default_name = user.first_name if user and user.first_name else "unknown"

    args = context.args or []
    person_a = args[0] if len(args) > 0 else default_name
    person_b = args[1] if len(args) > 1 else default_name

    line = random.choice(RIZZ_LINES).format(person_a=person_a, person_b=person_b)
    await message.reply_text(line, parse_mode=None)


async def handle_make_sticker_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    caption = message.caption or ""
    if "/make_sticker" not in caption:
        return
    LOG.info("Handling /make_sticker image for chat_id=%s", message.chat_id)

    try:
        if message.photo:
            file = await message.photo[-1].get_file()
        elif message.document and (message.document.mime_type or "").startswith("image/"):
            file = await message.document.get_file()
        else:
            await message.reply_text("Please send an image file.", parse_mode=None)
            return

        file_bytes = await file.download_as_bytearray()
        image = Image.open(BytesIO(file_bytes)).convert("RGBA")
        image.thumbnail((512, 512))

        output = BytesIO()
        image.save(output, format="WEBP")
        output.seek(0)

        await message.reply_sticker(sticker=InputFile(output, filename="sticker.webp"))
    except Exception as exc:
        LOG.exception("Failed to make sticker: %s", exc)
        await message.reply_text("Sorry, I couldn't make a sticker from that image.", parse_mode=None)


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    LOG.info("Handling /help for chat_id=%s", message.chat_id)

    await message.reply_text(REPLY_TEXT, parse_mode=None)
    await message.reply_text(HELP_TEXT, parse_mode=None)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    LOG.info("Handling message for chat_id=%s", message.chat_id)

    text = message.text or message.caption or ""

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
    application.add_handler(CommandHandler("rizz", handle_rizz))
    application.add_handler(CommandHandler("help", handle_help))
    application.add_handler(
        MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_make_sticker_image)
    )
    application.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, handle_message))
    return application
