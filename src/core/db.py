import os
from functools import cache
import time

import boto3


@cache
def _dynamodb():
    return boto3.resource("dynamodb")


def users_table():
    return _dynamodb().Table(os.environ["USERS_TABLE_NAME"])


def batch_users_items(keys):
    """Read user records consistently in batches of at most 100 keys."""
    table_name = os.environ["USERS_TABLE_NAME"]
    items = []
    for offset in range(0, len(keys), 100):
        request = {table_name: {"Keys": keys[offset:offset + 100], "ConsistentRead": True}}
        for attempt in range(5):
            response = _dynamodb().batch_get_item(RequestItems=request)
            items.extend(response.get("Responses", {}).get(table_name, []))
            request = response.get("UnprocessedKeys", {})
            if not request:
                break
            if attempt == 4:
                raise RuntimeError("I couldn't load all bills. Please try /bills again.")
            time.sleep(0.05 * 2 ** attempt)
    return items


def laundry_table():
    return _dynamodb().Table(os.environ["LAUNDRY_TABLE_NAME"])
