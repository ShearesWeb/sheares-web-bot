
from types import SimpleNamespace
from unittest.mock import Mock, patch

from features.auth.handlers import link_command


def make_context(user_id, chat_id):
    return SimpleNamespace(
        user_id=user_id,
        chat_id=chat_id,
        telegram=Mock(),
    )


def test_link_in_private_chat():
    ctx = make_context(12345, 12345)

    with patch("features.auth.handlers.service.create_link_request") as create:
        create.return_value = "test-token"
        link_command(ctx)

    create.assert_called_once_with(12345)
    ctx.telegram.send_message.assert_called_once()


def test_link_in_group_chat():
    ctx = make_context(12345, -100987654321)

    with patch("features.auth.handlers.service.create_link_request") as create:
        link_command(ctx)

    create.assert_not_called()
    ctx.telegram.send_message.assert_called_once()

    message = ctx.telegram.send_message.call_args.args[1]
    assert "private chat" in message.lower()
