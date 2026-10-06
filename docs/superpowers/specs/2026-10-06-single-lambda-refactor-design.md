# Single Lambda Refactor Design

Date: 2026-10-06

## Problem

Developers report the bot is hard to maintain. Routing, Telegram I/O, business rules and DynamoDB access are mixed in the same functions (`src/processor/laundry/handlers.py` is one 400-line module, and one callback function branches on nine prefixes). Adding a command means editing a hand-written if/elif chain in `processor/lambda_function.py`. There is no way to run the bot locally: `shared.py` reads env vars and builds boto3 tables at import time, and the receiver, processor and notifier only work as three wired-up Lambdas.

## Goals

1. One Lambda. `src/app.py` detects the invocation source and hands off to application code; it holds no business logic.
2. Clear layers per feature: Telegram UI, business rules, persistence.
3. Adding a command or button is a decorated function plus, for a new feature, one import line.
4. A developer runs the bot locally with `make dev`, configured entirely by `.env`.
5. User-visible behaviour is unchanged: same commands, same `callback_data` formats (so buttons already sitting in users' chats keep working), same DynamoDB tables and item shapes.

## Non-goals

- No local AWS emulation (no Docker Compose, no DynamoDB Local). Local runs use real AWS resources named in `.env`.
- No new features, no schema changes, no namespacing of `callback_data`.

## Folder structure

```
src/                          # Lambda task root
  app.py                      # lambda_handler + `python app.py` long-polling entrypoint
  core/
    __init__.py
    config.py                 # env vars, read on first use
    context.py                # Context dataclass passed to handlers
    router.py                 # Router: @command, @callback, @job + dispatch
    telegram.py               # TelegramClient: send_message, edit_message, get_updates, get_webhook_info
    scheduler.py              # EventBridgeScheduler, InProcessScheduler
    db.py                     # cached boto3 DynamoDB tables
  features/
    __init__.py               # imports every feature's handlers module so they register
    profile/
      __init__.py
      handlers.py
      repository.py
    laundry/
      __init__.py
      handlers.py
      service.py
      repository.py
  requirements.txt
tests/
  conftest.py
  core/test_router.py
  test_app.py
  laundry/test_service.py
  laundry/test_handlers.py
  profile/test_handlers.py
Makefile
.env.example
requirements-dev.txt
```

`src/receiver/`, `src/processor/` and `src/notifier/` are deleted. `src/.archive/` is left as is and is not copied into the image.

## `src/app.py`

`lambda_handler(event, context)` branches on event shape:

| Event | Detected by | Action |
|---|---|---|
| API Gateway HTTP API (Telegram webhook) | `"requestContext" in event` | Decode `body` (base64 when `isBase64Encoded`), `json.loads`, `router.dispatch_update(update, services)`. Always returns `{"statusCode": 200}`. |
| EventBridge Scheduler job | `"job" in event` | `router.dispatch_job(event["job"], event["payload"], services.telegram)`. |
| Anything else | | Raise `ValueError` naming the event keys, so misrouted invocations show up as Lambda errors. |

On the webhook path, any exception from dispatch is logged with `logger.exception` and the function still returns 200. Telegram retries non-2xx responses indefinitely, so returning 200 is how the error is handled; the traceback in CloudWatch is how it is surfaced. On the job path exceptions propagate so EventBridge Scheduler's retry policy applies.

Updates are processed synchronously inside the API Gateway request. Handlers make a handful of DynamoDB and Telegram calls and finish well under a second, API Gateway allows 30s, and Telegram waits up to 60s. This removes the receiver hop and the async self-invoke.

`services` is built once per container: `Services(telegram=TelegramClient(token), scheduler=EventBridgeScheduler(...))`.

`if __name__ == "__main__":` runs the local long-polling loop:

1. Build `telegram = TelegramClient(token)` and `Services(telegram=telegram, scheduler=InProcessScheduler(telegram))`.
2. Call `getWebhookInfo`. If the bot has a webhook URL set, exit with an error telling the developer to use their own BotFather bot. Polling a bot with a webhook fails, and deleting the webhook would break a deployed environment.
3. Loop on `getUpdates(offset, timeout=30, allowed_updates=["message", "callback_query"])`. For each update, call `dispatch_update`, logging any exception with its traceback and continuing, then advance `offset` to `update_id + 1`.
4. Ctrl-C exits cleanly.

The scheduler choice follows the entrypoint, not an env var: Lambda always uses EventBridge, local polling always runs jobs in-process. A local run must never create an EventBridge schedule, because that schedule would fire the deployed Lambda and send the reminder from the deployed bot.

## `core/`

**`config.py`**: a `functools.cache`d `settings()` returning a frozen dataclass read from `os.environ[...]`. Missing variables raise `KeyError` on first use, not at import, so tests and tooling can import modules without a full environment. Fields: `telegram_bot_token`, `users_table_name`, `laundry_table_name`. `SCHEDULER_ROLE_ARN` and `SCHEDULE_TARGET_ARN` are read by `EventBridgeScheduler` only, so local runs do not need them.

**`context.py`**:

```python
@dataclass(frozen=True)
class Context:
    user_id: int
    chat_id: int
    text: str            # message text, "" for callbacks
    data: str            # callback_data, "" for messages
    message_id: int | None  # message the button was on, None for messages
    telegram: TelegramClient
    scheduler: Scheduler
```

**`router.py`**: a module-level `router = Router()` with:

- `@router.command("/wash")`: matches when the first whitespace-separated token of the message text, with any `@BotName` suffix removed, equals the command. One handler may be registered for several commands by stacking decorators (`/help` and `/start`). Unmatched text is ignored, as today.
- `@router.callback("mclick_")`: matches when `callback_data` starts with the prefix. When several prefixes match, the longest wins. Registering the same command, prefix or job name twice raises `ValueError` at import.
- `@router.job("laundry_done")`: handler signature `(payload: dict, telegram: TelegramClient)`. Jobs only send messages, so they get the Telegram client rather than full `Services`.
- `dispatch_update(update, services)`: builds a `Context` and calls the matched handler. It keeps today's guards: messages without `from` or with `sender_chat` are ignored; callbacks without `message` or `data` are ignored. The user is `callback_query.from`, not the bot that sent the message.
- `dispatch_job(name, payload, telegram)`: raises `KeyError` for an unknown job.

Command and callback handlers take a single `ctx: Context` argument.

**`telegram.py`**: `TelegramClient(token)` wrapping `requests`, with `send_message(chat_id, text, keyboard=None)`, `edit_message(chat_id, message_id, text, keyboard=None)`, `get_updates(offset, timeout)` and `get_webhook_info()`. All messages use `parse_mode="Markdown"`, as the processor does today. Every call raises on a non-2xx response or `"ok": false`. Today these failures are silently dropped; with this change they reach the logs via the dispatch error handling above.

**`scheduler.py`**: both implementations expose `schedule(name: str, run_at: datetime, job: str, payload: dict)`.

- `EventBridgeScheduler(role_arn, target_arn)`: `create_schedule` with `at(...)`, `FlexibleTimeWindow OFF`, `ActionAfterCompletion DELETE` and `Input = json.dumps({"job": job, "payload": payload})`, as today. Schedule names keep the `Laundry_...` prefix that the IAM policy allows.
- `InProcessScheduler(telegram)`: starts a daemon `threading.Timer` that calls `dispatch_job(job, payload, telegram)` at `run_at`, and logs any exception from it.

**`db.py`**: a cached `boto3.resource("dynamodb")` plus `users_table()` and `laundry_table()` built from `settings()`.

## `features/`

`features/__init__.py` imports `features.profile.handlers` and `features.laundry.handlers`. `app.py` imports `features` so registration happens on cold start.

**Layer rules:**

- `handlers.py` owns everything Telegram-shaped: message text, keyboards, parsing `callback_data`. It calls `service` (or `repository` where there are no rules) and `ctx.telegram`.
- `service.py` owns business rules. It does not import `core.telegram` or build keyboards. It returns plain values (statuses, outcome enums, owner chat IDs) for handlers to render.
- `repository.py` is the only module that touches DynamoDB.

**`profile`**: `handlers.py` registers `/help`, `/start`, `/profile` and the `profile_` callback (block, level and year steps, unchanged). `repository.py` provides `get_profile(user_id)` and `put_profile(...)`, and holds `user_profile_key`. There is no service layer because there are no rules.

**`laundry`**:

- `repository.py`: `get_machine`, `query_block`, `put_machine`, `set_machine_state` (the repeated `SET status, currentUser, currentChatID, endTime` update), `add_user_machine`, `remove_user_machine`, `get_user_machines`, `get_user_block`, plus `get_notification_chat` with its legacy-record handling.
- `service.py`: `MACHINE_IDS`, `real_status(machine, now)`, `claim(user_id, chat_id, block, machine_id, duration_mins, scheduler)` (clears the previous owner's tracker, writes the machine, schedules `laundry_done`), `move(block, from_id, to_id)`, `move_override(block, from_id, to_id)`, `collect(user_id, block, machine_id)` (returns whether the user still owned it), and `block_summary(user_id, block, now)` for `/laundry`.
- `handlers.py`: registers `/laundry`, `/wash` and `/dry`, one callback handler per existing prefix (`drydur_`, `mclick_`, `bump_`, `move_`, `moveto_`, `over_`, `moveride_`, `collect_`, `cancel`), and the `laundry_done` job. A shared helper keeps today's preamble: strip `*` from `callback_data` parts, and require a profile block before any action except `cancel`.
- The `laundry_done` job payload is `{"chat_id", "block", "machine_id"}`. The job handler renders the same text and "Collected ✅" button the notifier sends today. Moving that text out of the schedule payload puts the reminder copy next to the rest of the laundry copy.

## Configuration

`.env.example` (committed; `.env` is already in `.gitignore`):

```
TELEGRAM_BOT_TOKEN=
USERS_TABLE_NAME=
LAUNDRY_TABLE_NAME=
AWS_PROFILE=
AWS_REGION=ap-southeast-1
```

The Lambda gets the same names from Terraform plus `SCHEDULER_ROLE_ARN` and `SCHEDULE_TARGET_ARN`. Local runs read and write whichever tables `.env` names; pointing at preprod tables means sharing preprod data.

## Makefile

| Target | Command |
|---|---|
| `install` | `python3.12 -m venv .venv` and `pip install -r requirements-dev.txt` |
| `dev` | `-include .env` + `export`, then `.venv/bin/python src/app.py` |
| `test` | `.venv/bin/pytest` |

`requirements-dev.txt` contains `-r src/requirements.txt`, `boto3` (provided by the Lambda runtime, so it stays out of `src/requirements.txt`), `pytest` and `moto[dynamodb,scheduler]`. `pytest` runs with `pythonpath = src` configured in `pyproject.toml`.

## Infrastructure

- `infra/lambda.tf`: replace the three modules with one `bot_function` named `${env}-app-func-${project_code}-bot`, `image_config.command = ["app.lambda_handler"]`, timeout 30, memory 512, env `TELEGRAM_BOT_TOKEN`, `USERS_TABLE_NAME`, `LAUNDRY_TABLE_NAME`, `SCHEDULER_ROLE_ARN`, `SCHEDULE_TARGET_ARN`. `SCHEDULE_TARGET_ARN` is built as a string from region, account ID and function name, because referencing the function's own ARN in its environment is a dependency cycle.
- `infra/apigw.tf`: the integration points at `bot_function`. The API and its URL are unchanged, so the registered webhook keeps working.
- `infra/eventbridge-scheduler.tf`: the scheduler role may invoke `bot_function`.
- `infra/iam.tf`: drop `InvokeProcessor`; keep DynamoDB, `scheduler:CreateSchedule` on `Laundry_*` and `iam:PassRole`.
- `infra/outputs.tf`: `function_names` outputs the single function name. The release workflows loop over it and need no change.
- `Dockerfile`: copy `src/app.py`, `src/core` and `src/features`; `CMD ["app.lambda_handler"]`.
- `.github/workflows/2-ci-merge-request.yml`: add a job that installs `requirements-dev.txt` and runs `pytest`, alongside the existing image smoke test.

## Migration

The first apply destroys the receiver, processor and notifier functions. EventBridge schedules created before the deploy target the notifier's ARN, so any laundry reminder still pending at deploy time (at most 60 minutes out) fails to fire. Accepting a payload in the old shape would not help, because those schedules invoke the deleted function, not the new one. Deploy prd outside peak laundry hours. No data migration is needed.

## Testing

All tests run without network access or AWS credentials: moto provides DynamoDB and Scheduler, and a `FakeTelegram` records `send_message` and `edit_message` calls. A `conftest.py` fixture sets the env vars, creates both tables in moto and builds `Services` with `FakeTelegram` and either a recording scheduler or a moto-backed `EventBridgeScheduler`.

- `core/test_router.py`: command token matching including `@BotName` and `/washing` not matching `/wash`; longest-prefix callback matching; duplicate registration raises; unknown text and malformed updates are ignored; unknown job raises.
- `test_app.py`: an API Gateway event (plain and base64 body) reaches the right handler and returns 200; a handler exception still returns 200 and is logged; a job event reaches the job handler and propagates exceptions; an unrecognised event raises.
- `laundry/test_service.py`: `real_status` flips `IN_USE` to `COMPLETED` after `endTime`; `claim` writes the machine, moves the tracker from the previous owner and creates a schedule with the `laundry_done` payload; `move` and `move_override` transfer trackers and machine state; `collect` clears only when the caller still owns the machine.
- `laundry/test_handlers.py` and `profile/test_handlers.py`: end-to-end flows through `dispatch_update` (`/wash` → `mclick_` → claim; `/profile` creation through to `put_item`), asserting on the recorded Telegram calls and table contents.

## Documentation

Update the README's System Architecture and Code Structure sections to describe the single function, and add "Adding a command" and "Running locally" sections. `aws_infra.png` still shows three Lambdas; it needs redrawing, which is out of scope here.
