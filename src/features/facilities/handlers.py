import logging
import time
import uuid
from datetime import datetime

from core.router import router
from features.facilities import repository as repo
from features.facilities import service

logger = logging.getLogger(__name__)


def _require_block(ctx):
    block = repo.get_user_block(ctx.user_id)
    if not block:
        ctx.telegram.send_message(ctx.chat_id, "Please create a /profile first.", parse_mode=None)
    return block


def _valid_facilities(block):
    return [view.name for view in service.block_summary(block, int(time.time()))]


def _show_facilities(ctx, block):
    now_dt = datetime.now(service.SINGAPORE)
    now = int(now_dt.timestamp())
    views = service.block_summary(block, now)
    if not views:
        ctx.telegram.send_message(ctx.chat_id, f"Block {block} has no facilities available for booking.")
        return

    lines = [f"*Block {block} Facilities*"]
    for view in views:
        if view.status == "UNAVAILABLE":
            status = "Unavailable"
        elif view.status == "IN_USE":
            end_time = service.format_time(view.booking["endTime"])
            status = f"In use until {end_time} (~{service.format_duration(view.mins_left * 60)} left)"
        elif view.status == "RESERVED":
            start_time = service.format_time(view.booking["startTime"])
            end_time = service.format_time(view.booking["endTime"])
            status = f"Reserved from {start_time} until {end_time}"
        else:
            status = "Free"
        lines.append(f"• *{view.name}*: {status}")
    lines.extend(("", "Use `/book <facility_name>` to book a facility."))
    ctx.telegram.send_message(ctx.chat_id, "\n".join(lines))


def _cancel_keyboard(request_id):
    return [[{"text": "Cancel ✖", "callback_data": f"facility_cancel_{request_id}"}]]


def _confirm_keyboard(request_id):
    return [
        [{"text": "Yes!", "callback_data": f"facility_confirm_{request_id}"}],
        [{"text": "Cancel ✖", "callback_data": f"facility_cancel_{request_id}"}],
    ]


def _begin_booking(ctx, block, facility, *, override, edit=False):
    request_id = uuid.uuid4().hex[:12]
    repo.set_pending(
        ctx.user_id,
        ctx.chat_id,
        block,
        facility["facilityID"],
        facility.get("facilityName", facility["facilityID"].title()),
        override,
        request_id,
        int(time.time()),
    )
    pending = {
        "requestID": request_id,
        "facilityName": facility.get("facilityName", facility["facilityID"].title()),
    }
    _prompt_start(ctx, pending, edit=edit)


def _send_prompt(ctx, text, keyboard, *, edit=False):
    if edit and ctx.message_id is not None:
        ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, text, keyboard)
    else:
        ctx.telegram.send_message(ctx.chat_id, text, keyboard)


def _prompt_start(ctx, pending, *, error=None, edit=False):
    lines = []
    if error:
        lines.extend((error, ""))
    lines.append(
        f"Send the start date and time for {pending['facilityName']} as `DDMMYYYY HHMM` "
        "in Singapore time."
    )
    _send_prompt(ctx, "\n".join(lines), _cancel_keyboard(pending["requestID"]), edit=edit)


def _prompt_end(ctx, pending, *, error=None):
    lines = []
    if error:
        lines.extend((error, ""))
    start_time = service.format_time(pending["startTime"])
    lines.append(
        f"Send the end date and time after `{start_time}` as `DDMMYYYY HHMM` "
        "in Singapore time."
    )
    _send_prompt(ctx, "\n".join(lines), _cancel_keyboard(pending["requestID"]))


def _prompt_confirmation(ctx, pending):
    start_time = service.format_time(pending["startTime"])
    end_time = service.format_time(pending["endTime"])
    text = (
        f"Will you keep the {pending['facilityName']} neat and tidy?\n\n"
        f"Booking time: `{start_time}` to `{end_time}`"
    )
    _send_prompt(ctx, text, _confirm_keyboard(pending["requestID"]))


def _show_facility(ctx, block, facility):
    now_dt = datetime.now(service.SINGAPORE)
    now = int(now_dt.timestamp())
    view = service.facility_view(block, facility, now)
    if not view:
        ctx.telegram.send_message(ctx.chat_id, "That facility is no longer available in your block.")
        return
    if view.status == "UNAVAILABLE":
        ctx.telegram.send_message(ctx.chat_id, f"*{view.name}* is currently unavailable.")
        return
    if view.status in ("IN_USE", "RESERVED"):
        booking = view.booking
        end_time = service.format_time(booking["endTime"])
        if view.status == "IN_USE":
            detail = f"is in use until *{end_time}* (~{service.format_duration(booking['endTime'] - now)} left)."
            button = "⚠️ Override booking"
        else:
            start_time = service.format_time(booking["startTime"])
            detail = f"is reserved from *{start_time}* until *{end_time}*."
            button = "⚠️ Override reservation"
        keyboard = [[{
            "text": button,
            "callback_data": f"facility_override_{view.facility_id}",
        }]]
        ctx.telegram.send_message(
            ctx.chat_id,
            f"*{view.name}* {detail}\nYou can override it to continue booking.",
            keyboard,
        )
        return
    _begin_booking(ctx, block, facility, override=False)


@router.command("/book")
def book_command(ctx):
    block = _require_block(ctx)
    if not block:
        return

    tokens = ctx.text.strip().split(maxsplit=1)
    requested_name = tokens[1].strip() if len(tokens) > 1 else ""
    if not requested_name:
        _show_facilities(ctx, block)
        return

    facility = service.find_facility(block, requested_name)
    if not facility:
        valid = _valid_facilities(block)
        if valid:
            ctx.telegram.send_message(
                ctx.chat_id,
                f"I couldn't find that facility in Block {block}. Valid facilities: {', '.join(valid)}.",
            )
        else:
            ctx.telegram.send_message(ctx.chat_id, f"Block {block} has no facilities available for booking.")
        return
    _show_facility(ctx, block, facility)


@router.callback("facility_override_")
def override_booking(ctx):
    block = _require_block(ctx)
    if not block:
        if ctx.callback_id:
            ctx.telegram.answer_callback_query(ctx.callback_id)
        return
    facility_id = ctx.data.removeprefix("facility_override_")
    facility = repo.get_facility(block, facility_id)
    if not facility:
        ctx.telegram.answer_callback_query(ctx.callback_id, "That facility is not in your block.")
        return
    view = service.facility_view(block, facility, int(time.time()))
    if not view or view.status not in ("IN_USE", "RESERVED"):
        ctx.telegram.answer_callback_query(ctx.callback_id, "The facility is free now. You can book it normally.")
        _begin_booking(ctx, block, facility, override=False, edit=True)
        return
    ctx.telegram.answer_callback_query(ctx.callback_id)
    _begin_booking(ctx, block, facility, override=True, edit=True)


@router.message("text")
def booking_time_message(ctx):
    pending = repo.get_pending(ctx.user_id)
    if not pending or pending.get("chatID") != str(ctx.chat_id):
        return False

    block = repo.get_user_block(ctx.user_id)
    if not block or block != pending.get("block"):
        repo.clear_pending(ctx.user_id)
        ctx.telegram.send_message(ctx.chat_id, "Your block profile changed. Start again with /book.")
        return True

    step = pending.get("step")
    if step == "CONFIRM":
        ctx.telegram.send_message(ctx.chat_id, "Please use the Yes! or Cancel button to finish this booking.")
        return True

    facility = repo.get_facility(block, pending["facilityID"])
    if not facility:
        repo.clear_pending(ctx.user_id)
        ctx.telegram.send_message(ctx.chat_id, "That facility is no longer available in your block.")
        return True

    if step == "START":
        try:
            start_time = service.parse_booking_datetime(ctx.text)
        except ValueError as exc:
            _prompt_start(ctx, pending, error=str(exc))
            return True
        repo.set_pending_end(ctx.user_id, start_time)
        pending["startTime"] = start_time
        pending["step"] = "END"
        _prompt_end(ctx, pending)
        return True

    if step == "END":
        try:
            end_time = service.parse_booking_datetime(ctx.text, after=pending["startTime"])
        except ValueError as exc:
            _prompt_end(ctx, pending, error=str(exc))
            return True
        repo.set_pending_confirmation(ctx.user_id, end_time)
        pending["endTime"] = end_time
        pending["step"] = "CONFIRM"
        _prompt_confirmation(ctx, pending)
        return True

    repo.clear_pending(ctx.user_id)
    ctx.telegram.send_message(ctx.chat_id, "That booking request expired. Start again with /book.")
    return True


@router.callback("facility_cancel_")
def cancel_booking(ctx):
    request_id = ctx.data.removeprefix("facility_cancel_")
    pending = repo.get_pending(ctx.user_id)
    if not pending or pending.get("requestID") != request_id or pending.get("chatID") != str(ctx.chat_id):
        ctx.telegram.answer_callback_query(ctx.callback_id, "This booking request is no longer active.")
        if ctx.message_id is not None:
            ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, "This booking request is no longer active.", [])
        return

    repo.clear_pending(ctx.user_id)
    ctx.telegram.answer_callback_query(ctx.callback_id, "Booking cancelled.")
    ctx.telegram.edit_message(
        ctx.chat_id,
        ctx.message_id,
        "Booking cancelled. Use /book to start again.",
        [],
    )


@router.callback("facility_confirm_")
def confirm_booking(ctx):
    request_id = ctx.data.removeprefix("facility_confirm_")
    pending = repo.get_pending(ctx.user_id)
    if (
        not pending
        or pending.get("requestID") != request_id
        or pending.get("chatID") != str(ctx.chat_id)
        or pending.get("step") != "CONFIRM"
    ):
        ctx.telegram.answer_callback_query(ctx.callback_id, "This booking request is no longer active.")
        if ctx.message_id is not None:
            ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, "This booking request is no longer active.", [])
        return

    block = repo.get_user_block(ctx.user_id)
    if not block or block != pending.get("block"):
        repo.clear_pending(ctx.user_id)
        ctx.telegram.answer_callback_query(ctx.callback_id, "Your block profile changed.")
        ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, "Your block profile changed. Start again with /book.", [])
        return

    facility = repo.get_facility(block, pending["facilityID"])
    if not facility:
        repo.clear_pending(ctx.user_id)
        ctx.telegram.answer_callback_query(ctx.callback_id, "That facility is no longer available.")
        ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, "That facility is no longer available in your block.", [])
        return

    ctx.telegram.answer_callback_query(ctx.callback_id, "Saving booking...")
    try:
        result = service.book(
            ctx.user_id,
            ctx.chat_id,
            block,
            facility,
            int(pending["startTime"]),
            int(pending["endTime"]),
            override=bool(pending.get("override")),
            scheduler=ctx.scheduler,
        )
    except Exception:
        logger.exception("Failed to book facility %s in block %s", facility["facilityID"], block)
        ctx.telegram.edit_message(
            ctx.chat_id,
            ctx.message_id,
            "I couldn't save that booking. Please try again with Yes! or cancel.",
            _confirm_keyboard(request_id),
        )
        return

    repo.clear_pending(ctx.user_id)
    if result.outcome is service.BookingOutcome.UNAVAILABLE:
        ctx.telegram.edit_message(
            ctx.chat_id,
            ctx.message_id,
            f"*{facility['facilityName']}* is currently unavailable.",
            [],
        )
    elif result.outcome is service.BookingOutcome.NOT_FOUND:
        ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, "That facility is no longer available in your block.", [])
    elif result.outcome is service.BookingOutcome.CONFLICT:
        ctx.telegram.edit_message(
            ctx.chat_id,
            ctx.message_id,
            "The facility's status changed while you were booking. Here is its latest status:",
            [],
        )
        _show_facility(ctx, block, facility)
    else:
        start_text = service.format_time(pending["startTime"])
        end_text = service.format_time(pending["endTime"])
        message = (
            f"✅ *{facility['facilityName']}* in Block {block} is booked from "
            f"*{start_text}* until *{end_text}*."
        )
        if pending.get("override"):
            message = "⚠️ Booking overridden. " + message
        if not result.reminder_scheduled:
            message += "\nI couldn't schedule an end reminder, but the booking time was saved."
        ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, message, [])

@router.job(service.FACILITY_BOOKING_DONE_JOB)
def facility_booking_done(payload, telegram):
    service.booking_done(payload, telegram)
