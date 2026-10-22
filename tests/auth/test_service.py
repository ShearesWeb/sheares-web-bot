
import time

from features.auth import repository, service
from core.db import users_table


def test_token_is_single_use():
    token = service.create_link_request(12345)

    request = service.consume_link_request(token)
    assert request["telegram_user_id"] == "12345"

    assert service.consume_link_request(token) is None


def test_tokens_are_unique():
    first = service.create_link_request(12345)
    second = service.create_link_request(12345)

    assert first != second


def test_expired_token_is_rejected():
    token = "expired-test-token"

    repository.save_link_request(
        token=token,
        telegram_user_id=12345,
        expires_at=int(time.time()) - 60,
    )

    assert service.consume_link_request(token) is None


def test_unknown_token_is_rejected():
    assert service.consume_link_request("invalid-token") is None


def test_database_does_not_store_raw_token():
    token = service.create_link_request(12345)

    response = users_table().get_item(
        Key=repository.token_key(token)
    )

    item = response["Item"]

    assert token not in str(item)
    assert item["telegram_user_id"] == "12345"


def test_existing_profile_is_not_overwritten():
    from features.profile import repository as profile_repo

    profile_repo.put_profile(12345, 12345, "A", 5, 2)

    token = service.create_link_request(12345)

    assert service.consume_link_request(token) is not None

    profile = profile_repo.get_profile(12345)
    assert profile["block"] == "A"
    assert profile["level"] == 5
