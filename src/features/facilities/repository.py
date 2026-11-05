import time

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from core.db import facilities_table, users_table
from features.profile.repository import get_profile

FACILITY_PROFILE = "FACILITY"
PENDING_PROFILE = "FACILITY_PENDING"
BOOKING_KEY_PREFIX = "BOOKING#"
PENDING_TTL_SECONDS = 15 * 60


def _user_key(user_id, profile):
    return {"userID": f"USER#{user_id}", "profile": profile}


def get_user_block(user_id):
    return (get_profile(user_id) or {}).get("block")


def list_block_items(block):
    return facilities_table().query(KeyConditionExpression=Key("block").eq(block)).get("Items", [])


def get_facility(block, facility_id):
    return facilities_table().get_item(
        Key={"block": block, "facilityID": facility_id},
    ).get("Item")


def get_booking(block, facility_id):
    return facilities_table().get_item(
        Key={"block": block, "facilityID": f"{BOOKING_KEY_PREFIX}{facility_id}"},
    ).get("Item")


def put_booking(booking, *, override, now):
    kwargs = {
        "Item": booking,
        "ReturnValues": "ALL_OLD",
    }
    if override:
        # Only replace a current reservation. A stale override button cannot
        # replace a booking that has already ended.
        kwargs.update(
            ConditionExpression="#status = :in_use",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":in_use": "IN_USE"},
        )
    else:
        # The conditional write makes simultaneous /book requests race safely.
        kwargs.update(
            ConditionExpression="attribute_not_exists(endTime) OR endTime <= :now OR #status = :free",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":now": now, ":free": "FREE"},
        )

    try:
        response = facilities_table().put_item(
            **kwargs,
        )
        return True, response.get("Attributes")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False, None
        raise


def put_user_booking(user_id, booking):
    booking_key = f"{booking['block']}#{booking['facilityID']}"
    users_table().update_item(
        Key=_user_key(user_id, FACILITY_PROFILE),
        UpdateExpression="SET #bookings = if_not_exists(#bookings, :empty)",
        ExpressionAttributeNames={"#bookings": "bookings"},
        ExpressionAttributeValues={":empty": {}},
    )
    users_table().update_item(
        Key=_user_key(user_id, FACILITY_PROFILE),
        UpdateExpression="SET #bookings.#booking_key = :booking",
        ExpressionAttributeNames={"#bookings": "bookings", "#booking_key": booking_key},
        ExpressionAttributeValues={":booking": {
            "bookingID": booking["bookingID"],
            "block": booking["block"],
            "facilityID": booking["facilityID"].removeprefix(BOOKING_KEY_PREFIX),
            "facilityName": booking["facilityName"],
            "startTime": booking["startTime"],
            "endTime": booking["endTime"],
            "chatID": booking["currentChatID"],
        }},
    )


def remove_user_booking(user_id, block, facility_id, booking_id):
    booking_key = f"{block}#{facility_id}"
    try:
        users_table().update_item(
            Key=_user_key(user_id, FACILITY_PROFILE),
            UpdateExpression="REMOVE #bookings.#booking_key",
            ConditionExpression="#bookings.#booking_key.bookingID = :booking_id",
            ExpressionAttributeNames={"#bookings": "bookings", "#booking_key": booking_key},
            ExpressionAttributeValues={":booking_id": booking_id},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise


def set_pending(user_id, chat_id, block, facility_id, facility_name, override, request_id, now):
    users_table().put_item(Item={
        **_user_key(user_id, PENDING_PROFILE),
        "chatID": str(chat_id),
        "block": block,
        "facilityID": facility_id,
        "facilityName": facility_name,
        "override": bool(override),
        "requestID": request_id,
        "step": "START",
        "expires_at": now + PENDING_TTL_SECONDS,
    })


def set_pending_end(user_id, start_time):
    users_table().update_item(
        Key=_user_key(user_id, PENDING_PROFILE),
        UpdateExpression="SET #step = :step, startTime = :start_time",
        ExpressionAttributeNames={"#step": "step"},
        ExpressionAttributeValues={":step": "END", ":start_time": start_time},
    )


def set_pending_confirmation(user_id, end_time):
    users_table().update_item(
        Key=_user_key(user_id, PENDING_PROFILE),
        UpdateExpression="SET #step = :step, endTime = :end_time",
        ExpressionAttributeNames={"#step": "step"},
        ExpressionAttributeValues={":step": "CONFIRM", ":end_time": end_time},
    )


def get_pending(user_id, now=None):
    pending = users_table().get_item(
        Key=_user_key(user_id, PENDING_PROFILE),
    ).get("Item")
    if pending and int(pending.get("expires_at", 0)) <= (now or int(time.time())):
        clear_pending(user_id)
        return None
    return pending


def clear_pending(user_id):
    users_table().delete_item(Key=_user_key(user_id, PENDING_PROFILE))


def finish_booking(block, facility_id, booking_id):
    try:
        return facilities_table().update_item(
            Key={"block": block, "facilityID": f"{BOOKING_KEY_PREFIX}{facility_id}"},
            UpdateExpression="SET #status = :free, currentUser = :none, currentChatID = :none",
            ConditionExpression="#status = :in_use AND bookingID = :booking_id",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":free": "FREE",
                ":in_use": "IN_USE",
                ":none": "NONE",
                ":booking_id": booking_id,
            },
            ReturnValues="ALL_OLD",
        ).get("Attributes")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return None
        raise
