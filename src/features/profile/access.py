"""Require a saved hall profile before using resident features."""

from functools import wraps

from features.profile import repository


def require_profile(ctx):
    if (repository.get_profile(ctx.user_id) or {}).get("block"):
        return True
    ctx.telegram.send_message(ctx.chat_id, "Please create a /profile first.", parse_mode=None)
    return False


def requires_profile(handler):
    @wraps(handler)
    def guarded(ctx):
        if not require_profile(ctx):
            if ctx.callback_id:
                ctx.telegram.answer_callback_query(ctx.callback_id)
            return
        return handler(ctx)

    return guarded
