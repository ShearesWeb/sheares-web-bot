import time
from uuid import uuid4

from botocore.exceptions import ClientError

from core.db import users_table
from features.split import allocation, ledger

SESSION_SECONDS = 15 * 60
PROCESSING_SECONDS = 90
EDITABLE_STATUSES = ("COLLECTING", "AWAITING_METHOD", "AWAITING_PARTICIPANTS", "AWAITING_ALLOCATION")


def _key(user_id, chat_id):
    return {"userID": f"USER#{user_id}", "profile": f"SPLIT#{chat_id}"}


def _update(user_id, chat_id, **kwargs):
    try:
        return users_table().update_item(Key=_key(user_id, chat_id), ReturnValues="ALL_NEW", **kwargs)["Attributes"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise


def start_session(user_id, chat_id):
    session = {
        **_key(user_id, chat_id), "sessionID": uuid4().hex, "status": "COLLECTING",
        "ownerID": str(user_id), "chatID": str(chat_id), "expenses": [], "processedUpdates": [],
        "expiresAt": int(time.time()) + SESSION_SECONDS,
    }
    users_table().put_item(Item=session)
    return session


def is_expired(session):
    return session.get("expiresAt", 0) <= int(time.time())


def get_session(user_id, chat_id, *, include_expired=False):
    item = users_table().get_item(Key=_key(user_id, chat_id), ConsistentRead=True).get("Item")
    if item and "expenses" in item and (include_expired or not is_expired(item)):
        return item
    return None


def can_collect(session):
    return session and (
        session["status"] == "COLLECTING"
        or (session["status"] == "PROCESSING" and session.get("processingUntil", 0) <= int(time.time()))
    )


def claim_input(user_id, chat_id, event_id):
    now = int(time.time())
    return _update(
        user_id, chat_id,
        UpdateExpression="SET #status = :processing, processingToken = :token, processingUntil = :until, processingEvent = :event",
        ConditionExpression=(
            "(#status = :collecting OR (#status = :processing AND processingUntil <= :now)) "
            "AND expiresAt > :now AND NOT contains(processedUpdates, :event)"
        ),
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":collecting": "COLLECTING", ":processing": "PROCESSING", ":now": now,
            ":event": str(event_id), ":token": uuid4().hex, ":until": now + PROCESSING_SECONDS,
        },
    )


def recover_input(user_id, chat_id, session_id):
    return _update(
        user_id, chat_id, UpdateExpression="SET #status = :collecting",
        ConditionExpression="sessionID = :session AND #status = :processing AND processingUntil <= :now AND expiresAt > :now",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":collecting": "COLLECTING", ":processing": "PROCESSING", ":session": session_id, ":now": int(time.time()),
        },
    )


def finish_input(user_id, chat_id, claim, event_id, *, expenses=None):
    now = int(time.time())
    expression = "SET #status = :collecting"
    values = {
        ":collecting": "COLLECTING", ":processing": "PROCESSING", ":session": claim["sessionID"],
        ":token": claim["processingToken"], ":now": now,
    }
    if expenses is not None:
        expression += ", expenses = list_append(expenses, :expenses), processedUpdates = list_append(processedUpdates, :events)"
        values.update({":expenses": expenses, ":events": [str(event_id)]})
    if expenses:
        # Text expenses and extracted receipt items share this deadline.
        # Failed or empty additions must not keep an unfinished bill open.
        expression += ", expiresAt = :expires REMOVE deletionToken, deletionChoices, deletionRevision"
        values[":expires"] = now + SESSION_SECONDS
    return _update(
        user_id, chat_id, UpdateExpression=expression,
        ConditionExpression="sessionID = :session AND processingToken = :token AND #status = :processing AND expiresAt > :now",
        ExpressionAttributeNames={"#status": "status"}, ExpressionAttributeValues=values,
    )


def begin_deletion(user_id, chat_id, session):
    if session["status"] not in EDITABLE_STATUSES:
        return None
    return _update(
        user_id, chat_id,
        UpdateExpression="SET deletionToken = :token, deletionChoices = :choices, deletionRevision = :zero",
        ConditionExpression=(
            "sessionID = :session AND #status = :status AND expenses = :expenses AND expiresAt > :now"
        ),
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":session": session["sessionID"], ":status": session["status"], ":expenses": session["expenses"],
            ":now": int(time.time()), ":token": uuid4().hex, ":choices": {}, ":zero": 0,
        },
    )


def set_deletion_choice(user_id, chat_id, session, option_index, *, selected, expected_state):
    if (session["status"] not in EDITABLE_STATUSES or not session.get("deletionToken")
            or option_index < 0 or expected_state < 0 or bool(expected_state % 2) == selected):
        return None
    path = "#choices.#option"
    state_condition = f"(attribute_not_exists({path}) OR {path} = :expected)" if expected_state == 0 else f"{path} = :expected"
    return _update(
        user_id, chat_id,
        UpdateExpression=f"SET deletionRevision = deletionRevision + :one, {path} = :next",
        ConditionExpression=(
            "sessionID = :session AND deletionToken = :token AND #status = :status "
            f"AND expiresAt > :now AND {state_condition}"
        ),
        ExpressionAttributeNames={"#status": "status", "#choices": "deletionChoices", "#option": str(option_index)},
        ExpressionAttributeValues={
            ":session": session["sessionID"], ":token": session["deletionToken"], ":status": session["status"],
            ":now": int(time.time()), ":expected": expected_state, ":next": expected_state + 1, ":one": 1,
        },
    )


def delete_expenses(user_id, chat_id, session, expenses):
    if session["status"] not in EDITABLE_STATUSES or not session.get("deletionToken"):
        return None
    return _update(
        user_id, chat_id,
        UpdateExpression=(
            "SET expenses = :expenses, #status = :nextStatus "
            "REMOVE deletionToken, deletionChoices, deletionRevision, selectionToken, requestedHandles, allocationUsers, splitMethod, "
            "itemChoices, itemRevision, requestID"
        ),
        ConditionExpression=(
            "sessionID = :session AND deletionToken = :token AND #status = :status "
            "AND deletionRevision = :revision AND expenses = :previous AND expiresAt > :now"
        ),
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":session": session["sessionID"], ":token": session["deletionToken"], ":status": session["status"],
            ":previous": session["expenses"], ":expenses": expenses,
            ":nextStatus": "COLLECTING" if expenses else "CANCELLED",
            ":now": int(time.time()), ":revision": session["deletionRevision"],
        },
    )


def cancel_deletion(user_id, chat_id, session):
    return _update(
        user_id, chat_id, UpdateExpression="REMOVE deletionToken, deletionChoices, deletionRevision",
        ConditionExpression="sessionID = :session AND deletionToken = :token AND expiresAt > :now",
        ExpressionAttributeValues={
            ":session": session["sessionID"], ":token": session["deletionToken"], ":now": int(time.time()),
        },
    )


def begin_method(user_id, chat_id, session_id):
    return _update(
        user_id, chat_id,
        UpdateExpression=(
            "SET #status = :method, selectionToken = :selection "
            "REMOVE requestedHandles, allocationUsers, splitMethod, itemChoices, itemRevision"
        ),
        ConditionExpression=(
            "sessionID = :session AND #status IN (:collecting, :method, :participants, :allocation) "
            "AND size(expenses) > :zero AND expiresAt > :now"
        ),
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":session": session_id, ":collecting": "COLLECTING", ":method": "AWAITING_METHOD",
            ":participants": "AWAITING_PARTICIPANTS", ":allocation": "AWAITING_ALLOCATION",
            ":selection": uuid4().hex, ":zero": 0, ":now": int(time.time()),
        },
    )


def begin_participants(user_id, chat_id, session_id, *, method="equal", selection_token=None):
    if method not in allocation.METHODS:
        raise ValueError("Unknown split method.")
    condition = "sessionID = :session AND #status = :previous AND size(expenses) > :zero AND expiresAt > :now"
    values = {
        ":session": session_id, ":previous": "AWAITING_METHOD" if selection_token else "COLLECTING",
        ":participants": "AWAITING_PARTICIPANTS", ":method": method,
        ":request": uuid4().int & 0x7fffffff, ":zero": 0, ":now": int(time.time()),
        ":selection": uuid4().hex,
    }
    if selection_token:
        condition += " AND selectionToken = :previousSelection"
        values[":previousSelection"] = selection_token
    return _update(
        user_id, chat_id,
        UpdateExpression="SET #status = :participants, splitMethod = :method, requestID = :request, selectionToken = :selection",
        ConditionExpression=condition,
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues=values,
    )


def set_handles(user_id, chat_id, session_id, handles):
    return _update(
        user_id, chat_id, UpdateExpression="SET requestedHandles = :handles, selectionToken = :selection",
        ConditionExpression="sessionID = :session AND #status = :participants AND expiresAt > :now",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":session": session_id, ":participants": "AWAITING_PARTICIPANTS", ":handles": handles,
            ":selection": uuid4().hex,
            ":now": int(time.time()),
        },
    )


def begin_allocation(user_id, chat_id, session_id, users, selection_token):
    return _update(
        user_id, chat_id,
        UpdateExpression="SET #status = :allocation, allocationUsers = :users, selectionToken = :selection",
        ConditionExpression="sessionID = :session AND selectionToken = :previousSelection AND #status = :participants AND expiresAt > :now",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":session": session_id, ":previousSelection": selection_token, ":participants": "AWAITING_PARTICIPANTS",
            ":allocation": "AWAITING_ALLOCATION", ":users": users, ":selection": uuid4().hex,
            ":now": int(time.time()),
        },
    )


def prepare_item_choices(user_id, chat_id, session):
    choices = {str(item["number"]): {} for item in allocation.item_catalog(session["expenses"])}
    return _update(
        user_id, chat_id,
        UpdateExpression=(
            "SET itemChoices = if_not_exists(itemChoices, :choices), "
            "itemRevision = if_not_exists(itemRevision, :zero)"
        ),
        ConditionExpression=(
            "sessionID = :session AND selectionToken = :selection AND #status = :allocation "
            "AND splitMethod = :item AND expiresAt > :now"
        ),
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":session": session["sessionID"], ":selection": session["selectionToken"],
            ":allocation": "AWAITING_ALLOCATION", ":item": "item", ":choices": choices, ":zero": 0,
            ":now": int(time.time()),
        },
    )


def set_item_choice(user_id, chat_id, session, person_id, item_number, *, selected, expected_state):
    people = {str(user["telegramID"]) for user in session["allocationUsers"]}
    if str(person_id) not in people or str(item_number) not in session.get("itemChoices", {}):
        raise ValueError("Choose a person and item from this bill.")
    if expected_state < 0 or bool(expected_state % 2) == selected:
        raise ValueError("Use the current item checkbox.")
    path = "#choices.#number.#person"
    # Odd states are checked; even states are unchecked. Retaining the state
    # after unchecking lets us reject older callbacks even after later clicks.
    expression = f"SET itemRevision = itemRevision + :one, {path} = :next"
    values = {
        ":session": session["sessionID"], ":selection": session["selectionToken"],
        ":allocation": "AWAITING_ALLOCATION", ":item": "item", ":one": 1,
        ":now": int(time.time()),
        ":expected": expected_state, ":next": expected_state + 1,
    }
    state_condition = f"(attribute_not_exists({path}) OR {path} = :expected)" if expected_state == 0 else f"{path} = :expected"
    return _update(
        user_id, chat_id, UpdateExpression=expression,
        ConditionExpression=(
            "sessionID = :session AND selectionToken = :selection AND #status = :allocation "
            "AND splitMethod = :item AND expiresAt > :now AND "
            + state_condition
        ),
        ExpressionAttributeNames={
            "#status": "status", "#choices": "itemChoices", "#number": str(item_number), "#person": str(person_id),
        },
        ExpressionAttributeValues=values,
    )


def complete(user_id, chat_id, session_id, participants, total_cents, selection_token, *, details=None, item_revision=None):
    session = get_session(user_id, chat_id)
    if not session or session["sessionID"] != session_id:
        return None
    method = session.get("splitMethod", "equal")
    if method not in allocation.METHODS:
        raise ValueError("Unknown split method.")
    expected_status = "AWAITING_PARTICIPANTS" if method == "equal" else "AWAITING_ALLOCATION"
    if session["status"] != expected_status or session.get("selectionToken") != selection_token:
        return None
    if method == "item" and item_revision != session.get("itemRevision", 0):
        return None
    if method != "equal" and (
        [str(person["userID"]) for person in participants]
        != [str(user["telegramID"]) for user in session.get("allocationUsers", [])]
    ):
        raise ValueError("Use the selected participants for this bill.")
    if (
        total_cents <= 0
        or total_cents != sum(int(batch["totalCents"]) for batch in session["expenses"])
        or len({str(person["userID"]) for person in participants}) != len(participants)
        or not 1 <= len(participants) <= 50
        or any(person["amountCents"] != int(person["amountCents"]) or int(person["amountCents"]) < 0 for person in participants)
        or sum(int(person["amountCents"]) for person in participants) != total_cents
    ):
        raise ValueError("The participant shares must add up to the bill total.")
    now = int(time.time())
    completed = {**session, "status": "COMPLETED", "participants": participants,
                 "totalCents": total_cents, "splitMethod": method, "allocation": details or {}, "createdAt": now}
    condition = "sessionID = :session AND selectionToken = :selection AND #status = :awaiting AND expiresAt > :now"
    values = {
        ":completed": "COMPLETED", ":awaiting": expected_status, ":session": session_id,
        ":people": participants, ":total": total_cents, ":now": now,
        ":selection": selection_token, ":method": method, ":allocation": details or {},
    }
    if method == "item":
        condition += " AND itemRevision = :revision"
        values[":revision"] = item_revision
    writes = [{"Update": {
        "TableName": users_table().name, "Key": _key(user_id, chat_id),
        "UpdateExpression": "SET #status = :completed, participants = :people, totalCents = :total, splitMethod = :method, #allocation = :allocation, createdAt = :now",
        "ConditionExpression": condition,
        "ExpressionAttributeNames": {"#status": "status", "#allocation": "allocation"},
        "ExpressionAttributeValues": values,
    }}, *ledger.new_bill_writes(ledger.snapshot(completed))]
    return completed if ledger.transact(writes) else None


def save_bill(session):
    return ledger.save_bill(session)


def cancel_session(user_id, chat_id):
    return _update(
        user_id, chat_id, UpdateExpression="SET #status = :cancelled",
        ConditionExpression="attribute_exists(sessionID) AND #status <> :completed",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":cancelled": "CANCELLED", ":completed": "COMPLETED"},
    )
