"""Private balance views and user-initiated settlement actions."""

from html import escape
import re

from core import identities
from core.router import router
from core.telegram import TelegramError
from features.profile.access import requires_profile
from features.split import billing, ledger, navigation, reminders

PAGE_SIZE = 5
MODES = {
    "owe": ("owe", False, "You owe"), "owed": ("owed", False, "You are owed"),
    "paid": ("owe", True, "Paid bills"), "received": ("owed", True, "Received payments"),
}


def _private(ctx):
    return not navigation.offer_private_chat(ctx, "bills")


def _answer(ctx, text=None):
    if ctx.callback_id:
        ctx.telegram.answer_callback_query(ctx.callback_id, text)


def _deliver(ctx, text, keyboard, *, edit=False):
    if edit:
        try:
            ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, text, keyboard, parse_mode="HTML")
        except TelegramError as exc:
            # Refreshing an unchanged view is already a successful refresh.
            if "message is not modified" not in str(exc).lower():
                raise
    else:
        ctx.telegram.send_message(ctx.chat_id, text, keyboard, parse_mode="HTML")


def _person(user_id):
    user = identities.get_user(user_id) or {}
    label = f"@{user['username']}" if user.get("username") else (user.get("firstName") or f"User {user_id}")
    return f'<a href="tg://user?id={int(user_id)}">{escape(label)}</a>'


def _overview(ctx, *, edit=False):
    outgoing = ledger.debts(ctx.user_id, "owe")
    incoming = ledger.debts(ctx.user_id, "owed")
    text = (
        "<b>Your bills</b>\n\n"
        f"You owe: <b>{billing.money(sum(item['amountCents'] for item in outgoing))}</b>\n"
        f"You are owed: <b>{billing.money(sum(item['amountCents'] for item in incoming))}</b>"
    )
    keyboard = [
        [{"text": "What I owe", "callback_data": "split_list:owe:0"},
         {"text": "Owed to me", "callback_data": "split_list:owed:0"}],
        [{"text": "Paid history", "callback_data": "split_list:paid:0"},
         {"text": "Received history", "callback_data": "split_list:received:0"}],
    ]
    _deliver(ctx, text, keyboard, edit=edit)


def _description(debt):
    names = []
    for batch in debt["expenses"]:
        if batch.get("source") == "receipt":
            names.append("Receipt: " + ", ".join(item["name"] for item in batch["items"]))
        else:
            names.extend(item["name"] for item in batch["items"])
    description = ", ".join(names).replace("\n", " ")
    return escape(description[:50] + ("..." if len(description) > 50 else "")) or "Bill split"


def _page(ctx, mode, offset=0, *, edit=False, notice=None):
    direction, paid, title = MODES[mode]
    debts = ledger.debts(ctx.user_id, direction, paid=paid)
    offset = max(0, min(offset, ((len(debts) - 1) // PAGE_SIZE) * PAGE_SIZE)) if debts else 0
    page = debts[offset:offset + PAGE_SIZE]
    lines = [f"<b>{title}</b>"]
    if notice:
        lines.extend(["", escape(notice)])
    total = sum(item["amountCents"] for item in debts)
    lines.extend(["", f"<b>Total: {billing.money(total)}</b>"])
    keyboard = []
    if not debts:
        lines.extend(["", "No paid bills yet." if paid else "No outstanding bills."])
    for index, debt in enumerate(page, offset + 1):
        person = debt["ownerID"] if direction == "owe" else debt["debtorID"]
        lines.extend(["", f"{index}. {_person(person)} - {billing.money(debt['amountCents'])}",
                      f"Bill: <code>{debt['billID'][:8]}</code>", _description(debt)])
        if not paid:
            action = "Mark paid" if direction == "owe" else "Mark received"
            data = f"split_pay:{debt['billID']}" if direction == "owe" else f"split_recv:{debt['billID']}:{debt['debtorID']}"
            keyboard.append([{"text": f"{action} #{index}", "callback_data": data}])
    navigation = []
    if offset:
        navigation.append({"text": "Previous", "callback_data": f"split_list:{mode}:{offset - PAGE_SIZE}"})
    if offset + PAGE_SIZE < len(debts):
        navigation.append({"text": "Next", "callback_data": f"split_list:{mode}:{offset + PAGE_SIZE}"})
    if navigation:
        keyboard.append(navigation)
    keyboard.append([{"text": "All bills", "callback_data": "split_list:overview:0"},
                     {"text": "Refresh", "callback_data": f"split_list:{mode}:{offset}"}])
    _deliver(ctx, "\n".join(lines), keyboard, edit=edit)


@router.command("/bills")
@requires_profile
def bills_command(ctx):
    if _private(ctx):
        _overview(ctx)


@router.callback("split_list:")
@requires_profile
def list_callback(ctx):
    if not _private(ctx):
        _answer(ctx)
        return
    match = re.fullmatch(r"split_list:(owe|owed|paid|received|overview):([0-9]{1,7})", ctx.data)
    if not match:
        _answer(ctx, "Invalid bill view.")
        return
    _answer(ctx)
    if match[1] == "overview":
        _overview(ctx, edit=True)
    else:
        _page(ctx, match[1], int(match[2]), edit=True)


@router.callback("split_pay:")
@requires_profile
def pay_callback(ctx):
    if not _private(ctx):
        _answer(ctx)
        return
    match = re.fullmatch(r"split_pay:(?:(ok):)?([a-f0-9]{32})", ctx.data)
    owner = ledger.payable_owner(ctx.user_id, match[2]) if match else None
    if owner and not match[1]:
        _confirm_payment(ctx, owner, match[2], ctx.user_id, "owe")
        return
    result = ledger.mark_paid(owner, match[2], ctx.user_id, ctx.user_id) if owner else "not_found"
    if owner and result in ("paid", "already_paid"):
        reminders.stop_if_paid(owner, match[2], ctx.scheduler)
    _payment_result(ctx, result, "owe")


@router.callback("split_recv:")
@requires_profile
def receive_callback(ctx):
    if not _private(ctx):
        _answer(ctx)
        return
    match = re.fullmatch(r"split_recv:(?:(ok):)?([a-f0-9]{32}):([0-9]{1,16})", ctx.data)
    if match and not match[1]:
        _confirm_payment(ctx, ctx.user_id, match[2], match[3], "owed")
        return
    result = ledger.mark_paid(ctx.user_id, match[2], match[3], ctx.user_id) if match else "not_found"
    if match and result in ("paid", "already_paid"):
        reminders.stop_if_paid(ctx.user_id, match[2], ctx.scheduler)
    _payment_result(ctx, result, "owed")


def _confirm_payment(ctx, owner_id, bill_id, debtor_id, mode):
    bill = ledger.get_bill(owner_id, bill_id)
    amount = ledger.outstanding_shares(bill).get(str(debtor_id)) if bill else None
    if amount is None:
        result = "already_paid" if bill and bill.get("payments", {}).get(str(debtor_id)) else "not_found"
        _payment_result(ctx, result, mode)
        return
    action = "paid" if mode == "owe" else "received"
    data = f"split_pay:ok:{bill_id}" if mode == "owe" else f"split_recv:ok:{bill_id}:{debtor_id}"
    _answer(ctx)
    _deliver(
        ctx, f"<b>Confirm payment</b>\n\nMark <b>{billing.money(amount)}</b> as {action}?\n"
        "This clears the share for both people and removes it from reminders.",
        [[{"text": f"Confirm {action}", "callback_data": data}],
         [{"text": "Back", "callback_data": f"split_list:{mode}:0"}]], edit=True,
    )


def _payment_result(ctx, result, mode):
    notices = {"paid": "Marked as paid.", "already_paid": "Already marked paid.", "not_found": "This payment wasn't found in your bills."}
    _answer(ctx, notices[result])
    _page(ctx, mode, edit=True)
