"""Actions offered to the creator after a split has been saved."""

from html import escape
import re

from core.router import router
from features.profile.access import requires_profile
from features.split import billing, ledger, reminders


def _answer(ctx, text=None):
    if ctx.callback_id:
        ctx.telegram.answer_callback_query(ctx.callback_id, text)


def _owned_bill(ctx, bill_id):
    bill = ledger.get_bill(ctx.user_id, bill_id)
    return ledger.index_bill(bill) if bill else None


def _show(ctx, bill):
    bill_id = bill["sessionID"]
    outstanding = ledger.outstanding_shares(bill)
    reminder = bill.get("reminder", {})
    lines = ["<b>Bill options</b>", f"Bill: <code>{bill_id[:8]}</code>"]
    keyboard = []
    if outstanding:
        lines.extend(["", f"Outstanding: <b>{billing.money(sum(outstanding.values()))}</b>",
                      "Set up group reminders or generate a payment message to forward to your friends."])
        label = "Change reminder group" if reminder.get("enabled") else "Set up reminders"
        keyboard.append([{"text": label, "callback_data": f"split_remind:setup:{bill_id}"}])
        keyboard.append([{"text": "Generate payment message", "callback_data": f"split_bill:share:{bill_id}"}])
    else:
        reminders.stop_if_paid(ctx.user_id, bill_id, ctx.scheduler)
        lines.extend(["", "There are no unpaid shares left for this bill."])
    if outstanding and reminder.get("enabled"):
        lines.extend(["", f"Reminders: {reminders.SCHEDULE_LABEL}.",
                      f"Group: {escape(reminder.get('groupTitle') or 'Selected group')}"])
        keyboard.append([{"text": "Stop reminder", "callback_data": f"split_remind:stop:{bill_id}"}])
    ctx.telegram.send_message(ctx.chat_id, "\n".join(lines), keyboard or None, parse_mode="HTML")


def show(ctx, bill_id):
    bill = _owned_bill(ctx, bill_id)
    if bill:
        _show(ctx, bill)


@router.callback("split_bill:")
@requires_profile
def bill_action_callback(ctx):
    match = re.fullmatch(r"split_bill:(options|share):([a-f0-9]{32})", ctx.data)
    if not match:
        _answer(ctx, "This bill option is no longer available.")
        return
    bill = _owned_bill(ctx, match[2])
    if not bill:
        _answer(ctx, "Only the bill creator can use these options. Open /bills for your own bills.")
        return
    if match[1] == "options":
        _answer(ctx)
        _show(ctx, bill)
    elif ledger.outstanding_shares(bill):
        _answer(ctx, "Forward the message below to your friends.")
        ctx.telegram.send_message(ctx.chat_id, reminders.payment_message(bill), parse_mode="HTML")
    else:
        _answer(ctx, "There are no unpaid shares left for this bill.")
