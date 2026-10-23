"""Canonical per-bill settlements, with per-user pointers for payable queries."""

import time

import boto3
from boto3.dynamodb.conditions import Attr, Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from core.db import batch_users_items, users_table

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
        "createdAt": session.get("createdAt", int(time.time())),
        "allocation": session.get("allocation", {}),
        "shares": _shares(session), "payments": {},
    }


def _pointer_writes(bill):
    return [{"Put": {"TableName": users_table().name, "Item": {
        "userID": f"USER#{debtor_id}", "profile": PAYABLE_PREFIX + bill["sessionID"],
        "ownerID": str(bill["ownerID"]), "billID": bill["sessionID"],
        "settled": False,
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


def save_bill(session):
    existing = get_bill(session["ownerID"], session["sessionID"])
    if existing:
        return existing
    bill = snapshot(session)
    if transact(new_bill_writes(bill)):
        return bill
    existing = get_bill(session["ownerID"], session["sessionID"])
    if existing:
        return existing
    raise RuntimeError("The bill could not be saved.")


def _query(user_id, prefix, *, filter_expression=None):
    params = {
        "KeyConditionExpression": Key("userID").eq(f"USER#{user_id}") & Key("profile").begins_with(prefix),
        "ConsistentRead": True,
    }
    if filter_expression is not None:
        params["FilterExpression"] = filter_expression
    while True:
        page = users_table().query(**params)
        yield from page.get("Items", [])
        if not page.get("LastEvaluatedKey"):
            break
        params["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def debts(user_id, direction, *, paid=False):
    if direction == "owed":
        bills = _query(user_id, BILL_PREFIX)
    elif direction == "owe":
        pointers = _query(user_id, PAYABLE_PREFIX, filter_expression=Attr("settled").eq(paid))
        keys = {(pointer["ownerID"], pointer["billID"]) for pointer in pointers}
        bills = batch_users_items([bill_key(owner_id, bill_id) for owner_id, bill_id in keys])
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
    return list(_query(owner_id, BILL_PREFIX))


def find_owned_bill(owner_id, reference):
    matches = list(_query(owner_id, BILL_PREFIX + reference))
    if len(matches) > 1:
        raise ValueError("More than one bill matches. Choose the bill through /bills and open its options.")
    return matches[0] if matches else None


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
    amount = bill.get("shares", {}).get(debtor_id)
    if amount is None:
        return "not_found"
    if bill.get("payments", {}).get(debtor_id):
        return "already_paid"
    saved = transact([
        {"Update": {
            "TableName": users_table().name, "Key": bill_key(owner_id, bill_id),
            "UpdateExpression": "SET #payments.#person = :payment",
            "ConditionExpression": "#shares.#person = :amount AND attribute_not_exists(#payments.#person)",
            "ExpressionAttributeNames": {"#payments": "payments", "#shares": "shares", "#person": debtor_id},
            "ExpressionAttributeValues": {
                ":amount": amount,
                ":payment": {"amountCents": int(amount), "paidAt": int(time.time()), "paidBy": actor_id},
            },
        }},
        {"Put": {"TableName": users_table().name, "Item": {
            "userID": f"USER#{debtor_id}", "profile": PAYABLE_PREFIX + bill_id,
            "ownerID": owner_id, "billID": bill_id, "settled": True,
        }}},
    ])
    return "paid" if saved else "already_paid"
