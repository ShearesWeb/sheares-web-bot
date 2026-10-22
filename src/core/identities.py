"""Telegram identities observed in updates, indexed by current username and stable ID."""

from core.db import users_table


def get_user(user_id):
    return users_table().get_item(
        Key={"userID": f"USER#{user_id}", "profile": "IDENTITY"}, ConsistentRead=True,
    ).get("Item")


def remember_user(user):
    if user.get("is_bot"):
        return
    user_id = int(user["id"])
    username = user.get("username", "")
    users_table().put_item(Item={
        "userID": f"USER#{user_id}", "profile": "IDENTITY", "telegramID": str(user_id),
        "username": username, "firstName": user.get("first_name", ""),
    })
    if username:
        users_table().put_item(Item={
            "userID": f"USERNAME#{username.casefold()}", "profile": "IDENTITY", "telegramID": str(user_id),
        })


def resolve_username(username):
    name = username.lstrip("@").casefold()
    alias = users_table().get_item(
        Key={"userID": f"USERNAME#{name}", "profile": "IDENTITY"}, ConsistentRead=True,
    ).get("Item")
    if not alias:
        return None
    user = get_user(alias["telegramID"])
    # An old username must stop resolving as soon as its owner changes it.
    if user and user.get("username", "").casefold() == name:
        return user
    return None
