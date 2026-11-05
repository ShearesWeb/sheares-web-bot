import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from enum import Enum
from math import ceil

from features.facilities import repository as repo

logger = logging.getLogger(__name__)

SINGAPORE = timezone(timedelta(hours=8), "Asia/Singapore")
FACILITY_BOOKING_DONE_JOB = "facility_booking_done"
DATETIME_PATTERN = re.compile(r"^(\d{8})\s+(\d{4})$")


class BookingOutcome(Enum):
    BOOKED = "booked"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class FacilityView:
    facility_id: str
    name: str
    status: str
    booking: dict | None
    mins_left: int | None


@dataclass(frozen=True)
class BookingResult:
    outcome: BookingOutcome
    booking: dict | None = None
    reminder_scheduled: bool = True


def _normalized_name(value):
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def find_facility(block, requested_name):
    target = _normalized_name(requested_name)
    for item in repo.list_block_items(block):
        facility_id = item.get("facilityID", "")
        if facility_id.startswith(repo.BOOKING_KEY_PREFIX):
            continue
        if target in {_normalized_name(facility_id), _normalized_name(item.get("facilityName", ""))}:
            return item
    return None


def block_summary(block, now):
    items = repo.list_block_items(block)
    catalog = {}
    bookings = {}
    for item in items:
        facility_id = item.get("facilityID", "")
        if facility_id.startswith(repo.BOOKING_KEY_PREFIX):
            bookings[facility_id[len(repo.BOOKING_KEY_PREFIX):]] = item
        else:
            catalog[facility_id] = item

    views = []
    for facility_id, item in catalog.items():
        booking = bookings.get(facility_id)
        if item.get("status", "FREE").upper() == "UNAVAILABLE":
            status, active_booking, mins_left = "UNAVAILABLE", None, None
        elif booking and booking.get("status") == "IN_USE" and int(booking.get("endTime", 0)) > now:
            active_booking = booking
            start_time = int(booking.get("startTime", 0))
            if start_time > now:
                status = "RESERVED"
                mins_left = None
            else:
                status = "IN_USE"
                mins_left = ceil((int(booking["endTime"]) - now) / 60)
        else:
            status, active_booking, mins_left = "FREE", None, None
        views.append(FacilityView(
            facility_id=facility_id,
            name=item.get("facilityName", facility_id.title()),
            status=status,
            booking=active_booking,
            mins_left=mins_left,
        ))
    return sorted(views, key=lambda view: view.name.casefold())


def facility_view(block, facility, now):
    return next(
        (view for view in block_summary(block, now) if view.facility_id == facility["facilityID"]),
        None,
    )


def parse_booking_datetime(text, *, now=None, after=None):
    """Parse a full DDMMYYYY HHMM timestamp in Singapore time."""
    match = DATETIME_PATTERN.fullmatch(text.strip())
    if not match:
        raise ValueError("Use the format DDMMYYYY HHMM, for example 07112026 1900.")
    date_text, time_text = match.groups()
    try:
        local_time = datetime.strptime(f"{date_text} {time_text}", "%d%m%Y %H%M").replace(tzinfo=SINGAPORE)
    except ValueError as exc:
        raise ValueError("That date or time is not valid. Use DDMMYYYY HHMM.") from exc

    if after is not None:
        start = after.astimezone(SINGAPORE) if isinstance(after, datetime) else datetime.fromtimestamp(int(after), SINGAPORE)
        if local_time <= start:
            raise ValueError("The end date and time must be after the start date and time.")
    else:
        current_time = (now or datetime.now(SINGAPORE)).astimezone(SINGAPORE)
        if local_time <= current_time:
            raise ValueError("The start date and time must be in the future.")
    return int(local_time.timestamp())


def format_time(timestamp):
    return datetime.fromtimestamp(int(timestamp), SINGAPORE).strftime("%d%m%Y %H%M")


def format_duration(seconds):
    minutes = max(1, ceil(seconds / 60))
    hours, remaining = divmod(minutes, 60)
    if hours and remaining:
        return f"{hours}h {remaining}m"
    if hours:
        return f"{hours}h"
    return f"{remaining}m"


def book(user_id, chat_id, block, facility, start_time, end_time, *, override, scheduler):
    current_facility = repo.get_facility(block, facility["facilityID"])
    if not current_facility:
        return BookingResult(BookingOutcome.NOT_FOUND)
    if current_facility.get("status", "FREE").upper() == "UNAVAILABLE":
        return BookingResult(BookingOutcome.UNAVAILABLE)

    booking_id = uuid.uuid4().hex
    booking = {
        "block": block,
        "facilityID": f"{repo.BOOKING_KEY_PREFIX}{facility['facilityID']}",
        "facilityName": facility.get("facilityName", facility["facilityID"].title()),
        "status": "IN_USE",
        "currentUser": str(user_id),
        "currentChatID": str(chat_id),
        "startTime": start_time,
        "endTime": end_time,
        "bookingID": booking_id,
    }
    booked, previous = repo.put_booking(
        booking,
        override=override,
        now=int(datetime.now(UTC).timestamp()),
    )
    if not booked:
        return BookingResult(BookingOutcome.CONFLICT)

    old_user = previous.get("currentUser") if previous else None
    old_booking_id = previous.get("bookingID") if previous else None
    if old_user not in (None, "NONE") and old_booking_id:
        repo.remove_user_booking(old_user, block, facility["facilityID"], old_booking_id)

    repo.put_user_booking(user_id, booking)
    payload = {
        "booking_id": booking_id,
        "user_id": str(user_id),
        "chat_id": str(chat_id),
        "block": block,
        "facility_id": facility["facilityID"],
        "facility_name": booking["facilityName"],
        "end_time": end_time,
    }
    try:
        scheduler.schedule(
            name=f"Facility_{block}_{facility['facilityID']}_{booking_id}",
            run_at=datetime.fromtimestamp(end_time, UTC),
            job=FACILITY_BOOKING_DONE_JOB,
            payload=payload,
        )
        reminder_scheduled = True
    except Exception:
        # The recorded interval still governs availability if Scheduler is
        # temporarily unavailable; surface the missing reminder in the reply.
        logger.exception("Could not schedule end reminder for facility %s in block %s", facility["facilityID"], block)
        reminder_scheduled = False
    return BookingResult(BookingOutcome.BOOKED, booking, reminder_scheduled)


def booking_done(payload, telegram):
    now = int(datetime.now(UTC).timestamp())
    if now < int(payload["end_time"]):
        return
    booking = repo.finish_booking(payload["block"], payload["facility_id"], payload["booking_id"])
    if not booking:
        return
    repo.remove_user_booking(
        payload["user_id"], payload["block"], payload["facility_id"], payload["booking_id"],
    )
    telegram.send_message(
        payload["chat_id"],
        f"Your booking of {payload['facility_name']} in Block {payload['block']} has ended.",
    )
