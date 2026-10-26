import base64
import json
import logging
from unittest.mock import Mock

import pytest

import app
from core.router import Router
from core.telegram import TelegramError
from support import FakeTelegram, message_update


@pytest.fixture
def router(monkeypatch, services):
    router = Router()
    monkeypatch.setattr(app, "router", router)
    monkeypatch.setattr(app, "_lambda_services", lambda: services)
    return router


def api_event(body, base64_encoded=False):
    if base64_encoded:
        body = base64.b64encode(body.encode()).decode()
    return {
        "requestContext": {"http": {"method": "POST"}},
        "headers": {
            "X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret",
        },
        "body": body,
        "isBase64Encoded": base64_encoded,
    }


@pytest.mark.parametrize("base64_encoded", [False, True])
def test_webhook_dispatches_update(router, base64_encoded):
    seen = []
    router.command("/help")(lambda ctx: seen.append(ctx.chat_id))

    response = app.lambda_handler(api_event(json.dumps(message_update("/help")), base64_encoded), None)

    assert response == {"statusCode": 200}
    assert seen == [10]


@pytest.mark.parametrize("body", [None, ""])
def test_webhook_with_empty_body_is_acknowledged(router, body):
    assert app.lambda_handler(api_event(body), None) == {"statusCode": 200}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
    ],
)
def test_webhook_rejects_untrusted_callers(router, headers):
    seen = []
    router.command("/help")(lambda ctx: seen.append(ctx.chat_id))

    event = api_event(json.dumps(message_update("/help")))
    event["headers"] = headers

    assert app.lambda_handler(event, None) == {"statusCode": 401}
    assert seen == []


def test_webhook_handler_failure_is_logged_and_acknowledged(router, caplog):
    @router.command("/boom")
    def boom(ctx):
        raise RuntimeError("kaput")

    with caplog.at_level(logging.ERROR):
        response = app.lambda_handler(api_event(json.dumps(message_update("/boom"))), None)

    assert response == {"statusCode": 200}
    assert "Failed to handle Telegram update" in caplog.text
    assert "kaput" in caplog.text


def test_job_event_runs_job_with_telegram(router, services):
    seen = []
    router.job("laundry_done")(lambda payload, telegram: seen.append((payload, telegram)))

    app.lambda_handler({"job": "laundry_done", "payload": {"chat_id": 1}}, None)

    assert seen == [({"chat_id": 1}, services.telegram)]


def test_job_failure_propagates_for_scheduler_retry(router):
    @router.job("laundry_done")
    def boom(payload, telegram):
        raise RuntimeError("kaput")

    with pytest.raises(RuntimeError, match="kaput"):
        app.lambda_handler({"job": "laundry_done", "payload": {}}, None)


def test_unrecognised_event_raises(router):
    with pytest.raises(ValueError, match=r"Unrecognised event with keys \['foo'\]"):
        app.lambda_handler({"foo": 1}, None)


class StopPolling(Exception):
    pass


class PollingTelegram(FakeTelegram):
    def __init__(self, webhook_url, batches):
        super().__init__()
        self.webhook_url = webhook_url
        self.batches = list(batches)
        self.offsets = []

    def get_webhook_info(self):
        return {"url": self.webhook_url}

    def get_updates(self, offset, timeout):
        self.offsets.append(offset)
        if not self.batches:
            raise StopPolling
        batch = self.batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return batch


def test_poll_refuses_bot_with_webhook(monkeypatch):
    telegram = PollingTelegram("https://example.com/webhook", [])
    monkeypatch.setattr(app, "TelegramClient", lambda token: telegram)

    with pytest.raises(SystemExit, match="own BotFather bot"):
        app.poll()

    assert telegram.offsets == []


def test_poll_survives_failing_update_and_advances_offset(monkeypatch, caplog):
    router = Router()
    seen = []

    @router.command("/boom")
    def boom(ctx):
        raise RuntimeError("kaput")

    router.command("/ok")(lambda ctx: seen.append(ctx.text))
    telegram = PollingTelegram("", [[message_update("/boom", update_id=7), message_update("/ok", update_id=8)]])
    monkeypatch.setattr(app, "router", router)
    monkeypatch.setattr(app, "TelegramClient", lambda token: telegram)

    with caplog.at_level(logging.ERROR), pytest.raises(StopPolling):
        app.poll()

    assert seen == ["/ok"]
    assert telegram.offsets == [None, 9]
    assert "Failed to handle update 7" in caplog.text


def test_poll_retries_network_failures_with_capped_backoff_and_keeps_offset(monkeypatch, caplog):
    router = Router()
    seen = []
    router.command("/ok")(lambda ctx: seen.append(ctx.update_id))
    telegram = PollingTelegram("", [
        [message_update("/ok", update_id=7)],
        TelegramError("secret-token"), *[TelegramError("Bad gateway", code=503) for _ in range(6)],
        [], TelegramError("Connection lost"), [message_update("/ok", update_id=8)],
    ])
    sleep = Mock()
    monkeypatch.setattr(app, "router", router)
    monkeypatch.setattr(app, "TelegramClient", lambda token: telegram)
    monkeypatch.setattr(app.time, "sleep", sleep)

    with caplog.at_level(logging.INFO), pytest.raises(StopPolling):
        app.poll()

    assert seen == [7, 8]
    assert telegram.offsets == [None, *([8] * 10), 9]
    assert [call.args[0] for call in sleep.call_args_list] == [1, 2, 4, 8, 16, 30, 30, 1]
    assert "Telegram connection restored" in caplog.text
    assert "secret-token" not in caplog.text


def test_poll_retries_the_startup_connection_check(monkeypatch):
    telegram = PollingTelegram("", [])
    attempts = iter([TelegramError("Offline"), {"url": ""}])

    def get_webhook_info():
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    sleep = Mock()
    monkeypatch.setattr(telegram, "get_webhook_info", get_webhook_info)
    monkeypatch.setattr(app, "TelegramClient", lambda token: telegram)
    monkeypatch.setattr(app.time, "sleep", sleep)

    with pytest.raises(StopPolling):
        app.poll()

    sleep.assert_called_once_with(1)
    assert telegram.offsets == [None]


@pytest.mark.parametrize("code", [401, 403, 409, 429])
def test_poll_does_not_retry_permanent_or_rate_limit_errors(monkeypatch, code):
    sleep = Mock()
    monkeypatch.setattr(app.time, "sleep", sleep)

    def get_updates():
        raise TelegramError("Rejected", code=code)

    with pytest.raises(TelegramError) as error:
        app._poll_request(get_updates)

    assert error.value.code == code
    sleep.assert_not_called()
