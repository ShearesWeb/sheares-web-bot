from uuid import uuid4

from features.split import billing, ledger


def save_bill():
    return ledger.save_bill({
        "ownerID": "1", "chatID": "10", "sessionID": uuid4().hex, "splitMethod": "equal",
        "expenses": billing.parse_expenses("Lunch - 10"), "totalCents": 1000,
        "participants": [{"userID": "2", "username": "alice", "amountCents": 1000}],
    })


def test_balances_batch_matching_pointers_and_preserve_paid_history(monkeypatch):
    unpaid, paid = save_bill(), save_bill()
    assert ledger.mark_paid(1, paid["sessionID"], 2, 2) == "paid"
    calls = []
    batch_read = ledger.batch_users_items

    def record(keys):
        calls.append(keys)
        return batch_read(keys)

    monkeypatch.setattr(ledger, "batch_users_items", record)
    assert [item["billID"] for item in ledger.debts(2, "owe")] == [unpaid["sessionID"]]
    assert calls[-1] == [ledger.bill_key(1, unpaid["sessionID"])]
    assert [item["billID"] for item in ledger.debts(2, "owe", paid=True)] == [paid["sessionID"]]
    assert calls[-1] == [ledger.bill_key(1, paid["sessionID"])]
    assert [item["billID"] for item in ledger.debts(1, "owed")] == [unpaid["sessionID"]]
    assert ledger.debts(1, "owed", paid=True)[0]["payment"]["paidBy"] == "2"


def test_payment_is_idempotent_and_requires_the_owner_or_debtor():
    bill = save_bill()
    assert ledger.mark_paid(1, bill["sessionID"], 2, 9) == "not_found"
    assert ledger.outstanding_shares(ledger.get_bill(1, bill["sessionID"])) == {"2": 1000}
    assert ledger.mark_paid(1, bill["sessionID"], 2, 1) == "paid"
    payment = ledger.get_bill(1, bill["sessionID"])["payments"]["2"]
    assert ledger.mark_paid(1, bill["sessionID"], 2, 2) == "already_paid"
    assert ledger.get_bill(1, bill["sessionID"])["payments"]["2"] == payment
