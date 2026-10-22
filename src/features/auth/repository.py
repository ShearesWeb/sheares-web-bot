
import hashlib
import time

from botocore.exceptions import ClientError
from core.db import users_table


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