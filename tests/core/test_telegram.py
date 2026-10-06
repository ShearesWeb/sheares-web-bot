import pytest

from core import telegram
from core.telegram import TelegramClient, TelegramError


class FakeResponse:
    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self.text = text
        self._body = body
        self.headers = {"Content-Type": "application/json" if body is not None else "text/html"}

    def json(self):
        return self._body


@pytest.fixture
def posts(monkeypatch):
    calls = []
    responses = []

    def fake_post(url, json, timeout):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return responses.pop(0)

    monkeypatch.setattr(telegram.requests, "post", fake_post)
    return calls, responses


def test_send_message_posts_markdown_with_keyboard(posts):
    calls, responses = posts
    responses.append(FakeResponse(200, {"ok": True, "result": {"message_id": 1}}))

    TelegramClient("tok").send_message(10, "*hi*", [[{"text": "A", "callback_data": "a"}]])

    assert calls == [{
        "url": "https://api.telegram.org/bottok/sendMessage",
        "json": {
            "chat_id": 10, "text": "*hi*", "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": [[{"text": "A", "callback_data": "a"}]]},
        },
        "timeout": 10,
    }]


def test_edit_message_without_keyboard_omits_reply_markup(posts):
    calls, responses = posts
    responses.append(FakeResponse(200, {"ok": True, "result": True}))

    TelegramClient("tok").edit_message(10, 99, "done")

    assert calls[0]["url"].endswith("/editMessageText")
    assert calls[0]["json"] == {"chat_id": 10, "message_id": 99, "text": "done", "parse_mode": "Markdown"}


def test_raises_with_telegram_description_when_not_ok(posts):
    _, responses = posts
    responses.append(FakeResponse(400, {"ok": False, "description": "Bad Request: chat not found"}))

    with pytest.raises(TelegramError, match="sendMessage failed with HTTP 400: Bad Request: chat not found"):
        TelegramClient("tok").send_message(10, "hi")


def test_raises_on_non_json_error_page(posts):
    _, responses = posts
    responses.append(FakeResponse(502, text="<html>Bad Gateway</html>"))

    with pytest.raises(TelegramError, match="HTTP 502: <html>Bad Gateway</html>"):
        TelegramClient("tok").send_message(10, "hi")


def test_get_updates_sends_offset_and_outlasts_long_poll(posts):
    calls, responses = posts
    responses.append(FakeResponse(200, {"ok": True, "result": []}))
    responses.append(FakeResponse(200, {"ok": True, "result": [{"update_id": 3}]}))
    client = TelegramClient("tok")

    assert client.get_updates(None, timeout=30) == []
    assert client.get_updates(3, timeout=30) == [{"update_id": 3}]

    assert calls[0]["json"] == {"timeout": 30, "allowed_updates": ["message", "callback_query"]}
    assert calls[0]["timeout"] == 40
    assert calls[1]["json"]["offset"] == 3
