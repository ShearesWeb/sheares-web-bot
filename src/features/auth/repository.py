
import hashlib
import time

from botocore.exceptions import ClientError
from core.db import users_table


class LinkConflictError(Exception):
    """Either the Telegram or resident account already has a link."""


def token_key(token):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    return {
        "userID": f"LINK#{token_hash}",
        "profile": "AUTH_REQUEST",
    }


def save_link_request(token, telegram_user_id, expires_at):
    users_table().put_item(
        Item={
            **token_key(token),
            "telegram_user_id": str(telegram_user_id),
            "expires_at": int(expires_at),
        },
        ConditionExpression=(
            "attribute_not_exists(userID) AND "
            "attribute_not_exists(profile)"
        ),
    )


def consume_link_request(token):
    now = int(time.time())

    try:
        response = users_table().delete_item(
            Key=token_key(token),
            ConditionExpression="expires_at > :now",
            ExpressionAttributeValues={":now": now},
            ReturnValues="ALL_OLD",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise

    item = response.get("Attributes")
    if item is None:
        return None

    return {"telegram_user_id": item["telegram_user_id"]}


def get_link_request(token):
    """Check whether a linking request is still valid.

    Unlike consume_link_request, this does not delete it.
    """
    response = users_table().get_item(
        Key=token_key(token),
        ConsistentRead=True,
    )

    item = response.get("Item")

    if item is None:
        return None

    if int(item["expires_at"]) <= int(time.time()):
        return None

    return {
        "telegram_user_id": item["telegram_user_id"],
    }


def telegram_link_key(telegram_user_id):
    return {
        "userID": f"USER#{telegram_user_id}",
        "profile": "INTRANET_LINK",
    }


def resident_link_key(resident_id):
    return {
        "userID": f"RESIDENT#{resident_id}",
        "profile": "TELEGRAM_LINK",
    }


def get_link_for_telegram(telegram_user_id):
    return users_table().get_item(
        Key=telegram_link_key(telegram_user_id),
        ConsistentRead=True,
    ).get("Item")


def get_link_for_resident(resident_id):
    return users_table().get_item(
        Key=resident_link_key(resident_id),
        ConsistentRead=True,
    ).get("Item")


def complete_link_request(token, resident_id):
    """Atomically consume a token and create a one-to-one account link."""
    request = get_link_request(token)
    if request is None:
        return None

    telegram_user_id = request["telegram_user_id"]
    if (
        get_link_for_telegram(telegram_user_id) is not None
        or get_link_for_resident(resident_id) is not None
    ):
        raise LinkConflictError

    table = users_table()
    now = int(time.time())

    try:
        table.meta.client.transact_write_items(
            TransactItems=[
                {
                    "Delete": {
                        "TableName": table.name,
                        "Key": token_key(token),
                        "ConditionExpression": (
                            "expires_at > :now AND telegram_user_id = :telegram"
                        ),
                        "ExpressionAttributeValues": {
                            ":now": now,
                            ":telegram": telegram_user_id,
                        },
                    }
                },
                {
                    "Put": {
                        "TableName": table.name,
                        "Item": {
                            **telegram_link_key(telegram_user_id),
                            "resident_id": str(resident_id),
                            "linked_at": now,
                        },
                        "ConditionExpression": (
                            "attribute_not_exists(userID) AND "
                            "attribute_not_exists(profile)"
                        ),
                    }
                },
                {
                    "Put": {
                        "TableName": table.name,
                        "Item": {
                            **resident_link_key(resident_id),
                            "telegram_user_id": str(telegram_user_id),
                            "linked_at": now,
                        },
                        "ConditionExpression": (
                            "attribute_not_exists(userID) AND "
                            "attribute_not_exists(profile)"
                        ),
                    }
                },
            ]
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "TransactionCanceledException":
            # A concurrent completion either consumed the token or claimed one
            # of the accounts. Re-read to return the most useful safe error.
            if get_link_request(token) is None:
                return None
            raise LinkConflictError from error
        raise

    return {
        "telegram_user_id": telegram_user_id,
        "resident_id": str(resident_id),
    }
