import pytest

import features  # noqa: F401
from core.router import router
from features.split import billing, repository
from support import callback_update, message_update, photo_update


@pytest.fixture
def draft(owner_profile, services):
    router.dispatch_update(message_update("/split"), services)
    router.dispatch_update(message_update("Mcdonalds - 36.60\nTea - 3.40", update_id=2), services)
    claim = repository.claim_input(1, 10, "receipt")
    return repository.finish_input(1, 10, claim, "receipt", expenses=[{
        "source": "receipt", "totalCents": 15704,
        "items": [{"name": "Meal", "price": "140.00", "quantity": "1"}],
        "subtotal": "140.00", "tax": "17.04", "serviceCharge": "",
    }])


def button(message, label):
    return next(item["callback_data"] for row in message["keyboard"] for item in row if item["text"] == label)


def entry(message, number):
    return next(item["callback_data"] for row in message["keyboard"] for item in row
                if item["text"].startswith((f"\u2610 {number}.", f"\u2611 {number}.")))


def click(services, data):
    router.dispatch_update(callback_update(data), services)


def open_checklist(services):
    router.dispatch_update(message_update("/delete"), services)
    return services.telegram.sent[-1]


def test_done_deletes_selected_text_and_whole_receipt_once(draft, services):
    menu = open_checklist(services)
    assert "Mcdonalds - 36.60" in menu["text"]
    assert "Receipt 1 - 157.04" in menu["text"]
    click(services, entry(menu, 1))
    click(services, entry(services.telegram.edited[-1], 3))

    assert repository.get_session(1, 10)["expenses"] == draft["expenses"]
    menu = services.telegram.edited[-1]
    assert "Selected: 2 | Amount: 193.64" in menu["text"]
    done = button(menu, "Done")
    click(services, done)

    saved = repository.get_session(1, 10)
    assert saved["status"] == "COLLECTING"
    assert saved["expenses"] == [{
        "source": "text", "totalCents": 340,
        "items": [{"name": "Tea", "price": "3.40", "quantity": ""}],
    }]
    assert saved["processedUpdates"] == draft["processedUpdates"]
    assert saved["expiresAt"] == draft["expiresAt"]
    assert "<b>Total: 3.40</b>" in services.telegram.sent[-1]["text"]
    sent_before = len(services.telegram.sent)
    click(services, done)
    assert repository.get_session(1, 10) == saved
    assert len(services.telegram.sent) == sent_before


def test_cancel_keeps_expenses_and_existing_allocation_draft(draft, services):
    session = repository.begin_method(1, 10, draft["sessionID"])
    session = repository.begin_participants(1, 10, session["sessionID"], method="exact",
                                          selection_token=session["selectionToken"])
    session = repository.begin_allocation(1, 10, session["sessionID"], [{"telegramID": "2"}], session["selectionToken"])
    menu = open_checklist(services)
    click(services, entry(menu, 1))
    click(services, button(services.telegram.edited[-1], "Cancel"))

    assert repository.get_session(1, 10) == session
    assert "Nothing deleted" in services.telegram.edited[-1]["text"]


def test_deleting_every_entry_cancels_the_split(draft, services, textract):
    menu = open_checklist(services)
    for number in (1, 2, 3):
        click(services, entry(menu, number))
        menu = services.telegram.edited[-1]
    click(services, button(menu, "Done"))

    session = repository.get_session(1, 10)
    assert session["status"] == "CANCELLED"
    assert session["expenses"] == []
    assert services.telegram.sent[-1]["text"] == "Bill cancelled. Send /split to start again."
    assert services.telegram.sent[-1]["reply_markup"] == {"remove_keyboard": True}
    sent_before = len(services.telegram.sent)
    router.dispatch_update(photo_update(update_id=10), services)
    assert len(services.telegram.sent) == sent_before
    assert services.telegram.downloaded == []


def test_checkbox_retries_unchecking_and_old_done_preserve_the_bill(draft, services):
    menu = open_checklist(services)
    old_done = button(menu, "Done")
    check = entry(menu, 1)
    click(services, check)
    checked = repository.get_session(1, 10)
    click(services, check)
    assert repository.get_session(1, 10) == checked

    click(services, entry(services.telegram.edited[-1], 1))
    assert "Selected: 0" in services.telegram.edited[-1]["text"]
    click(services, entry(services.telegram.edited[-1], 2))
    click(services, old_done)
    assert "Selections changed" in services.telegram.callback_answers[-1]["text"]
    assert repository.get_session(1, 10)["expenses"] == draft["expenses"]

    click(services, button(services.telegram.edited[-1], "Done"))
    assert billing.total_cents(repository.get_session(1, 10)["expenses"]) == 19364


def test_adding_an_expense_invalidates_the_old_delete_checklist(draft, services):
    menu = open_checklist(services)
    click(services, entry(menu, 1))
    done = button(services.telegram.edited[-1], "Done")
    router.dispatch_update(message_update("Snacks - 2.00", update_id=4), services)
    current = repository.get_session(1, 10)
    click(services, done)

    assert repository.get_session(1, 10) == current
    assert billing.total_cents(current["expenses"]) == 19904
    assert "deletionToken" not in current
    assert "changed or expired" in services.telegram.callback_answers[-1]["text"]


def test_selections_persist_across_pages_and_delete_together(draft, services):
    router.dispatch_update(message_update("\n".join(f"Extra {index} - 1.00" for index in range(9)), update_id=4), services)
    menu = open_checklist(services)
    click(services, entry(menu, 1))
    click(services, button(services.telegram.edited[-1], "Next"))
    click(services, entry(services.telegram.edited[-1], 12))
    click(services, button(services.telegram.edited[-1], "Previous"))
    menu = services.telegram.edited[-1]
    assert "\u2611 1. Mcdonalds" in menu["text"]
    assert "Selected: 2" in menu["text"]
    click(services, button(menu, "Done"))

    expenses = repository.get_session(1, 10)["expenses"]
    assert all(batch["source"] == "text" for batch in expenses)
    assert billing.total_cents(expenses) == 1240
    assert sum(len(batch["items"]) for batch in expenses) == 10
