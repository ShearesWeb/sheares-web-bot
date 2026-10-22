
import json
from unittest.mock import patch

from auth_app import lambda_handler
from features.auth import repository, service


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

    # Preview is deliberately read-only, even when requested repeatedly.
    assert lambda_handler(make_event({"token": token}), None)["statusCode"] == 200
    assert service.consume_link_request(token) is not None


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


def test_complete_link_is_single_use_and_creates_both_mappings():
    token = service.create_link_request(12345)

    result = lambda_handler(
        make_event(
            {"token": token, "resident_id": 987},
            route="POST /internal/telegram-link/complete",
        ),
        None,
    )

    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"linked": True}
    assert repository.get_link_for_telegram(12345)["resident_id"] == "987"
    assert repository.get_link_for_resident(987)["telegram_user_id"] == "12345"

    replay = lambda_handler(
        make_event(
            {"token": token, "resident_id": 987},
            route="POST /internal/telegram-link/complete",
        ),
        None,
    )
    assert replay["statusCode"] == 404


def test_complete_link_rejects_duplicate_resident_without_overwriting():
    first_token = service.create_link_request(12345)
    second_token = service.create_link_request(67890)
    route = "POST /internal/telegram-link/complete"

    assert lambda_handler(
        make_event({"token": first_token, "resident_id": 987}, route=route),
        None,
    )["statusCode"] == 200

    duplicate = lambda_handler(
        make_event({"token": second_token, "resident_id": 987}, route=route),
        None,
    )

    assert duplicate["statusCode"] == 409
    assert repository.get_link_for_resident(987)["telegram_user_id"] == "12345"
    assert repository.get_link_for_telegram(67890) is None
    assert repository.get_link_request(second_token) is not None


def test_complete_link_rejects_duplicate_telegram_without_overwriting():
    first_token = service.create_link_request(12345)
    route = "POST /internal/telegram-link/complete"

    assert lambda_handler(
        make_event({"token": first_token, "resident_id": 987}, route=route),
        None,
    )["statusCode"] == 200

    second_token = service.create_link_request(12345)
    duplicate = lambda_handler(
        make_event({"token": second_token, "resident_id": 654}, route=route),
        None,
    )

    assert duplicate["statusCode"] == 409
    assert repository.get_link_for_telegram(12345)["resident_id"] == "987"
    assert repository.get_link_for_resident(654) is None


def test_complete_link_validates_resident_id():
    route = "POST /internal/telegram-link/complete"

    for resident_id in (None, "123", 0, -1, True):
        result = lambda_handler(
            make_event({"token": "token", "resident_id": resident_id}, route=route),
            None,
        )
        assert result["statusCode"] == 400


def test_internal_failure_returns_generic_error(caplog):
    with patch(
        "auth_app.repository.get_link_request",
        side_effect=RuntimeError("database detail"),
    ), caplog.at_level("ERROR"):
        result = lambda_handler(make_event({"token": "token"}), None)

    assert result["statusCode"] == 500
    assert json.loads(result["body"]) == {"error": "Internal server error"}
    assert "database detail" not in result["body"]
    assert "database detail" in caplog.text


def test_invalid_json_shape_is_rejected():
    event = {
        "routeKey": "POST /internal/telegram-link/preview",
        "body": "[]",
    }

    assert lambda_handler(event, None)["statusCode"] == 400
