"""Open bill splitting, balances, and reminders in the bot's private chat."""

from core.telegram import TelegramError

PRIVATE_ACTIONS = {
    "split": ("Open split privately", "Continue in private chat to split a bill."),
    "bills": ("Open bills privately", "View and manage your bills in private chat."),
    "reminders": ("Open reminders privately", "View and manage your pending reminders in private chat."),
}


def offer_private_chat(ctx, action):
    """Return True after directing a non-private chat to the requested flow."""
    if ctx.chat_type == "private":
        return False
    label, text = PRIVATE_ACTIONS[action]
    try:
        username = ctx.telegram.get_me()["username"]
    except TelegramError:
        ctx.telegram.send_message(
            ctx.chat_id, f"Open a private chat with me and send /{action} to continue.", parse_mode=None,
        )
        return True
    ctx.telegram.send_message(
        ctx.chat_id, text + " Tap the button below, then press Start if prompted.",
        [[{"text": label, "url": f"https://t.me/{username}?start={action}"}]], parse_mode=None,
    )
    return True
