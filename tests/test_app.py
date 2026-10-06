import base64
import json
import logging

import pytest

import app
from core.router import Router
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
    return {"requestContext": {"http": {"method": "POST"}}, "body": body, "isBase64Encoded": base64_encoded}


@pytest.mark.parametrize("base64_encoded", [False, True])
def test_webhook_dispatches_update(router, base64_encoded):
    seen = []
    router.command("/help")(lambda ctx: seen.append(ctx.chat_id))

    response = app.lambda_handler(api_event(json.dumps(message_update("/help")), base64_encoded), None)

    assert response == {"statusCode": 200}
    assert seen == [10]


@pytest.mark.parametrize("body", [None, ""])
def test_webhook_with_empty_body_is_acknowledged(router, body):
    assert app.lambda_handler({"requestContext": {}, "body": body}, None) == {"statusCode": 200}


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
        return self.batches.pop(0)


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
