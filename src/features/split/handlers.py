import logging
from html import escape
import re
from uuid import uuid4

from botocore.exceptions import BotoCoreError, ClientError

from core import identities
from core.router import router
from core.telegram import FileTooLargeError, TelegramError
from features.profile.access import require_profile, requires_profile
from features.split import allocation, bill_actions, billing, checklist, deletion, navigation, quota, repository, service

logger = logging.getLogger(__name__)


@router.command("/split")
@requires_profile
def split_command(ctx):
    if navigation.offer_private_chat(ctx, "split"):
        return
    repository.start_session(ctx.user_id, ctx.chat_id)
    ctx.telegram.send_message(
        ctx.chat_id,
        "Send expenses as text, one per line:\nFong Seng - 36.21\nAl Amaan - 74.82\n\n"
        "You can also upload a photo of your receipt or a JPEG/PNG file (up to 10 MB). "
        "Wait for each receipt to be added before sending the next one, then send /done.\n\n"
        "Send /delete to remove an expense or receipt, or /cancel to stop. "
        "The session closes after 15 minutes without adding text expenses or receipts.",
        parse_mode=None, reply_markup={"remove_keyboard": True},
    )


def _event_id(ctx):
    return str(ctx.update_id) if ctx.update_id is not None else uuid4().hex


def _prompt_restart(ctx):
    ctx.telegram.send_message(
        ctx.chat_id, "There's no active split session. Send /split to start again.",
        parse_mode=None, reply_markup={"remove_keyboard": True},
    )


def _save_expenses(ctx, claim, event_id, expenses):
    saved = repository.finish_input(ctx.user_id, ctx.chat_id, claim, event_id, expenses=expenses)
    if saved is None and not repository.get_session(ctx.user_id, ctx.chat_id):
        _prompt_restart(ctx)
    return saved


def _send_consolidated(ctx, session):
    lines = [*billing.expense_summary_lines(session["expenses"], html=True), "",
             "Send more expenses, /delete to remove an expense or receipt, or /done."]
    for message in _chunk_lines(lines):
        ctx.telegram.send_message(ctx.chat_id, message, parse_mode="HTML", reply_markup={"remove_keyboard": True})


@router.message("text")
def split_text(ctx):
    session = repository.get_session(ctx.user_id, ctx.chat_id, include_expired=True)
    if not session or session["status"] in ("COMPLETED", "CANCELLED"):
        return
    if not require_profile(ctx):
        return
    if repository.is_expired(session):
        _prompt_restart(ctx)
        return
    if session["status"] == "AWAITING_METHOD":
        method = ctx.text.strip().casefold()
        method = next((key for key, label in allocation.METHODS.items() if label.casefold() == method), method)
        if method in allocation.METHODS:
            _choose_method(ctx, session, method)
        else:
            _ask_method(ctx, session)
        return
    if session["status"] == "AWAITING_ALLOCATION":
        _allocate_text(ctx, session)
        return
    if session["status"] == "AWAITING_PARTICIPANTS":
        try:
            handles = billing.parse_handles(ctx.text)
        except ValueError as exc:
            ctx.telegram.send_message(ctx.chat_id, str(exc), parse_mode=None)
            return
        session = repository.set_handles(ctx.user_id, ctx.chat_id, session["sessionID"], handles)
        if session:
            _resolve_and_complete(ctx, session, handles)
        return
    if not repository.can_collect(session):
        if session["status"] == "PROCESSING" and session.get("processingEvent") != _event_id(ctx):
            ctx.telegram.send_message(ctx.chat_id, "I'm still reading the previous receipt. Please send this expense again in a moment.", parse_mode=None)
        return
    try:
        expenses = billing.parse_expenses(ctx.text)
    except ValueError as exc:
        ctx.telegram.send_message(ctx.chat_id, str(exc), parse_mode=None)
        return
    event_id = _event_id(ctx)
    claim = repository.claim_input(ctx.user_id, ctx.chat_id, event_id)
    if not claim:
        current = repository.get_session(ctx.user_id, ctx.chat_id)
        if not current:
            _prompt_restart(ctx)
        elif current["status"] == "PROCESSING" and current.get("processingEvent") != event_id:
            ctx.telegram.send_message(ctx.chat_id, "I'm still saving the previous addition. Please send this expense again in a moment.", parse_mode=None)
        return
    saved = None
    try:
        billing.validate_capacity(claim["expenses"], expenses)
        saved = _save_expenses(ctx, claim, event_id, expenses)
    except ValueError as exc:
        ctx.telegram.send_message(ctx.chat_id, str(exc), parse_mode=None)
    finally:
        if saved is None:
            repository.finish_input(ctx.user_id, ctx.chat_id, claim, event_id)
    if saved:
        _send_consolidated(ctx, saved)


@router.command("/done")
@requires_profile
def done_command(ctx):
    session = repository.get_session(ctx.user_id, ctx.chat_id)
    if not session or session["status"] == "CANCELLED":
        ctx.telegram.send_message(ctx.chat_id, "Send /split to start a bill first.", parse_mode=None)
    elif session["status"] == "COMPLETED":
        repository.save_bill(session)
        _send_bill(ctx, session)
    elif session["status"] == "PROCESSING":
        if repository.can_collect(session):
            if repository.recover_input(ctx.user_id, ctx.chat_id, session["sessionID"]):
                ctx.telegram.send_message(ctx.chat_id, "The previous addition didn't finish. Please resend it, or send /done again to split the saved expenses.", parse_mode=None)
        else:
            ctx.telegram.send_message(ctx.chat_id, "I'm still reading a receipt. Send /done again in a moment.", parse_mode=None)
    elif session["status"] == "COLLECTING":
        if not session["expenses"]:
            ctx.telegram.send_message(ctx.chat_id, "Add at least one expense or receipt before /done.", parse_mode=None)
            return
        session = repository.begin_method(ctx.user_id, ctx.chat_id, session["sessionID"])
        if session:
            _ask_method(ctx, session)
    elif session["status"] == "AWAITING_METHOD":
        _ask_method(ctx, session)
    elif session["status"] == "AWAITING_ALLOCATION":
        _ask_allocation(ctx, session)
    elif session["status"] == "AWAITING_PARTICIPANTS":
        session = repository.set_handles(ctx.user_id, ctx.chat_id, session["sessionID"], session.get("requestedHandles", []))
        if not session:
            return
        if session.get("requestedHandles"):
            _resolve_and_complete(ctx, session, session["requestedHandles"])
        else:
            _ask_participants(ctx, session)


@router.command("/method")
@requires_profile
def method_command(ctx):
    session = repository.get_session(ctx.user_id, ctx.chat_id)
    if not session or session["status"] == "CANCELLED":
        ctx.telegram.send_message(ctx.chat_id, "Send /split and add expenses first.", parse_mode=None)
    elif session["status"] == "COMPLETED":
        ctx.telegram.send_message(ctx.chat_id, "This bill is already saved. Send /split to create another bill.", parse_mode=None)
    elif session["status"] == "PROCESSING":
        ctx.telegram.send_message(ctx.chat_id, "Wait for the current addition to finish, then send /done.", parse_mode=None)
    elif not session["expenses"]:
        ctx.telegram.send_message(ctx.chat_id, "Add at least one expense or receipt before choosing a split method.", parse_mode=None)
    else:
        session = repository.begin_method(ctx.user_id, ctx.chat_id, session["sessionID"])
        if session:
            ctx.telegram.send_message(
                ctx.chat_id, "Choose a new method below, then select the people again.",
                reply_markup={"remove_keyboard": True}, parse_mode=None,
            )
            _ask_method(ctx, session)


def _ask_method(ctx, session):
    lines = [*billing.expense_summary_lines(session["expenses"], html=True), "", "How would you like to split this bill?"]
    keyboard = [[{"text": allocation.METHODS[method], "callback_data": f"split_method:{method}:{session['selectionToken']}"}
                 for method in methods] for methods in (("equal", "exact"), ("percentage", "item"))]
    messages = list(_chunk_lines(lines))
    for index, message in enumerate(messages):
        ctx.telegram.send_message(ctx.chat_id, message, keyboard if index == len(messages) - 1 else None, parse_mode="HTML")


@router.callback("split_method:")
@requires_profile
def method_callback(ctx):
    match = re.fullmatch(r"split_method:(equal|exact|percentage|item):([a-f0-9]{32})", ctx.data)
    session = repository.get_session(ctx.user_id, ctx.chat_id)
    valid = match and session and session["status"] == "AWAITING_METHOD" and session.get("selectionToken") == match[2]
    if ctx.callback_id:
        ctx.telegram.answer_callback_query(ctx.callback_id, None if valid else "This choice is no longer active. Send /done to continue your bill.")
    if valid:
        _choose_method(ctx, session, match[1])


def _choose_method(ctx, session, method):
    if method == "item":
        try:
            allocation.item_catalog(session["expenses"])
        except ValueError as exc:
            ctx.telegram.send_message(ctx.chat_id, str(exc), parse_mode=None)
            return
    session = repository.begin_participants(
        ctx.user_id, ctx.chat_id, session["sessionID"], method=method, selection_token=session["selectionToken"],
    )
    if session:
        _ask_participants(ctx, session)


@router.command("/cancel")
@requires_profile
def cancel_command(ctx):
    if repository.cancel_session(ctx.user_id, ctx.chat_id):
        _send_cancelled(ctx)


def _send_cancelled(ctx):
    ctx.telegram.send_message(ctx.chat_id, "Bill cancelled. Send /split to start again.", reply_markup={"remove_keyboard": True}, parse_mode=None)


@router.command("/delete")
def delete_command(ctx):
    if ctx.chat_type == "private" and require_profile(ctx):
        deletion.show(ctx)


@router.callback("split_delete:")
def delete_callback(ctx):
    if ctx.chat_type != "private":
        if ctx.callback_id:
            ctx.telegram.answer_callback_query(ctx.callback_id)
        return
    if not require_profile(ctx):
        if ctx.callback_id:
            ctx.telegram.answer_callback_query(ctx.callback_id)
        return
    saved = deletion.handle_callback(ctx)
    if saved:
        if saved["status"] == "CANCELLED":
            _send_cancelled(ctx)
        else:
            _send_consolidated(ctx, saved)


def _people_keyboard(ctx, session):
    if ctx.chat_type != "private":
        return None
    return {"keyboard": [[{"text": "Choose people", "request_users": {
        "request_id": int(session["requestID"]), "user_is_bot": False, "max_quantity": 10,
        "request_name": True, "request_username": True,
    }}]], "resize_keyboard": True, "one_time_keyboard": True}


def _ask_participants(ctx, session, missing=None):
    if missing:
        text = (
            "I don't know these handles yet: " + ", ".join(f"@{name}" for name in missing)
            + ".\nAsk them to send /start to this bot, then resend the handles or /done."
        )
    else:
        text = (
            f"Split method: {allocation.METHODS[session.get('splitMethod', 'equal')]}\n\n"
            "Send all Telegram handles to split among, for example:\n@alice @bob\n\n"
            "Only these people are included. Add your own handle if you want a share."
        )
    if ctx.chat_type == "private":
        text += "\nYou can also use Choose people below."
    messages = list(_chunk_lines([*billing.expense_summary_lines(session["expenses"], html=True), "", escape(text, quote=False)]))
    for index, message in enumerate(messages):
        markup = _people_keyboard(ctx, session) if index == len(messages) - 1 else None
        ctx.telegram.send_message(ctx.chat_id, message, parse_mode="HTML", reply_markup=markup)


def _resolve_and_complete(ctx, session, handles):
    users = [identities.resolve_username(handle) for handle in handles]
    missing = [handle for handle, user in zip(handles, users) if user is None]
    if missing:
        _ask_participants(ctx, session, missing)
        return
    _complete_bill(ctx, session, users)


@router.message("users_shared")
@requires_profile
def participants_shared(ctx):
    session = repository.get_session(ctx.user_id, ctx.chat_id)
    if (
        not session or session["status"] != "AWAITING_PARTICIPANTS" or ctx.chat_type != "private"
        or ctx.shared_request_id != int(session["requestID"])
    ):
        return
    users = []
    for shared in ctx.shared_users[:10]:
        identities.remember_user({
            "id": shared["user_id"], "username": shared.get("username", ""),
            "first_name": shared.get("first_name", ""),
        })
        users.append(identities.get_user(shared["user_id"]))
    if session.get("requestedHandles"):
        _resolve_and_complete(ctx, session, session["requestedHandles"])
    elif users:
        _complete_bill(ctx, session, users)


def _complete_bill(ctx, session, users):
    users = list({str(user["telegramID"]): {
        "telegramID": str(user["telegramID"]), "username": user.get("username", ""), "firstName": user.get("firstName", ""),
    } for user in users}.values())
    if session.get("splitMethod", "equal") != "equal":
        session = repository.begin_allocation(ctx.user_id, ctx.chat_id, session["sessionID"], users, session["selectionToken"])
        if session:
            _ask_allocation(ctx, session)
        return
    participants, details = allocation.calculate(session["expenses"], users, "equal")
    _save_allocation(ctx, session, participants, details)


def _allocation_users(session):
    return [identities.get_user(user["telegramID"]) or user for user in session["allocationUsers"]]


def _ask_allocation(ctx, session):
    method = session["splitMethod"]
    if method == "item":
        checklist.show(ctx, session)
        return
    users = _allocation_users(session)
    total = billing.total_cents(session["expenses"])
    lines = [f"<b>{allocation.METHODS[method]}</b>", f"Total: {billing.money(total)}", "", "People:"]
    for index, user in enumerate(users, 1):
        label = f"@{user['username']}" if user.get("username") else (user.get("firstName") or f"User {user['telegramID']}")
        lines.append(f'{index}. <a href="tg://user?id={int(user["telegramID"])}">{escape(label)}</a>')
    lines.append("")
    if method == "exact":
        lines.extend(["Send each person's exact amount, one per line. Include everyone, using 0 for no share.",
                      "The amounts must add up to the total, including tax and service charges.", "", "Example (edit the amounts):"])
        base, remainder = divmod(total, len(users))
        lines.extend(f"{index + 1} - {billing.money(base + (index < remainder))}" for index in range(len(users)))
    elif method == "percentage":
        lines.extend(["Send each person's percentage, one per line. Include everyone, using 0% for no share.",
                      "Percentages must add up to 100%. Up to four decimal places are allowed.", "", "Example (edit the percentages):"])
        base, remainder = divmod(1_000_000, len(users))
        lines.extend(f"{index + 1} - {(base + (index < remainder)) // 10_000}.{(base + (index < remainder)) % 10_000:04d}%"
                     for index in range(len(users)))
    lines.extend(["", "You can use @handles instead of person numbers. Send all lines together in one message.",
                  "Send /done to see these instructions again, /method to change the method or people, or /cancel to stop."])
    for message in _chunk_lines(lines):
        ctx.telegram.send_message(ctx.chat_id, message, parse_mode="HTML", reply_markup={"remove_keyboard": True})


def _allocate_text(ctx, session):
    if session["splitMethod"] == "item":
        checklist.show(ctx, session)
        return
    try:
        participants, details = allocation.calculate(session["expenses"], _allocation_users(session), session["splitMethod"], ctx.text)
    except ValueError as exc:
        ctx.telegram.send_message(ctx.chat_id, str(exc), parse_mode=None)
        return
    _save_allocation(ctx, session, participants, details)


@router.callback("split_pick:")
@requires_profile
def item_checklist_callback(ctx):
    bill = checklist.handle_callback(ctx)
    if bill:
        _send_bill(ctx, bill)


def _save_allocation(ctx, session, participants, details):
    bill = repository.complete(
        ctx.user_id, ctx.chat_id, session["sessionID"], participants, billing.total_cents(session["expenses"]),
        session["selectionToken"], details=details,
    )
    if bill:
        _send_bill(ctx, bill)


def _send_bill(ctx, bill):
    current_users = {str(person["userID"]): identities.get_user(person["userID"]) for person in bill["participants"]}
    for message in _chunk_lines(billing.bill_lines(bill, current_users)):
        ctx.telegram.send_message(ctx.chat_id, message, parse_mode="HTML", reply_markup={"remove_keyboard": True})
    bill_actions.show(ctx, bill["sessionID"])


@router.message("photo")
@router.message("document")
def receipt_uploaded(ctx):
    if ctx.chat_type != "private" or not ctx.attachment:
        return
    session = repository.get_session(ctx.user_id, ctx.chat_id, include_expired=True)
    if not session or session["status"] in ("COMPLETED", "CANCELLED"):
        return
    if not require_profile(ctx):
        return
    if repository.is_expired(session):
        _prompt_restart(ctx)
        return
    event_id = _event_id(ctx)
    claim = repository.claim_input(ctx.user_id, ctx.chat_id, event_id)
    if claim is None:
        session = repository.get_session(ctx.user_id, ctx.chat_id, include_expired=True)
        if not session or session["status"] in ("COMPLETED", "CANCELLED"):
            return
        if repository.is_expired(session):
            _prompt_restart(ctx)
        elif session["status"] == "PROCESSING" and session.get("processingEvent") != event_id:
            ctx.telegram.send_message(ctx.chat_id, "I'm still reading the previous receipt. Please resend this picture after it finishes.", parse_mode=None)
        elif session["status"] in ("AWAITING_METHOD", "AWAITING_PARTICIPANTS", "AWAITING_ALLOCATION"):
            ctx.telegram.send_message(ctx.chat_id, "Expenses are complete. Send /done to continue splitting, or /split to start a new bill.", parse_mode=None)
        return
    saved = None
    status_message_id = None
    try:
        if ctx.attachment.mime_type not in ("image/jpeg", "image/png"):
            ctx.telegram.send_message(ctx.chat_id, "Please upload a JPEG or PNG image of your receipt.", parse_mode=None)
            return
        if (ctx.attachment.file_size or 0) > service.MAX_RECEIPT_BYTES:
            raise FileTooLargeError()
        quota.reserve_scan(ctx.user_id)
        status = ctx.telegram.send_message(ctx.chat_id, "Processing image...", parse_mode=None)
        status_message_id = status.get("message_id") if status else None
        image_bytes = ctx.telegram.download_file(ctx.attachment.file_id, max_bytes=service.MAX_RECEIPT_BYTES)
        receipts = service.analyze_receipt(image_bytes)
        if not any(receipt.items for receipt in receipts):
            ctx.telegram.send_message(
                ctx.chat_id, "I couldn't find receipt items. Please upload a clearer photo showing the whole receipt.", parse_mode=None,
            )
            return
        expenses = billing.receipt_expenses(receipts)
        billing.validate_capacity(claim["expenses"], expenses)
        saved = _save_expenses(ctx, claim, event_id, expenses)
    except FileTooLargeError:
        ctx.telegram.send_message(ctx.chat_id, "Please upload a receipt image smaller than 10 MB.", parse_mode=None)
    except ValueError as exc:
        ctx.telegram.send_message(ctx.chat_id, str(exc), parse_mode=None)
    except ClientError as exc:
        logger.exception("Textract could not process a receipt")
        if exc.response["Error"]["Code"] in ("BadDocumentException", "UnsupportedDocumentException"):
            text = "I couldn't read that image. Please upload a clearer JPEG or PNG photo of your receipt."
        else:
            text = "I couldn't read your receipt right now. Please upload it again in a moment."
        ctx.telegram.send_message(ctx.chat_id, text, parse_mode=None)
    except (BotoCoreError, TelegramError):
        logger.exception("Could not download or analyze a receipt")
        ctx.telegram.send_message(ctx.chat_id, "I couldn't read your receipt right now. Please upload it again in a moment.", parse_mode=None)
    finally:
        if saved is None:
            repository.finish_input(ctx.user_id, ctx.chat_id, claim, event_id)
        if status_message_id is not None:
            try:
                ctx.telegram.edit_message(
                    ctx.chat_id, status_message_id,
                    "Receipt added." if saved else "Receipt wasn't added.", parse_mode=None,
                )
            except TelegramError:
                logger.warning("Could not update the receipt processing status")
    if saved:
        _send_consolidated(ctx, saved)


def receipt_messages(receipts):
    lines = []
    for index, receipt in enumerate(receipts, 1):
        if not receipt.items:
            continue
        if lines:
            lines.append("")
        lines.extend(billing.receipt_lines({
            "items": [{"name": item.name, "price": item.price, "quantity": item.quantity} for item in receipt.items],
            "subtotal": receipt.subtotal, "serviceCharge": receipt.service_charge,
            "tax": receipt.tax, "total": receipt.total,
            "taxLabel": receipt.tax_label, "serviceChargeLabel": receipt.service_charge_label,
        }, index))
    yield from _chunk_lines(lines)


def _chunk_lines(lines):
    # 1900 code points also fit below Telegram's limit when encoded as UTF-16.
    message = ""
    for line in lines:
        while len(line) > 1900:
            if message:
                yield message
                message = ""
            yield line[:1900]
            line = line[1900:]
        candidate = f"{message}\n{line}" if message else line
        if len(candidate) > 1900:
            yield message
            message = line
        else:
            message = candidate
    if message:
        yield message
