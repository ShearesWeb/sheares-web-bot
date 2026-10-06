# Single Lambda Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the receiver, processor and notifier Lambdas with one Lambda whose `src/app.py` only routes events, move application logic into `core/` and `features/`, and let developers run the bot locally with `make dev` configured by `.env`.

**Architecture:** `app.lambda_handler` detects an API Gateway webhook event or an EventBridge Scheduler job event and hands it to a decorator-based router in `core/router.py`. Each feature under `features/` has `handlers.py` (Telegram UI), optionally `service.py` (rules) and `repository.py` (DynamoDB). Running `python src/app.py` long-polls Telegram and runs scheduled jobs in-process.

**Tech Stack:** Python 3.12, requests, boto3, pytest 9.1.1, moto 5.2.3, Terraform 1.11.0 (AWS provider 6.4.0), GNU Make.

**Spec:** `docs/superpowers/specs/2026-10-06-single-lambda-refactor-design.md`

## Global Constraints

- Python 3.12 (`public.ecr.aws/lambda/python:3.12`).
- Behaviour-preserving. Every user-facing string, keyboard layout and `callback_data` format stays exactly as in the current `src/processor/**` code. Copy strings verbatim; the tests in this plan pin them.
- `callback_data` formats unchanged: `profile_create_yes`, `profile_create_no`, `profile_blk_{B}`, `profile_lvl_{B}_{L}`, `profile_yr_{B}_{L}_{Y}`, `drydur_{dur}`, `mclick_{id}_{dur}`, `bump_{id}`, `move_{id}`, `moveto_{from}_{to}`, `over_{id}_{dur}`, `moveride_{from}_{to}`, `cancel`, `collect_{id}`.
- DynamoDB keys and attributes unchanged: users `userID`=`USER#{id}`, `profile` in `PROFILE`/`LAUNDRY`; laundry `block`, `machineID`, `status`, `currentUser`, `currentChatID`, `endTime`, `type`.
- EventBridge schedule names keep the `Laundry_` prefix (the IAM policy only allows `schedule/default/Laundry_*`).
- Lambda function name: `${var.env}-app-func-${var.project_code}-bot`. Handler: `app.lambda_handler`.
- Env var names: `TELEGRAM_BOT_TOKEN`, `USERS_TABLE_NAME`, `LAUNDRY_TABLE_NAME`, `SCHEDULER_ROLE_ARN`, `SCHEDULE_TARGET_ARN`.
- Out of scope (deliberately excluded by the user): no `answerCallbackQuery`, no conditional writes on claim, no special-casing of "message is not modified".
- Repo conventions (`~/.claude/CLAUDE.md`): no em dashes anywhere; comments explain why, not what; no Args/Returns docstrings; don't catch exceptions you can't handle; one paragraph per line in Markdown; commit subjects imperative with no generated-by footer.
- Work on branch `refactor/single-lambda` (already created).

## Review Focus

1. A schedule `run_at` given in a non-UTC timezone must still produce a UTC `at(...)` expression, or reminders fire hours off. Pinned in Task 3.
2. Legacy machine rows that store the chat in `currentUser` and have no `currentChatID` must still get bump and move notifications. Pinned in Task 6.
3. `callback_data` whose parts arrive wrapped in Markdown asterisks (`mclick_*W1*_30`) must still parse. Pinned in Task 6.
4. `make dev` against a bot that has a webhook must refuse to start, and one failing update must not stop the polling loop or replay forever. Pinned in Task 7.
5. Webhook requests with an empty body or a base64-encoded body must return 200 and not raise. Pinned in Task 7.

---

## File Map

| Path | Responsibility |
|---|---|
| `pyproject.toml` | pytest config only |
| `requirements-dev.txt` | local and CI dependencies |
| `Makefile` | `install`, `dev`, `test` |
| `.env.example` | documented local configuration |
| `src/app.py` | Lambda entrypoint and local polling entrypoint |
| `src/core/config.py` | lazy env-backed settings |
| `src/core/db.py` | DynamoDB table accessors |
| `src/core/telegram.py` | Telegram Bot API client |
| `src/core/context.py` | `Services` and `Context` dataclasses |
| `src/core/router.py` | handler registry and dispatch |
| `src/core/scheduler.py` | EventBridge and in-process schedulers |
| `src/features/__init__.py` | imports handler modules so they register |
| `src/features/profile/{handlers,repository}.py` | profile feature |
| `src/features/laundry/{handlers,service,repository}.py` | laundry feature |
| `tests/support.py` | fakes and update builders |
| `tests/conftest.py` | moto and env fixtures |
| `infra/*.tf`, `Dockerfile`, `.github/workflows/2-ci-merge-request.yml`, `README.md` | deployment and docs |

---

### Task 1: Tooling, config, DynamoDB accessors and Telegram client

**Files:**
- Create: `pyproject.toml`, `requirements-dev.txt`, `Makefile`
- Create: `src/core/__init__.py` (empty), `src/core/config.py`, `src/core/db.py`, `src/core/telegram.py`
- Create: `tests/support.py`, `tests/conftest.py`, `tests/core/test_telegram.py`

**Interfaces:**
- Produces: `core.config.settings() -> Settings` with `.telegram_bot_token`, `.users_table_name`, `.laundry_table_name` (cached; `settings.cache_clear()` exists). `core.db.users_table()`, `core.db.laundry_table()` return boto3 `Table`; `core.db._dynamodb.cache_clear()`. `core.telegram.TelegramClient(token)` with `send_message(chat_id, text, keyboard=None)`, `edit_message(chat_id, message_id, text, keyboard=None)`, `get_updates(offset, timeout) -> list[dict]`, `get_webhook_info() -> dict`; `core.telegram.TelegramError`. `tests/support.py`: `FakeTelegram` (`.sent`, `.edited` lists of dicts), `RecordingScheduler` (`.scheduled` list of dicts), `message_update(text, user_id=1, chat_id=10, update_id=1)`, `callback_update(data, user_id=1, chat_id=10, message_id=99)`, `seed_profile(user_id, block, level=3, year=2)`, `seed_machine(block, machine_id, **attrs)`. Fixture `services` in `conftest.py`.

- [ ] **Step 1: Add tooling files**

`pyproject.toml`:

```toml
[tool.pytest.ini_options]
pythonpath = ["src", "tests"]
testpaths = ["tests"]
# Feature test folders reuse basenames like test_handlers.py.
addopts = "--import-mode=importlib"
```

`requirements-dev.txt`:

```
-r src/requirements.txt
# The Lambda runtime ships boto3, so it is only pinned for local runs and tests.
boto3==1.43.108
pytest==9.1.1
moto[dynamodb,scheduler]==5.2.3
```

`Makefile` (recipe lines are indented with a tab):

```make
PYTHON_BIN ?= python3.12
VENV := .venv
PYTHON := $(VENV)/bin/python

.PHONY: install test

install:
	$(PYTHON_BIN) -m venv $(VENV)
	$(PYTHON) -m pip install -r requirements-dev.txt

test:
	$(PYTHON) -m pytest
```

Run: `make install`
Expected: venv created, pip finishes with "Successfully installed ... moto-5.2.3 ... pytest-9.1.1".

- [ ] **Step 2: Write test support and fixtures**

`tests/support.py`:

```python
from core import db


class FakeTelegram:
    """Records outgoing Telegram calls so tests assert on what the user would see."""

    def __init__(self):
        self.sent = []
        self.edited = []

    def send_message(self, chat_id, text, keyboard=None):
        self.sent.append({"chat_id": chat_id, "text": text, "keyboard": keyboard})

    def edit_message(self, chat_id, message_id, text, keyboard=None):
        self.edited.append({"chat_id": chat_id, "message_id": message_id, "text": text, "keyboard": keyboard})


class RecordingScheduler:
    def __init__(self):
        self.scheduled = []

    def schedule(self, name, run_at, job, payload):
        self.scheduled.append({"name": name, "run_at": run_at, "job": job, "payload": payload})


def message_update(text, user_id=1, chat_id=10, update_id=1):
    return {
        "update_id": update_id,
        "message": {"message_id": 5, "from": {"id": user_id}, "chat": {"id": chat_id}, "text": text},
    }


def callback_update(data, user_id=1, chat_id=10, message_id=99):
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cb",
            "from": {"id": user_id},
            "data": data,
            "message": {"message_id": message_id, "from": {"id": 999, "is_bot": True}, "chat": {"id": chat_id}},
        },
    }


def seed_profile(user_id, block, level=3, year=2):
    db.users_table().put_item(Item={
        "userID": f"USER#{user_id}", "profile": "PROFILE", "chatID": str(user_id * 10),
        "block": block, "level": level, "year": year,
    })


def seed_machine(block, machine_id, **attrs):
    db.laundry_table().put_item(Item={"block": block, "machineID": machine_id, **attrs})
```

`tests/conftest.py`:

```python
import boto3
import pytest
from moto import mock_aws

from core import db
from core.config import settings
from core.context import Services
from support import FakeTelegram, RecordingScheduler

TEST_ENV = {
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "ap-southeast-1",
    "TELEGRAM_BOT_TOKEN": "test-token",
    "USERS_TABLE_NAME": "users",
    "LAUNDRY_TABLE_NAME": "laundry",
}


@pytest.fixture(autouse=True)
def aws(monkeypatch):
    """Fresh moto tables and env per test, so nothing reaches real AWS or depends on a developer's .env."""
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    settings.cache_clear()
    db._dynamodb.cache_clear()
    with mock_aws():
        dynamodb = boto3.resource("dynamodb")
        for name, hash_key, range_key in (("users", "userID", "profile"), ("laundry", "block", "machineID")):
            dynamodb.create_table(
                TableName=name,
                BillingMode="PAY_PER_REQUEST",
                KeySchema=[
                    {"AttributeName": hash_key, "KeyType": "HASH"},
                    {"AttributeName": range_key, "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": hash_key, "AttributeType": "S"},
                    {"AttributeName": range_key, "AttributeType": "S"},
                ],
            )
        yield
    settings.cache_clear()
    db._dynamodb.cache_clear()


@pytest.fixture
def services():
    return Services(telegram=FakeTelegram(), scheduler=RecordingScheduler())
```

`conftest.py` imports `core.context`, which Task 2 creates. For this task only, create `src/core/context.py` with the full content from Task 2 Step 3 now (it has no dependencies), and Task 2 then only adds tests for it.

- [ ] **Step 3: Write the failing Telegram client tests**

`tests/core/test_telegram.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/core/test_telegram.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'core.config'` (or `core.telegram`).

- [ ] **Step 5: Implement config, db and Telegram client**

`src/core/__init__.py`: empty file.

`src/core/config.py`:

```python
import os
from dataclasses import dataclass
from functools import cache


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    users_table_name: str
    laundry_table_name: str


@cache
def settings():
    """Reads the environment on first use so modules import without a full environment (tests, tooling)."""
    return Settings(
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        users_table_name=os.environ["USERS_TABLE_NAME"],
        laundry_table_name=os.environ["LAUNDRY_TABLE_NAME"],
    )
```

`src/core/db.py`:

```python
from functools import cache

import boto3

from core.config import settings


@cache
def _dynamodb():
    return boto3.resource("dynamodb")


def users_table():
    return _dynamodb().Table(settings().users_table_name)


def laundry_table():
    return _dynamodb().Table(settings().laundry_table_name)
```

`src/core/telegram.py`:

```python
import requests


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, token):
        self._base_url = f"https://api.telegram.org/bot{token}"

    def send_message(self, chat_id, text, keyboard=None):
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        return self._call("sendMessage", payload)

    def edit_message(self, chat_id, message_id, text, keyboard=None):
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "Markdown"}
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        return self._call("editMessageText", payload)

    def get_updates(self, offset, timeout):
        payload = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        # Telegram holds the request open for `timeout` seconds, so the HTTP timeout must outlast it.
        return self._call("getUpdates", payload, http_timeout=timeout + 10)

    def get_webhook_info(self):
        return self._call("getWebhookInfo", {})

    def _call(self, method, payload, http_timeout=10):
        resp = requests.post(f"{self._base_url}/{method}", json=payload, timeout=http_timeout)
        is_json = resp.headers.get("Content-Type", "").startswith("application/json")
        body = resp.json() if is_json else {}
        if not body.get("ok"):
            detail = body.get("description") or resp.text[:200]
            raise TelegramError(f"{method} failed with HTTP {resp.status_code}: {detail}")
        return body["result"]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `make test`
Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml requirements-dev.txt Makefile src/core tests
git commit -m "Add core config, DynamoDB accessors and Telegram client"
```

---

### Task 2: Context and router

**Files:**
- Create: `src/core/context.py` (already created in Task 1 Step 2; content below is authoritative)
- Create: `src/core/router.py`
- Test: `tests/core/test_router.py`

**Interfaces:**
- Consumes: nothing at runtime.
- Produces: `core.context.Services(telegram, scheduler)`; `core.context.Context(user_id, chat_id, text, data, message_id, telegram, scheduler)`. `core.router.Router` with decorators `command(name)`, `callback(prefix)`, `job(name)`, and `dispatch_update(update, services) -> None`, `dispatch_job(name, payload, telegram) -> None`. Module-level singleton `core.router.router`. Command and callback handlers take `(ctx)`. Job handlers take `(payload, telegram)`.

- [ ] **Step 1: Write the failing router tests**

`tests/core/test_router.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/core/test_router.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'core.router'`.

- [ ] **Step 3: Implement context and router**

`src/core/context.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.scheduler import Scheduler
    from core.telegram import TelegramClient


@dataclass(frozen=True)
class Services:
    """Side-effecting clients, swapped for fakes in tests and for in-process scheduling locally."""

    telegram: TelegramClient
    scheduler: Scheduler


@dataclass(frozen=True)
class Context:
    """One Telegram update reduced to what handlers use. `data` is set for button clicks, `text` for messages."""

    user_id: int
    chat_id: int
    text: str
    data: str
    message_id: int | None
    telegram: TelegramClient
    scheduler: Scheduler
```

`src/core/router.py`:

```python
from core.context import Context


class Router:
    """Maps Telegram commands, callback_data prefixes and scheduled job names to handler functions."""

    def __init__(self):
        self._commands = {}
        self._callbacks = {}
        self._jobs = {}

    def command(self, name):
        return _registrar(self._commands, name)

    def callback(self, prefix):
        return _registrar(self._callbacks, prefix)

    def job(self, name):
        return _registrar(self._jobs, name)

    def dispatch_update(self, update, services):
        if "message" in update:
            self._dispatch_message(update["message"], services)
        elif "callback_query" in update:
            self._dispatch_callback(update["callback_query"], services)

    def dispatch_job(self, name, payload, telegram):
        if name not in self._jobs:
            raise KeyError(f"No job registered as {name!r}")
        self._jobs[name](payload, telegram)

    def _dispatch_message(self, message, services):
        sender = message.get("from")
        # Anonymous/chat senders do not expose a real user identity.
        if not sender or message.get("sender_chat"):
            return
        text = message.get("text", "")
        handler = self._commands.get(_command_name(text))
        if handler:
            handler(Context(
                user_id=sender["id"], chat_id=message["chat"]["id"], text=text, data="", message_id=None,
                telegram=services.telegram, scheduler=services.scheduler,
            ))

    def _dispatch_callback(self, callback, services):
        if not callback.get("message") or "data" not in callback:
            return
        data = callback["data"]
        matches = [prefix for prefix in self._callbacks if data.startswith(prefix)]
        if not matches:
            return
        message = callback["message"]
        # The button clicker is callback.from, not the bot that sent the message.
        self._callbacks[max(matches, key=len)](Context(
            user_id=callback["from"]["id"], chat_id=message["chat"]["id"], text="", data=data,
            message_id=message["message_id"], telegram=services.telegram, scheduler=services.scheduler,
        ))


def _registrar(table, key):
    def register(fn):
        if key in table:
            raise ValueError(f"{key!r} is already registered to {table[key].__name__}")
        table[key] = fn
        return fn
    return register


def _command_name(text):
    tokens = text.split(maxsplit=1)
    # Group chats send "/wash@ShearesBot"; the suffix names the bot, not the command.
    return tokens[0].split("@", 1)[0] if tokens else ""


router = Router()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `make test`
Expected: 16 passed (5 Telegram + 11 router; the duplicate test counts 3).

- [ ] **Step 5: Commit**

```bash
git add src/core/context.py src/core/router.py tests/core/test_router.py
git commit -m "Add decorator-based router for commands, callbacks and jobs"
```

---

### Task 3: Schedulers

**Files:**
- Create: `src/core/scheduler.py`
- Test: `tests/core/test_scheduler.py`

**Interfaces:**
- Consumes: `core.router.router.dispatch_job(name, payload, telegram)`.
- Produces: `core.scheduler.Scheduler` (Protocol), `EventBridgeScheduler(role_arn, target_arn)` and `InProcessScheduler(telegram)`, both with `schedule(name: str, run_at: datetime, job: str, payload: dict) -> None`. `run_at` must be timezone-aware.

- [ ] **Step 1: Write the failing scheduler tests**

`tests/core/test_scheduler.py`:

```python
import json
import threading
from datetime import UTC, datetime, timedelta, timezone

import boto3

from core import scheduler as scheduler_module
from core.router import Router
from core.scheduler import EventBridgeScheduler, InProcessScheduler

ROLE_ARN = "arn:aws:iam::123456789012:role/scheduler"
TARGET_ARN = "arn:aws:lambda:ap-southeast-1:123456789012:function:preprod-app-func-sheareswebbot-bot"


def test_eventbridge_schedule_targets_job_event_in_utc():
    sgt = timezone(timedelta(hours=8))

    EventBridgeScheduler(ROLE_ARN, TARGET_ARN).schedule(
        "Laundry_A_W1_1", datetime(2026, 10, 6, 20, 30, tzinfo=sgt), "laundry_done", {"chat_id": 10},
    )

    got = boto3.client("scheduler").get_schedule(Name="Laundry_A_W1_1")
    assert got["ScheduleExpression"] == "at(2026-10-06T12:30:00)"
    assert got["FlexibleTimeWindow"] == {"Mode": "OFF"}
    assert got["Target"]["Arn"] == TARGET_ARN
    assert got["Target"]["RoleArn"] == ROLE_ARN
    assert json.loads(got["Target"]["Input"]) == {"job": "laundry_done", "payload": {"chat_id": 10}}


def test_in_process_scheduler_runs_job_with_its_telegram(monkeypatch, services):
    router = Router()
    ran = threading.Event()
    seen = []

    @router.job("laundry_done")
    def done(payload, telegram):
        seen.append((payload, telegram))
        ran.set()

    monkeypatch.setattr(scheduler_module, "router", router)

    InProcessScheduler(services.telegram).schedule("Laundry_A_W1_1", datetime.now(UTC), "laundry_done", {"chat_id": 10})

    assert ran.wait(timeout=2)
    assert seen == [({"chat_id": 10}, services.telegram)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/core/test_scheduler.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'core.scheduler'`.

- [ ] **Step 3: Implement the schedulers**

`src/core/scheduler.py`:

```python
import json
import threading
from datetime import UTC, datetime
from typing import Protocol

import boto3

from core.router import router


class Scheduler(Protocol):
    def schedule(self, name: str, run_at: datetime, job: str, payload: dict) -> None: ...


class EventBridgeScheduler:
    """Creates one-off schedules that invoke this Lambda with a job event."""

    def __init__(self, role_arn, target_arn):
        self._client = boto3.client("scheduler")
        self._role_arn = role_arn
        self._target_arn = target_arn

    def schedule(self, name, run_at, job, payload):
        # at() expressions are evaluated in UTC unless a schedule timezone is given.
        run_at_utc = run_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
        self._client.create_schedule(
            Name=name,
            ScheduleExpression=f"at({run_at_utc})",
            Target={
                "Arn": self._target_arn,
                "RoleArn": self._role_arn,
                "Input": json.dumps({"job": job, "payload": payload}),
            },
            FlexibleTimeWindow={"Mode": "OFF"},
            ActionAfterCompletion="DELETE",
        )


class InProcessScheduler:
    """Runs jobs in this process for local runs. A real schedule would fire the deployed Lambda and reply from its bot."""

    def __init__(self, telegram):
        self._telegram = telegram

    def schedule(self, name, run_at, job, payload):
        delay = max(0.0, (run_at - datetime.now(UTC)).total_seconds())
        timer = threading.Timer(delay, router.dispatch_job, args=(job, payload, self._telegram))
        timer.daemon = True
        timer.start()
```

The timer thread references the module-level `router` name at call time through `router.dispatch_job` bound when `schedule` runs, which is what the test's monkeypatch replaces.

- [ ] **Step 4: Run tests to verify they pass**

Run: `make test`
Expected: 18 passed.

- [ ] **Step 5: Commit**

```bash
git add src/core/scheduler.py tests/core/test_scheduler.py
git commit -m "Add EventBridge and in-process schedulers"
```

---

### Task 4: Profile feature

**Files:**
- Create: `src/features/__init__.py`, `src/features/profile/__init__.py` (empty), `src/features/profile/repository.py`, `src/features/profile/handlers.py`
- Test: `tests/profile/test_handlers.py`

**Interfaces:**
- Consumes: `core.router.router`, `core.db.users_table()`, `Context`.
- Produces: `features.profile.repository.get_profile(user_id) -> dict | None`, `put_profile(user_id, chat_id, block, level: int, year: int)`, `user_profile_key(user_id) -> dict`. Registers `/help`, `/start`, `/profile`, `profile_create_yes`, `profile_create_no`, `profile_blk_`, `profile_lvl_`, `profile_yr_`.

- [ ] **Step 1: Write the failing profile tests**

`tests/profile/test_handlers.py`:

```python
import features  # noqa: F401  (registers handlers)
from core import db
from core.router import router
from support import callback_update, message_update, seed_profile


def test_start_and_help_send_welcome(services):
    router.dispatch_update(message_update("/start"), services)
    router.dispatch_update(message_update("/help"), services)

    texts = [m["text"] for m in services.telegram.sent]
    assert len(texts) == 2
    assert all("🤖🧡💙 *Welcome to Sheares Bot!*" in t and "• /dry - Track your drying progress" in t for t in texts)


def test_profile_without_record_offers_creation(services):
    router.dispatch_update(message_update("/profile"), services)

    assert services.telegram.sent == [{
        "chat_id": 10,
        "text": "You don't have a profile yet. Would you like to create one?",
        "keyboard": [[
            {"text": "Yes", "callback_data": "profile_create_yes"},
            {"text": "No", "callback_data": "profile_create_no"},
        ]],
    }]


def test_profile_shows_existing_record(services):
    seed_profile(1, "B", level=3, year=2)

    router.dispatch_update(message_update("/profile"), services)

    (msg,) = services.telegram.sent
    assert msg["text"].startswith("👤 *Your Profile*\nBlock: *B3*\nYear: *2*\n")


def test_profile_creation_flow_saves_record(services):
    for data in ("profile_create_yes", "profile_blk_B", "profile_lvl_B_3", "profile_yr_B_3_2"):
        router.dispatch_update(callback_update(data), services)

    edits = services.telegram.edited
    assert edits[0]["text"] == "Select your Block:"
    assert edits[0]["keyboard"][1][0] == {"text": "D", "callback_data": "profile_blk_D"}
    assert edits[1]["text"] == "Block B selected.\nNow select your Level:"
    assert edits[1]["keyboard"][1][3] == {"text": "8", "callback_data": "profile_lvl_B_8"}
    assert edits[2]["text"] == "Block B, Level 3 selected.\nFinally, select your Year of Study:"
    assert len(edits[2]["keyboard"][0]) == 6
    assert edits[3]["text"] == "✅ Profile created successfully!\nBlock: B\nLevel: 3\nYear: 2"
    item = db.users_table().get_item(Key={"userID": "USER#1", "profile": "PROFILE"})["Item"]
    assert item == {"userID": "USER#1", "profile": "PROFILE", "chatID": "10", "block": "B", "level": 3, "year": 2}


def test_declining_creation_cancels(services):
    router.dispatch_update(callback_update("profile_create_no"), services)

    assert services.telegram.edited[0]["text"] == "Profile creation cancelled."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/profile -v`
Expected: collection error `ModuleNotFoundError: No module named 'features'`.

- [ ] **Step 3: Implement the profile feature**

`src/features/__init__.py`:

```python
# Importing each handlers module registers its commands, callbacks and jobs with core.router.
from features.profile import handlers as _profile  # noqa: F401
```

`src/features/profile/__init__.py`: empty file.

`src/features/profile/repository.py`:

```python
from core.db import users_table


def user_profile_key(user_id):
    return {"userID": f"USER#{user_id}", "profile": "PROFILE"}


def get_profile(user_id):
    return users_table().get_item(Key=user_profile_key(user_id)).get("Item")


def put_profile(user_id, chat_id, block, level, year):
    users_table().put_item(Item={
        **user_profile_key(user_id),
        "chatID": str(chat_id),
        "block": block,
        "level": level,
        "year": year,
    })
```

`src/features/profile/handlers.py` (strings copied verbatim from `src/processor/user_profile/handlers.py`, including the help text's leading newline and trailing indentation):

```python
from core.router import router
from features.profile import repository

BLOCKS = ["A", "B", "C", "D", "E", "F"]

HELP_TEXT = """
🤖🧡💙 *Welcome to Sheares Bot!*

Here are some basic commands to get you started:

👤 *Account*
• /profile - Set your block, level, and year

🧺 *Laundry*
• /laundry - Status of laundry machines in your block
• /wash - Track your washing progress
• /dry - Track your drying progress

_More features coming soon!_
            """


@router.command("/help")
@router.command("/start")
def help_command(ctx):
    ctx.telegram.send_message(ctx.chat_id, HELP_TEXT)


@router.command("/profile")
def profile_command(ctx):
    profile = repository.get_profile(ctx.user_id)
    if not profile:
        keyboard = [[
            {"text": "Yes", "callback_data": "profile_create_yes"},
            {"text": "No", "callback_data": "profile_create_no"},
        ]]
        ctx.telegram.send_message(ctx.chat_id, "You don't have a profile yet. Would you like to create one?", keyboard)
        return

    details = (
        f"👤 *Your Profile*\n"
        f"Block: *{profile.get('block')}{profile.get('level')}*\n"
        f"Year: *{profile.get('year')}*\n"
        f"-------------------------\n"
        f"*Washing Machine stats*\n"
        f"No. of recorded washing cycles: *N* (*X minutes*)\n"
        f"No. of recorded drying cycles: *N* (*X minutes*)\n"
        f"Total spent: ($Y)"
    )
    ctx.telegram.send_message(ctx.chat_id, details)


@router.callback("profile_create_no")
def cancel_creation(ctx):
    ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, "Profile creation cancelled.")


@router.callback("profile_create_yes")
def choose_block(ctx):
    keyboard = [
        [{"text": b, "callback_data": f"profile_blk_{b}"} for b in BLOCKS[:3]],
        [{"text": b, "callback_data": f"profile_blk_{b}"} for b in BLOCKS[3:]],
    ]
    ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, "Select your Block:", keyboard)


## TODO: Also get INTRANET email in profile for future integrations
## ... and perhaps other Optional profile info like Major, role in block etc
@router.callback("profile_blk_")
def choose_level(ctx):
    block = ctx.data.split("_")[2]
    keyboard = [
        [{"text": str(i), "callback_data": f"profile_lvl_{block}_{i}"} for i in range(1, 5)],
        [{"text": str(i), "callback_data": f"profile_lvl_{block}_{i}"} for i in range(5, 9)],
    ]
    ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, f"Block {block} selected.\nNow select your Level:", keyboard)


@router.callback("profile_lvl_")
def choose_year(ctx):
    parts = ctx.data.split("_")
    block, level = parts[2], parts[3]
    keyboard = [[{"text": str(i), "callback_data": f"profile_yr_{block}_{level}_{i}"} for i in range(1, 7)]]
    ctx.telegram.edit_message(
        ctx.chat_id, ctx.message_id,
        f"Block {block}, Level {level} selected.\nFinally, select your Year of Study:", keyboard,
    )


@router.callback("profile_yr_")
def save_profile(ctx):
    parts = ctx.data.split("_")
    block, level, year = parts[2], parts[3], parts[4]
    repository.put_profile(ctx.user_id, ctx.chat_id, block, int(level), int(year))
    ctx.telegram.edit_message(
        ctx.chat_id, ctx.message_id,
        f"✅ Profile created successfully!\nBlock: {block}\nLevel: {level}\nYear: {year}",
    )
```

The two `## TODO` lines are carried over from the existing code, not new placeholders.

- [ ] **Step 4: Run tests to verify they pass**

Run: `make test`
Expected: 23 passed.

- [ ] **Step 5: Commit**

```bash
git add src/features tests/profile
git commit -m "Port profile commands to the router"
```

---

### Task 5: Laundry repository and service

**Files:**
- Create: `src/features/laundry/__init__.py` (empty), `src/features/laundry/repository.py`, `src/features/laundry/service.py`
- Test: `tests/laundry/test_service.py`

**Interfaces:**
- Consumes: `core.db`, `features.profile.repository.get_profile`, a `Scheduler`.
- Produces (`repository`): `get_machine(block, machine_id) -> dict | None`, `query_block(block) -> list[dict]`, `put_machine(block, machine_id, user_id, chat_id, end_time)`, `set_machine_state(block, machine_id, status, user, chat)`, `add_user_machine(user_id, machine_id)`, `remove_user_machine(user_id, machine_id)`, `get_user_machines(user_id) -> set[str]`, `get_user_block(user_id) -> str | None`, `get_notification_chat(machine) -> str | None`.
- Produces (`service`): `MACHINE_IDS`, `LAUNDRY_DONE_JOB = "laundry_done"`, `MoveOutcome` enum (`MOVED`, `TARGET_OCCUPIED`, `SOURCE_EMPTY`), `MachineView(machine_id, status, mins_left, mine)`, `real_status(machine, now) -> str`, `machine_status(block, machine_id, now) -> str`, `owner_chat(block, machine_id) -> str | None`, `claim(user_id, chat_id, block, machine_id, duration_mins, scheduler)`, `move(block, from_id, to_id, now) -> tuple[MoveOutcome, str | None]`, `move_override(block, from_id, to_id) -> MoveOutcome`, `collect(user_id, block, machine_id) -> bool`, `block_summary(user_id, block, now) -> tuple[list[MachineView], list[str]]`.

- [ ] **Step 1: Write the failing service tests**

`tests/laundry/test_service.py`:

```python
import time
from datetime import UTC, datetime

from features.laundry import repository as repo
from features.laundry import service
from features.laundry.service import MachineView, MoveOutcome
from support import RecordingScheduler, seed_machine


def test_real_status():
    assert service.real_status(None, 100) == "AVAILABLE"
    assert service.real_status({"status": "IN_USE", "endTime": 200}, 100) == "IN_USE"
    assert service.real_status({"status": "IN_USE", "endTime": 100}, 100) == "COMPLETED"
    assert service.real_status({"status": "IN_USE", "endTime": 0}, 100) == "IN_USE"
    assert service.real_status({"status": "COMPLETED", "endTime": 0}, 100) == "COMPLETED"


def test_claim_writes_cycle_tracks_user_and_schedules_reminder():
    scheduler = RecordingScheduler()
    before = int(time.time())

    service.claim(1, 10, "A", "W1", 30, scheduler)

    machine = repo.get_machine("A", "W1")
    assert {k: machine[k] for k in ("status", "currentUser", "currentChatID", "type")} == {
        "status": "IN_USE", "currentUser": "1", "currentChatID": "10", "type": "WASHER",
    }
    assert before + 1800 <= int(machine["endTime"]) <= int(time.time()) + 1800
    assert repo.get_user_machines(1) == {"W1"}
    (job,) = scheduler.scheduled
    assert job["name"].startswith("Laundry_A_W1_")
    assert job["job"] == "laundry_done"
    assert job["payload"] == {"chat_id": 10, "block": "A", "machine_id": "W1"}
    assert job["run_at"].tzinfo is not None
    assert abs((job["run_at"] - datetime.now(UTC)).total_seconds() - 1800) < 5


def test_claim_over_another_user_clears_their_tracker():
    seed_machine("A", "D2", status="IN_USE", currentUser="2", currentChatID="20", endTime=0)
    repo.add_user_machine("2", "D2")

    service.claim(1, 10, "A", "D2", 45, RecordingScheduler())

    assert repo.get_user_machines("2") == set()
    assert repo.get_machine("A", "D2")["type"] == "DRYER"


def test_move_transfers_contents_and_trackers():
    seed_machine("A", "W1", status="IN_USE", currentUser="2", currentChatID="20", endTime=1)
    repo.add_user_machine("2", "W1")

    outcome, chat = service.move("A", "W1", "W3", now=int(time.time()))

    assert (outcome, chat) == (MoveOutcome.MOVED, "20")
    w3, w1 = repo.get_machine("A", "W3"), repo.get_machine("A", "W1")
    assert (w3["status"], w3["currentUser"], w3["currentChatID"], w3["endTime"]) == ("COMPLETED", "2", "20", 0)
    assert (w1["status"], w1["currentUser"], w1["currentChatID"], w1["endTime"]) == ("AVAILABLE", "NONE", "NONE", 0)
    assert repo.get_user_machines("2") == {"W3"}


def test_move_into_occupied_machine_changes_nothing():
    seed_machine("A", "W1", status="COMPLETED", currentUser="2", currentChatID="20", endTime=0)
    seed_machine("A", "W3", status="IN_USE", currentUser="3", currentChatID="30", endTime=int(time.time()) + 600)

    assert service.move("A", "W1", "W3", now=int(time.time())) == (MoveOutcome.TARGET_OCCUPIED, None)
    assert repo.get_machine("A", "W1")["status"] == "COMPLETED"


def test_move_from_unrecorded_machine():
    assert service.move("A", "W1", "W3", now=int(time.time())) == (MoveOutcome.SOURCE_EMPTY, None)


def test_move_override_clears_target_owner():
    seed_machine("A", "W1", status="COMPLETED", currentUser="2", currentChatID="20", endTime=0)
    seed_machine("A", "W3", status="IN_USE", currentUser="3", currentChatID="30", endTime=int(time.time()) + 600)
    repo.add_user_machine("2", "W1")
    repo.add_user_machine("3", "W3")

    assert service.move_override("A", "W1", "W3") == MoveOutcome.MOVED

    assert repo.get_user_machines("3") == set()
    assert repo.get_user_machines("2") == {"W3"}
    assert repo.get_machine("A", "W3")["currentUser"] == "2"
    assert repo.get_machine("A", "W1")["status"] == "AVAILABLE"


def test_collect_only_clears_for_current_owner():
    seed_machine("A", "W1", status="COMPLETED", currentUser="1", currentChatID="10", endTime=0)
    repo.add_user_machine(1, "W1")

    assert service.collect(2, "A", "W1") is False
    assert repo.get_machine("A", "W1")["status"] == "COMPLETED"

    assert service.collect(1, "A", "W1") is True
    assert repo.get_machine("A", "W1")["status"] == "AVAILABLE"
    assert repo.get_user_machines(1) == set()


def test_owner_chat_falls_back_to_legacy_current_user():
    seed_machine("A", "W2", status="COMPLETED", currentUser="555", endTime=0)
    seed_machine("A", "W3", status="AVAILABLE", currentUser="NONE", currentChatID="NONE", endTime=0)

    assert service.owner_chat("A", "W2") == "555"
    assert service.owner_chat("A", "W3") is None
    assert service.owner_chat("A", "W4") is None


def test_block_summary_lists_all_machines_with_ownership():
    now = int(time.time())
    seed_machine("A", "W1", status="IN_USE", currentUser="1", currentChatID="10", endTime=now + 600)
    seed_machine("A", "D2", status="IN_USE", currentUser="2", currentChatID="20", endTime=now - 1)
    repo.add_user_machine(1, "W1")
    repo.add_user_machine(1, "D4")

    views, mine = service.block_summary(1, "A", now)

    assert [v.machine_id for v in views] == ["W1", "W2", "W3", "W4", "D1", "D2", "D3", "D4"]
    assert views[0] == MachineView("W1", "IN_USE", 10, True)
    assert views[1] == MachineView("W2", "AVAILABLE", None, False)
    assert views[5] == MachineView("D2", "COMPLETED", None, False)
    assert views[7] == MachineView("D4", "AVAILABLE", None, False)
    assert mine == ["D4", "W1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/laundry/test_service.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'features.laundry'`.

- [ ] **Step 3: Implement the repository**

`src/features/laundry/__init__.py`: empty file.

`src/features/laundry/repository.py`:

```python
from boto3.dynamodb.conditions import Key

from core.db import laundry_table, users_table
from features.profile.repository import get_profile


def _tracker_key(user_id):
    return {"userID": f"USER#{user_id}", "profile": "LAUNDRY"}


def add_user_machine(user_id, machine_id):
    if not user_id or user_id == "NONE":
        return
    users_table().update_item(
        Key=_tracker_key(user_id),
        UpdateExpression="ADD machines :m",
        ExpressionAttributeValues={":m": {machine_id}},
    )


def remove_user_machine(user_id, machine_id):
    if not user_id or user_id == "NONE":
        return
    users_table().update_item(
        Key=_tracker_key(user_id),
        UpdateExpression="DELETE machines :m",
        ExpressionAttributeValues={":m": {machine_id}},
    )


def get_user_machines(user_id):
    return users_table().get_item(Key=_tracker_key(user_id)).get("Item", {}).get("machines", set())


def get_user_block(user_id):
    return (get_profile(user_id) or {}).get("block")


def get_machine(block, machine_id):
    return laundry_table().get_item(Key={"block": block, "machineID": machine_id}).get("Item")


def query_block(block):
    return laundry_table().query(KeyConditionExpression=Key("block").eq(block)).get("Items", [])


def put_machine(block, machine_id, user_id, chat_id, end_time):
    """Records a new IN_USE cycle, replacing whatever the row held."""
    laundry_table().put_item(Item={
        "block": block,
        "machineID": machine_id,
        "status": "IN_USE",
        "currentUser": str(user_id),
        "currentChatID": str(chat_id),
        "endTime": end_time,
        "type": "WASHER" if "W" in machine_id else "DRYER",
    })


def set_machine_state(block, machine_id, status, user, chat):
    laundry_table().update_item(
        Key={"block": block, "machineID": machine_id},
        UpdateExpression="SET #s = :s, currentUser = :u, currentChatID = :c, endTime = :e",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status, ":u": user, ":c": chat, ":e": 0},
    )


def get_notification_chat(machine):
    """Return the owner's recorded chat, including records written before currentChatID existed."""
    if not machine or machine.get("currentUser") in (None, "NONE"):
        return None
    if "currentChatID" in machine:
        chat_id = machine["currentChatID"]
        return None if chat_id in (None, "NONE") else chat_id
    # Legacy records stored the notification chat in currentUser.
    return machine.get("currentUser")
```

- [ ] **Step 4: Implement the service**

`src/features/laundry/service.py`:

```python
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from math import ceil

from features.laundry import repository as repo

# Machines exist physically whether or not anyone has used them yet, so they
# have no DynamoDB row until their first claim.
MACHINE_IDS = [f"{typ}{i}" for typ in ("W", "D") for i in range(1, 5)]

LAUNDRY_DONE_JOB = "laundry_done"


class MoveOutcome(Enum):
    MOVED = "moved"
    TARGET_OCCUPIED = "target_occupied"
    SOURCE_EMPTY = "source_empty"


@dataclass(frozen=True)
class MachineView:
    machine_id: str
    status: str
    mins_left: int | None
    mine: bool


def real_status(machine, now):
    """Rows stay IN_USE after the timer ends; the cycle counts as COMPLETED once endTime has passed."""
    if not machine:
        return "AVAILABLE"
    status = machine.get("status", "AVAILABLE")
    end_time = int(machine.get("endTime", 0))
    if status == "IN_USE" and end_time > 0 and now >= end_time:
        return "COMPLETED"
    return status


def machine_status(block, machine_id, now):
    return real_status(repo.get_machine(block, machine_id), now)


def owner_chat(block, machine_id):
    return repo.get_notification_chat(repo.get_machine(block, machine_id))


def claim(user_id, chat_id, block, machine_id, duration_mins, scheduler):
    old_machine = repo.get_machine(block, machine_id)
    old_user = old_machine.get("currentUser", "NONE") if old_machine else "NONE"
    if old_user != "NONE" and old_user != str(user_id):
        repo.remove_user_machine(old_user, machine_id)
    repo.add_user_machine(user_id, machine_id)

    started = int(time.time())
    repo.put_machine(block, machine_id, user_id, chat_id, started + duration_mins * 60)
    scheduler.schedule(
        name=f"Laundry_{block}_{machine_id}_{started}",
        run_at=datetime.fromtimestamp(started, UTC) + timedelta(minutes=duration_mins),
        job=LAUNDRY_DONE_JOB,
        payload={"chat_id": chat_id, "block": block, "machine_id": machine_id},
    )


def move(block, from_id, to_id, now):
    """Moves the previous user's laundry to a free machine. Returns the outcome and the owner's chat to notify."""
    if real_status(repo.get_machine(block, to_id), now) != "AVAILABLE":
        return MoveOutcome.TARGET_OCCUPIED, None
    orig = repo.get_machine(block, from_id)
    if not orig:
        return MoveOutcome.SOURCE_EMPTY, None
    return MoveOutcome.MOVED, _transfer(block, from_id, to_id, orig)


def move_override(block, from_id, to_id):
    """Moves laundry into an occupied machine, dropping that machine's current owner."""
    target = repo.get_machine(block, to_id)
    target_user = target.get("currentUser", "NONE") if target else "NONE"
    orig = repo.get_machine(block, from_id)
    if not orig:
        return MoveOutcome.SOURCE_EMPTY
    if target_user != "NONE":
        repo.remove_user_machine(target_user, to_id)
    _transfer(block, from_id, to_id, orig)
    return MoveOutcome.MOVED


def collect(user_id, block, machine_id):
    machine = repo.get_machine(block, machine_id)
    # Only clear if the user still owns it, because it could have been overridden or moved since.
    if not machine or str(machine.get("currentUser")) != str(user_id):
        return False
    repo.remove_user_machine(user_id, machine_id)
    repo.set_machine_state(block, machine_id, "AVAILABLE", "NONE", "NONE")
    return True


def block_summary(user_id, block, now):
    by_id = {m["machineID"]: m for m in repo.query_block(block)}
    my_machines = repo.get_user_machines(user_id)
    views = []
    for machine_id in MACHINE_IDS:
        machine = by_id.get(machine_id)
        status = real_status(machine, now)
        end_time = int(machine.get("endTime", 0)) if machine else 0
        mins_left = ceil((end_time - now) / 60) if status == "IN_USE" and end_time > now else None
        views.append(MachineView(machine_id, status, mins_left, machine_id in my_machines and status != "AVAILABLE"))
    return views, sorted(my_machines)


def _transfer(block, from_id, to_id, orig):
    orig_user = orig.get("currentUser", "NONE")
    orig_chat = repo.get_notification_chat(orig) or "NONE"
    if orig_user != "NONE":
        repo.add_user_machine(orig_user, to_id)
        repo.remove_user_machine(orig_user, from_id)
    repo.set_machine_state(block, to_id, "COMPLETED", orig_user, orig_chat)
    repo.set_machine_state(block, from_id, "AVAILABLE", "NONE", "NONE")
    return None if orig_chat == "NONE" else orig_chat
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `make test`
Expected: 33 passed.

- [ ] **Step 6: Commit**

```bash
git add src/features/laundry tests/laundry/test_service.py
git commit -m "Split laundry rules and persistence out of the handlers"
```

---

### Task 6: Laundry handlers and reminder job

**Files:**
- Create: `src/features/laundry/handlers.py`
- Modify: `src/features/__init__.py`
- Test: `tests/laundry/test_handlers.py`

**Interfaces:**
- Consumes: everything `service` and `repository` produce in Task 5; `core.router.router`.
- Produces: registers `/laundry`, `/wash`, `/dry`, callbacks `cancel`, `drydur_`, `mclick_`, `bump_`, `move_`, `moveto_`, `over_`, `moveride_`, `collect_`, and job `laundry_done(payload, telegram)` where `payload = {"chat_id", "block", "machine_id"}`.

- [ ] **Step 1: Write the failing handler tests**

`tests/laundry/test_handlers.py`:

```python
import time

import features  # noqa: F401  (registers handlers)
from core.router import router
from features.laundry import repository as repo
from features.laundry import service
from support import callback_update, message_update, seed_machine, seed_profile

CANCEL_ROW = [{"text": "❌ Cancel", "callback_data": "cancel"}]


def test_wash_requires_profile(services):
    router.dispatch_update(message_update("/wash"), services)

    assert services.telegram.sent[0]["text"] == "Please create a /profile first."


def test_wash_offers_washers(services):
    seed_profile(1, "A")

    router.dispatch_update(message_update("/wash"), services)

    assert services.telegram.sent == [{
        "chat_id": 10,
        "text": "Which washing machine would you like to use? (30 mins)",
        "keyboard": [[{"text": f"W{i}", "callback_data": f"mclick_W{i}_30"} for i in range(1, 5)], CANCEL_ROW],
    }]


def test_dry_then_duration_offers_dryers(services):
    seed_profile(1, "A")

    router.dispatch_update(message_update("/dry"), services)
    router.dispatch_update(callback_update("drydur_45"), services)

    assert services.telegram.sent[0]["text"] == "How long do you need the dryer for?"
    assert services.telegram.sent[0]["keyboard"][0][1] == {"text": "45 mins", "callback_data": "drydur_45"}
    assert services.telegram.edited == [{
        "chat_id": 10, "message_id": 99,
        "text": "Which dryer would you like to use for 45 mins?",
        "keyboard": [[{"text": f"D{i}", "callback_data": f"mclick_D{i}_45"} for i in range(1, 5)], CANCEL_ROW],
    }]


def test_clicking_free_machine_claims_it(services):
    seed_profile(1, "A")

    router.dispatch_update(callback_update("mclick_W1_30"), services)

    assert services.telegram.edited[0]["text"] == "✅ *W1* is yours for 30 minutes. I'll remind you when it's done!"
    assert repo.get_machine("A", "W1")["status"] == "IN_USE"
    assert services.scheduler.scheduled[0]["payload"] == {"chat_id": 10, "block": "A", "machine_id": "W1"}


def test_machine_click_tolerates_markdown_asterisks(services):
    seed_profile(1, "A")

    router.dispatch_update(callback_update("mclick_*W1*_30"), services)

    assert services.telegram.edited[0]["text"] == "✅ *W1* is yours for 30 minutes. I'll remind you when it's done!"
    assert repo.get_machine("A", "W1")["currentUser"] == "1"


def test_clicking_busy_machine_offers_override(services):
    seed_profile(1, "A")
    seed_machine("A", "W2", status="IN_USE", currentUser="2", currentChatID="20", endTime=int(time.time()) + 600)

    router.dispatch_update(callback_update("mclick_W2_30"), services)

    assert services.telegram.edited[0]["text"] == "⚠️ *W2* is currently IN USE. Are you sure you want to override?"
    assert services.telegram.edited[0]["keyboard"] == [
        [{"text": "⚠️ Yes, Override", "callback_data": "over_W2_30"}], CANCEL_ROW,
    ]


def test_clicking_finished_machine_offers_bump_move_override(services):
    seed_profile(1, "A")
    seed_machine("A", "W2", status="IN_USE", currentUser="2", currentChatID="20", endTime=1)

    router.dispatch_update(callback_update("mclick_W2_30"), services)

    assert services.telegram.edited[0]["text"] == (
        "*W2* is currently awaiting collection. Send a reminder to the last user?\n"
        "You can also move the contents to another machine.\n"
        "_Don't see anything? Perhaps they forgot to click collect. Would you like to override?_"
    )
    assert services.telegram.edited[0]["keyboard"] == [
        [{"text": "🔔 Bump", "callback_data": "bump_W2"}],
        [{"text": "🔁 Move", "callback_data": "move_W2"}],
        [{"text": "⚠️ Override", "callback_data": "over_W2_30"}],
        CANCEL_ROW,
    ]


def test_bump_reaches_legacy_owner(services):
    seed_profile(1, "A")
    seed_machine("A", "W2", status="COMPLETED", currentUser="555", endTime=0)

    router.dispatch_update(callback_update("bump_W2"), services)

    assert services.telegram.sent == [{
        "chat_id": "555",
        "text": "🔔 Reminder to take your clothes from *W2* as someone is trying to use it!",
        "keyboard": None,
    }]
    assert services.telegram.edited[0]["text"] == "✅ Reminder sent to the user of *W2*."


def test_bump_without_owner(services):
    seed_profile(1, "A")

    router.dispatch_update(callback_update("bump_W2"), services)

    assert services.telegram.edited[0]["text"] == "⚠️ No registered user to remind for *W2*."
    assert services.telegram.sent == []


def test_move_lists_every_other_machine(services):
    seed_profile(1, "A")

    router.dispatch_update(callback_update("move_W2"), services)

    edit = services.telegram.edited[0]
    assert edit["text"] == "Where are you moving the contents of *W2* to?"
    assert [b["text"] for b in edit["keyboard"][0]] == ["W1", "W3", "W4", "D1"]
    assert [b["text"] for b in edit["keyboard"][1]] == ["D2", "D3", "D4"]
    assert edit["keyboard"][0][1]["callback_data"] == "moveto_W2_W3"
    assert edit["keyboard"][2] == CANCEL_ROW


def test_moveto_free_machine_notifies_owner(services):
    seed_profile(1, "A")
    seed_machine("A", "W1", status="IN_USE", currentUser="2", currentChatID="20", endTime=1)

    router.dispatch_update(callback_update("moveto_W1_W3"), services)

    assert services.telegram.sent == [{
        "chat_id": "20", "text": "🔔 Your clothes were moved from W1 to W3 by the next user.", "keyboard": None,
    }]
    assert services.telegram.edited[0]["text"] == (
        "✅ Contents moved to W3. W1 is now empty. Use /wash or /dry to start your cycle."
    )


def test_moveto_occupied_machine_offers_override(services):
    seed_profile(1, "A")
    seed_machine("A", "W1", status="COMPLETED", currentUser="2", currentChatID="20", endTime=0)
    seed_machine("A", "W3", status="IN_USE", currentUser="3", currentChatID="30", endTime=int(time.time()) + 600)

    router.dispatch_update(callback_update("moveto_W1_W3"), services)

    assert services.telegram.edited[0]["text"] == "⚠️ W3 is occupied. You can cancel, or override the target machine."
    assert services.telegram.edited[0]["keyboard"] == [
        [{"text": "⚠️ Override", "callback_data": "moveride_W1_W3"}], CANCEL_ROW,
    ]


def test_moveto_from_unrecorded_machine(services):
    seed_profile(1, "A")

    router.dispatch_update(callback_update("moveto_W1_W3"), services)

    assert services.telegram.edited[0]["text"] == (
        "*W1* has nothing recorded in it. Use /wash or /dry to start your cycle."
    )


def test_moveride_moves_without_notifying(services):
    seed_profile(1, "A")
    seed_machine("A", "W1", status="COMPLETED", currentUser="2", currentChatID="20", endTime=0)
    seed_machine("A", "W3", status="IN_USE", currentUser="3", currentChatID="30", endTime=int(time.time()) + 600)

    router.dispatch_update(callback_update("moveride_W1_W3"), services)

    assert services.telegram.sent == []
    assert services.telegram.edited[0]["text"] == (
        "✅ Overridden and moved. W1 is now empty. Use /wash or /dry to start your cycle."
    )
    assert repo.get_machine("A", "W3")["currentUser"] == "2"


def test_override_claims_machine(services):
    seed_profile(1, "A")
    seed_machine("A", "W2", status="IN_USE", currentUser="2", currentChatID="20", endTime=int(time.time()) + 600)

    router.dispatch_update(callback_update("over_W2_30"), services)

    assert services.telegram.edited[0]["text"] == "✅ Overridden. *W2* is yours for 30 minutes."
    assert repo.get_machine("A", "W2")["currentUser"] == "1"


def test_reminder_job_then_collect(services):
    seed_profile(1, "A")
    router.dispatch_update(callback_update("mclick_W1_30"), services)
    job = services.scheduler.scheduled[0]

    router.dispatch_job(job["job"], job["payload"], services.telegram)
    router.dispatch_update(callback_update("collect_W1"), services)

    assert services.telegram.sent == [{
        "chat_id": 10,
        "text": "🔔 Your laundry in W1 (Block A) is done!",
        "keyboard": [[{"text": "Collected ✅", "callback_data": "collect_W1"}]],
    }]
    assert services.telegram.edited[-1]["text"] == "✅ Thank you for collecting from *W1*. The machine is now available."
    assert repo.get_machine("A", "W1")["status"] == "AVAILABLE"


def test_collect_after_someone_else_took_over(services):
    seed_profile(1, "A")
    seed_machine("A", "W1", status="IN_USE", currentUser="2", currentChatID="20", endTime=1)

    router.dispatch_update(callback_update("collect_W1"), services)

    assert services.telegram.edited[0]["text"] == "✅ Thank you for collecting from *W1*"
    assert repo.get_machine("A", "W1")["currentUser"] == "2"


def test_cancel(services):
    router.dispatch_update(callback_update("cancel"), services)

    assert services.telegram.edited[0]["text"] == "Action cancelled."


def test_laundry_status_message(services):
    now = int(time.time())
    seed_profile(1, "A")
    seed_machine("A", "W1", status="IN_USE", currentUser="1", currentChatID="10", endTime=now + 600)
    seed_machine("A", "D2", status="IN_USE", currentUser="2", currentChatID="20", endTime=now - 1)
    repo.add_user_machine(1, "W1")

    router.dispatch_update(message_update("/laundry"), services)

    assert services.telegram.sent[0]["text"] == (
        "₊˚⊹ 🧺 *Block A Laundry Room* 🫧 ⊹˚₊\n\n"
        "*Washers*\n"
        "*W1*: 🔴 In use *(by you)* (~10mins left)\n"
        "*W2*: 🟢 Available\n"
        "*W3*: 🟢 Available\n"
        "*W4*: 🟢 Available\n\n"
        "*Dryers*\n"
        "*D1*: 🟢 Available\n"
        "*D2*: 🟡 Awaiting collection\n"
        "*D3*: 🟢 Available\n"
        "*D4*: 🟢 Available\n\n"
        "_You are currently using: W1_"
    )


def test_laundry_status_without_profile(services):
    router.dispatch_update(message_update("/laundry"), services)

    assert services.telegram.sent[0]["text"] == "Create a profile with /profile to get started!"


def test_laundry_status_reports_lookup_failure(monkeypatch, services):
    seed_profile(1, "A")

    def broken(*args):
        raise RuntimeError("dynamodb down")

    monkeypatch.setattr(service, "block_summary", broken)

    router.dispatch_update(message_update("/laundry"), services)

    assert services.telegram.sent[0]["text"] == "⚠️ Error retrieving laundry status."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/laundry/test_handlers.py -v`
Expected: most tests FAIL with empty `sent`/`edited` lists (no laundry handlers registered).

- [ ] **Step 3: Implement the handlers**

`src/features/laundry/handlers.py` (strings copied verbatim from `src/processor/laundry/handlers.py`):

```python
import logging
import time

from core.router import router
from features.laundry import repository as repo
from features.laundry import service
from features.laundry.service import MoveOutcome

logger = logging.getLogger(__name__)

CANCEL_ROW = [{"text": "❌ Cancel", "callback_data": "cancel"}]

STATUS_LABELS = {
    "AVAILABLE": "🟢 Available",
    "IN_USE": "🔴 In use",
    "COMPLETED": "🟡 Awaiting collection",
}


def _parts(ctx):
    # Button data can come back with Markdown asterisks around its parts.
    return [part.strip("*") for part in ctx.data.split("_")]


def _require_block(ctx):
    block = repo.get_user_block(ctx.user_id)
    if not block:
        ctx.telegram.send_message(ctx.chat_id, "Please create a /profile first.")
    return block


def _edit(ctx, text, keyboard=None):
    ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, text, keyboard)


@router.command("/wash")
def wash_command(ctx):
    if not _require_block(ctx):
        return
    kb = [[{"text": f"W{i}", "callback_data": f"mclick_W{i}_30"} for i in range(1, 5)], CANCEL_ROW]
    ctx.telegram.send_message(ctx.chat_id, "Which washing machine would you like to use? (30 mins)", kb)


@router.command("/dry")
def dry_command(ctx):
    if not _require_block(ctx):
        return
    kb = [
        [
            {"text": "30 mins", "callback_data": "drydur_30"},
            {"text": "45 mins", "callback_data": "drydur_45"},
            {"text": "60 mins", "callback_data": "drydur_60"},
        ],
        CANCEL_ROW,
    ]
    ctx.telegram.send_message(ctx.chat_id, "How long do you need the dryer for?", kb)


@router.command("/laundry")
def laundry_command(ctx):
    block = repo.get_user_block(ctx.user_id)
    if not block:
        ctx.telegram.send_message(ctx.chat_id, "Create a profile with /profile to get started!")
        return

    try:
        machines, my_machines = service.block_summary(ctx.user_id, block, int(time.time()))
    except Exception:
        logger.exception("Failed to load laundry status for block %s", block)
        ctx.telegram.send_message(ctx.chat_id, "⚠️ Error retrieving laundry status.")
        return

    washers, dryers = [], []
    for m in machines:
        status_text = STATUS_LABELS.get(m.status, "[Error]")
        if m.mine:
            status_text += " *(by you)*"
        display = f"*{m.machine_id}*: {status_text}"
        if m.mins_left is not None:
            display += f" (~{m.mins_left}mins left)"
        (washers if m.machine_id.startswith("W") else dryers).append(display)

    if my_machines:
        summary_text = f"_You are currently using: {', '.join(my_machines)}_"
    else:
        summary_text = "_You are not using any machines._"

    message_text = (
        f"₊˚⊹ 🧺 *Block {block} Laundry Room* 🫧 ⊹˚₊\n\n"
        f"*Washers*\n"
        f"{chr(10).join(washers)}\n\n"
        f"*Dryers*\n"
        f"{chr(10).join(dryers)}\n\n"
        f"{summary_text}"
    )
    ctx.telegram.send_message(ctx.chat_id, message_text)


@router.callback("cancel")
def cancel(ctx):
    _edit(ctx, "Action cancelled.")


@router.callback("drydur_")
def choose_dryer(ctx):
    if not _require_block(ctx):
        return
    dur = _parts(ctx)[1]
    kb = [[{"text": f"D{i}", "callback_data": f"mclick_D{i}_{dur}"} for i in range(1, 5)], CANCEL_ROW]
    _edit(ctx, f"Which dryer would you like to use for {dur} mins?", kb)


@router.callback("mclick_")
def machine_clicked(ctx):
    block = _require_block(ctx)
    if not block:
        return
    parts = _parts(ctx)
    m_id, dur = parts[1], parts[2]
    status = service.machine_status(block, m_id, int(time.time()))

    if status == "AVAILABLE":
        service.claim(ctx.user_id, ctx.chat_id, block, m_id, int(dur), ctx.scheduler)
        _edit(ctx, f"✅ *{m_id}* is yours for {dur} minutes. I'll remind you when it's done!")
    elif status == "COMPLETED":
        msg = (
            f"*{m_id}* is currently awaiting collection. Send a reminder to the last user?\n"
            f"You can also move the contents to another machine.\n"
            f"_Don't see anything? Perhaps they forgot to click collect. Would you like to override?_"
        )
        kb = [
            [{"text": "🔔 Bump", "callback_data": f"bump_{m_id}"}],
            [{"text": "🔁 Move", "callback_data": f"move_{m_id}"}],
            [{"text": "⚠️ Override", "callback_data": f"over_{m_id}_{dur}"}],
            CANCEL_ROW,
        ]
        _edit(ctx, msg, kb)
    elif status == "IN_USE":
        kb = [[{"text": "⚠️ Yes, Override", "callback_data": f"over_{m_id}_{dur}"}], CANCEL_ROW]
        _edit(ctx, f"⚠️ *{m_id}* is currently IN USE. Are you sure you want to override?", kb)


@router.callback("bump_")
def bump(ctx):
    block = _require_block(ctx)
    if not block:
        return
    m_id = _parts(ctx)[1]
    owner_chat = service.owner_chat(block, m_id)
    if owner_chat is None:
        _edit(ctx, f"⚠️ No registered user to remind for *{m_id}*.")
        return
    ctx.telegram.send_message(owner_chat, f"🔔 Reminder to take your clothes from *{m_id}* as someone is trying to use it!")
    _edit(ctx, f"✅ Reminder sent to the user of *{m_id}*.")


@router.callback("move_")
def choose_move_target(ctx):
    if not _require_block(ctx):
        return
    m_id = _parts(ctx)[1]
    targets = [
        {"text": target, "callback_data": f"moveto_{m_id}_{target}"}
        for target in service.MACHINE_IDS
        if target != m_id
    ]
    kb = [targets[i:i + 4] for i in range(0, len(targets), 4)]
    kb.append(CANCEL_ROW)
    _edit(ctx, f"Where are you moving the contents of *{m_id}* to?", kb)


@router.callback("moveto_")
def move_contents(ctx):
    block = _require_block(ctx)
    if not block:
        return
    parts = _parts(ctx)
    from_id, to_id = parts[1], parts[2]
    outcome, orig_chat = service.move(block, from_id, to_id, int(time.time()))

    if outcome is MoveOutcome.SOURCE_EMPTY:
        _edit(ctx, f"*{from_id}* has nothing recorded in it. Use /wash or /dry to start your cycle.")
    elif outcome is MoveOutcome.TARGET_OCCUPIED:
        kb = [[{"text": "⚠️ Override", "callback_data": f"moveride_{from_id}_{to_id}"}], CANCEL_ROW]
        _edit(ctx, f"⚠️ {to_id} is occupied. You can cancel, or override the target machine.", kb)
    else:
        if orig_chat:
            ctx.telegram.send_message(orig_chat, f"🔔 Your clothes were moved from {from_id} to {to_id} by the next user.")
        _edit(ctx, f"✅ Contents moved to {to_id}. {from_id} is now empty. Use /wash or /dry to start your cycle.")


@router.callback("over_")
def override(ctx):
    block = _require_block(ctx)
    if not block:
        return
    parts = _parts(ctx)
    m_id, dur = parts[1], parts[2]
    service.claim(ctx.user_id, ctx.chat_id, block, m_id, int(dur), ctx.scheduler)
    _edit(ctx, f"✅ Overridden. *{m_id}* is yours for {dur} minutes.")


@router.callback("moveride_")
def move_override(ctx):
    block = _require_block(ctx)
    if not block:
        return
    parts = _parts(ctx)
    from_id, to_id = parts[1], parts[2]
    if service.move_override(block, from_id, to_id) is MoveOutcome.SOURCE_EMPTY:
        _edit(ctx, f"*{from_id}* has nothing recorded in it. Use /wash or /dry to start your cycle.")
        return
    _edit(ctx, f"✅ Overridden and moved. {from_id} is now empty. Use /wash or /dry to start your cycle.")


@router.callback("collect_")
def collect(ctx):
    block = _require_block(ctx)
    if not block:
        return
    m_id = _parts(ctx)[1]
    if service.collect(ctx.user_id, block, m_id):
        _edit(ctx, f"✅ Thank you for collecting from *{m_id}*. The machine is now available.")
    else:
        # They took too long and someone else claimed or moved it.
        _edit(ctx, f"✅ Thank you for collecting from *{m_id}*")


@router.job(service.LAUNDRY_DONE_JOB)
def laundry_done(payload, telegram):
    machine_id = payload["machine_id"]
    telegram.send_message(
        payload["chat_id"],
        f"🔔 Your laundry in {machine_id} (Block {payload['block']}) is done!",
        [[{"text": "Collected ✅", "callback_data": f"collect_{machine_id}"}]],
    )
```

`src/features/__init__.py` becomes:

```python
# Importing each handlers module registers its commands, callbacks and jobs with core.router.
from features.laundry import handlers as _laundry  # noqa: F401
from features.profile import handlers as _profile  # noqa: F401
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `make test`
Expected: 54 passed.

- [ ] **Step 5: Commit**

```bash
git add src/features tests/laundry/test_handlers.py
git commit -m "Port laundry commands and reminder job to the router"
```

---

### Task 7: `app.py` entrypoints, `make dev` and `.env.example`

**Files:**
- Create: `src/app.py`, `.env.example`
- Modify: `Makefile`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `router.dispatch_update`, `router.dispatch_job`, `Services`, `TelegramClient`, `EventBridgeScheduler`, `InProcessScheduler`, `settings()`, `features`.
- Produces: `app.lambda_handler(event, context)`, `app.poll()`, `app._lambda_services()` (cached).

- [ ] **Step 1: Write the failing app tests**

`tests/test_app.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_app.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'app'`.

- [ ] **Step 3: Implement `app.py`**

`src/app.py`:

```python
import base64
import json
import logging
import os
from functools import cache

import features  # noqa: F401  (importing registers every handler)
from core.config import settings
from core.context import Services
from core.router import router
from core.scheduler import EventBridgeScheduler, InProcessScheduler
from core.telegram import TelegramClient

logger = logging.getLogger(__name__)


@cache
def _lambda_services():
    return Services(
        telegram=TelegramClient(settings().telegram_bot_token),
        scheduler=EventBridgeScheduler(os.environ["SCHEDULER_ROLE_ARN"], os.environ["SCHEDULE_TARGET_ARN"]),
    )


def lambda_handler(event, context):
    if "requestContext" in event:
        return _handle_webhook(event)
    if "job" in event:
        router.dispatch_job(event["job"], event["payload"], _lambda_services().telegram)
        return None
    raise ValueError(f"Unrecognised event with keys {sorted(event)}")


def _handle_webhook(event):
    body = event.get("body")
    if not body:
        return {"statusCode": 200}
    try:
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode()
        router.dispatch_update(json.loads(body), _lambda_services())
    except Exception:
        # Telegram retries non-2xx responses indefinitely, so a failed update is logged and acknowledged.
        logger.exception("Failed to handle Telegram update")
    return {"statusCode": 200}


def poll():
    """Local entrypoint: long-polls Telegram so the bot runs without deploying."""
    telegram = TelegramClient(settings().telegram_bot_token)
    webhook_url = telegram.get_webhook_info().get("url")
    if webhook_url:
        raise SystemExit(
            f"This bot has a webhook set ({webhook_url}), so it belongs to a deployed environment. "
            "Use your own BotFather bot for local runs."
        )
    services = Services(telegram=telegram, scheduler=InProcessScheduler(telegram))
    offset = None
    logger.info("Polling Telegram for updates")
    while True:
        for update in telegram.get_updates(offset, timeout=30):
            offset = update["update_id"] + 1
            try:
                router.dispatch_update(update, services)
            except Exception:
                logger.exception("Failed to handle update %s", update["update_id"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        poll()
    except KeyboardInterrupt:
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `make test`
Expected: 64 passed.

- [ ] **Step 5: Add `make dev` and `.env.example`**

`Makefile` becomes (recipe lines indented with a tab):

```make
PYTHON_BIN ?= python3.12
VENV := .venv
PYTHON := $(VENV)/bin/python

# Everything that differs per developer or environment lives in .env; see .env.example.
-include .env
export

.PHONY: install dev test

install:
	$(PYTHON_BIN) -m venv $(VENV)
	$(PYTHON) -m pip install -r requirements-dev.txt

dev: .env
	$(PYTHON) src/app.py

test:
	$(PYTHON) -m pytest
```

`.env.example`:

```
# Copy to .env and fill in. Values are read by Make, so do not quote them.

# Token of your own bot from @BotFather. Never a deployed bot: those have a webhook and make dev refuses them.
TELEGRAM_BOT_TOKEN=

# Real DynamoDB tables. Local runs read and write whatever you put here.
USERS_TABLE_NAME=preprod-db-table-sheareswebbot-users
LAUNDRY_TABLE_NAME=preprod-db-table-sheareswebbot-laundry-machines

AWS_PROFILE=
AWS_DEFAULT_REGION=ap-southeast-1
```

- [ ] **Step 6: Verify `make dev` wiring**

Run: `make dev` with no `.env` present.
Expected: `make: *** No rule to make target '.env', needed by 'dev'.  Stop.`

Run: `printf 'TELEGRAM_BOT_TOKEN=123:fake\nUSERS_TABLE_NAME=u\nLAUNDRY_TABLE_NAME=l\nAWS_DEFAULT_REGION=ap-southeast-1\n' > .env && timeout 20 make dev; rm .env`
Expected: the process starts and exits with `core.telegram.TelegramError: getWebhookInfo failed with HTTP 401: Unauthorized`. That proves `.env` is exported, `app.py` imports cleanly as a script, and the webhook guard runs first. Do not commit this `.env`.

- [ ] **Step 7: Commit**

```bash
git add src/app.py tests/test_app.py Makefile .env.example
git commit -m "Add single Lambda entrypoint and local polling via make dev"
```

---

### Task 8: Remove the old Lambdas, update the image and CI

**Files:**
- Delete: `src/receiver/`, `src/processor/`, `src/notifier/`
- Modify: `Dockerfile`, `.github/workflows/2-ci-merge-request.yml`

**Interfaces:**
- Consumes: `src/app.py`, `src/core/`, `src/features/`.
- Produces: an image whose `CMD` is `app.lambda_handler`.

- [ ] **Step 1: Delete the old code**

```bash
git rm -r src/receiver src/processor src/notifier
```

Run: `grep -rn "processor\|receiver\|notifier" src tests`
Expected: no output.

- [ ] **Step 2: Update the Dockerfile**

`Dockerfile`:

```dockerfile
# Python Lambda base image
FROM public.ecr.aws/lambda/python:3.12

# Install Python dependencies into the Lambda task root
COPY ./src/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -t ${LAMBDA_TASK_ROOT}

# Copy application code. src/.archive is deliberately left out.
COPY ./src/app.py ${LAMBDA_TASK_ROOT}/
COPY ./src/core ${LAMBDA_TASK_ROOT}/core
COPY ./src/features ${LAMBDA_TASK_ROOT}/features

CMD [ "app.lambda_handler" ]
```

- [ ] **Step 3: Verify the image layout without Docker**

Docker is not installed on this machine, so rebuild the image's task root from exactly the paths the Dockerfile copies and invoke the handler from there:

```bash
root="$(mktemp -d)"
cp src/app.py "$root/" && cp -r src/core src/features "$root/"
cd "$root" && env -i PATH="$PATH" TELEGRAM_BOT_TOKEN=x USERS_TABLE_NAME=u LAUNDRY_TABLE_NAME=l \
  SCHEDULER_ROLE_ARN=x SCHEDULE_TARGET_ARN=x AWS_DEFAULT_REGION=ap-southeast-1 \
  "$OLDPWD/.venv/bin/python" -c "
import app
print(app.lambda_handler({'requestContext': {}, 'body': ''}, None))
try:
    app.lambda_handler({}, None)
except ValueError as e:
    print('ValueError:', e)
"
cd "$OLDPWD" && rm -rf "$root"
```

Expected:

```
{'statusCode': 200}
ValueError: Unrecognised event with keys []
```

If Docker is available, also run: `docker build -t sheares-bot . && docker run --rm -d -p 9000:8080 --name sheares-bot sheares-bot && sleep 2 && curl -s localhost:9000/2015-03-31/functions/function/invocations -d '{}'; docker rm -f sheares-bot`
Expected: a JSON error with `"errorType": "ValueError"` and `"Unrecognised event with keys []"`.

- [ ] **Step 4: Add the unit test job to CI**

In `.github/workflows/2-ci-merge-request.yml`, add this job under `jobs:` after `docker-build-smoke-test`:

```yaml
  unit-tests:
    runs-on: ubuntu-latest

    steps:
      - name: 1. ⬇️ Checkout Code
        uses: actions/checkout@v4

      - name: 2. 🐍 Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: 3. 📦 Install Dependencies
        run: make install PYTHON_BIN=python

      - name: 4. 🧪 Run Tests
        run: make test
```

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/2-ci-merge-request.yml'))" && make test`
Expected: no YAML error; 64 passed. (If PyYAML is missing, `.venv/bin/pip install pyyaml` into the venv first, or skip the YAML parse and rely on review.)

- [ ] **Step 5: Commit**

```bash
git add -A Dockerfile .github/workflows/2-ci-merge-request.yml src
git commit -m "Ship one handler in the image and run tests in CI"
```

---

### Task 9: Terraform for a single function

**Files:**
- Modify: `infra/lambda.tf`, `infra/apigw.tf`, `infra/eventbridge-scheduler.tf`, `infra/iam.tf`, `infra/outputs.tf`

**Interfaces:**
- Produces: `module.bot_function`; `local.bot_function_name`, `local.bot_function_arn`; output `function_names` = the bot function name.

- [ ] **Step 1: Replace the three functions**

`infra/lambda.tf`:

```hcl
locals {
  bot_function_name = "${var.env}-app-func-${var.project_code}-bot"
  # Built from the name: the function's env and the scheduler role both need this ARN,
  # and referencing module.bot_function from either is a dependency cycle.
  bot_function_arn = "arn:aws:lambda:${var.region}:${data.aws_caller_identity.current.account_id}:function:${local.bot_function_name}"
}

module "bot_function" {
  source             = "./modules/lambda_function"
  function_name      = local.bot_function_name
  execution_role_arn = module.lambda_execution_role.role.arn
  deployment_package = {
    image_uri = "${aws_ecr_repository.this.repository_url}:placeholder"
  }
  image_config = {
    command = ["app.lambda_handler"]
  }
  ignore_deployment_package_changes = true

  depends_on = [null_resource.push_placeholder_image]

  timeout     = 30
  memory_size = 512

  environment_variables = {
    TELEGRAM_BOT_TOKEN  = var.telegram_bot_token
    USERS_TABLE_NAME    = aws_dynamodb_table.users.name
    LAUNDRY_TABLE_NAME  = aws_dynamodb_table.laundry_machines.name
    SCHEDULER_ROLE_ARN  = module.scheduler_execution_role.role.arn
    SCHEDULE_TARGET_ARN = local.bot_function_arn
  }
}
```

- [ ] **Step 2: Point API Gateway, the scheduler role, IAM and outputs at it**

`infra/apigw.tf`: in `module "lambda_integration"`, change the two function lines to:

```hcl
  function_name             = module.bot_function.function.function_name
  function_invoke_arn       = module.bot_function.function.invoke_arn
```

`infra/eventbridge-scheduler.tf`: change the comment and resource:

```hcl
# Role EventBridge Scheduler assumes to invoke the bot when a laundry
# reminder fires. The schedules themselves are created at runtime by the bot.
```

and `resources = [module.notifier_function.function.arn]` becomes `resources = [local.bot_function_arn]`.

`infra/iam.tf`: change the comment `# Shared by the receiver, processor and notifier functions.` to `# Execution role for the bot function.` and delete the whole `InvokeProcessor = { ... }` block.

`infra/outputs.tf`: replace the `function_names` output with:

```hcl
# The release workflows loop over this space-separated list; there is one function now.
output "function_names" {
  value = module.bot_function.function.function_name
}
```

- [ ] **Step 3: Validate**

Terraform is not installed locally. Fetch the version CI uses into the scratchpad (not the repo):

```bash
tf_dir="$SCRATCHPAD/terraform"   # the session scratchpad directory
mkdir -p "$tf_dir" && curl -sSLo "$tf_dir/tf.zip" https://releases.hashicorp.com/terraform/1.11.0/terraform_1.11.0_linux_amd64.zip \
  && python3 -m zipfile -e "$tf_dir/tf.zip" "$tf_dir" && chmod +x "$tf_dir/terraform"
"$tf_dir/terraform" -chdir=infra fmt -check -diff
"$tf_dir/terraform" -chdir=infra init -backend=false -input=false
"$tf_dir/terraform" -chdir=infra validate
grep -rn "receiver_function\|processor_function\|notifier_function" infra/*.tf
```

Expected: `fmt` prints nothing, `init` ends with "Terraform has been successfully initialized!", `validate` prints "Success! The configuration is valid.", and `grep` prints nothing. `init -backend=false` writes `infra/.terraform/`, which `.gitignore` already covers; delete it afterwards with `rm -rf infra/.terraform infra/.terraform.lock.hcl`.

- [ ] **Step 4: Commit**

```bash
git add infra/lambda.tf infra/apigw.tf infra/eventbridge-scheduler.tf infra/iam.tf infra/outputs.tf
git commit -m "Replace receiver, processor and notifier with one bot function"
```

---

### Task 10: README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Rewrite the architecture bullets**

Replace the five bullets under `## System Architecture` (keep the heading, image and intro sentence) with:

```markdown
*   **Ingestion:** A Shearite sends a message to the Telegram Bot, which forwards the update via webhook to an API Gateway.
*   **Bot Lambda:** API Gateway invokes the single bot function. `src/app.py` sees a webhook event, routes the update to the matching handler, and returns 200 to Telegram once it has been handled. Failures are logged and still acknowledged so Telegram does not retry them forever.
*   **Storage:** Handlers read and write user and machine state in DynamoDB.
*   **Scheduling:** When a user starts a time-delayed action (like a laundry cycle), the bot creates a one-off EventBridge schedule.
*   **Notification:** When the schedule fires, EventBridge invokes the same function with a job event (`{"job": "laundry_done", "payload": {...}}`), and the job handler messages the user.

The diagram still shows the earlier three-function layout.
```

- [ ] **Step 2: Replace `## Code Structure` and add the two new sections**

Replace the whole `## Code Structure` section (heading and bullets) with:

````markdown
## Code Structure

```
src/
  app.py          Lambda entrypoint and local polling entrypoint; routing only
  core/           config, router, Telegram client, schedulers, DynamoDB tables
  features/
    profile/      handlers.py, repository.py
    laundry/      handlers.py, service.py, repository.py
tests/            pytest suite (moto for AWS, a fake Telegram client)
```

Each feature splits into three layers. `handlers.py` owns everything Telegram-shaped: message text, keyboards, parsing `callback_data`. `service.py` holds the rules and never imports Telegram. `repository.py` is the only module that talks to DynamoDB. A feature without real rules (like `profile`) skips `service.py`.

All code ships in one Docker image (see `Dockerfile`) run by one Lambda with handler `app.lambda_handler`.

## Adding a Command

Register handlers with the decorators in `core/router.py`. A command or button handler receives a `Context` with `user_id`, `chat_id`, `text`, `data`, `message_id`, `telegram` and `scheduler`.

```python
from core.router import router


@router.command("/feedback")
def feedback_command(ctx):
    kb = [[{"text": "Hall events", "callback_data": "fb_events"}]]
    ctx.telegram.send_message(ctx.chat_id, "What is your feedback about?", kb)


@router.callback("fb_")
def feedback_topic(ctx):
    ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, f"Thanks! Topic: {ctx.data[3:]}")
```

Callback handlers match on a `callback_data` prefix, and the longest matching prefix wins. Registering the same command or prefix twice fails at startup. For a new feature, create `src/features/<name>/` and add its `handlers` import to `src/features/__init__.py`. Scheduled work uses `@router.job("<name>")` with a `(payload, telegram)` handler and `ctx.scheduler.schedule(name, run_at, job, payload)`; EventBridge schedule names must start with `Laundry_` until the IAM policy is widened.

## Running Locally

1.  Create your own bot with [@BotFather](https://t.me/BotFather) and copy its token. Do not use the preprod or prd bot: they have webhooks, and `make dev` refuses to poll a bot with a webhook.
2.  `cp .env.example .env` and fill it in. The table names point at real DynamoDB tables, so whatever you do locally changes those tables. Your `AWS_PROFILE` needs read and write access to them.
3.  `make install` once, then `make dev`. Message your bot in Telegram.

Reminders run in-process while `make dev` is running, so stopping it drops pending ones. Run the tests with `make test`; they use moto and need no AWS access.
````

- [ ] **Step 3: Check conventions**

Run: `grep -n "—" README.md; grep -n "processor\|receiver\|notifier" README.md`
Expected: no output from either grep.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Document the single Lambda layout, adding commands and local runs"
```

---

### Task 11: Commit the spec and plan, final verification

- [ ] **Step 1: Full test run and convention scan**

Run: `make test && grep -rn "—" src tests Makefile .env.example infra/*.tf Dockerfile docs/superpowers`
Expected: 64 passed; no em dash hits.

- [ ] **Step 2: Commit the design documents**

```bash
git add docs/superpowers/specs/2026-10-06-single-lambda-refactor-design.md docs/superpowers/plans/2026-10-06-single-lambda-refactor.md
git commit -m "Add single Lambda refactor spec and plan"
```

- [ ] **Step 3: Report**

Report to the user: test count from Step 1, Terraform validate result from Task 9, the image-layout check from Task 8 (and that the real `docker build` smoke test runs in CI only), and the migration note from the spec (pending reminders at deploy time are lost; deploy prd outside peak laundry hours).
