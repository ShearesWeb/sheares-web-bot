
from core.router import router
from features.auth import service


@router.command("/link")
def link_command(ctx):
    """Begin linking a Telegram account to Sheares Intranet."""

    # Only allow linking from private Telegram chats.
    if ctx.chat_id != ctx.user_id:
        ctx.telegram.send_message(
            ctx.chat_id,
            "🔒 Please open a private chat with me and send /link.",
        )
        return

    # Create a temporary account-linking request.
    service.create_link_request(ctx.user_id)

    ctx.telegram.send_message(
        ctx.chat_id,
        "🔗 Account-linking request created!\n\n"
        "The Sheares Intranet verification page is "
        "not connected yet.\n\n"
        "This feature is currently in development.",
    )
