import logging
import time

from core.router import router
from features.laundry import repository as repo
from features.laundry import service
from features.laundry.service import MoveOutcome

logger = logging.getLogger(__name__)

CANCEL_ROW = [{"text": "❌ Cancel", "callback_data": "cancel"}]

STATUS_LABELS = {
    "AVAILABLE": "🟢 Available",
    "IN_USE": "🔴 In use",
    "COMPLETED": "🟡 Awaiting collection",
}


def _parts(ctx):
    # Button data can come back with Markdown asterisks around its parts.
    return [part.strip("*") for part in ctx.data.split("_")]


def _require_block(ctx):
    block = repo.get_user_block(ctx.user_id)
    if not block:
        ctx.telegram.send_message(ctx.chat_id, "Please create a /profile first.")
    return block


def _edit(ctx, text, keyboard=None):
    ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, text, keyboard)


@router.command("/wash")
def wash_command(ctx):
    if not _require_block(ctx):
        return
    kb = [[{"text": f"W{i}", "callback_data": f"mclick_W{i}_30"} for i in range(1, 5)], CANCEL_ROW]
    ctx.telegram.send_message(ctx.chat_id, "Which washing machine would you like to use? (30 mins)", kb)


@router.command("/dry")
def dry_command(ctx):
    if not _require_block(ctx):
        return
    kb = [
        [
            {"text": "30 mins", "callback_data": "drydur_30"},
            {"text": "45 mins", "callback_data": "drydur_45"},
            {"text": "60 mins", "callback_data": "drydur_60"},
        ],
        CANCEL_ROW,
    ]
    ctx.telegram.send_message(ctx.chat_id, "How long do you need the dryer for?", kb)


@router.command("/laundry")
def laundry_command(ctx):
    block = repo.get_user_block(ctx.user_id)
    if not block:
        ctx.telegram.send_message(ctx.chat_id, "Create a profile with /profile to get started!")
        return

    try:
        machines, my_machines = service.block_summary(ctx.user_id, block, int(time.time()))
    except Exception:
        logger.exception("Failed to load laundry status for block %s", block)
        ctx.telegram.send_message(ctx.chat_id, "⚠️ Error retrieving laundry status.")
        return

    washers, dryers = [], []
    for m in machines:
        status_text = STATUS_LABELS.get(m.status, "[Error]")
        if m.mine:
            status_text += " *(by you)*"
        display = f"*{m.machine_id}*: {status_text}"
        if m.mins_left is not None:
            display += f" (~{m.mins_left}mins left)"
        (washers if m.machine_id.startswith("W") else dryers).append(display)

    if my_machines:
        summary_text = f"_You are currently using: {', '.join(my_machines)}_"
    else:
        summary_text = "_You are not using any machines._"

    message_text = (
        f"₊˚⊹ 🧺 *Block {block} Laundry Room* 🫧 ⊹˚₊\n\n"
        f"*Washers*\n"
        f"{chr(10).join(washers)}\n\n"
        f"*Dryers*\n"
        f"{chr(10).join(dryers)}\n\n"
        f"{summary_text}"
    )
    ctx.telegram.send_message(ctx.chat_id, message_text)


@router.callback("cancel")
def cancel(ctx):
    _edit(ctx, "Action cancelled.")


@router.callback("drydur_")
def choose_dryer(ctx):
    if not _require_block(ctx):
        return
    dur = _parts(ctx)[1]
    kb = [[{"text": f"D{i}", "callback_data": f"mclick_D{i}_{dur}"} for i in range(1, 5)], CANCEL_ROW]
    _edit(ctx, f"Which dryer would you like to use for {dur} mins?", kb)


@router.callback("mclick_")
def machine_clicked(ctx):
    block = _require_block(ctx)
    if not block:
        return
    parts = _parts(ctx)
    m_id, dur = parts[1], parts[2]
    status = service.machine_status(block, m_id, int(time.time()))

    if status == "AVAILABLE":
        service.claim(ctx.user_id, ctx.chat_id, block, m_id, int(dur), ctx.scheduler)
        _edit(ctx, f"✅ *{m_id}* is yours for {dur} minutes. I'll remind you when it's done!")
    elif status == "COMPLETED":
        msg = (
            f"*{m_id}* is currently awaiting collection. Send a reminder to the last user?\n"
            f"You can also move the contents to another machine.\n"
            f"_Don't see anything? Perhaps they forgot to click collect. Would you like to override?_"
        )
        kb = [
            [{"text": "🔔 Bump", "callback_data": f"bump_{m_id}"}],
            [{"text": "🔁 Move", "callback_data": f"move_{m_id}"}],
            [{"text": "⚠️ Override", "callback_data": f"over_{m_id}_{dur}"}],
            CANCEL_ROW,
        ]
        _edit(ctx, msg, kb)
    elif status == "IN_USE":
        kb = [[{"text": "⚠️ Yes, Override", "callback_data": f"over_{m_id}_{dur}"}], CANCEL_ROW]
        _edit(ctx, f"⚠️ *{m_id}* is currently IN USE. Are you sure you want to override?", kb)


@router.callback("bump_")
def bump(ctx):
    block = _require_block(ctx)
    if not block:
        return
    m_id = _parts(ctx)[1]
    owner_chat = service.owner_chat(block, m_id)
    if owner_chat is None:
        _edit(ctx, f"⚠️ No registered user to remind for *{m_id}*.")
        return
    ctx.telegram.send_message(owner_chat, f"🔔 Reminder to take your clothes from *{m_id}* as someone is trying to use it!")
    _edit(ctx, f"✅ Reminder sent to the user of *{m_id}*.")


@router.callback("move_")
def choose_move_target(ctx):
    if not _require_block(ctx):
        return
    m_id = _parts(ctx)[1]
    targets = [
        {"text": target, "callback_data": f"moveto_{m_id}_{target}"}
        for target in service.MACHINE_IDS
        if target != m_id
    ]
    kb = [targets[i:i + 4] for i in range(0, len(targets), 4)]
    kb.append(CANCEL_ROW)
    _edit(ctx, f"Where are you moving the contents of *{m_id}* to?", kb)


@router.callback("moveto_")
def move_contents(ctx):
    block = _require_block(ctx)
    if not block:
        return
    parts = _parts(ctx)
    from_id, to_id = parts[1], parts[2]
    outcome, orig_chat = service.move(block, from_id, to_id, int(time.time()))

    if outcome is MoveOutcome.SOURCE_EMPTY:
        _edit(ctx, f"*{from_id}* has nothing recorded in it. Use /wash or /dry to start your cycle.")
    elif outcome is MoveOutcome.TARGET_OCCUPIED:
        kb = [[{"text": "⚠️ Override", "callback_data": f"moveride_{from_id}_{to_id}"}], CANCEL_ROW]
        _edit(ctx, f"⚠️ {to_id} is occupied. You can cancel, or override the target machine.", kb)
    else:
        if orig_chat:
            ctx.telegram.send_message(orig_chat, f"🔔 Your clothes were moved from {from_id} to {to_id} by the next user.")
        _edit(ctx, f"✅ Contents moved to {to_id}. {from_id} is now empty. Use /wash or /dry to start your cycle.")


@router.callback("over_")
def override(ctx):
    block = _require_block(ctx)
    if not block:
        return
    parts = _parts(ctx)
    m_id, dur = parts[1], parts[2]
    service.claim(ctx.user_id, ctx.chat_id, block, m_id, int(dur), ctx.scheduler)
    _edit(ctx, f"✅ Overridden. *{m_id}* is yours for {dur} minutes.")


@router.callback("moveride_")
def move_override(ctx):
    block = _require_block(ctx)
    if not block:
        return
    parts = _parts(ctx)
    from_id, to_id = parts[1], parts[2]
    if service.move_override(block, from_id, to_id) is MoveOutcome.SOURCE_EMPTY:
        _edit(ctx, f"*{from_id}* has nothing recorded in it. Use /wash or /dry to start your cycle.")
        return
    _edit(ctx, f"✅ Overridden and moved. {from_id} is now empty. Use /wash or /dry to start your cycle.")


@router.callback("collect_")
def collect(ctx):
    block = _require_block(ctx)
    if not block:
        return
    m_id = _parts(ctx)[1]
    if service.collect(ctx.user_id, block, m_id):
        _edit(ctx, f"✅ Thank you for collecting from *{m_id}*. The machine is now available.")
    else:
        # They took too long and someone else claimed or moved it.
        _edit(ctx, f"✅ Thank you for collecting from *{m_id}*")


@router.job(service.LAUNDRY_DONE_JOB)
def laundry_done(payload, telegram):
    machine_id = payload["machine_id"]
    telegram.send_message(
        payload["chat_id"],
        f"🔔 Your laundry in {machine_id} (Block {payload['block']}) is done!",
        [[{"text": "Collected ✅", "callback_data": f"collect_{machine_id}"}]],
    )
