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
        services = _lambda_services()
        router.dispatch_job(event["job"], event["payload"], services.telegram, scheduler=services.scheduler)
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
