
import base64
import json
import logging

from features.auth import repository

logger = logging.getLogger(__name__)


def response(status_code, data):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(data),
    }


def lambda_handler(event, context):
    """Dedicated intranet integration Lambda.

    Must only be exposed through an authenticated
    API Gateway route. Not the public bot webhook.
    """

    route = event.get("routeKey")

    try:
        if route == "POST /internal/telegram-link/preview":
            return preview_link(event)
        if route == "POST /internal/telegram-link/complete":
            return complete_link(event)
    except Exception:
        logger.exception("Telegram account-link API failed")
        return response(500, {"error": "Internal server error"})

    return response(404, {"error": "Not found"})


def preview_link(event):
    body = parse_body(event)
    if body is None:
        return response(400, {"error": "Invalid JSON"})

    token = body.get("token")

    if not isinstance(token, str) or not token:
        return response(400, {"error": "Missing token"})

    request = repository.get_link_request(token)

    if request is None:
        return response(404, {"error": "Invalid or expired token"})

    return response(
        200,
        {
            "valid": True,
            "telegram_user_id": request["telegram_user_id"],
        },
    )


def complete_link(event):
    body = parse_body(event)
    if body is None:
        return response(400, {"error": "Invalid JSON"})

    token = body.get("token")
    resident_id = body.get("resident_id")

    if not isinstance(token, str) or not token:
        return response(400, {"error": "Missing token"})
    if not (
        isinstance(resident_id, int)
        and not isinstance(resident_id, bool)
        and resident_id > 0
    ):
        return response(400, {"error": "Invalid resident ID"})

    try:
        link = repository.complete_link_request(token, resident_id)
    except repository.LinkConflictError:
        return response(409, {"error": "An account is already linked"})

    if link is None:
        return response(404, {"error": "Invalid or expired token"})

    # Do not echo either internal identifier. The trusted intranet backend
    # already knows the resident and only needs the completion result.
    return response(200, {"linked": True})


def parse_body(event):
    try:
        raw_body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            raw_body = base64.b64decode(raw_body, validate=True).decode()
        body = json.loads(raw_body)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None
