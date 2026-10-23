"""A checklist for deleting expenses after the creator confirms with Done."""

import logging
import re

from core.telegram import TelegramError
from features.split import billing, repository

logger = logging.getLogger(__name__)
PAGE_SIZE = 8
CALLBACK = re.compile(r"split_delete:(page|add|remove|done|back):([a-f0-9]{32}):([0-9]{1,3})(?::([0-9]{1,6}))?")
STALE = "This list has changed or expired. Send /delete to choose again."


def _answer(ctx, text=None):
    if ctx.callback_id:
        ctx.telegram.answer_callback_query(ctx.callback_id, text)


def _deliver(ctx, text, keyboard, *, edit=False):
    if edit:
        try:
            ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, text, keyboard, parse_mode=None)
        except TelegramError as exc:
            if "message is not modified" not in str(exc).lower():
                raise
    else:
        ctx.telegram.send_message(ctx.chat_id, text, keyboard, parse_mode=None)


def _button(session, label, action, value=0, state=None):
    data = f"split_delete:{action}:{session['deletionToken']}:{value}"
    if state is not None:
        data += f":{state}"
    return {"text": label, "callback_data": data}


def _selected(session):
    return {int(index) for index, state in session.get("deletionChoices", {}).items() if int(state) % 2}


def _render(ctx, session, *, page=0, edit=False):
    options = billing.deletion_options(session["expenses"])
    pages = max(1, (len(options) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    selected = _selected(session)
    lines = ["Select expenses or receipts to delete:", ""]
    keyboard = []
    for index in range(page * PAGE_SIZE, min((page + 1) * PAGE_SIZE, len(options))):
        option = options[index]
        amount = billing.money(option["amountCents"])
        state = int(session.get("deletionChoices", {}).get(str(index), 0))
        mark = "☑" if index in selected else "☐"
        lines.append(f"{mark} {index + 1}. {option['name']} - {amount}")
        name = option["name"] if len(option["name"]) <= 40 else option["name"][:37] + "..."
        keyboard.append([_button(session, f"{mark} {index + 1}. {name} - {amount}",
                                 "remove" if index in selected else "add", index, state)])
    selected_total = sum(option["amountCents"] for index, option in enumerate(options) if index in selected)
    lines.extend(["", f"Selected: {len(selected)} | Amount: {billing.money(selected_total)}",
                  "Press Done to delete the selected entries, or Cancel to keep everything.",
                  "Deleting a receipt removes all its items."])
    navigation = []
    if page:
        navigation.append(_button(session, "Previous", "page", page - 1))
    if page + 1 < pages:
        navigation.append(_button(session, "Next", "page", page + 1))
    if navigation:
        keyboard.append(navigation)
        lines.append(f"Page {page + 1}/{pages}")
    keyboard.append([_button(session, "Done", "done", state=int(session["deletionRevision"]))])
    keyboard.append([_button(session, "Cancel", "back")])
    _deliver(ctx, "\n".join(lines), keyboard, edit=edit)


def show(ctx):
    session = repository.get_session(ctx.user_id, ctx.chat_id)
    if not session or session["status"] == "CANCELLED":
        _deliver(ctx, "There's no active split session. Send /split to start again.", None)
        return
    if session["status"] == "COMPLETED":
        _deliver(ctx, "This bill has already been saved. Send /split to create a new bill.", None)
        return
    if session["status"] == "PROCESSING":
        if not repository.can_collect(session):
            _deliver(ctx, "Wait for the current addition to finish, then send /delete again.", None)
            return
        session = repository.recover_input(ctx.user_id, ctx.chat_id, session["sessionID"])
        if not session:
            _deliver(ctx, "The bill changed. Send /delete again.", None)
            return
    if not session["expenses"]:
        _deliver(ctx, "There are no expenses or receipts to delete. Add some expenses first.", None)
        return
    session = repository.begin_deletion(ctx.user_id, ctx.chat_id, session)
    if session:
        _render(ctx, session)
    else:
        _deliver(ctx, "The bill changed. Send /delete again.", None)


def handle_callback(ctx):
    match = CALLBACK.fullmatch(ctx.data)
    session = repository.get_session(ctx.user_id, ctx.chat_id)
    if (not match or not session or session["status"] not in repository.EDITABLE_STATUSES
            or session.get("deletionToken") != match[2] or "deletionRevision" not in session):
        _answer(ctx, STALE)
        return None
    action, value = match[1], int(match[3])
    state = int(match[4]) if match[4] is not None else None
    if (action in ("add", "remove", "done")) != (state is not None):
        _answer(ctx, STALE)
        return None
    if action == "page":
        _answer(ctx)
        _render(ctx, session, page=value, edit=True)
        return None
    if action == "back":
        if repository.cancel_deletion(ctx.user_id, ctx.chat_id, session):
            _answer(ctx)
            _deliver(ctx, "Nothing deleted, you can continue adding items. Send /done to continue or /delete to choose again.", [], edit=True)
        else:
            _answer(ctx, STALE)
        return None
    options = billing.deletion_options(session["expenses"])
    if action in ("add", "remove"):
        if value >= len(options) or bool(state % 2) == (action == "add"):
            _answer(ctx, STALE)
            return None
        saved = repository.set_deletion_choice(
            ctx.user_id, ctx.chat_id, session, value, selected=action == "add", expected_state=state,
        )
        current = saved or repository.get_session(ctx.user_id, ctx.chat_id)
        if (not current or current["status"] not in repository.EDITABLE_STATUSES
                or current.get("deletionToken") != match[2]):
            _answer(ctx, STALE)
            return None
        _answer(ctx)
        _render(ctx, current, page=value // PAGE_SIZE, edit=True)
        return None
    if state != int(session["deletionRevision"]):
        _answer(ctx, "Selections changed. Review them and press Done again.")
        _render(ctx, session, edit=True)
        return None
    selected = _selected(session)
    if not selected:
        _answer(ctx, "Select at least one expense or receipt, or press Cancel.")
        return None
    chosen = [option for index, option in enumerate(options) if index in selected]
    saved = repository.delete_expenses(
        ctx.user_id, ctx.chat_id, session, billing.remove_expenses(session["expenses"], chosen),
    )
    if not saved:
        _answer(ctx, STALE)
        return None
    _answer(ctx, "Deleted.")
    try:
        amount = billing.money(sum(option["amountCents"] for option in chosen))
        _deliver(ctx, f"Deleted {len(chosen)} selected entries - {amount}", [], edit=True)
    except TelegramError:
        logger.warning("Could not close the expense deletion picker")
    return saved
