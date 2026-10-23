from datetime import UTC, datetime

import pytest
from botocore.exceptions import ClientError

import features  # noqa: F401
from core.db import users_table
from core.router import router
from features.split import quota, repository, service
from support import document_update, message_update, photo_update


def test_daily_cap_is_per_user_and_survives_new_split_sessions(
    owner_profile, services, textract, receipt_image, expense_response, monkeypatch,
):
    monkeypatch.setattr(quota, "DAILY_LIMIT", 2)
    services.telegram.files["receipt"] = receipt_image
    for _ in range(2):
        textract.add_response("analyze_expense", expense_response, {"Document": {"Bytes": receipt_image}})
    router.dispatch_update(message_update("/split"), services)
    for update_id in (100, 101):
        router.dispatch_update(photo_update(update_id=update_id), services)
    assert len(repository.get_session(1, 10)["expenses"]) == 2

    router.dispatch_update(photo_update(update_id=102), services)
    assert "limit of 2 receipt scans" in services.telegram.sent[-1]["text"]
    assert services.telegram.sent[-1]["parse_mode"] is None
    router.dispatch_update(message_update("/split", update_id=200), services)
    router.dispatch_update(photo_update(update_id=201), services)
    assert "limit of 2 receipt scans" in services.telegram.sent[-1]["text"]
    assert len(services.telegram.downloaded) == 2
    quota.reserve_scan(2)
    quota.reserve_scan(2)
    with pytest.raises(ValueError, match="limit of 2"):
        quota.reserve_scan(2)
    router.dispatch_update(message_update("Lunch - 10", update_id=202), services)
    assert repository.get_session(1, 10)["expenses"][0]["source"] == "text"


def test_allowance_resets_at_singapore_midnight(monkeypatch):
    monkeypatch.setattr(quota, "DAILY_LIMIT", 1)
    before_midnight = datetime(2026, 10, 24, 15, 59, 59, tzinfo=UTC).timestamp()
    monkeypatch.setattr(quota.time, "time", lambda: before_midnight)
    quota.reserve_scan(1)
    with pytest.raises(ValueError, match="limit of 1"):
        quota.reserve_scan(1)
    monkeypatch.setattr(quota.time, "time", lambda: before_midnight + 1)
    quota.reserve_scan(1)
    item = users_table().get_item(Key={"userID": "USER#1", "profile": "SPLIT_OCR_QUOTA"})["Item"]
    assert item["day"] == "2026-10-25" and item["used"] == 1


def test_concurrent_day_initialization_retries_the_conditional_increment(monkeypatch):
    calls = []

    def update(user_id, **kwargs):
        calls.append(kwargs)
        return len(calls) == 3

    monkeypatch.setattr(quota, "_update", update)
    quota.reserve_scan(1)
    assert calls[0] == calls[2]
    assert "#used < :limit" in calls[0]["ConditionExpression"]
    assert "#day < :day" in calls[1]["ConditionExpression"]


def test_failed_textract_requests_still_consume_the_allowance(owner_profile, services, textract, receipt_image, monkeypatch):
    monkeypatch.setattr(quota, "DAILY_LIMIT", 1)
    textract.add_client_error("analyze_expense", "AccessDeniedException", expected_params={"Document": {"Bytes": receipt_image}})
    services.telegram.files["receipt"] = receipt_image
    router.dispatch_update(message_update("/split"), services)
    router.dispatch_update(photo_update(update_id=100), services)
    assert "upload it again" in services.telegram.sent[-1]["text"]
    router.dispatch_update(photo_update(update_id=101), services)
    assert "limit of 1" in services.telegram.sent[-1]["text"]
    assert len(services.telegram.downloaded) == 1


@pytest.mark.parametrize("update", [document_update(mime_type="application/pdf"), photo_update(file_size=service.MAX_RECEIPT_BYTES + 1)])
def test_rejected_file_types_and_sizes_do_not_consume_the_allowance(owner_profile, services, textract, update):
    router.dispatch_update(message_update("/split"), services)
    router.dispatch_update(update, services)
    assert services.telegram.downloaded == []
    assert "Item" not in users_table().get_item(Key={"userID": "USER#1", "profile": "SPLIT_OCR_QUOTA"})


def test_quota_storage_errors_prevent_downloads_and_textract_calls(owner_profile, services, textract, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ClientError({"Error": {"Code": "InternalServerError", "Message": "Unavailable"}}, "UpdateItem")

    monkeypatch.setattr(quota, "_update", unavailable)
    router.dispatch_update(message_update("/split"), services)
    router.dispatch_update(photo_update(), services)
    assert "upload it again" in services.telegram.sent[-1]["text"]
    assert services.telegram.downloaded == []
