"""Creator-controlled, recurring group reminders for unpaid bill shares."""

from datetime import UTC, datetime, timedelta, timezone
from html import escape
import logging
import re
import time
from uuid import uuid4

from botocore.exceptions import BotoCoreError, ClientError

from core import identities
from core.router import router
from core.telegram import TelegramError
from features.profile.access import requires_profile
from features.split import billing, ledger, navigation, reminder_repository as repo

logger = logging.getLogger(__name__)
JOB = "split_bill_reminder"
INTERVAL_DAYS = 2
INTERVAL_SECONDS = INTERVAL_DAYS * 86400
# Singapore uses UTC+8 year-round. A fixed offset also works on Windows without
# requiring a separately installed IANA timezone database.
SINGAPORE = timezone(timedelta(hours=8), "Asia/Singapore")
SCHEDULE_LABEL = "Every 2 days at 12:00 pm Singapore time"
BILL_OPTIONS_HINT = "Use the buttons under your completed bill, or open /reminders to manage active reminders."
PAGE_SIZE = 5


def _schedule_name(bill_id, generation):
    return f"Split_{bill_id[:16]}_{generation}"


def _standard_reminder(bill_id, generation, chat_id, title):
    first = (datetime.fromtimestamp(time.time(), SINGAPORE) + timedelta(days=INTERVAL_DAYS)).replace(
        hour=12, minute=0, second=0, microsecond=0,
    )
    return {
        "enabled": True, "generation": generation, "scheduleName": _schedule_name(bill_id, generation),
        "chatID": str(chat_id), "groupTitle": title,
        "intervalDays": INTERVAL_DAYS, "intervalSeconds": INTERVAL_SECONDS,
        "firstAt": int(first.timestamp()), "timeZone": "Asia/Singapore", "localTime": "12:00",
        "scheduleVersion": 2,
    }


def _is_standard(reminder):
    return (reminder.get("scheduleVersion") == 2 and reminder.get("intervalSeconds") == INTERVAL_SECONDS
            and reminder.get("timeZone") == "Asia/Singapore" and reminder.get("localTime") == "12:00")


def _schedule(scheduler, owner_id, bill_id, reminder):
    scheduler.schedule_recurring(reminder["scheduleName"], INTERVAL_SECONDS, JOB, {
        "ownerID": str(owner_id), "billID": bill_id, "generation": reminder["generation"],
    }, start_at=datetime.fromtimestamp(int(reminder["firstAt"]), UTC))


def _upgrade_schedule(bill, scheduler):
    """Move existing enabled reminders to noon without posting on their old cadence."""
    old = bill.get("reminder", {})
    if not old.get("enabled") or _is_standard(old):
        return bill
    owner_id, bill_id = bill["ownerID"], bill["sessionID"]
    reminder = _standard_reminder(bill_id, uuid4().hex, old["chatID"], old.get("groupTitle", "Selected group"))
    _schedule(scheduler, owner_id, bill_id, reminder)
    # If the write times out, leave the candidate schedule in place: the write
    # may have committed. Jobs for an unused generation delete themselves.
    updated = repo.replace_active(owner_id, bill_id, old["generation"], reminder)
    if not updated:
        _cancel_schedule(scheduler, reminder["scheduleName"])
        return ledger.get_bill(owner_id, bill_id)
    _cancel_schedule(scheduler, old.get("scheduleName"))
    return updated


def _cancel_schedule(scheduler, name):
    if name:
        try:
            scheduler.cancel(name)
        except (BotoCoreError, ClientError):
            # Disabled/generation checks still suppress posts if cleanup fails.
            logger.warning("Could not remove an inactive bill reminder schedule")


def stop_if_paid(owner_id, bill_id, scheduler):
    try:
        bill = ledger.get_bill(owner_id, bill_id)
        reminder = bill.get("reminder", {}) if bill else {}
        if reminder.get("enabled") and not ledger.outstanding_shares(bill):
            repo.disable(owner_id, bill_id, reminder["generation"], "paid")
            _cancel_schedule(scheduler, reminder.get("scheduleName"))
            return True
    except (BotoCoreError, ClientError):
        # Payment is already saved; a future reminder run checks it again.
        logger.warning("Could not clean up reminders after a payment")
    return False


def _answer(ctx, text=None):
    if ctx.callback_id:
        ctx.telegram.answer_callback_query(ctx.callback_id, text)


def _send(ctx, text, keyboard=None, *, edit=False):
    if edit:
        try:
            ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, text, keyboard or [], parse_mode="HTML")
        except TelegramError as exc:
            if "message is not modified" not in str(exc).lower():
                raise
    else:
        ctx.telegram.send_message(ctx.chat_id, text, keyboard, parse_mode="HTML")


def _bill(owner_id, bill_id):
    bill = ledger.get_bill(owner_id, bill_id)
    return ledger.index_bill(bill) if bill else None


def _send_options(ctx, text, bill_id):
    _send(ctx, text, [[{"text": "Bill options", "callback_data": f"split_bill:options:{bill_id}"}]])


def _reminder_actions(bill_id):
    return [[{"text": "Stop reminder", "callback_data": f"split_remind:stop:{bill_id}"}],
            [{"text": "Generate payment message", "callback_data": f"split_bill:share:{bill_id}"}],
            [{"text": "Change reminder group", "callback_data": f"split_remind:setup:{bill_id}"}]]


def _pending_link(ctx, text, *, page=0, edit=False):
    _send(ctx, text, [[{"text": "Back to all reminders", "callback_data": f"split_remind:list:{page}"}]], edit=edit)


def _pending_reminders(ctx, page=0, *, edit=False):
    if navigation.offer_private_chat(ctx, "reminders"):
        return
    bills = []
    for bill in ledger.owned_bills(ctx.user_id):
        if not bill or not bill.get("reminder", {}).get("enabled"):
            continue
        if ledger.outstanding_shares(bill):
            bills.append(bill)
        else:
            stop_if_paid(ctx.user_id, bill["sessionID"], ctx.scheduler)
    bills.sort(key=lambda bill: (int(bill.get("createdAt", 0)), bill["sessionID"]), reverse=True)
    page_count = max(1, (len(bills) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, page_count - 1))
    lines = ["<b>Pending reminders</b>"]
    keyboard = []
    if not bills:
        lines.extend(["", "You have no pending reminders.",
                      "Set up reminders after completing a split."])
    else:
        lines.extend([f"Active reminders: {len(bills)}", "Choose a reminder to view or manage it."])
    for index, bill in enumerate(bills[page * PAGE_SIZE:(page + 1) * PAGE_SIZE], page * PAGE_SIZE + 1):
        reminder = bill["reminder"]
        outstanding = ledger.outstanding_shares(bill)
        group = reminder.get("groupTitle") or "Selected group"
        names = ", ".join(item["name"] for batch in bill["expenses"] for item in batch["items"])
        lines.extend(["", f"<b>{index}. {escape(group[:80])}</b>",
                      f"Bill: <code>{bill['sessionID'][:8]}</code>", escape(names[:70]),
                      f"Unpaid people: {len(outstanding)} | Outstanding: {billing.money(sum(outstanding.values()))}"])
        keyboard.append([{"text": f"View reminder #{index}", "callback_data": f"split_remind:view:{bill['sessionID']}:{page}"}])
    navigation_buttons = []
    if page:
        navigation_buttons.append({"text": "Previous", "callback_data": f"split_remind:list:{page - 1}"})
    if page + 1 < page_count:
        navigation_buttons.append({"text": "Next", "callback_data": f"split_remind:list:{page + 1}"})
    if navigation_buttons:
        keyboard.append(navigation_buttons)
    keyboard.append([{"text": "Refresh", "callback_data": f"split_remind:list:{page}"}])
    if bills:
        lines.extend(["", f"Page {page + 1}/{page_count}"])
    _send(ctx, "\n".join(lines), keyboard, edit=edit)


def _view_reminder(ctx, bill_id, *, page=0, edit=False):
    if navigation.offer_private_chat(ctx, "reminders"):
        return
    bill = _bill(ctx.user_id, bill_id)
    if not bill:
        _pending_link(ctx, "That reminder wasn't found among your bills.", page=page, edit=edit)
        return
    reminder = bill.get("reminder", {})
    if not ledger.outstanding_shares(bill):
        stop_if_paid(ctx.user_id, bill_id, ctx.scheduler)
        _pending_link(ctx, "Everyone has marked their share paid. This reminder is no longer pending.", page=page, edit=edit)
        return
    if not reminder.get("enabled"):
        _pending_link(ctx, "This reminder has been stopped and is no longer pending.", page=page, edit=edit)
        return
    first_at = int(reminder["firstAt"])
    interval = int(reminder["intervalSeconds"])
    next_at = first_at + max(0, (int(time.time()) - first_at) // interval + 1) * interval
    next_date = datetime.fromtimestamp(next_at, SINGAPORE).strftime("%d %b %Y at %I:%M %p")
    text = (f"Group: {escape((reminder.get('groupTitle') or 'Selected group')[:80])}\n"
            f"{SCHEDULE_LABEL}.\nNext scheduled reminder: {next_date} Singapore time.\n\n"
            + payment_message(bill, reminder=True))
    keyboard = _reminder_actions(bill_id)
    keyboard.append([{"text": "Back to all reminders", "callback_data": f"split_remind:list:{page}"}])
    _send(ctx, text, keyboard, edit=edit)


@router.command("/reminders")
@router.command("/reminder")
@requires_profile
def reminder_command(ctx):
    _pending_reminders(ctx)


def _validate_group(telegram, chat_id):
    chat = telegram.get_chat(int(chat_id))
    if chat["type"] not in ("group", "supergroup"):
        raise ValueError("Choose a group or supergroup where this bot has been added.")
    member = telegram.get_chat_member(int(chat_id), telegram.get_me()["id"])
    status = member["status"]
    allowed = status in ("creator", "administrator")
    if status == "member":
        permissions = chat.get("permissions")
        allowed = permissions is None or permissions.get("can_send_messages", False)
    elif status == "restricted":
        allowed = member.get("is_member", False) and member.get("can_send_messages", False)
    if not allowed:
        raise ValueError("Add this bot to the group and allow it to send messages, then choose the group again.")
    return chat


def _start(ctx, bill_id):
    bill = _bill(ctx.user_id, bill_id)
    if not bill:
        _send(ctx, "Only the bill creator can set reminders. " + BILL_OPTIONS_HINT)
        return
    if not ledger.outstanding_shares(bill):
        stop_if_paid(ctx.user_id, bill_id, ctx.scheduler)
        _send(ctx, "This bill has no unpaid shares left. It doesn't need reminders.")
        return
    if ctx.chat_type not in ("private", "group", "supergroup"):
        _send(ctx, "Set up reminders in private chat or in the target group.")
        return
    setup = repo.start_setup(
        ctx.user_id, ctx.chat_id, bill_id,
        target_chat_id=ctx.chat_id if ctx.chat_type != "private" else None, title=ctx.chat_title,
    )
    if ctx.chat_type == "private":
        ctx.telegram.send_message(
            ctx.chat_id, f"Choose the group for bill {bill_id[:8]}. Add this bot to that group first.\n\n"
            "I'll send an initial reminder as soon as you choose the group, then remind every two days at 12:00 pm Singapore time.",
            parse_mode=None, reply_markup={"keyboard": [[{"text": "Choose reminder group", "request_chat": {
                "request_id": int(setup["requestID"]), "chat_is_channel": False,
                "bot_is_member": True, "request_title": True,
            }}]], "resize_keyboard": True, "one_time_keyboard": True},
        )
    else:
        _enable(ctx, setup["token"])


@router.message("chat_shared")
@requires_profile
def group_shared(ctx):
    setup = repo.get_setup(ctx.user_id)
    shared = ctx.shared_chat or {}
    if (ctx.chat_type != "private" or not setup or setup["status"] != "GROUP"
            or setup["sourceChatID"] != str(ctx.chat_id) or shared.get("request_id") != int(setup["requestID"])):
        return
    try:
        chat = _validate_group(ctx.telegram, shared["chat_id"])
    except (TelegramError, ValueError):
        _send(ctx, "I couldn't access that group or post there. Add me to the group and allow messages, then choose it again.")
        return
    setup = repo.choose_group(ctx.user_id, setup, chat["id"], chat.get("title", "Selected group"))
    if setup:
        confirmation = ctx.telegram.send_message(
            ctx.chat_id, "Group selected.", parse_mode=None, reply_markup={"remove_keyboard": True},
        )
        _enable(ctx, setup["token"], confirmation_message_id=confirmation.get("message_id") if confirmation else None)


def _stop(ctx, bill_id):
    bill = _bill(ctx.user_id, bill_id)
    if not bill:
        _send(ctx, "Only the bill creator can change its reminders.")
        return
    repo.cancel_setup(ctx.user_id, bill_id)
    reminder = bill.get("reminder", {})
    if reminder:
        if not repo.disable(ctx.user_id, bill_id, reminder["generation"], "manual"):
            _send_options(ctx, "Reminder settings changed. Open this bill's options to stop the current schedule.", bill_id)
            return
        _cancel_schedule(ctx.scheduler, reminder.get("scheduleName"))
    _pending_link(ctx, f"Reminders are off for bill <code>{bill_id[:8]}</code>.")


def _enable(ctx, token, *, confirmation_message_id=None):
    setup = repo.get_setup(ctx.user_id)
    if not setup or setup["token"] != token or setup["sourceChatID"] != str(ctx.chat_id):
        _send(ctx, "This setup is no longer active. " + BILL_OPTIONS_HINT)
        return
    setup = repo.claim_setup(ctx.user_id, token)
    if not setup:
        _send(ctx, "This setup was already used. " + BILL_OPTIONS_HINT)
        return
    name = _schedule_name(setup["billID"], setup["generation"])
    bill = None
    try:
        chat = _validate_group(ctx.telegram, setup["targetChatID"])
        bill = _bill(ctx.user_id, setup["billID"])
        if not bill or not ledger.outstanding_shares(bill):
            repo.cancel_setup(ctx.user_id, setup["billID"])
            _send(ctx, "There are no unpaid shares left. Reminders weren't enabled.")
            return
        reminder = _standard_reminder(setup["billID"], setup["generation"], chat["id"], chat.get("title", "Selected group"))
        _schedule(ctx.scheduler, ctx.user_id, setup["billID"], reminder)
        if not repo.activate(ctx.user_id, setup, bill, reminder):
            _cancel_schedule(ctx.scheduler, name)
            repo.release_setup(ctx.user_id, setup)
            _send_options(ctx, "The bill or setup changed. Open its options to check the current settings.", setup["billID"])
            return
    except (TelegramError, ValueError):
        _cancel_schedule(ctx.scheduler, name)
        repo.release_setup(ctx.user_id, setup)
        _send_options(ctx, "I couldn't post to that group. Check my group permissions, then choose Set up reminders again.", setup["billID"])
        return
    except (BotoCoreError, ClientError):
        # A transaction timeout may have committed. Keep its schedule if the
        # canonical bill confirms this generation, rather than disabling it.
        try:
            current = ledger.get_bill(ctx.user_id, setup["billID"])
        except (BotoCoreError, ClientError):
            logger.warning("Could not confirm a reminder configuration")
            _send_options(ctx, "I couldn't confirm the reminder settings. Open the bill options to check them before trying again.", setup["billID"])
            return
        if current and current.get("reminder", {}).get("generation") == setup["generation"]:
            reminder = current["reminder"]
        else:
            _cancel_schedule(ctx.scheduler, name)
            repo.release_setup(ctx.user_id, setup)
            logger.warning("Could not enable a bill reminder schedule")
            _send_options(ctx, "I couldn't save the reminder schedule. Open the bill options to try again.", setup["billID"])
            return
    old = (bill or {}).get("reminder", {})
    if old.get("scheduleName") != name:
        _cancel_schedule(ctx.scheduler, old.get("scheduleName"))
    if not reminder.get("enabled"):
        _send_options(ctx, "Reminders are off for this bill.", setup["billID"])
        return
    if stop_if_paid(ctx.user_id, setup["billID"], ctx.scheduler):
        _send(ctx, "Everyone has marked their share paid. Reminders are off.")
        return
    initial = _try_initial(ctx, setup["billID"], setup["generation"])
    if initial not in ("sent", "failed"):
        _initial_result(ctx, setup["billID"], initial)
        return
    confirmation = "Group selected."
    keyboard = _reminder_actions(setup["billID"])
    if initial == "failed":
        confirmation += "\nI couldn't send the initial reminder. Tap Retry initial reminder below."
        keyboard.insert(0, [{"text": "Retry initial reminder", "callback_data": f"split_remind:initial:{setup['billID']}"}])
    if confirmation_message_id is not None:
        try:
            ctx.telegram.edit_message(ctx.chat_id, confirmation_message_id, confirmation, keyboard, parse_mode=None)
        except TelegramError:
            logger.warning("Could not add controls to the group selection confirmation")
            if initial == "failed":
                _initial_result(ctx, setup["billID"], initial)
        return
    ctx.telegram.send_message(ctx.chat_id, confirmation, keyboard, parse_mode=None)


def _try_initial(ctx, bill_id, generation):
    try:
        # Slot -1 is reserved for the immediate post. Regular noon occurrences
        # start at slot 0, so sending now never consumes a scheduled reminder.
        return _deliver_reminder(ctx.user_id, bill_id, generation, -1, ctx.telegram, ctx.scheduler)
    except (TelegramError, BotoCoreError, ClientError):
        logger.warning("Could not send an initial bill reminder")
        return "failed"


def _initial_result(ctx, bill_id, result):
    if result == "failed":
        _send(ctx, "I couldn't send the initial reminder. The recurring schedule is still enabled. You can try again below.",
              [[{"text": "Retry initial reminder", "callback_data": f"split_remind:initial:{bill_id}"}],
               [{"text": "Bill options", "callback_data": f"split_bill:options:{bill_id}"}]])
        return
    notices = {
        "sent": "Initial reminder sent to the group. The noon reminder schedule is still enabled.",
        "paid": "Everyone has marked their share paid. Reminders are off.",
        "inactive": "The reminder settings changed or reminders were stopped. Open the bill options to check them.",
        "posting_failed": "I couldn't post to that group. Reminders are paused. Check my permissions, then choose the group again.",
        "skipped": "The initial reminder was already handled or the reminder settings changed. No additional message was sent.",
    }
    _send_options(ctx, notices[result], bill_id)


def _retry_initial(ctx, bill_id):
    bill = _bill(ctx.user_id, bill_id)
    if not bill:
        _send(ctx, "Only the bill creator can send its reminders. " + BILL_OPTIONS_HINT)
        return
    reminder = bill.get("reminder", {})
    if not reminder.get("enabled"):
        _initial_result(ctx, bill_id, "inactive")
        return
    if not ledger.outstanding_shares(bill):
        stop_if_paid(ctx.user_id, bill_id, ctx.scheduler)
        _initial_result(ctx, bill_id, "paid")
        return
    _initial_result(ctx, bill_id, _try_initial(ctx, bill_id, reminder["generation"]))


@router.callback("split_remind:")
@requires_profile
def reminder_callback(ctx):
    match = re.fullmatch(r"split_remind:(list|view|setup|stop|initial|cancel):([a-f0-9]{32}|[0-9]{1,7})(?::([0-9]{1,7}))?", ctx.data)
    if not match:
        _answer(ctx, "This button is no longer available. Use /reminders or the buttons under your completed bill.")
        return
    action, value, page = match[1], match[2], match[3]
    if ((action == "list" and (not value.isdigit() or len(value) > 7))
            or (action != "list" and len(value) != 32) or (page is not None and action != "view")):
        _answer(ctx, "Invalid reminder action.")
        return
    _answer(ctx)
    if action == "list":
        _pending_reminders(ctx, int(value), edit=True)
    elif action == "view":
        _view_reminder(ctx, value, page=int(page or 0), edit=True)
    elif action == "setup":
        _start(ctx, value)
    elif action == "stop":
        _stop(ctx, value)
    elif action == "initial":
        _retry_initial(ctx, value)
    else:
        setup = repo.get_setup(ctx.user_id)
        if setup and setup["token"] == value and setup["sourceChatID"] == str(ctx.chat_id):
            repo.cancel_setup(ctx.user_id, setup["billID"])
            ctx.telegram.send_message(ctx.chat_id, "Reminder setup cancelled.", reply_markup={"remove_keyboard": True})


def _person(user_id, fallback=None):
    user = identities.get_user(user_id) or fallback or {"telegramID": user_id}
    if user.get("username"):
        label = f"@{user['username'][:32]}"
    else:
        label = user.get("firstName") or f"User {user_id}"
        if len(label) > 24:
            label = label[:24] + "…"
    return f'<a href="tg://user?id={int(user_id)}">{escape(label)}</a>'


def payment_message(bill, *, reminder=False):
    outstanding = ledger.outstanding_shares(bill)
    people = {str(person["userID"]): person for person in bill["participants"]}
    title = "Payment reminder" if reminder else "Payment request"
    lines = [f"<b>{title}</b>", f"Bill: <code>{bill['sessionID'][:8]}</code>",
             f"Pay {_person(bill['ownerID'])}.", "", "Still unpaid:" if reminder else "Amounts owed:"]
    for index, (user_id, amount) in enumerate(outstanding.items(), 1):
        lines.append(f"{index}. {_person(user_id, people.get(user_id))} - {billing.money(amount)}")
    lines.extend(["", f"<b>Outstanding: {billing.money(sum(outstanding.values()))}</b>",
                  "Open /bills in a private chat, choose What I owe, then tap Mark paid."])
    return "\n".join(lines)


@router.job(JOB, with_scheduler=True)
def reminder_job(payload, telegram, scheduler):
    owner_id, bill_id, generation = (str(payload[key]) for key in ("ownerID", "billID", "generation"))
    if not re.fullmatch(r"[0-9]{1,16}", owner_id) or not all(re.fullmatch(r"[a-f0-9]{32}", value) for value in (bill_id, generation)):
        raise ValueError("Invalid reminder job.")
    name = _schedule_name(bill_id, generation)
    bill = ledger.get_bill(owner_id, bill_id)
    reminder = bill.get("reminder", {}) if bill else {}
    if not reminder.get("enabled") or reminder.get("generation") != generation:
        _cancel_schedule(scheduler, name)
        return
    if not ledger.outstanding_shares(bill):
        stop_if_paid(owner_id, bill_id, scheduler)
        return
    if not _is_standard(reminder):
        _upgrade_schedule(bill, scheduler)
        return
    now = int(time.time())
    if now < int(reminder["firstAt"]):
        return
    slot = (now - int(reminder["firstAt"])) // int(reminder["intervalSeconds"])
    _deliver_reminder(owner_id, bill_id, generation, slot, telegram, scheduler)


def _deliver_reminder(owner_id, bill_id, generation, slot, telegram, scheduler):
    name = _schedule_name(bill_id, generation)
    if not repo.claim_delivery(owner_id, bill_id, generation, slot):
        return "skipped"
    try:
        bill = ledger.get_bill(owner_id, bill_id)
        current = bill.get("reminder", {}) if bill else {}
        if not current.get("enabled") or current.get("generation") != generation:
            _cancel_schedule(scheduler, name)
            return "inactive"
        if not ledger.outstanding_shares(bill):
            stop_if_paid(owner_id, bill_id, scheduler)
            return "paid"
        telegram.send_message(int(current["chatID"]), payment_message(bill, reminder=True), parse_mode="HTML")
    except TelegramError as exc:
        if exc.code in (400, 403):
            repo.disable(owner_id, bill_id, generation, "posting_failed")
            _cancel_schedule(scheduler, name)
            return "posting_failed"
        repo.release_delivery(owner_id, bill_id, generation, slot)
        raise
    except (BotoCoreError, ClientError):
        repo.release_delivery(owner_id, bill_id, generation, slot)
        raise
    repo.sent(owner_id, bill_id, generation, slot)
    return "sent"
