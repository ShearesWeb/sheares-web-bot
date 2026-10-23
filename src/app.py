import base64
import hmac
import json
import logging
import os
import time
from functools import cache

import features  # noqa: F401  (importing registers every handler)
from core.config import settings
from core.context import Services
from core.router import router
from core.scheduler import EventBridgeScheduler, InProcessScheduler
from core.telegram import TelegramClient, TelegramError

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
        services = _lambda_services()
        router.dispatch_job(event["job"], event["payload"], services.telegram, scheduler=services.scheduler)
        return None
    raise ValueError(f"Unrecognised event with keys {sorted(event)}")


def _handle_webhook(event):
    # API Gateway normalises headers today, but treating names case-insensitively
    # also keeps this correct for direct Lambda tests and future integrations.
    headers = {
        key.lower(): value
        for key, value in (event.get("headers") or {}).items()
    }
    supplied_secret = headers.get("x-telegram-bot-api-secret-token", "")
    expected_secret = settings().telegram_webhook_secret
    if not supplied_secret or not hmac.compare_digest(
        supplied_secret,
        expected_secret,
    ):
        logger.warning("Rejected request with invalid Telegram webhook secret")
        return {"statusCode": 401}

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


def _poll_request(request, **kwargs):
    """Retry polling reads after connection failures or temporary server errors."""
    delay = 1
    while True:
        try:
            result = request(**kwargs)
        except TelegramError as exc:
            if exc.code is not None and not 500 <= exc.code < 600:
                raise
            reason = "connection error" if exc.code is None else f"HTTP {exc.code}"
            # Request exceptions can include the bot token; log only a safe summary.
            logger.warning(
                "Telegram %s failed (%s). Retrying in %s seconds.", request.__name__, reason, delay,
            )
            time.sleep(delay)
            delay = min(delay * 2, 30)
        else:
            if delay > 1:
                logger.info("Telegram connection restored.")
            return result


def poll():
    """Local entrypoint: long-polls Telegram so the bot runs without deploying."""
    telegram = TelegramClient(settings().telegram_bot_token)
    webhook_url = _poll_request(telegram.get_webhook_info).get("url")
    if webhook_url:
        raise SystemExit(
            f"This bot has a webhook set ({webhook_url}), so it belongs to a deployed environment. "
            "Use your own BotFather bot for local runs."
        )
    services = Services(telegram=telegram, scheduler=InProcessScheduler(telegram))
    offset = None
    logger.info("Polling Telegram for updates")
    while True:
        updates = _poll_request(telegram.get_updates, offset=offset, timeout=30)
        for update in updates:
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
