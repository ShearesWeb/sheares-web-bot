import time

import features  # noqa: F401  (registers handlers)
from core.router import router
from features.laundry import repository as repo
from features.laundry import service
from support import callback_update, message_update, seed_machine, seed_profile

CANCEL_ROW = [{"text": "❌ Cancel", "callback_data": "cancel"}]


def test_wash_requires_profile(services):
    router.dispatch_update(message_update("/wash"), services)

    assert services.telegram.sent[0]["text"] == "Please create a /profile first."


def test_wash_offers_washers(services):
    seed_profile(1, "A")

    router.dispatch_update(message_update("/wash"), services)

    assert services.telegram.sent == [{
        "chat_id": 10,
        "text": "Which washing machine would you like to use? (30 mins)",
        "keyboard": [[{"text": f"W{i}", "callback_data": f"mclick_W{i}_30"} for i in range(1, 5)], CANCEL_ROW],
    }]


def test_dry_then_duration_offers_dryers(services):
    seed_profile(1, "A")

    router.dispatch_update(message_update("/dry"), services)
    router.dispatch_update(callback_update("drydur_45"), services)

    assert services.telegram.sent[0]["text"] == "How long do you need the dryer for?"
    assert services.telegram.sent[0]["keyboard"][0][1] == {"text": "45 mins", "callback_data": "drydur_45"}
    assert services.telegram.edited == [{
        "chat_id": 10, "message_id": 99,
        "text": "Which dryer would you like to use for 45 mins?",
        "keyboard": [[{"text": f"D{i}", "callback_data": f"mclick_D{i}_45"} for i in range(1, 5)], CANCEL_ROW],
    }]


def test_clicking_free_machine_claims_it(services):
    seed_profile(1, "A")

    router.dispatch_update(callback_update("mclick_W1_30"), services)

    assert services.telegram.edited[0]["text"] == "✅ *W1* is yours for 30 minutes. I'll remind you when it's done!"
    assert repo.get_machine("A", "W1")["status"] == "IN_USE"
    assert services.scheduler.scheduled[0]["payload"] == {"chat_id": 10, "block": "A", "machine_id": "W1"}


def test_machine_click_tolerates_markdown_asterisks(services):
    seed_profile(1, "A")

    router.dispatch_update(callback_update("mclick_*W1*_30"), services)

    assert services.telegram.edited[0]["text"] == "✅ *W1* is yours for 30 minutes. I'll remind you when it's done!"
    assert repo.get_machine("A", "W1")["currentUser"] == "1"


def test_clicking_busy_machine_offers_override(services):
    seed_profile(1, "A")
    seed_machine("A", "W2", status="IN_USE", currentUser="2", currentChatID="20", endTime=int(time.time()) + 600)

    router.dispatch_update(callback_update("mclick_W2_30"), services)

    assert services.telegram.edited[0]["text"] == "⚠️ *W2* is currently IN USE. Are you sure you want to override?"
    assert services.telegram.edited[0]["keyboard"] == [
        [{"text": "⚠️ Yes, Override", "callback_data": "over_W2_30"}], CANCEL_ROW,
    ]


def test_clicking_finished_machine_offers_bump_move_override(services):
    seed_profile(1, "A")
    seed_machine("A", "W2", status="IN_USE", currentUser="2", currentChatID="20", endTime=1)

    router.dispatch_update(callback_update("mclick_W2_30"), services)

    assert services.telegram.edited[0]["text"] == (
        "*W2* is currently awaiting collection. Send a reminder to the last user?\n"
        "You can also move the contents to another machine.\n"
        "_Don't see anything? Perhaps they forgot to click collect. Would you like to override?_"
    )
    assert services.telegram.edited[0]["keyboard"] == [
        [{"text": "🔔 Bump", "callback_data": "bump_W2"}],
        [{"text": "🔁 Move", "callback_data": "move_W2"}],
        [{"text": "⚠️ Override", "callback_data": "over_W2_30"}],
        CANCEL_ROW,
    ]


def test_bump_reaches_legacy_owner(services):
    seed_profile(1, "A")
    seed_machine("A", "W2", status="COMPLETED", currentUser="555", endTime=0)

    router.dispatch_update(callback_update("bump_W2"), services)

    assert services.telegram.sent == [{
        "chat_id": "555",
        "text": "🔔 Reminder to take your clothes from *W2* as someone is trying to use it!",
        "keyboard": None,
    }]
    assert services.telegram.edited[0]["text"] == "✅ Reminder sent to the user of *W2*."


def test_bump_without_owner(services):
    seed_profile(1, "A")

    router.dispatch_update(callback_update("bump_W2"), services)

    assert services.telegram.edited[0]["text"] == "⚠️ No registered user to remind for *W2*."
    assert services.telegram.sent == []


def test_move_lists_every_other_machine(services):
    seed_profile(1, "A")

    router.dispatch_update(callback_update("move_W2"), services)

    edit = services.telegram.edited[0]
    assert edit["text"] == "Where are you moving the contents of *W2* to?"
    assert [b["text"] for b in edit["keyboard"][0]] == ["W1", "W3", "W4", "D1"]
    assert [b["text"] for b in edit["keyboard"][1]] == ["D2", "D3", "D4"]
    assert edit["keyboard"][0][1]["callback_data"] == "moveto_W2_W3"
    assert edit["keyboard"][2] == CANCEL_ROW


def test_moveto_free_machine_notifies_owner(services):
    seed_profile(1, "A")
    seed_machine("A", "W1", status="IN_USE", currentUser="2", currentChatID="20", endTime=1)

    router.dispatch_update(callback_update("moveto_W1_W3"), services)

    assert services.telegram.sent == [{
        "chat_id": "20", "text": "🔔 Your clothes were moved from W1 to W3 by the next user.", "keyboard": None,
    }]
    assert services.telegram.edited[0]["text"] == (
        "✅ Contents moved to W3. W1 is now empty. Use /wash or /dry to start your cycle."
    )


def test_moveto_occupied_machine_offers_override(services):
    seed_profile(1, "A")
    seed_machine("A", "W1", status="COMPLETED", currentUser="2", currentChatID="20", endTime=0)
    seed_machine("A", "W3", status="IN_USE", currentUser="3", currentChatID="30", endTime=int(time.time()) + 600)

    router.dispatch_update(callback_update("moveto_W1_W3"), services)

    assert services.telegram.edited[0]["text"] == "⚠️ W3 is occupied. You can cancel, or override the target machine."
    assert services.telegram.edited[0]["keyboard"] == [
        [{"text": "⚠️ Override", "callback_data": "moveride_W1_W3"}], CANCEL_ROW,
    ]


def test_moveto_from_unrecorded_machine(services):
    seed_profile(1, "A")

    router.dispatch_update(callback_update("moveto_W1_W3"), services)

    assert services.telegram.edited[0]["text"] == (
        "*W1* has nothing recorded in it. Use /wash or /dry to start your cycle."
    )


def test_moveride_moves_without_notifying(services):
    seed_profile(1, "A")
    seed_machine("A", "W1", status="COMPLETED", currentUser="2", currentChatID="20", endTime=0)
    seed_machine("A", "W3", status="IN_USE", currentUser="3", currentChatID="30", endTime=int(time.time()) + 600)

    router.dispatch_update(callback_update("moveride_W1_W3"), services)

    assert services.telegram.sent == []
    assert services.telegram.edited[0]["text"] == (
        "✅ Overridden and moved. W1 is now empty. Use /wash or /dry to start your cycle."
    )
    assert repo.get_machine("A", "W3")["currentUser"] == "2"


def test_override_claims_machine(services):
    seed_profile(1, "A")
    seed_machine("A", "W2", status="IN_USE", currentUser="2", currentChatID="20", endTime=int(time.time()) + 600)

    router.dispatch_update(callback_update("over_W2_30"), services)

    assert services.telegram.edited[0]["text"] == "✅ Overridden. *W2* is yours for 30 minutes."
    assert repo.get_machine("A", "W2")["currentUser"] == "1"


def test_reminder_job_then_collect(services):
    seed_profile(1, "A")
    router.dispatch_update(callback_update("mclick_W1_30"), services)
    job = services.scheduler.scheduled[0]

    router.dispatch_job(job["job"], job["payload"], services.telegram)
    router.dispatch_update(callback_update("collect_W1"), services)

    assert services.telegram.sent == [{
        "chat_id": 10,
        "text": "🔔 Your laundry in W1 (Block A) is done!",
        "keyboard": [[{"text": "Collected ✅", "callback_data": "collect_W1"}]],
    }]
    assert services.telegram.edited[-1]["text"] == "✅ Thank you for collecting from *W1*. The machine is now available."
    assert repo.get_machine("A", "W1")["status"] == "AVAILABLE"


def test_collect_after_someone_else_took_over(services):
    seed_profile(1, "A")
    seed_machine("A", "W1", status="IN_USE", currentUser="2", currentChatID="20", endTime=1)

    router.dispatch_update(callback_update("collect_W1"), services)

    assert services.telegram.edited[0]["text"] == "✅ Thank you for collecting from *W1*"
    assert repo.get_machine("A", "W1")["currentUser"] == "2"


def test_cancel(services):
    router.dispatch_update(callback_update("cancel"), services)

    assert services.telegram.edited[0]["text"] == "Action cancelled."


def test_laundry_status_message(services):
    now = int(time.time())
    seed_profile(1, "A")
    seed_machine("A", "W1", status="IN_USE", currentUser="1", currentChatID="10", endTime=now + 600)
    seed_machine("A", "D2", status="IN_USE", currentUser="2", currentChatID="20", endTime=now - 1)
    repo.add_user_machine(1, "W1")

    router.dispatch_update(message_update("/laundry"), services)

    assert services.telegram.sent[0]["text"] == (
        "₊˚⊹ 🧺 *Block A Laundry Room* 🫧 ⊹˚₊\n\n"
        "*Washers*\n"
        "*W1*: 🔴 In use *(by you)* (~10mins left)\n"
        "*W2*: 🟢 Available\n"
        "*W3*: 🟢 Available\n"
        "*W4*: 🟢 Available\n\n"
        "*Dryers*\n"
        "*D1*: 🟢 Available\n"
        "*D2*: 🟡 Awaiting collection\n"
        "*D3*: 🟢 Available\n"
        "*D4*: 🟢 Available\n\n"
        "_You are currently using: W1_"
    )


def test_laundry_status_without_profile(services):
    router.dispatch_update(message_update("/laundry"), services)

    assert services.telegram.sent[0]["text"] == "Create a profile with /profile to get started!"


def test_laundry_status_reports_lookup_failure(monkeypatch, services):
    seed_profile(1, "A")

    def broken(*args):
        raise RuntimeError("dynamodb down")

    monkeypatch.setattr(service, "block_summary", broken)

    router.dispatch_update(message_update("/laundry"), services)

    assert services.telegram.sent[0]["text"] == "⚠️ Error retrieving laundry status."
