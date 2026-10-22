"""Reminder setup requests and delivery state in the existing users table."""

import time
from uuid import uuid4

from botocore.exceptions import ClientError

from core.db import users_table
from features.split import ledger

SETUP_PROFILE = "SPLIT_REMINDER_SETUP"
SETUP_SECONDS = 15 * 60


def _setup_key(owner_id):
    return {"userID": f"USER#{owner_id}", "profile": SETUP_PROFILE}


def _update(key, **kwargs):
    try:
        return users_table().update_item(Key=key, ReturnValues="ALL_NEW", **kwargs)["Attributes"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise


def start_setup(owner_id, source_chat_id, bill_id, *, target_chat_id=None, title=""):
    setup = {
        **_setup_key(owner_id), "ownerID": str(owner_id), "sourceChatID": str(source_chat_id),
        "billID": bill_id, "token": uuid4().hex, "requestID": uuid4().int & 0x7fffffff,
        "status": "READY" if target_chat_id is not None else "GROUP",
        "expiresAt": int(time.time()) + SETUP_SECONDS,
    }
    if target_chat_id is not None:
        setup.update(targetChatID=str(target_chat_id), groupTitle=title)
    users_table().put_item(Item=setup)
    return setup


def get_setup(owner_id):
    item = users_table().get_item(Key=_setup_key(owner_id), ConsistentRead=True).get("Item")
    return item if item and item.get("expiresAt", 0) > int(time.time()) else None


def choose_group(owner_id, setup, chat_id, title):
    return _update(
        _setup_key(owner_id),
        UpdateExpression="SET #status = :ready, targetChatID = :chat, groupTitle = :title, expiresAt = :expires",
        ConditionExpression="#token = :token AND #status = :group AND expiresAt > :now",
        ExpressionAttributeNames={"#status": "status", "#token": "token"},
        ExpressionAttributeValues={
            ":token": setup["token"], ":group": "GROUP", ":ready": "READY", ":chat": str(chat_id),
            ":title": title, ":expires": int(time.time()) + SETUP_SECONDS, ":now": int(time.time()),
        },
    )


def claim_setup(owner_id, token):
    return _update(
        _setup_key(owner_id),
        UpdateExpression="SET #status = :configuring, #generation = :generation",
        ConditionExpression="#token = :token AND #status = :ready AND expiresAt > :now",
        ExpressionAttributeNames={"#status": "status", "#token": "token", "#generation": "generation"},
        ExpressionAttributeValues={
            ":token": token, ":ready": "READY", ":configuring": "CONFIGURING",
            ":generation": uuid4().hex, ":now": int(time.time()),
        },
    )


def release_setup(owner_id, setup):
    return _update(
        _setup_key(owner_id), UpdateExpression="SET #status = :ready",
        ConditionExpression="#token = :token AND #status = :configuring AND #generation = :generation",
        ExpressionAttributeNames={"#status": "status", "#token": "token", "#generation": "generation"},
        ExpressionAttributeValues={":token": setup["token"], ":generation": setup["generation"],
                                   ":configuring": "CONFIGURING", ":ready": "READY"},
    )


def activate(owner_id, setup, bill, reminder):
    names = {"#payments": "payments", "#reminder": "reminder"}
    unpaid_checks = []
    for index, user_id in enumerate(ledger.outstanding_shares(bill)):
        names[f"#person{index}"] = user_id
        unpaid_checks.append(f"attribute_not_exists(#payments.#person{index})")
    if not unpaid_checks:
        return False
    return ledger.transact([
        {"Update": {
            "TableName": users_table().name, "Key": ledger.bill_key(owner_id, setup["billID"]),
            "UpdateExpression": "SET #reminder = :reminder",
            "ConditionExpression": "attribute_exists(userID) AND (" + " OR ".join(unpaid_checks) + ")",
            "ExpressionAttributeNames": names, "ExpressionAttributeValues": {":reminder": reminder},
        }},
        {"Update": {
            "TableName": users_table().name, "Key": _setup_key(owner_id),
            "UpdateExpression": "SET #status = :complete",
            "ConditionExpression": "#token = :token AND #status = :configuring AND #generation = :generation AND expiresAt > :now",
            "ExpressionAttributeNames": {"#status": "status", "#token": "token", "#generation": "generation"},
            "ExpressionAttributeValues": {
                ":token": setup["token"], ":generation": setup["generation"], ":configuring": "CONFIGURING",
                ":complete": "COMPLETED", ":now": int(time.time()),
            },
        }},
    ])


def cancel_setup(owner_id, bill_id):
    return _update(
        _setup_key(owner_id), UpdateExpression="SET #status = :cancelled",
        ConditionExpression="billID = :bill AND attribute_exists(userID)",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":bill": bill_id, ":cancelled": "CANCELLED"},
    )


def replace_active(owner_id, bill_id, generation, reminder):
    """Upgrade an old schedule without overwriting a concurrent stop or setup."""
    return _update(
        ledger.bill_key(owner_id, bill_id), UpdateExpression="SET #r = :reminder",
        ConditionExpression="#r.#enabled = :yes AND #r.#generation = :generation",
        ExpressionAttributeNames={"#r": "reminder", "#enabled": "enabled", "#generation": "generation"},
        ExpressionAttributeValues={":yes": True, ":generation": generation, ":reminder": reminder},
    )


def disable(owner_id, bill_id, generation, reason):
    return _update(
        ledger.bill_key(owner_id, bill_id),
        UpdateExpression="SET #r.#enabled = :no, #r.stopReason = :reason, #r.stoppedAt = :now",
        ConditionExpression="#r.#generation = :generation",
        ExpressionAttributeNames={"#r": "reminder", "#enabled": "enabled", "#generation": "generation"},
        ExpressionAttributeValues={":no": False, ":generation": generation, ":reason": reason, ":now": int(time.time())},
    )


def _delivery_names(slot):
    # Initial posts keep their own claim so a failed scheduled delivery cannot
    # clear the record that the initial reminder has already been sent.
    return {"#r": "reminder", "#generation": "generation",
            "#slot": "initialSlot" if slot == -1 else "lastSlot"}


def claim_delivery(owner_id, bill_id, generation, slot):
    return _update(
        ledger.bill_key(owner_id, bill_id), UpdateExpression="SET #r.#slot = :slot",
        ConditionExpression=(
            "#r.#enabled = :yes AND #r.#generation = :generation "
            "AND (attribute_not_exists(#r.#slot) OR #r.#slot < :slot)"
        ),
        ExpressionAttributeNames={**_delivery_names(slot), "#enabled": "enabled"},
        ExpressionAttributeValues={":yes": True, ":generation": generation, ":slot": slot},
    )


def release_delivery(owner_id, bill_id, generation, slot):
    return _update(
        ledger.bill_key(owner_id, bill_id), UpdateExpression="REMOVE #r.#slot",
        ConditionExpression="#r.#generation = :generation AND #r.#slot = :slot",
        ExpressionAttributeNames=_delivery_names(slot),
        ExpressionAttributeValues={":generation": generation, ":slot": slot},
    )


def sent(owner_id, bill_id, generation, slot):
    expression = "SET #r.lastSentAt = :now"
    if slot == -1:
        expression += ", #r.initialSentAt = :now"
    return _update(
        ledger.bill_key(owner_id, bill_id), UpdateExpression=expression,
        ConditionExpression="#r.#generation = :generation AND #r.#slot = :slot",
        ExpressionAttributeNames=_delivery_names(slot),
        ExpressionAttributeValues={":generation": generation, ":slot": slot, ":now": int(time.time())},
    )
