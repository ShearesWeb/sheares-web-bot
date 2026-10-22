
from urllib.parse import urlencode

from core.config import settings
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
    token = service.create_link_request(ctx.user_id)
    link_url = (
        f"{settings().intranet_base_url.rstrip('/')}/dashboard/link-telegram?"
        f"{urlencode({'token': token})}"
    )

    ctx.telegram.send_message(
        ctx.chat_id,
        "🔗 Your secure Sheares Intranet linking request is ready.\n\n"
        "Open the page below, sign in, review both accounts, and confirm. "
        "This link expires in 10 minutes and works once.",
        keyboard=[[{"text": "Continue securely", "url": link_url}]],
    )
