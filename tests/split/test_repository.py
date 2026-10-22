from features.split import repository as repo
from core.db import users_table


def test_claim_requires_a_request_from_the_same_user_and_chat():
    assert repo.claim_input(1, 10, "1") is None
    repo.start_session(1, 10)

    assert repo.claim_input(2, 10, "1") is None
    assert repo.claim_input(1, 20, "1") is None
    assert repo.claim_input(1, 10, "1") is not None


def test_only_one_upload_can_claim_a_pending_request():
    repo.start_session(1, 10)

    assert repo.claim_input(1, 10, "1") is not None
    assert repo.claim_input(1, 10, "2") is None


def test_failed_upload_can_retry_and_successful_upload_is_deduplicated():
    repo.start_session(1, 10)
    claim = repo.claim_input(1, 10, "1")

    repo.finish_input(1, 10, claim, "1")
    retry = repo.claim_input(1, 10, "1")
    assert retry["sessionID"] == claim["sessionID"]
    repo.finish_input(1, 10, retry, "1", expenses=[{"items": [], "totalCents": 2000}])
    assert repo.claim_input(1, 10, "1") is None
    assert repo.claim_input(1, 10, "2") is not None


def test_pending_request_expires(monkeypatch):
    monkeypatch.setattr(repo.time, "time", lambda: 1000)
    repo.start_session(1, 10)
    monkeypatch.setattr(repo.time, "time", lambda: 1000 + repo.SESSION_SECONDS)

    assert repo.get_session(1, 10) is None
    assert repo.claim_input(1, 10, "1") is None


def test_finishing_old_upload_does_not_clear_a_new_split_request():
    repo.start_session(1, 10)
    old_session = repo.claim_input(1, 10, "1")
    new_session = repo.start_session(1, 10)

    assert repo.finish_input(1, 10, old_session, "1", expenses=[]) is None

    claim = repo.claim_input(1, 10, "1")
    assert claim["sessionID"] == new_session["sessionID"]
    assert claim["sessionID"] != old_session["sessionID"]


def test_expired_processing_lease_can_recover_without_stale_writer(monkeypatch):
    monkeypatch.setattr(repo.time, "time", lambda: 1000)
    repo.start_session(1, 10)
    old = repo.claim_input(1, 10, "1")
    monkeypatch.setattr(repo.time, "time", lambda: 1000 + repo.PROCESSING_SECONDS)
    new = repo.claim_input(1, 10, "1")

    assert new["processingToken"] != old["processingToken"]
    assert repo.finish_input(1, 10, old, "1", expenses=[]) is None
    assert repo.finish_input(1, 10, new, "1", expenses=[]) is not None


def test_bill_requires_expenses_and_finalizes_once_with_persistent_user_ids():
    session = repo.start_session(1, 10)
    assert repo.begin_participants(1, 10, session["sessionID"]) is None
    claim = repo.claim_input(1, 10, "1")
    repo.finish_input(1, 10, claim, "1", expenses=[{"items": [], "totalCents": 5600}])
    session = repo.begin_participants(1, 10, session["sessionID"])
    assert session["status"] == "AWAITING_PARTICIPANTS"
    assert repo.claim_input(1, 10, "2") is None
    participants = [{"userID": "1234567890123", "username": "alice", "amountCents": 5600}]

    current = repo.set_handles(1, 10, session["sessionID"], ["alice"])
    assert repo.complete(1, 10, session["sessionID"], participants, 5600, session["selectionToken"]) is None
    session = current

    bill = repo.complete(1, 10, session["sessionID"], participants, 5600, session["selectionToken"])

    assert bill["status"] == "COMPLETED"
    assert repo.complete(1, 10, session["sessionID"], participants, 5600, session["selectionToken"]) is None
    repo.start_session(1, 10)
    stored = users_table().get_item(Key={"userID": "USER#1", "profile": f"SPLIT_BILL#{bill['sessionID']}"})["Item"]
    assert stored["participants"] == participants
    assert stored["splitMethod"] == "equal"
