"""Canonical per-bill settlements, with per-user pointers for payable queries."""

import time

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from core.db import users_table

BILL_PREFIX = "SPLIT_BILL#"
PAYABLE_PREFIX = "SPLIT_PAYABLE#"


def bill_key(owner_id, bill_id):
    return {"userID": f"USER#{owner_id}", "profile": BILL_PREFIX + bill_id}


def get_bill(owner_id, bill_id):
    return users_table().get_item(Key=bill_key(owner_id, bill_id), ConsistentRead=True).get("Item")


def _shares(bill):
    return {
        str(person["userID"]): int(person["amountCents"])
        for person in bill["participants"]
        if str(person["userID"]) != str(bill["ownerID"]) and int(person["amountCents"]) > 0
    }


def snapshot(session):
    return {
        **bill_key(session["ownerID"], session["sessionID"]),
        **{key: session[key] for key in ("sessionID", "ownerID", "chatID", "expenses", "participants", "totalCents", "splitMethod")},
        "createdAt": session.get("createdAt", int(time.time())), "ledgerVersion": 1,
        "allocation": session.get("allocation", {}),
        "shares": _shares(session), "payments": {},
    }


def _pointer_writes(bill):
    return [{"Put": {"TableName": users_table().name, "Item": {
        "userID": f"USER#{debtor_id}", "profile": PAYABLE_PREFIX + bill["sessionID"],
        "ownerID": str(bill["ownerID"]), "billID": bill["sessionID"],
    }}} for debtor_id in _shares(bill)]


def new_bill_writes(bill):
    return [{"Put": {
        "TableName": users_table().name, "Item": bill, "ConditionExpression": "attribute_not_exists(userID)",
    }}, *_pointer_writes(bill)]


def transact(writes):
    serializer = TypeSerializer()
    encoded = []
    for write in writes:
        kind, operation = next(iter(write.items()))
        operation = dict(operation)
        for field in ("Item", "Key", "ExpressionAttributeValues"):
            if field in operation:
                operation[field] = {key: serializer.serialize(value) for key, value in operation[field].items()}
        encoded.append({kind: operation})
    try:
        boto3.client("dynamodb").transact_write_items(TransactItems=encoded)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "TransactionCanceledException" and any(
            reason.get("Code") == "ConditionalCheckFailed" for reason in exc.response.get("CancellationReasons", [])
        ):
            return False
        raise


def index_bill(bill):
    """Backfill an older snapshot without replacing expenses or settled payments."""
    if bill.get("ledgerVersion") == 1:
        return bill
    writes = [{"Update": {
        "TableName": users_table().name, "Key": bill_key(bill["ownerID"], bill["sessionID"]),
        "UpdateExpression": "SET #version = :version, #shares = :shares, #payments = if_not_exists(#payments, :empty)",
        "ConditionExpression": "attribute_exists(userID)",
        "ExpressionAttributeNames": {"#version": "ledgerVersion", "#shares": "shares", "#payments": "payments"},
        "ExpressionAttributeValues": {":version": 1, ":shares": _shares(bill), ":empty": {}},
    }}, *_pointer_writes(bill)]
    transact(writes)
    return get_bill(bill["ownerID"], bill["sessionID"])


def save_bill(session):
    existing = get_bill(session["ownerID"], session["sessionID"])
    if existing:
        return index_bill(existing)
    bill = snapshot(session)
    if transact(new_bill_writes(bill)):
        return bill
    existing = get_bill(session["ownerID"], session["sessionID"])
    if existing:
        return index_bill(existing)
    raise RuntimeError("The bill could not be saved.")


def _query(user_id, prefix):
    params = {
        "KeyConditionExpression": Key("userID").eq(f"USER#{user_id}") & Key("profile").begins_with(prefix),
        "ConsistentRead": True,
    }
    while True:
        page = users_table().query(**params)
        yield from page.get("Items", [])
        if not page.get("LastEvaluatedKey"):
            break
        params["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def debts(user_id, direction, *, paid=False):
    if direction == "owed":
        bills = (index_bill(bill) for bill in _query(user_id, BILL_PREFIX))
    elif direction == "owe":
        bills = (get_bill(pointer["ownerID"], pointer["billID"]) for pointer in _query(user_id, PAYABLE_PREFIX))
    else:
        raise ValueError("Unknown balance direction.")
    result = []
    for bill in bills:
        if not bill:
            continue
        for debtor_id, amount in bill.get("shares", {}).items():
            if direction == "owe" and debtor_id != str(user_id):
                continue
            payment = bill.get("payments", {}).get(debtor_id)
            if bool(payment) != paid:
                continue
            result.append({
                "billID": bill["sessionID"], "ownerID": str(bill["ownerID"]), "debtorID": debtor_id,
                "amountCents": int(amount), "payment": payment,
                "expenses": bill.get("expenses", []),
            })
    return sorted(result, key=lambda item: (item["billID"], item["debtorID"]))


def payable_owner(debtor_id, bill_id):
    pointer = users_table().get_item(
        Key={"userID": f"USER#{debtor_id}", "profile": PAYABLE_PREFIX + bill_id}, ConsistentRead=True,
    ).get("Item")
    return pointer["ownerID"] if pointer else None


def find_payable(debtor_id, reference):
    matches = list(_query(debtor_id, PAYABLE_PREFIX + reference))
    if len(matches) > 1:
        raise ValueError("More than one bill matches. Use a full bill ID:\n" + "\n".join(item["billID"] for item in matches))
    return matches[0] if matches else None


def owned_bills(owner_id):
    return [index_bill(bill) for bill in _query(owner_id, BILL_PREFIX)]


def find_owned_bill(owner_id, reference):
    matches = list(_query(owner_id, BILL_PREFIX + reference))
    if len(matches) > 1:
        raise ValueError("More than one bill matches. Choose the bill through /bills and open its options.")
    return index_bill(matches[0]) if matches else None


def outstanding_shares(bill):
    payments = bill.get("payments", {})
    return {str(user_id): int(amount) for user_id, amount in bill.get("shares", {}).items()
            if int(amount) > 0 and not payments.get(str(user_id))}


def mark_paid(owner_id, bill_id, debtor_id, actor_id):
    owner_id, debtor_id, actor_id = str(owner_id), str(debtor_id), str(actor_id)
    if actor_id not in {owner_id, debtor_id} or owner_id == debtor_id:
        return "not_found"
    bill = get_bill(owner_id, bill_id)
    if not bill:
        return "not_found"
    bill = index_bill(bill)
    if not bill:
        return "not_found"
    amount = bill.get("shares", {}).get(debtor_id)
    if amount is None:
        return "not_found"
    try:
        users_table().update_item(
            Key=bill_key(owner_id, bill_id),
            UpdateExpression="SET #payments.#person = :payment",
            ConditionExpression="#shares.#person = :amount AND attribute_not_exists(#payments.#person)",
            ExpressionAttributeNames={"#payments": "payments", "#shares": "shares", "#person": debtor_id},
            ExpressionAttributeValues={
                ":amount": amount,
                ":payment": {"amountCents": int(amount), "paidAt": int(time.time()), "paidBy": actor_id},
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return "already_paid"
        raise
    return "paid"
