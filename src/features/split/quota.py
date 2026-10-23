"""A daily Textract allowance per Telegram user, shared by all split sessions."""

from datetime import datetime, timedelta, timezone
import time

from botocore.exceptions import ClientError

from core.db import users_table

DAILY_LIMIT = 20
SINGAPORE = timezone(timedelta(hours=8), "Asia/Singapore")


def _update(user_id, **kwargs):
    try:
        users_table().update_item(
            Key={"userID": f"USER#{user_id}", "profile": "SPLIT_OCR_QUOTA"},
            ExpressionAttributeNames={"#day": "day", "#used": "used"}, **kwargs,
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return False


def reserve_scan(user_id):
    """Reserve before downloading; failed downloads and OCR attempts also count."""
    day = datetime.fromtimestamp(time.time(), SINGAPORE).date().isoformat()
    increment = {
        "UpdateExpression": "SET #used = #used + :one",
        "ConditionExpression": "#day = :day AND #used < :limit",
        "ExpressionAttributeValues": {":day": day, ":limit": DAILY_LIMIT, ":one": 1},
    }
    if _update(user_id, **increment):
        return
    if _update(
        user_id, UpdateExpression="SET #day = :day, #used = :one",
        ConditionExpression="attribute_not_exists(#day) OR #day < :day",
        ExpressionAttributeValues={":day": day, ":one": 1},
    ):
        return
    # Another request may have initialized today's counter between our writes.
    if _update(user_id, **increment):
        return
    raise ValueError(
        f"You've reached today's limit of {DAILY_LIMIT} receipt scans. "
        "Try again after 12:00 am Singapore time, or add expenses as text."
    )
