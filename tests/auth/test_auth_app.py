
import json

from auth_app import lambda_handler
from features.auth import service


def make_event(body, route="POST /internal/telegram-link/preview"):
    return {
        "routeKey": route,
        "body": json.dumps(body),
    }


def test_valid_token_preview():
    token = service.create_link_request(12345)

    result = lambda_handler(make_event({"token": token}), None)

    assert result["statusCode"] == 200
    data = json.loads(result["body"])
    assert data["valid"] is True
    assert data["telegram_user_id"] == "12345"


def test_invalid_token_preview():
    result = lambda_handler(make_event({"token": "invalid"}), None)

    assert result["statusCode"] == 404


def test_missing_token():
    result = lambda_handler(make_event({}), None)

    assert result["statusCode"] == 400


def test_unknown_route():
    result = lambda_handler(
        make_event({}, route="POST /unknown"),
        None,
    )

    assert result["statusCode"] == 404
