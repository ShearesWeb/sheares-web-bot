import time
from datetime import UTC, datetime

from features.laundry import repository as repo
from features.laundry import service
from features.laundry.service import MachineView, MoveOutcome
from support import RecordingScheduler, seed_machine


def test_real_status():
    assert service.real_status(None, 100) == "AVAILABLE"
    assert service.real_status({"status": "IN_USE", "endTime": 200}, 100) == "IN_USE"
    assert service.real_status({"status": "IN_USE", "endTime": 100}, 100) == "COMPLETED"
    assert service.real_status({"status": "IN_USE", "endTime": 0}, 100) == "IN_USE"
    assert service.real_status({"status": "COMPLETED", "endTime": 0}, 100) == "COMPLETED"


def test_claim_writes_cycle_tracks_user_and_schedules_reminder():
    scheduler = RecordingScheduler()
    before = int(time.time())

    service.claim(1, 10, "A", "W1", 30, scheduler)

    machine = repo.get_machine("A", "W1")
    assert {k: machine[k] for k in ("status", "currentUser", "currentChatID", "type")} == {
        "status": "IN_USE", "currentUser": "1", "currentChatID": "10", "type": "WASHER",
    }
    assert before + 1800 <= int(machine["endTime"]) <= int(time.time()) + 1800
    assert repo.get_user_machines(1) == {"W1"}
    (job,) = scheduler.scheduled
    assert job["name"].startswith("Laundry_A_W1_")
    assert job["job"] == "laundry_done"
    assert job["payload"] == {"chat_id": 10, "block": "A", "machine_id": "W1"}
    assert job["run_at"].tzinfo is not None
    assert abs((job["run_at"] - datetime.now(UTC)).total_seconds() - 1800) < 5


def test_claim_over_another_user_clears_their_tracker():
    seed_machine("A", "D2", status="IN_USE", currentUser="2", currentChatID="20", endTime=0)
    repo.add_user_machine("2", "D2")

    service.claim(1, 10, "A", "D2", 45, RecordingScheduler())

    assert repo.get_user_machines("2") == set()
    assert repo.get_machine("A", "D2")["type"] == "DRYER"


def test_move_transfers_contents_and_trackers():
    seed_machine("A", "W1", status="IN_USE", currentUser="2", currentChatID="20", endTime=1)
    repo.add_user_machine("2", "W1")

    outcome, chat = service.move("A", "W1", "W3", now=int(time.time()))

    assert (outcome, chat) == (MoveOutcome.MOVED, "20")
    w3, w1 = repo.get_machine("A", "W3"), repo.get_machine("A", "W1")
    assert (w3["status"], w3["currentUser"], w3["currentChatID"], w3["endTime"]) == ("COMPLETED", "2", "20", 0)
    assert (w1["status"], w1["currentUser"], w1["currentChatID"], w1["endTime"]) == ("AVAILABLE", "NONE", "NONE", 0)
    assert repo.get_user_machines("2") == {"W3"}


def test_move_into_occupied_machine_changes_nothing():
    seed_machine("A", "W1", status="COMPLETED", currentUser="2", currentChatID="20", endTime=0)
    seed_machine("A", "W3", status="IN_USE", currentUser="3", currentChatID="30", endTime=int(time.time()) + 600)

    assert service.move("A", "W1", "W3", now=int(time.time())) == (MoveOutcome.TARGET_OCCUPIED, None)
    assert repo.get_machine("A", "W1")["status"] == "COMPLETED"


def test_move_from_unrecorded_machine():
    assert service.move("A", "W1", "W3", now=int(time.time())) == (MoveOutcome.SOURCE_EMPTY, None)


def test_move_override_clears_target_owner():
    seed_machine("A", "W1", status="COMPLETED", currentUser="2", currentChatID="20", endTime=0)
    seed_machine("A", "W3", status="IN_USE", currentUser="3", currentChatID="30", endTime=int(time.time()) + 600)
    repo.add_user_machine("2", "W1")
    repo.add_user_machine("3", "W3")

    assert service.move_override("A", "W1", "W3") == MoveOutcome.MOVED

    assert repo.get_user_machines("3") == set()
    assert repo.get_user_machines("2") == {"W3"}
    assert repo.get_machine("A", "W3")["currentUser"] == "2"
    assert repo.get_machine("A", "W1")["status"] == "AVAILABLE"


def test_collect_only_clears_for_current_owner():
    seed_machine("A", "W1", status="COMPLETED", currentUser="1", currentChatID="10", endTime=0)
    repo.add_user_machine(1, "W1")

    assert service.collect(2, "A", "W1") is False
    assert repo.get_machine("A", "W1")["status"] == "COMPLETED"

    assert service.collect(1, "A", "W1") is True
    assert repo.get_machine("A", "W1")["status"] == "AVAILABLE"
    assert repo.get_user_machines(1) == set()


def test_owner_chat_falls_back_to_legacy_current_user():
    seed_machine("A", "W2", status="COMPLETED", currentUser="555", endTime=0)
    seed_machine("A", "W3", status="AVAILABLE", currentUser="NONE", currentChatID="NONE", endTime=0)

    assert service.owner_chat("A", "W2") == "555"
    assert service.owner_chat("A", "W3") is None
    assert service.owner_chat("A", "W4") is None


def test_block_summary_lists_all_machines_with_ownership():
    now = int(time.time())
    seed_machine("A", "W1", status="IN_USE", currentUser="1", currentChatID="10", endTime=now + 600)
    seed_machine("A", "D2", status="IN_USE", currentUser="2", currentChatID="20", endTime=now - 1)
    repo.add_user_machine(1, "W1")
    repo.add_user_machine(1, "D4")

    views, mine = service.block_summary(1, "A", now)

    assert [v.machine_id for v in views] == ["W1", "W2", "W3", "W4", "D1", "D2", "D3", "D4"]
    assert views[0] == MachineView("W1", "IN_USE", 10, True)
    assert views[1] == MachineView("W2", "AVAILABLE", None, False)
    assert views[5] == MachineView("D2", "COMPLETED", None, False)
    assert views[7] == MachineView("D4", "AVAILABLE", None, False)
    assert mine == ["D4", "W1"]
