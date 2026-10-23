import pytest

import features  # noqa: F401
from core.router import router
from core.telegram import TelegramError
from features.split import handlers, repository, service
from support import document_update, message_update, photo_update, seed_profile

pytestmark = pytest.mark.usefixtures("owner_profile")


@pytest.mark.parametrize("upload", [photo_update, document_update])
def test_split_upload_returns_items_once_and_keeps_collecting(
    services, textract, receipt_image, expense_response, upload,
):
    services.telegram.files["receipt"] = receipt_image
    textract.add_response("analyze_expense", expense_response, {"Document": {"Bytes": receipt_image}})

    router.dispatch_update(message_update("/split"), services)
    assert "upload a photo of your receipt" in services.telegram.sent[0]["text"]
    router.dispatch_update(upload(), services)
    router.dispatch_update(upload(), services)

    assert len(services.telegram.downloaded) == 1
    assert services.telegram.downloaded[0]["file_id"] == "receipt"
    result = services.telegram.sent[-1]
    assert "Noodles_[special]* (QTY: 2) - 8.00" in result["text"]
    assert "Tax: 0.72" in result["text"]
    assert "Total: 8.72" in result["text"]
    assert "4.00" not in result["text"]
    assert result["parse_mode"] == "HTML"
    assert services.telegram.sent[1]["text"] == "Processing image..."
    assert services.telegram.edited == [{
        "chat_id": 10, "message_id": 2, "text": "Receipt added.", "keyboard": None, "parse_mode": None,
    }]
    session = repository.get_session(1, 10)
    assert session["status"] == "COLLECTING"
    assert len(session["expenses"]) == 1


@pytest.mark.parametrize("user_id, chat_id", [(1, 10), (2, 10), (1, 20)])
def test_photos_without_a_matching_session_are_ignored_without_analysis(services, textract, user_id, chat_id):
    seed_profile(user_id, "A")
    if (user_id, chat_id) != (1, 10):
        router.dispatch_update(message_update("/split"), services)
    sent_before = len(services.telegram.sent)

    router.dispatch_update(photo_update(user_id=user_id, chat_id=chat_id), services)

    assert len(services.telegram.sent) == sent_before
    assert services.telegram.downloaded == []


@pytest.mark.parametrize("upload, text", [
    (document_update(mime_type="application/pdf"), "JPEG or PNG"),
    (photo_update(file_size=service.MAX_RECEIPT_BYTES + 1), "10 MB"),
])
def test_invalid_uploads_explain_problem_and_allow_retry(services, textract, upload, text):
    router.dispatch_update(message_update("/split"), services)

    router.dispatch_update(upload, services)

    assert text in services.telegram.sent[-1]["text"]
    assert services.telegram.downloaded == []
    assert repository.get_session(1, 10)["status"] == "COLLECTING"


def test_disguised_image_is_rejected_before_aws(services, textract):
    services.telegram.files["receipt"] = b"%PDF-1.7"
    router.dispatch_update(message_update("/split"), services)

    router.dispatch_update(document_update(), services)

    assert "JPEG or PNG" in services.telegram.sent[-1]["text"]
    assert repository.get_session(1, 10)["status"] == "COLLECTING"


def test_unreadable_receipt_asks_for_another_photo(services, textract, receipt_image):
    services.telegram.files["receipt"] = receipt_image
    textract.add_response("analyze_expense", {"ExpenseDocuments": []}, {"Document": {"Bytes": receipt_image}})
    router.dispatch_update(message_update("/split"), services)

    router.dispatch_update(photo_update(), services)

    assert "clearer photo" in services.telegram.sent[-1]["text"]
    assert repository.get_session(1, 10)["status"] == "COLLECTING"


def test_textract_failure_allows_user_to_upload_again(services, textract, receipt_image, expense_response):
    services.telegram.files["receipt"] = receipt_image
    params = {"Document": {"Bytes": receipt_image}}
    textract.add_client_error("analyze_expense", "AccessDeniedException", expected_params=params)
    textract.add_response("analyze_expense", expense_response, params)
    router.dispatch_update(message_update("/split"), services)

    router.dispatch_update(photo_update(), services)
    assert "upload it again" in services.telegram.sent[-1]["text"]
    router.dispatch_update(photo_update(), services)

    assert "Total: 8.72" in services.telegram.sent[-1]["text"]


def test_download_failure_allows_another_upload(services, monkeypatch):
    def fail(*args, **kwargs):
        raise TelegramError("Download failed")

    monkeypatch.setattr(services.telegram, "download_file", fail)
    router.dispatch_update(message_update("/split"), services)

    router.dispatch_update(photo_update(), services)

    assert "upload it again" in services.telegram.sent[-1]["text"]
    assert repository.get_session(1, 10)["status"] == "COLLECTING"


def test_long_receipts_are_split_without_losing_items():
    items = tuple(service.ReceiptItem(f"Item {number} {'x' * 50}", "1.00") for number in range(150))

    messages = list(handlers.receipt_messages([service.Receipt(items)]))

    assert len(messages) > 1
    assert all(len(message.encode("utf-16-le")) // 2 <= 4096 for message in messages)
    assert all(item.name in "\n".join(messages) for item in items)


def test_multiple_receipts_and_missing_prices_are_labeled():
    receipts = [
        service.Receipt((service.ReceiptItem("Tea", ""),)),
        service.Receipt((service.ReceiptItem("Rice", "3.00"),), service_charge="0.30"),
    ]

    text = "\n".join(handlers.receipt_messages(receipts))

    assert "Receipt 1" in text and "Receipt 2" in text
    assert "Tea - price not detected" in text
    assert "Service charge: 0.30" in text


def test_upload_removes_singapore_currency_prefix_and_shows_pre_tax_subtotal(
    services, textract, receipt_image, expense_response,
):
    document = expense_response["ExpenseDocuments"][0]
    fields = document["LineItemGroups"][0]["LineItems"][0]["LineItemExpenseFields"]
    for field in fields:
        if field["Type"]["Text"] in {"PRICE", "UNIT_PRICE"}:
            field["ValueDetection"]["Text"] = "S$ " + field["ValueDetection"]["Text"]
    document["SummaryFields"] = [
        {"Type": {"Text": kind}, "ValueDetection": {"Text": "S$ " + value}}
        for kind, value in (
            ("SUBTOTAL", "9.59"), ("SERVICE_CHARGE", "0.80"), ("TAX", "0.79"), ("TOTAL", "9.59"),
        )
    ]
    services.telegram.files["receipt"] = receipt_image
    textract.add_response("analyze_expense", expense_response, {"Document": {"Bytes": receipt_image}})

    router.dispatch_update(message_update("/split"), services)
    router.dispatch_update(photo_update(), services)

    text = services.telegram.sent[-1]["text"]
    assert "S$" not in text
    assert "Noodles_[special]* (QTY: 2) - 8.00" in text
    assert "Subtotal: 8.00\nService charge: 0.80\nTax: 0.79\nTotal: 9.59" in text


def test_testing_2_ginger_tea_quantity_is_corrected_in_upload_reply(
    services, textract, receipt_image, expense_response,
):
    fields = expense_response["ExpenseDocuments"][0]["LineItemGroups"][0]["LineItems"][0]["LineItemExpenseFields"]
    values = {"ITEM": "D62 ICE GINGER TEA", "QUANTITY": "I", "UNIT_PRICE": "2.00", "PRICE": "2.00"}
    for field in fields:
        field["ValueDetection"]["Text"] = values[field["Type"]["Text"]]
    services.telegram.files["receipt"] = receipt_image
    textract.add_response("analyze_expense", expense_response, {"Document": {"Bytes": receipt_image}})

    router.dispatch_update(message_update("/split"), services)
    router.dispatch_update(photo_update(), services)

    assert "D62 ICE GINGER TEA (QTY: 1) - 2.00" in services.telegram.sent[-1]["text"]


@pytest.mark.parametrize("raw", ["I", "Ⅰ", "Ｉ"])
def test_formatter_receives_normalized_quantities_even_for_direct_receipt_items(raw):
    receipt = service.Receipt((service.ReceiptItem("D62 ICE GINGER TEA", "2.00", raw),))

    text = "\n".join(handlers.receipt_messages([receipt]))

    assert "D62 ICE GINGER TEA (QTY: 1) - 2.00" in text
