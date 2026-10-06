from boto3.dynamodb.conditions import Key

from core.db import laundry_table, users_table
from features.profile.repository import get_profile


def _tracker_key(user_id):
    return {"userID": f"USER#{user_id}", "profile": "LAUNDRY"}


def add_user_machine(user_id, machine_id):
    if not user_id or user_id == "NONE":
        return
    users_table().update_item(
        Key=_tracker_key(user_id),
        UpdateExpression="ADD machines :m",
        ExpressionAttributeValues={":m": {machine_id}},
    )


def remove_user_machine(user_id, machine_id):
    if not user_id or user_id == "NONE":
        return
    users_table().update_item(
        Key=_tracker_key(user_id),
        UpdateExpression="DELETE machines :m",
        ExpressionAttributeValues={":m": {machine_id}},
    )


def get_user_machines(user_id):
    return users_table().get_item(Key=_tracker_key(user_id)).get("Item", {}).get("machines", set())


def get_user_block(user_id):
    return (get_profile(user_id) or {}).get("block")


def get_machine(block, machine_id):
    return laundry_table().get_item(Key={"block": block, "machineID": machine_id}).get("Item")


def query_block(block):
    return laundry_table().query(KeyConditionExpression=Key("block").eq(block)).get("Items", [])


def put_machine(block, machine_id, user_id, chat_id, end_time):
    """Records a new IN_USE cycle, replacing whatever the row held."""
    laundry_table().put_item(Item={
        "block": block,
        "machineID": machine_id,
        "status": "IN_USE",
        "currentUser": str(user_id),
        "currentChatID": str(chat_id),
        "endTime": end_time,
        "type": "WASHER" if "W" in machine_id else "DRYER",
    })


def set_machine_state(block, machine_id, status, user, chat):
    laundry_table().update_item(
        Key={"block": block, "machineID": machine_id},
        UpdateExpression="SET #s = :s, currentUser = :u, currentChatID = :c, endTime = :e",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status, ":u": user, ":c": chat, ":e": 0},
    )


def get_notification_chat(machine):
    """Return the owner's recorded chat, including records written before currentChatID existed."""
    if not machine or machine.get("currentUser") in (None, "NONE"):
        return None
    if "currentChatID" in machine:
        chat_id = machine["currentChatID"]
        return None if chat_id in (None, "NONE") else chat_id
    # Legacy records stored the notification chat in currentUser.
    return machine.get("currentUser")
