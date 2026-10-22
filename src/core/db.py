import os
from functools import cache

import boto3


@cache
def _dynamodb():
    return boto3.resource("dynamodb")


def users_table():
    return _dynamodb().Table(os.environ["USERS_TABLE_NAME"])


def laundry_table():
    return _dynamodb().Table(os.environ["LAUNDRY_TABLE_NAME"])
