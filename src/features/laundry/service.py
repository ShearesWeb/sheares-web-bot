import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from math import ceil

from features.laundry import repository as repo

# Machines exist physically whether or not anyone has used them yet, so they
# have no DynamoDB row until their first claim.
MACHINE_IDS = [f"{typ}{i}" for typ in ("W", "D") for i in range(1, 5)]

LAUNDRY_DONE_JOB = "laundry_done"


class MoveOutcome(Enum):
    MOVED = "moved"
    TARGET_OCCUPIED = "target_occupied"
    SOURCE_EMPTY = "source_empty"


@dataclass(frozen=True)
class MachineView:
    machine_id: str
    status: str
    mins_left: int | None
    mine: bool


def real_status(machine, now):
    """Rows stay IN_USE after the timer ends; the cycle counts as COMPLETED once endTime has passed."""
    if not machine:
        return "AVAILABLE"
    status = machine.get("status", "AVAILABLE")
    end_time = int(machine.get("endTime", 0))
    if status == "IN_USE" and end_time > 0 and now >= end_time:
        return "COMPLETED"
    return status


def machine_status(block, machine_id, now):
    return real_status(repo.get_machine(block, machine_id), now)


def owner_chat(block, machine_id):
    return repo.get_notification_chat(repo.get_machine(block, machine_id))


def claim(user_id, chat_id, block, machine_id, duration_mins, scheduler):
    old_machine = repo.get_machine(block, machine_id)
    old_user = old_machine.get("currentUser", "NONE") if old_machine else "NONE"
    if old_user != "NONE" and old_user != str(user_id):
        repo.remove_user_machine(old_user, machine_id)
    repo.add_user_machine(user_id, machine_id)

    started = int(time.time())
    repo.put_machine(block, machine_id, user_id, chat_id, started + duration_mins * 60)
    scheduler.schedule(
        name=f"Laundry_{block}_{machine_id}_{started}",
        run_at=datetime.fromtimestamp(started, UTC) + timedelta(minutes=duration_mins),
        job=LAUNDRY_DONE_JOB,
        payload={"chat_id": chat_id, "block": block, "machine_id": machine_id},
    )


def move(block, from_id, to_id, now):
    """Moves the previous user's laundry to a free machine. Returns the outcome and the owner's chat to notify."""
    if real_status(repo.get_machine(block, to_id), now) != "AVAILABLE":
        return MoveOutcome.TARGET_OCCUPIED, None
    orig = repo.get_machine(block, from_id)
    if not orig:
        return MoveOutcome.SOURCE_EMPTY, None
    return MoveOutcome.MOVED, _transfer(block, from_id, to_id, orig)


def move_override(block, from_id, to_id):
    """Moves laundry into an occupied machine, dropping that machine's current owner."""
    target = repo.get_machine(block, to_id)
    target_user = target.get("currentUser", "NONE") if target else "NONE"
    orig = repo.get_machine(block, from_id)
    if not orig:
        return MoveOutcome.SOURCE_EMPTY
    if target_user != "NONE":
        repo.remove_user_machine(target_user, to_id)
    _transfer(block, from_id, to_id, orig)
    return MoveOutcome.MOVED


def collect(user_id, block, machine_id):
    machine = repo.get_machine(block, machine_id)
    # Only clear if the user still owns it, because it could have been overridden or moved since.
    if not machine or str(machine.get("currentUser")) != str(user_id):
        return False
    repo.remove_user_machine(user_id, machine_id)
    repo.set_machine_state(block, machine_id, "AVAILABLE", "NONE", "NONE")
    return True


def block_summary(user_id, block, now):
    by_id = {m["machineID"]: m for m in repo.query_block(block)}
    my_machines = repo.get_user_machines(user_id)
    views = []
    for machine_id in MACHINE_IDS:
        machine = by_id.get(machine_id)
        status = real_status(machine, now)
        end_time = int(machine.get("endTime", 0)) if machine else 0
        mins_left = ceil((end_time - now) / 60) if status == "IN_USE" and end_time > now else None
        views.append(MachineView(machine_id, status, mins_left, machine_id in my_machines and status != "AVAILABLE"))
    return views, sorted(my_machines)


def _transfer(block, from_id, to_id, orig):
    orig_user = orig.get("currentUser", "NONE")
    orig_chat = repo.get_notification_chat(orig) or "NONE"
    if orig_user != "NONE":
        repo.add_user_machine(orig_user, to_id)
        repo.remove_user_machine(orig_user, from_id)
    repo.set_machine_state(block, to_id, "COMPLETED", orig_user, orig_chat)
    repo.set_machine_state(block, from_id, "AVAILABLE", "NONE", "NONE")
    return None if orig_chat == "NONE" else orig_chat
