import boto3
import pytest
from moto import mock_aws

from core import db
from core.config import settings
from core.context import Services
from support import FakeTelegram, RecordingScheduler

TEST_ENV = {
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "ap-southeast-1",
    "TELEGRAM_BOT_TOKEN": "test-token",
    "USERS_TABLE_NAME": "users",
    "LAUNDRY_TABLE_NAME": "laundry",
}


@pytest.fixture(autouse=True)
def aws(monkeypatch):
    """Fresh moto tables and env per test, so nothing reaches real AWS or depends on a developer's .env."""
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    settings.cache_clear()
    db._dynamodb.cache_clear()
    with mock_aws():
        dynamodb = boto3.resource("dynamodb")
        for name, hash_key, range_key in (("users", "userID", "profile"), ("laundry", "block", "machineID")):
            dynamodb.create_table(
                TableName=name,
                BillingMode="PAY_PER_REQUEST",
                KeySchema=[
                    {"AttributeName": hash_key, "KeyType": "HASH"},
                    {"AttributeName": range_key, "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": hash_key, "AttributeType": "S"},
                    {"AttributeName": range_key, "AttributeType": "S"},
                ],
            )
        yield
    settings.cache_clear()
    db._dynamodb.cache_clear()


@pytest.fixture
def services():
    return Services(telegram=FakeTelegram(), scheduler=RecordingScheduler())
