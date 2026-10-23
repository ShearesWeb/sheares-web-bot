from itertools import count
from copy import deepcopy

import pytest

import features  # noqa: F401
from core import identities
from core.db import users_table
from core.router import router
from features.split import billing, handlers, repository, service
from support import callback_update, message_update, photo_update, users_shared_update

pytestmark = pytest.mark.usefixtures("owner_profile")


@pytest.fixture
def send(services):
    sequence = count(1)

    def dispatch(text, user_id=1, chat_id=10, username=None):
        update = message_update(text, user_id=user_id, chat_id=chat_id, username=username, update_id=next(sequence))
        router.dispatch_update(update, services)
        return update

    return dispatch


def choose_equal(services):
    button = services.telegram.sent[-1]["keyboard"][0][0]
    assert button["text"] == "Equal"
    router.dispatch_update(callback_update(button["callback_data"]), services)


def saved_bill_message(services):
    assert services.telegram.sent[-1]["text"].startswith("<b>Bill options</b>")
    return services.telegram.sent[-2]


def test_mixed_text_and_receipts_split_only_entered_people_and_keep_ids_after_rename(
    send, services, textract, receipt_image, expense_response,
):
    send("/start", user_id=2, chat_id=20, username="alice")
    send("/start", user_id=3, chat_id=30, username="bob")
    send("/split", username="owner")
    send("KFC - 20\nBurger King - 36")
    services.telegram.files["receipt"] = receipt_image
    textract.add_response("analyze_expense", expense_response, {"Document": {"Bytes": receipt_image}})
    router.dispatch_update(photo_update(update_id=100), services)
    send("Coffee - 1.28")
    send("/done")
    assert "How would you like to split this bill?" in services.telegram.sent[-1]["text"]
    choose_equal(services)
    assert "Send all Telegram handles" in services.telegram.sent[-1]["text"]
    send("@alice, @bob\n@ALICE")

    bill = repository.get_session(1, 10)
    assert bill["status"] == "COMPLETED"
    assert len(bill["expenses"]) == 3
    assert bill["totalCents"] == 6600
    assert {person["userID"] for person in bill["participants"]} == {"2", "3"}
    assert [person["amountCents"] for person in bill["participants"]] == [3300, 3300]
    result = saved_bill_message(services)
    assert result["parse_mode"] == "HTML"
    assert '<a href="tg://user?id=2">@alice</a> - 33.00' in result["text"]
    assert '<a href="tg://user?id=3">@bob</a> - 33.00' in result["text"]

    send("/start", user_id=2, chat_id=20, username="new_alice")
    send("/done")
    assert '<a href="tg://user?id=2">@new_alice</a> - 33.00' in saved_bill_message(services)["text"]
    send("/split")
    stored = users_table().get_item(Key={"userID": "USER#1", "profile": f"SPLIT_BILL#{bill['sessionID']}"})["Item"]
    assert stored["participants"][0]["userID"] == "2"
    assert repository.get_session(1, 10)["expenses"] == []


def test_each_addition_done_and_final_bill_show_all_expenses_with_receipts_last(
    send, services, textract, receipt_image, expense_response,
):
    send("/start", user_id=2, chat_id=20, username="alice")
    send("/split")
    second_receipt = deepcopy(expense_response)
    document = second_receipt["ExpenseDocuments"][0]
    for field in document["LineItemGroups"][0]["LineItems"][0]["LineItemExpenseFields"]:
        field["ValueDetection"]["Text"] = {
            "ITEM": "Tea & cake", "QUANTITY": "1", "UNIT_PRICE": "12.00", "PRICE": "12.00",
        }[field["Type"]["Text"]]
    document["SummaryFields"] = [
        {"Type": {"Text": kind}, "ValueDetection": {"Text": value}}
        for kind, value in (("SUBTOTAL", "12.00"), ("TAX", "1.08"), ("TOTAL", "13.08"))
    ]
    services.telegram.files.update({"first": receipt_image, "second": receipt_image})
    for response in (expense_response, second_receipt):
        textract.add_response("analyze_expense", response, {"Document": {"Bytes": receipt_image}})

    router.dispatch_update(photo_update(file_id="first", update_id=100), services)
    assert "Receipt 1:" in services.telegram.sent[-1]["text"]
    send("KFC - 20")
    text = services.telegram.sent[-1]["text"]
    assert text.index("KFC - 20.00") < text.index("Receipt 1:")
    assert "Noodles_[special]* (QTY: 2) - 8.00" in text

    router.dispatch_update(photo_update(file_id="second", update_id=101), services)
    text = services.telegram.sent[-1]["text"]
    assert "KFC - 20.00" in text and "Noodles_[special]*" in text and "Tea &amp; cake" in text
    assert text.index("Receipt 1:") < text.index("Receipt 2:")
    send("Burger King - 36")
    text = services.telegram.sent[-1]["text"]
    assert text.index("KFC - 20.00") < text.index("Burger King - 36.00") < text.index("Receipt 1:")
    assert "Tea &amp; cake (QTY: 1) - 12.00" in text
    assert "----------------------------\n<b>Total: 77.80</b>" in text

    send("/done")
    choose_equal(services)
    prompt = services.telegram.sent[-1]["text"]
    assert "Send all Telegram handles" in prompt
    assert prompt.index("Burger King - 36.00") < prompt.index("Receipt 1:") < prompt.index("Receipt 2:")
    assert "Noodles_[special]*" in prompt and "Tea &amp; cake" in prompt
    send("@alice")
    final = saved_bill_message(services)["text"]
    assert "Each person pays:" in final and "77.80" in final
    assert final.index("Burger King - 36.00") < final.index("Receipt 1:") < final.index("Receipt 2:")
    assert "Noodles_[special]*" in final and "Tea &amp; cake (QTY: 1) - 12.00" in final
    assert final.startswith("<b>Splitting Bill</b>\n\n<b>Total: 77.80</b>\n\nEach person pays:")
    assert final.index("Each person pays:") < final.index("Burger King - 36.00")
    assert "<b>Total: 77.80</b>\n\nEach person pays:\n" in final
    assert "Please check these items and prices against your receipt." not in "\n".join(
        message["text"] for message in services.telegram.sent
    )


def test_long_final_bill_keeps_all_receipt_items_and_text_in_telegram_sized_messages():
    items = tuple(service.ReceiptItem(f"Item {number} {'x' * 50}", "1.00") for number in range(150))
    expenses = [
        *billing.receipt_expenses([service.Receipt(items, total="150.00")]),
        *billing.parse_expenses("KFC - 20\nBurger King - 36"),
    ]
    total = billing.total_cents(expenses)
    people = billing.split_equally(total, [{"telegramID": "2", "username": "alice"}])
    messages = list(handlers._chunk_lines(billing.bill_lines({
        "expenses": expenses, "totalCents": total, "participants": people,
    }, {})))
    text = "\n".join(messages)

    assert len(messages) > 1
    assert all(len(message.encode("utf-16-le")) // 2 <= 4096 for message in messages)
    assert all(item.name in text for item in items)
    assert text.index("KFC - 20.00") < text.index("Burger King - 36.00") < text.index("Receipt 1:")


def test_remainder_cents_are_conserved_without_adding_bill_creator(send, services):
    for user_id, name in ((2, "alice"), (3, "bob"), (4, "carol")):
        send("/start", user_id=user_id, chat_id=user_id * 10, username=name)
    send("/split")
    send("KFC - 20\nBurger King - 36")
    send("/done")
    choose_equal(services)
    send("alice bob carol")

    bill = repository.get_session(1, 10)
    assert sorted(person["amountCents"] for person in bill["participants"]) == [1866, 1867, 1867]
    assert sum(person["amountCents"] for person in bill["participants"]) == 5600
    assert "remaining cents" in saved_bill_message(services)["text"]


def test_unknown_handle_waits_for_registration_and_done_retries(send, services):
    send("/split")
    send("Lunch - 10")
    send("/done")
    choose_equal(services)
    send("@new_person")
    assert "I don't know these handles yet" in services.telegram.sent[-1]["text"]
    assert repository.get_session(1, 10)["status"] == "AWAITING_PARTICIPANTS"
    send("/start", user_id=9, chat_id=90, username="new_person")
    send("/done")
    assert repository.get_session(1, 10)["participants"][0]["userID"] == "9"


def test_people_picker_is_bound_to_session_and_stores_large_ids(send, services):
    send("/split")
    send("Lunch - 10")
    send("/done")
    choose_equal(services)
    request = services.telegram.sent[-1]["reply_markup"]["keyboard"][0][0]["request_users"]
    user_id = 1234567890123
    users = [{"user_id": user_id, "username": "friend", "first_name": "Friend"}]
    router.dispatch_update(users_shared_update(users, request["request_id"] + 1), services)
    assert identities.get_user(user_id) is None
    router.dispatch_update(users_shared_update(users, request["request_id"]), services)

    bill = repository.get_session(1, 10)
    assert bill["participants"][0]["userID"] == str(user_id)
    assert f'tg://user?id={user_id}' in saved_bill_message(services)["text"]
    assert saved_bill_message(services)["reply_markup"] == {"remove_keyboard": True}


def test_people_picker_can_resolve_unknown_handles_in_multiple_selections(send, services):
    send("/split")
    send("Lunch - 10")
    send("/done")
    choose_equal(services)
    send("@alice @bob")
    request_id = int(repository.get_session(1, 10)["requestID"])
    router.dispatch_update(users_shared_update([{"user_id": 2, "username": "alice"}], request_id), services)
    assert repository.get_session(1, 10)["status"] == "AWAITING_PARTICIPANTS"
    router.dispatch_update(users_shared_update([{"user_id": 3, "username": "bob"}], request_id), services)
    assert {person["userID"] for person in repository.get_session(1, 10)["participants"]} == {"2", "3"}


def test_busy_receipts_request_resend_and_done_recovers_expired_work(send, services, monkeypatch):
    monkeypatch.setattr(repository.time, "time", lambda: 1000)
    send("/split")
    send("Lunch - 10")
    claim = repository.claim_input(1, 10, "100")
    router.dispatch_update(photo_update(update_id=101), services)
    assert "resend this picture" in services.telegram.sent[-1]["text"]
    send("/done")
    assert "still reading" in services.telegram.sent[-1]["text"]
    monkeypatch.setattr(repository.time, "time", lambda: 1000 + repository.PROCESSING_SECONDS)
    send("/done")
    assert "didn't finish" in services.telegram.sent[-1]["text"]
    assert repository.finish_input(1, 10, claim, "100", expenses=[]) is None
    send("/done")
    assert repository.get_session(1, 10)["status"] == "AWAITING_METHOD"
    choose_equal(services)
    assert repository.get_session(1, 10)["status"] == "AWAITING_PARTICIPANTS"
    assert billing.total_cents(repository.get_session(1, 10)["expenses"]) == 1000


def test_invalid_lines_are_atomic_duplicate_updates_are_ignored_and_cancel_stops_collection(send, services):
    send("/split")
    send("KFC - 20\nWrong format")
    assert repository.get_session(1, 10)["expenses"] == []
    update = send("KFC - 20\nBurger King - 36")
    router.dispatch_update(update, services)
    send("Another - 9", user_id=2)
    assert billing.total_cents(repository.get_session(1, 10)["expenses"]) == 5600
    send("/cancel")
    send("Another - 9")
    assert repository.get_session(1, 10)["status"] == "CANCELLED"
    assert billing.total_cents(repository.get_session(1, 10)["expenses"]) == 5600


def test_done_requires_a_session_with_expenses(send, services):
    send("/done")
    assert "start a bill" in services.telegram.sent[-1]["text"]
    send("/split")
    send("/done")
    assert "at least one" in services.telegram.sent[-1]["text"]


def test_group_split_links_to_private_chat_without_starting_a_group_session(send, services):
    send("/split", chat_id=-100)
    assert services.telegram.sent[-1]["keyboard"] == [[{
        "text": "Open split privately", "url": "https://t.me/ShearesTestBot?start=split",
    }]]
    assert repository.get_session(1, -100) is None
    send("/start split")
    assert repository.get_session(1, 10)["status"] == "COLLECTING"


@pytest.mark.parametrize("text", ["KFC - -20", "KFC - 0", "KFC - NaN", "KFC - 1.001", "KFC - 1e3"])
def test_text_expenses_reject_invalid_money(text):
    with pytest.raises(ValueError):
        billing.parse_expenses(text)


def test_final_message_escapes_expense_text(send, services):
    send("/start", user_id=2, chat_id=20, username="alice")
    send("/split")
    send("<b>KFC & Tea</b> - 20")
    send("/done")
    choose_equal(services)
    send("@alice")
    assert "&lt;b&gt;KFC &amp; Tea&lt;/b&gt; - 20.00" in saved_bill_message(services)["text"]
    assert "<b>Total: 20.00</b>" in saved_bill_message(services)["text"]


def test_tax_inclusive_receipts_use_net_total_without_adding_tax_again():
    receipt = service.Receipt(
        (service.ReceiptItem("Meal", "33.80"),), subtotal="33.80", tax="2.79", total="33.80",
    )
    assert receipt.subtotal == "31.01"
    assert billing.receipt_expenses([receipt])[0]["totalCents"] == 3380


def test_receipt_with_unreadable_total_and_tax_requires_manual_final_amount():
    receipt = service.Receipt((service.ReceiptItem("Meal", "10.00"),), tax="0.90")
    with pytest.raises(ValueError, match="final total"):
        billing.receipt_expenses([receipt])


def test_saved_receipt_summary_hides_zero_items_and_renumbers_remaining_items():
    receipt = {
        "items": [{"name": "Miscellaneous charges", "price": "0.00"},
                  {"name": "TOYOTAVIOS", "price": "81.75"}],
        "totalCents": 8175,
    }

    text = "\n".join(billing.receipt_lines(receipt, 1))

    assert "Miscellaneous charges" not in text
    assert "1. TOYOTAVIOS - 81.75" in text
    assert "Total: 81.75" in text
