import pytest

from core.router import Router
from support import callback_update, message_update


@pytest.fixture
def router():
    return Router()


def test_command_matches_first_token_and_ignores_bot_suffix(router, services):
    seen = []
    router.command("/wash")(lambda ctx: seen.append(ctx.text))

    for text in ("/wash@ShearesBot now", "/washing", "hello /wash", ""):
        router.dispatch_update(message_update(text), services)

    assert seen == ["/wash@ShearesBot now"]


def test_command_context_carries_ids_and_services(router, services):
    seen = []
    router.command("/help")(seen.append)

    router.dispatch_update(message_update("/help", user_id=7, chat_id=70), services)

    (ctx,) = seen
    assert (ctx.user_id, ctx.chat_id, ctx.text, ctx.data, ctx.message_id) == (7, 70, "/help", "", None)
    assert ctx.telegram is services.telegram
    assert ctx.scheduler is services.scheduler


def test_stacked_command_decorators_share_one_handler(router, services):
    seen = []

    @router.command("/help")
    @router.command("/start")
    def help_command(ctx):
        seen.append(ctx.text)

    router.dispatch_update(message_update("/start"), services)
    router.dispatch_update(message_update("/help"), services)

    assert seen == ["/start", "/help"]


def test_callback_longest_matching_prefix_wins(router, services):
    seen = []
    router.callback("profile_")(lambda ctx: seen.append("generic"))
    router.callback("profile_blk_")(lambda ctx: seen.append("block"))

    router.dispatch_update(callback_update("profile_blk_A"), services)
    router.dispatch_update(callback_update("profile_other"), services)
    router.dispatch_update(callback_update("unknown_x"), services)

    assert seen == ["block", "generic"]


def test_callback_user_is_the_clicker_not_the_bot(router, services):
    seen = []
    router.callback("bump_")(seen.append)

    router.dispatch_update(callback_update("bump_W1", user_id=5, chat_id=50, message_id=77), services)

    (ctx,) = seen
    assert (ctx.user_id, ctx.chat_id, ctx.message_id, ctx.data, ctx.text) == (5, 50, 77, "bump_W1", "")


@pytest.mark.parametrize("register", [
    lambda r: r.command("/x"),
    lambda r: r.callback("x_"),
    lambda r: r.job("x"),
])
def test_duplicate_registration_raises(router, register):
    register(router)(lambda *a: None)

    with pytest.raises(ValueError, match="already registered"):
        register(router)(lambda *a: None)


def test_ignores_anonymous_and_malformed_updates(router, services):
    seen = []
    router.command("/help")(seen.append)
    router.callback("bump_")(seen.append)
    no_sender = message_update("/help")
    del no_sender["message"]["from"]
    channel_post = message_update("/help")
    channel_post["message"]["sender_chat"] = {"id": -100}
    no_message = callback_update("bump_W1")
    del no_message["callback_query"]["message"]
    no_data = callback_update("bump_W1")
    del no_data["callback_query"]["data"]

    for update in (no_sender, channel_post, no_message, no_data, {"update_id": 9, "edited_message": {}}):
        router.dispatch_update(update, services)

    assert seen == []


def test_dispatch_job_passes_payload_and_telegram(router, services):
    seen = []
    router.job("laundry_done")(lambda payload, telegram: seen.append((payload, telegram)))

    router.dispatch_job("laundry_done", {"chat_id": 1}, services.telegram)

    assert seen == [({"chat_id": 1}, services.telegram)]


def test_dispatch_unknown_job_raises(router, services):
    with pytest.raises(KeyError, match="nope"):
        router.dispatch_job("nope", {}, services.telegram)
