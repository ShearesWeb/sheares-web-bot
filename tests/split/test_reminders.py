from datetime import datetime

import pytest

import features  # noqa: F401
from core.router import router
from features.split import ledger, reminders, repository
from support import callback_update, message_update, seed_profile, users_shared_update


@pytest.fixture
def active_reminder(owner_profile, services, monkeypatch):
    setup_time = datetime(2026, 10, 19, 8, tzinfo=reminders.SINGAPORE)
    monkeypatch.setattr(reminders.time, "time", lambda: setup_time.timestamp())
    for update_id, text in enumerate(("/split", "Lunch - 10", "/done", "equal"), 1):
        router.dispatch_update(message_update(text, update_id=update_id), services)
    session = repository.get_session(1, 10)
    router.dispatch_update(users_shared_update(
        [{"user_id": 2, "username": "alice"}], int(session["requestID"]),
    ), services)
    bill = repository.get_session(1, 10)
    assert bill["status"] == "COMPLETED"

    services.telegram.chats[-100] = {"id": -100, "type": "supergroup", "title": "Friends"}
    services.telegram.chat_members[(-100, 999)] = {"status": "member"}
    router.dispatch_update(callback_update(f"split_remind:setup:{bill['sessionID']}"), services)
    request = services.telegram.sent[-1]["reply_markup"]["keyboard"][0][0]["request_chat"]
    update = message_update("", update_id=30)
    del update["message"]["text"]
    update["message"]["chat_shared"] = {"request_id": request["request_id"], "chat_id": -100}
    sent_before = len(services.telegram.sent)
    router.dispatch_update(update, services)
    return bill["sessionID"], update, sent_before


def test_group_selection_sends_one_short_confirmation_and_one_initial_group_reminder(services, active_reminder):
    bill_id, update, sent_before = active_reminder
    messages = services.telegram.sent[sent_before:]
    private = [message for message in messages if message["chat_id"] == 10]
    group = [message for message in messages if message["chat_id"] == -100]

    assert len(private) == 1
    assert private[0]["text"] == "Group selected."
    assert private[0]["reply_markup"] == {"remove_keyboard": True}
    assert len(group) == 1
    assert "<b>Payment reminder</b>" in group[0]["text"]
    assert "@alice</a> - 10.00" in group[0]["text"]
    confirmation = services.telegram.edited[-1]
    assert confirmation["message_id"] == sent_before + 1
    assert confirmation["text"] == "Group selected."
    assert [row[0]["text"] for row in confirmation["keyboard"]] == [
        "Stop reminder", "Generate payment message", "Change reminder group",
    ]
    assert len(services.scheduler.scheduled) == 1
    schedule = services.scheduler.scheduled[0]
    assert schedule["interval_seconds"] == 2 * 24 * 60 * 60
    assert schedule["start_at"].astimezone(reminders.SINGAPORE) == datetime(
        2026, 10, 21, 12, tzinfo=reminders.SINGAPORE,
    )
    assert ledger.get_bill(1, bill_id)["reminder"]["enabled"]

    before_duplicate = len(services.telegram.sent)
    router.dispatch_update(update, services)
    assert len(services.telegram.sent) == before_duplicate
    assert len(services.scheduler.scheduled) == 1


def test_stopping_a_reminder_offers_back_to_all_reminders(services, active_reminder):
    bill_id, _, _ = active_reminder

    router.dispatch_update(callback_update(f"split_remind:stop:{bill_id}"), services)

    assert ledger.get_bill(1, bill_id)["reminder"]["enabled"] is False
    assert services.scheduler.scheduled[0]["name"] in services.scheduler.cancelled
    assert services.telegram.sent[-1]["keyboard"] == [[{
        "text": "Back to all reminders", "callback_data": "split_remind:list:0",
    }]]
    router.dispatch_update(callback_update("split_remind:list:0"), services)
    assert "You have no pending reminders." in services.telegram.edited[-1]["text"]


def test_marking_the_last_share_paid_stops_future_reminders(services, active_reminder):
    bill_id, _, _ = active_reminder
    seed_profile(2, "A")

    router.dispatch_update(callback_update(f"split_pay:{bill_id}", user_id=2, chat_id=20), services)

    bill = ledger.get_bill(1, bill_id)
    assert ledger.outstanding_shares(bill) == {}
    assert bill["reminder"]["enabled"] is False
    assert services.scheduler.scheduled[0]["name"] in services.scheduler.cancelled
    sent_before = len(services.telegram.sent)
    reminders.reminder_job(services.scheduler.scheduled[0]["payload"], services.telegram, services.scheduler)
    assert len(services.telegram.sent) == sent_before
