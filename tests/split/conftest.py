import base64

import boto3
import pytest
from botocore.stub import Stubber

from features.split import service
from support import seed_profile


@pytest.fixture
def owner_profile(aws):
    """Give the split creator a saved profile without bypassing access checks."""
    seed_profile(1, "A")


@pytest.fixture
def receipt_image():
    # A real PNG; Textract itself is stubbed, so tests never send images to AWS.
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII="
    )


@pytest.fixture
def expense_response():
    def field(kind, text):
        return {"Type": {"Text": kind}, "ValueDetection": {"Text": text}}

    return {"ExpenseDocuments": [{
        "ExpenseIndex": 1,
        "LineItemGroups": [{"LineItemGroupIndex": 1, "LineItems": [{
            "LineItemExpenseFields": [
                field("ITEM", "Noodles_[special]*"), field("QUANTITY", "2"),
                field("UNIT_PRICE", "4.00"), field("PRICE", "8.00"),
            ],
        }]}],
        "SummaryFields": [
            field("VENDOR_NAME", "Test Restaurant"), field("SUBTOTAL", "8.00"),
            field("TAX", "0.72"), field("TOTAL", "8.72"),
        ],
    }]}


@pytest.fixture
def textract(monkeypatch):
    client = boto3.client("textract", region_name="ap-southeast-1")
    monkeypatch.setattr(service, "_textract", lambda: client)
    with Stubber(client) as stub:
        yield stub
        stub.assert_no_pending_responses()
