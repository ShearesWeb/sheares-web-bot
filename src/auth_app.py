
import json

from features.auth import repository


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

    if route == "POST /internal/telegram-link/preview":
        return preview_link(event)

    return response(404, {"error": "Not found"})


def preview_link(event):
    try:
        body = json.loads(event.get("body") or "{}")
    except (ValueError, TypeError):
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
