from functools import cache

import boto3

from core.config import settings


@cache
def _dynamodb():
    return boto3.resource("dynamodb")


def users_table():
    return _dynamodb().Table(settings().users_table_name)


def laundry_table():
    return _dynamodb().Table(settings().laundry_table_name)
