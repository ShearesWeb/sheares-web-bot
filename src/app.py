import asyncio
import base64
import json
import logging
from typing import Any, Dict, Optional

from telegram import Update

from telegram_app import build_application, get_bot_token

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def _extract_update_payload(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(event, dict):
        return None

    body = event.get("body")
    if body is None:
        if "update_id" in event:
            return event
        return None

    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")

    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8")

    if isinstance(body, str):
        body = body.strip()
        if not body:
            return None
        return json.loads(body)

    return None


async def _process_update(payload: Dict[str, Any]) -> None:
    bot_token = get_bot_token()
    application = build_application(bot_token)

    await application.initialize()
    try:
        update = Update.de_json(payload, application.bot)
        if update is None:
            return
        await application.process_update(update)
    finally:
        await application.shutdown()


def handler(event, context):
    payload = _extract_update_payload(event or {})
    if payload is None:
        LOG.info("No update payload received.")
        return {"statusCode": 200, "body": json.dumps("No update")}

    asyncio.run(_process_update(payload))
    return {"statusCode": 200, "body": json.dumps("OK")}


if __name__ == "__main__":
    handler({}, None)
