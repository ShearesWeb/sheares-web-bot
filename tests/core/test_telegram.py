import pytest

from core import telegram
from core.telegram import FileTooLargeError, TelegramClient, TelegramError


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


def test_send_message_supports_telegram_people_picker(posts):
    calls, responses = posts
    responses.append(FakeResponse(200, {"ok": True, "result": {"message_id": 1}}))
    markup = {"keyboard": [[{"text": "Choose people", "request_users": {"request_id": 1, "user_is_bot": False}}]]}

    TelegramClient("tok").send_message(10, "Choose people", parse_mode=None, reply_markup=markup)

    assert calls[0]["json"] == {"chat_id": 10, "text": "Choose people", "reply_markup": markup}


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


class StreamResponse:
    def __init__(self, chunks, status_code=200):
        self.chunks = chunks
        self.status_code = status_code
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_content(self, chunk_size):
        yield from self.chunks


@pytest.fixture
def downloads(monkeypatch):
    calls, responses = [], []

    def get(url, stream, timeout):
        calls.append({"url": url, "stream": stream, "timeout": timeout})
        return responses.pop(0)

    monkeypatch.setattr(telegram.requests, "get", get)
    return calls, responses


def test_receipt_text_can_be_sent_without_markdown(posts):
    calls, responses = posts
    responses.append(FakeResponse(200, {"ok": True, "result": {}}))

    TelegramClient("tok").send_message(10, "Noodles_[special]* - 8.00", parse_mode=None)

    assert calls[0]["json"] == {"chat_id": 10, "text": "Noodles_[special]* - 8.00"}


def test_download_fetches_getfile_path_and_streams_bytes(posts, downloads):
    calls, responses = posts
    get_calls, streams = downloads
    responses.append(FakeResponse(200, {"ok": True, "result": {"file_path": "photos/receipt.jpg", "file_size": 6}}))
    stream = StreamResponse([b"abc", b"def"])
    streams.append(stream)

    content = TelegramClient("tok").download_file("receipt-id", max_bytes=6)

    assert content == b"abcdef"
    assert calls[0]["url"].endswith("/getFile")
    assert calls[0]["json"] == {"file_id": "receipt-id"}
    assert get_calls == [{
        "url": "https://api.telegram.org/file/bottok/photos/receipt.jpg", "stream": True, "timeout": 10,
    }]
    assert stream.closed


def test_download_rejects_oversized_metadata_without_downloading(posts, downloads):
    _, responses = posts
    get_calls, _ = downloads
    responses.append(FakeResponse(200, {"ok": True, "result": {"file_path": "large.jpg", "file_size": 7}}))

    with pytest.raises(FileTooLargeError):
        TelegramClient("tok").download_file("receipt-id", max_bytes=6)

    assert get_calls == []


def test_download_enforces_size_even_when_metadata_omits_it(posts, downloads):
    _, responses = posts
    _, streams = downloads
    responses.append(FakeResponse(200, {"ok": True, "result": {"file_path": "large.jpg"}}))
    stream = StreamResponse([b"abc", b"defg"])
    streams.append(stream)

    with pytest.raises(FileTooLargeError):
        TelegramClient("tok").download_file("receipt-id", max_bytes=6)

    assert stream.closed


def test_download_network_errors_do_not_expose_bot_token(posts, monkeypatch):
    _, responses = posts
    responses.append(FakeResponse(200, {"ok": True, "result": {"file_path": "receipt.jpg"}}))

    def fail(*args, **kwargs):
        raise telegram.requests.Timeout("https://api.telegram.org/file/botsecret-token/receipt.jpg")

    monkeypatch.setattr(telegram.requests, "get", fail)
    with pytest.raises(TelegramError, match="Could not download") as exc:
        TelegramClient("secret-token").download_file("receipt-id", max_bytes=100)

    assert "secret-token" not in str(exc.value)
    assert exc.value.__suppress_context__
