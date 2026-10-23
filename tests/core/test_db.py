from unittest.mock import Mock

import pytest

from core import db


def test_batch_reads_chunk_keys_and_use_consistent_reads(monkeypatch):
    keys = [{"userID": "USER#2", "profile": f"BILL#{number}"} for number in range(101)]
    read = Mock(side_effect=[{"Responses": {"users": keys[:100]}}, {"Responses": {"users": keys[100:]}}])
    monkeypatch.setattr(db._dynamodb(), "batch_get_item", read)

    assert db.batch_users_items(keys) == keys
    assert [len(call.kwargs["RequestItems"]["users"]["Keys"]) for call in read.call_args_list] == [100, 1]
    assert all(call.kwargs["RequestItems"]["users"]["ConsistentRead"] for call in read.call_args_list)


def test_partial_batches_retry_only_unprocessed_keys_with_backoff(monkeypatch):
    keys = [{"userID": "USER#2", "profile": f"BILL#{number}"} for number in range(2)]
    pending = {"users": {"Keys": keys[1:], "ConsistentRead": True}}
    read = Mock(side_effect=[
        {"Responses": {"users": keys[:1]}, "UnprocessedKeys": pending}, {"Responses": {"users": keys[1:]}},
    ])
    sleep = Mock()
    monkeypatch.setattr(db._dynamodb(), "batch_get_item", read)
    monkeypatch.setattr(db.time, "sleep", sleep)

    assert db.batch_users_items(keys) == keys
    assert read.call_args_list[1].kwargs["RequestItems"] == pending
    sleep.assert_called_once_with(0.05)


def test_exhausted_retries_never_return_an_incomplete_balance(monkeypatch):
    keys = [{"userID": "USER#2", "profile": "BILL#1"}]
    read = Mock(return_value={"UnprocessedKeys": {"users": {"Keys": keys, "ConsistentRead": True}}})
    monkeypatch.setattr(db._dynamodb(), "batch_get_item", read)
    monkeypatch.setattr(db.time, "sleep", Mock())

    with pytest.raises(RuntimeError, match="couldn't load all bills"):
        db.batch_users_items(keys)
    assert read.call_count == 5
