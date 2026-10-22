"""Person-first item checklists, backed by the creator's active split session."""

from html import escape
import logging
import re

from core import identities
from core.telegram import TelegramError
from features.split import allocation, billing, repository

logger = logging.getLogger(__name__)
PEOPLE_PER_PAGE = 8
ITEMS_PER_PAGE = 6
CALLBACK = re.compile(r"split_pick:(people|person|add|remove|finish):([a-f0-9]{32})(?::([0-9]{1,3}))?(?::([0-9]{1,3}))?(?::([0-9]{1,6}))?")
ARGUMENTS = {"people": 1, "person": 2, "add": 3, "remove": 3, "finish": 0}


def _data(session, action, *args):
    return ":".join(["split_pick", action, session["selectionToken"], *(str(arg) for arg in args)])


def _button(session, label, action, *args):
    return {"text": label, "callback_data": _data(session, action, *args)}


def _answer(ctx, text=None):
    if ctx.callback_id:
        ctx.telegram.answer_callback_query(ctx.callback_id, text)


def _deliver(ctx, text, keyboard, *, edit=False):
    if edit:
        try:
            ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, text, keyboard, parse_mode="HTML")
        except TelegramError as exc:
            if "message is not modified" not in str(exc).lower():
                raise
    else:
        ctx.telegram.send_message(ctx.chat_id, text, keyboard, parse_mode="HTML")


def _label(user):
    return f"@{user['username']}" if user.get("username") else (user.get("firstName") or f"User {user['telegramID']}")


def _person(session, index):
    stored = session["allocationUsers"][index]
    return identities.get_user(stored["telegramID"]) or stored


def _short(text, limit=34):
    text = " ".join(text.split())
    return text[:limit - 1] + "…" if len(text) > limit else text


def _page(page, count, page_size):
    return max(0, min(page, (count - 1) // page_size))


def _navigation(session, action, page, count, page_size, *args):
    buttons = []
    if page:
        buttons.append(_button(session, "Previous", action, *args, page - 1))
    if (page + 1) * page_size < count:
        buttons.append(_button(session, "Next", action, *args, page + 1))
    return [buttons] if buttons else []


def _selected_choices(session):
    return {number: {user_id: True for user_id, state in owners.items() if int(state) % 2}
            for number, owners in session.get("itemChoices", {}).items()}


def _missing(session, catalog):
    choices = _selected_choices(session)
    return [item["number"] for item in catalog if not any(choices.get(str(item["number"]), {}).values())]


def _render(ctx, session, *, person=None, page=0, edit=False, notice=None):
    catalog = allocation.item_catalog(session["expenses"])
    missing = _missing(session, catalog)
    choices = _selected_choices(session)
    users = session["allocationUsers"]
    preview = None
    if not missing:
        preview, _ = allocation.calculate_item_choices(session["expenses"], users, choices)
    lines = ["<b>Split by item</b>", f"Bill total: {billing.money(billing.total_cents(session['expenses']))}",
             f"Assigned items: {len(catalog) - len(missing)}/{len(catalog)}"]
    if notice:
        lines.extend(["", escape(notice)])
    keyboard = []
    if person is None:
        page = _page(page, len(users), PEOPLE_PER_PAGE)
        lines.extend(["", "Choose a person to select their items."])
        if preview:
            lines.append("All items are assigned. Review the shares, then tap Finish split.")
        for index in range(page * PEOPLE_PER_PAGE, min((page + 1) * PEOPLE_PER_PAGE, len(users))):
            user = _person(session, index)
            user_id = str(users[index]["telegramID"])
            count = sum(bool(owners.get(user_id)) for owners in choices.values())
            amount = f" - {billing.money(preview[index]['amountCents'])}" if preview else ""
            lines.append(f'{index + 1}. <a href="tg://user?id={int(user_id)}">{escape(_label(user))}</a>{amount} ({count} items)')
            keyboard.append([_button(session, f"{index + 1}. {_short(_label(user))} · {count} items", "person", index, 0)])
        keyboard.extend(_navigation(session, "people", page, len(users), PEOPLE_PER_PAGE))
        lines.append(f"People page {page + 1}/{(len(users) - 1) // PEOPLE_PER_PAGE + 1}")
    else:
        page = _page(page, len(catalog), ITEMS_PER_PAGE)
        user = _person(session, person)
        user_id = str(users[person]["telegramID"])
        lines.extend(["", f'<b>Items for <a href="tg://user?id={int(user_id)}">{escape(_label(user))}</a></b>',
                      "Tap to check or uncheck an item for this person.",
                      "An item selected for multiple people is shared equally."])
        if preview:
            lines.append(f"This person's total, including receipt adjustments: {billing.money(preview[person]['amountCents'])}")
        receipt_numbers = {index: number for number, index in enumerate(
            (index for index, batch in enumerate(session["expenses"]) if batch["source"] == "receipt"), 1
        )}
        for item in catalog[page * ITEMS_PER_PAGE:(page + 1) * ITEMS_PER_PAGE]:
            owners = choices[str(item["number"])]
            selected = bool(owners.get(user_id))
            count = sum(bool(value) for value in owners.values())
            mark = "☑" if selected else "☐"
            source = f"Receipt {receipt_numbers[item['batchIndex']]}" if item["batchIndex"] in receipt_numbers else "Text expense"
            quantity = f" (QTY: {escape(item['quantity'][:20])})" if item["quantity"] else ""
            shared = f" · shared by {count}" if count > 1 else (" · assigned" if count and not selected else "")
            lines.extend(["", f"{source} · {mark} {item['number']}. {escape(item['name'])}{quantity} - {billing.money(item['priceCents'])}{shared}"])
            label = f"{mark} {item['number']}. {_short(item['name'])} - {billing.money(item['priceCents'])}"
            state = int(session["itemChoices"][str(item["number"])].get(user_id, 0))
            keyboard.append([_button(session, label, "remove" if selected else "add", person, item["number"], state)])
        keyboard.extend(_navigation(session, "person", page, len(catalog), ITEMS_PER_PAGE, person))
        switch = [_button(session, "Choose another person", "people", person // PEOPLE_PER_PAGE)]
        if person + 1 < len(users):
            switch.append(_button(session, "Next person", "person", person + 1, 0))
        keyboard.append(switch)
        lines.append(f"\nItems page {page + 1}/{(len(catalog) - 1) // ITEMS_PER_PAGE + 1}")
    keyboard.append([_button(session, "Finish split", "finish")])
    lines.extend(["", "Receipt tax and service charges are shared proportionally to each person's items.",
                  "Use /done to reopen this checklist, /method to change the method or people, or /cancel to stop."])
    _deliver(ctx, "\n".join(lines), keyboard, edit=edit)


def show(ctx, session):
    session = repository.prepare_item_choices(ctx.user_id, ctx.chat_id, session)
    if session:
        ctx.telegram.send_message(
            ctx.chat_id, "Choose a person below, tick their items, then tap Finish split when everyone is assigned.",
            parse_mode=None, reply_markup={"remove_keyboard": True},
        )
        _render(ctx, session)


def _valid(session, token):
    return (session and session["status"] == "AWAITING_ALLOCATION" and session.get("splitMethod") == "item"
            and session.get("selectionToken") == token)


def handle_callback(ctx):
    match = CALLBACK.fullmatch(ctx.data)
    if not match:
        _answer(ctx, "Invalid item selection.")
        return None
    action, token = match[1], match[2]
    args = [int(value) for value in (match[3], match[4], match[5]) if value is not None]
    session = repository.get_session(ctx.user_id, ctx.chat_id)
    if len(args) != ARGUMENTS[action] or not _valid(session, token):
        _answer(ctx, "Only the bill creator can use the active checklist. Send /done to continue your bill.")
        return None
    catalog = allocation.item_catalog(session["expenses"])
    if (action in ("person", "add", "remove") and args[0] >= len(session["allocationUsers"])) or (
        action in ("add", "remove") and not 1 <= args[1] <= len(catalog)
    ) or (
        action in ("add", "remove") and bool(args[2] % 2) == (action == "add")
    ):
        _answer(ctx, "Choose a person and item from this bill.")
        return None
    session = repository.prepare_item_choices(ctx.user_id, ctx.chat_id, session)
    if not session:
        _answer(ctx, "This checklist is no longer active. Send /done to continue.")
        return None
    _answer(ctx)
    if action == "people":
        _render(ctx, session, page=args[0], edit=True)
    elif action == "person":
        _render(ctx, session, person=args[0], page=args[1], edit=True)
    elif action in ("add", "remove"):
        person, item_number, state = args
        saved = repository.set_item_choice(
            ctx.user_id, ctx.chat_id, session, session["allocationUsers"][person]["telegramID"],
            item_number, selected=action == "add", expected_state=state,
        )
        # Buttons set an explicit state: a repeated callback cannot toggle it back.
        saved = saved or repository.get_session(ctx.user_id, ctx.chat_id)
        if _valid(saved, token):
            _render(ctx, saved, person=person, page=(item_number - 1) // ITEMS_PER_PAGE, edit=True)
    else:
        missing = _missing(session, catalog)
        if missing:
            numbers = ", ".join(str(number) for number in missing[:12])
            remaining = f" and {len(missing) - 12} more" if len(missing) > 12 else ""
            _render(ctx, session, edit=True, notice=f"Assign these items before finishing: {numbers}{remaining}.")
            return None
        participants, details = allocation.calculate_item_choices(session["expenses"], session["allocationUsers"], _selected_choices(session))
        bill = repository.complete(
            ctx.user_id, ctx.chat_id, session["sessionID"], participants, billing.total_cents(session["expenses"]),
            session["selectionToken"], details=details, item_revision=session["itemRevision"],
        )
        if bill:
            try:
                _deliver(ctx, "<b>Item split saved.</b>\nYour completed bill follows below.", [], edit=True)
            except TelegramError:
                logger.warning("Could not close the completed item checklist")
            return bill
        current = repository.get_session(ctx.user_id, ctx.chat_id)
        if _valid(current, token):
            _render(ctx, current, edit=True, notice="Selections changed. Review the shares and tap Finish split again.")
    return None
