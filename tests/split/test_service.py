import pytest

from features.split import service


def test_textract_timeouts_fit_the_receipt_webhook_budget():
    service._textract.cache_clear()
    try:
        config = service._textract().meta.config
        assert config.connect_timeout == 2 and config.read_timeout == 8
        assert config.retries["total_max_attempts"] == 1
    finally:
        service._textract.cache_clear()


def test_analyze_expense_uses_image_bytes_and_preserves_line_total(textract, receipt_image, expense_response):
    textract.add_response("analyze_expense", expense_response, {"Document": {"Bytes": receipt_image}})

    (receipt,) = service.analyze_receipt(receipt_image)

    assert receipt.items == (service.ReceiptItem("Noodles_[special]*", "8.00", "2"),)
    assert (receipt.vendor, receipt.subtotal, receipt.tax, receipt.total) == (
        "Test Restaurant", "8.00", "0.72", "8.72",
    )


@pytest.mark.parametrize("image_bytes, error", [
    (b"", "JPEG or PNG"),
    (b"%PDF-1.7", "JPEG or PNG"),
    (b"not an image", "JPEG or PNG"),
])
def test_unsupported_images_never_reach_textract(textract, image_bytes, error):
    with pytest.raises(ValueError, match=error):
        service.analyze_receipt(image_bytes)


def test_oversized_image_never_reaches_textract(textract, monkeypatch, receipt_image):
    monkeypatch.setattr(service, "MAX_RECEIPT_BYTES", len(receipt_image) - 1)

    with pytest.raises(ValueError, match="10 MB"):
        service.analyze_receipt(receipt_image)


def test_unpriced_lines_are_ignored_and_all_groups_are_read():
    def line(**fields):
        return {"LineItemExpenseFields": [
            {"Type": {"Text": kind}, "ValueDetection": {"Text": value}}
            for kind, value in fields.items()
        ]}

    response = {"ExpenseDocuments": [
        {"LineItemGroups": [
            {"LineItems": [line(ITEM="Tea\nwith milk", UNIT_PRICE="2.00")]},
            {"LineItems": [line(EXPENSE_ROW="Rice bowl 5.00", PRICE="5.00"), line(PRICE="99.00")]},
        ]},
        {"LineItemGroups": [{"LineItems": [line(ITEM="Coffee", PRICE="3.00")]}]},
    ]}

    receipts = service.parse_receipts(response)

    assert receipts[0].items == (service.ReceiptItem("Rice bowl 5.00", "5.00"),)
    assert receipts[1].items == (service.ReceiptItem("Coffee", "3.00"),)


def test_no_receipt_lines_returns_no_items():
    assert service.parse_receipts({}) == []
    assert service.parse_receipts({"ExpenseDocuments": [{}]})[0].items == ()


def geometry(left, top, width, height=0.02, slope=0):
    points = [
        {"X": left, "Y": top}, {"X": left + width, "Y": top + slope * width},
        {"X": left + width, "Y": top + slope * width + height}, {"X": left, "Y": top + height},
    ]
    return {"BoundingBox": {
        "Left": left, "Top": min(point["Y"] for point in points), "Width": width,
        "Height": max(point["Y"] for point in points) - min(point["Y"] for point in points),
    }, "Polygon": points}


def expense_field(kind, text, region=None):
    detection = {"Text": text}
    if region:
        detection["Geometry"] = region
    return {"Type": {"Text": kind}, "ValueDetection": detection, "PageNumber": 1}


def word(text, left, top, width=0.05, *, slope=0, page=1):
    return {"BlockType": "WORD", "Text": text, "Geometry": geometry(left, top, width, slope=slope), "Page": page}


def test_testing_1_layout_and_tax_inclusive_summary():
    # Model the printed layout in testing_1.jpg; these are simulated Textract fields.
    components = ["Rendang Lion Mane Mushroom", "Sesame Rice", "White Rice", "Daily Soup of the Day"]
    set_fields = [
        expense_field("ITEM", "Set for 2\n" + "\n".join(components), geometry(0.2, 0.25, 0.32, 0.28)),
        expense_field("QUANTITY", "I", geometry(0.55, 0.25, 0.02)),
        expense_field("PRICE", "S$ 29.90", geometry(0.7, 0.25, 0.1)),
    ]
    drink_fields = [
        expense_field("ITEM", "Chilled\nWintermelon Tea\nwith Osmanthus\nJelly", geometry(0.2, 0.6, 0.32, 0.1)),
        expense_field("QUANTITY", "1", geometry(0.55, 0.6, 0.02)),
        expense_field("PRICE", "S$ 3.90", geometry(0.7, 0.6, 0.1)),
    ]
    blocks = [word("Set", 0.2, 0.25), word("for", 0.27, 0.25), word("2", 0.34, 0.25)]
    blocks.extend(word(component, 0.2, 0.3 + i * 0.04, width=0.3) for i, component in enumerate(components))
    blocks.extend(word("1", 0.55, 0.3 + i * 0.04, width=0.02) for i in range(len(components)))
    blocks.extend([
        word("Chilled", 0.2, 0.6, 0.12), word("Wintermelon Tea", 0.2, 0.63, 0.3),
        word("with Osmanthus", 0.2, 0.66, 0.3), word("Jelly", 0.2, 0.69, 0.08),
    ])
    lines = [{"LineItemExpenseFields": set_fields}, {"LineItemExpenseFields": drink_fields}]
    lines[1:1] = [{"LineItemExpenseFields": [
        expense_field("ITEM", component), expense_field("QUANTITY", "1"),
    ]} for component in components]
    response = {"ExpenseDocuments": [{
        "Blocks": blocks, "LineItemGroups": [{"LineItems": lines}],
        "SummaryFields": [
            expense_field("SUBTOTAL", "S$ 33.80"), expense_field("TAX", "S$ 2.79"),
            expense_field("TOTAL", "S$ 33.80"),
        ],
    }]}

    (receipt,) = service.parse_receipts(response)

    assert receipt.items == (
        service.ReceiptItem("Set for 2", "29.90", "1"),
        service.ReceiptItem("Chilled Wintermelon Tea with Osmanthus Jelly", "3.90", "1"),
    )
    assert (receipt.subtotal, receipt.tax, receipt.total) == ("31.01", "2.79", "33.80")


@pytest.mark.parametrize("price, subtotal, tax, service_charge, total, expected", [
    ("10.00", "11.99", "0.99", "1.00", "11.99", "10.00"),
    ("140.50", "153.20", "12.65", "", "153.20", "140.50"),
    ("unreadable", "10.90", "0.90", "", "10.90", "10.00"),
])
def test_pre_tax_subtotal_corrects_duplicate_total_and_allows_receipt_rounding(
    price, subtotal, tax, service_charge, total, expected,
):
    receipt = service.Receipt(
        (service.ReceiptItem("Meal", price),), subtotal=subtotal, tax=tax,
        service_charge=service_charge, total=total,
    )

    assert receipt.subtotal == expected
    assert receipt.total == total


def test_item_name_is_selected_by_price_position_rather_than_first_description_line():
    fields = [
        expense_field("ITEM", "Details\nSet for 2\nSoup", geometry(0.2, 0.2, 0.3, 0.14)),
        expense_field("PRICE", "29.90", geometry(0.7, 0.26, 0.1)),
    ]
    blocks = [word("Details", 0.2, 0.2), word("Soup", 0.2, 0.32),
              word("2", 0.34, 0.26), word("Set", 0.2, 0.26), word("for", 0.27, 0.26)]
    response = {"ExpenseDocuments": [{"Blocks": blocks, "LineItemGroups": [{
        "LineItems": [{"LineItemExpenseFields": fields}, {"LineItemExpenseFields": [
            expense_field("ITEM", "Soup"), expense_field("QUANTITY", "1"),
        ]}],
    }]}]}

    assert service.parse_receipts(response)[0].items == (service.ReceiptItem("Set for 2", "29.90"),)


def test_price_row_matching_handles_a_tilted_receipt_and_filters_other_pages():
    fields = [
        expense_field("ITEM", "Wrong row\nSet for 2", geometry(0.19, 0.33, 0.4, 0.18)),
        expense_field("PRICE", "29.90", geometry(0.7, 0.3, 0.1, slope=-0.25)),
    ]
    blocks = [
        word("Wrong row", 0.2, 0.35, 0.2, slope=-0.25),
        word("Set", 0.2, 0.425, slope=-0.25), word("for", 0.27, 0.4075, slope=-0.25),
        word("2", 0.34, 0.39, slope=-0.25), word("Other page", 0.2, 0.425, slope=-0.25, page=2),
    ]
    response = {"ExpenseDocuments": [{"Blocks": blocks, "LineItemGroups": [{
        "LineItems": [{"LineItemExpenseFields": fields}, {"LineItemExpenseFields": [
            expense_field("ITEM", "Soup"), expense_field("QUANTITY", "1"),
        ]}],
    }]}]}

    assert service.parse_receipts(response)[0].items == (service.ReceiptItem("Set for 2", "29.90"),)


def test_expense_row_geometry_excludes_quantity_and_price_columns():
    fields = [
        expense_field("EXPENSE_ROW", "Set for 2 I 29.90", geometry(0.2, 0.25, 0.6)),
        expense_field("QUANTITY", "I", geometry(0.55, 0.25, 0.02)),
        expense_field("PRICE", "29.90", geometry(0.7, 0.25, 0.1)),
    ]
    blocks = [word("Set", 0.2, 0.25), word("for", 0.27, 0.25), word("2", 0.34, 0.25),
              word("I", 0.55, 0.25, 0.02), word("29.90", 0.7, 0.25, 0.1)]
    response = {"ExpenseDocuments": [{"Blocks": blocks, "LineItemGroups": [{
        "LineItems": [{"LineItemExpenseFields": fields}],
    }]}]}

    assert service.parse_receipts(response)[0].items == (service.ReceiptItem("Set for 2", "29.90", "1"),)


def test_unaligned_description_is_not_attached_to_a_price_on_another_row():
    fields = [
        expense_field("ITEM", "Soup", geometry(0.2, 0.3, 0.3)),
        expense_field("PRICE", "29.90", geometry(0.7, 0.25, 0.1)),
    ]
    response = {"ExpenseDocuments": [{"Blocks": [word("Soup", 0.2, 0.3)], "LineItemGroups": [{
        "LineItems": [{"LineItemExpenseFields": fields}],
    }]}]}

    assert service.parse_receipts(response)[0].items == ()


def test_missing_geometry_keeps_only_parent_title_when_components_have_quantities():
    fields = [expense_field("ITEM", "Set for 2\nSoup\nRice"), expense_field("PRICE", "29.90")]
    response = {"ExpenseDocuments": [{"LineItemGroups": [{"LineItems": [
        {"LineItemExpenseFields": fields},
        {"LineItemExpenseFields": [expense_field("ITEM", "Soup"), expense_field("QUANTITY", "1")]},
        {"LineItemExpenseFields": [expense_field("ITEM", "Rice"), expense_field("QUANTITY", "1")]},
    ]}]}]}

    assert service.parse_receipts(response)[0].items == (service.ReceiptItem("Set for 2", "29.90"),)


@pytest.mark.parametrize("description", ["Set for 2\nSoup\nRice", "Set for 2 Soup Rice"])
def test_line_blocks_do_not_include_quantity_or_price_in_item_name(description):
    fields = [
        expense_field("ITEM", description, geometry(0.2, 0.25, 0.32, 0.2)),
        expense_field("QUANTITY", "I", geometry(0.55, 0.25, 0.02)),
        expense_field("PRICE", "29.90", geometry(0.7, 0.25, 0.1)),
    ]
    blocks = [
        {"BlockType": "LINE", "Text": "Set for 2 I 29.90", "Geometry": geometry(0.2, 0.25, 0.6)},
        {"BlockType": "LINE", "Text": "Soup", "Geometry": geometry(0.2, 0.3, 0.1)},
        {"BlockType": "LINE", "Text": "Rice", "Geometry": geometry(0.2, 0.35, 0.1)},
    ]
    response = {"ExpenseDocuments": [{"Blocks": blocks, "LineItemGroups": [{
        "LineItems": [{"LineItemExpenseFields": fields}, {"LineItemExpenseFields": [
            expense_field("ITEM", "Soup"), expense_field("QUANTITY", "1"),
        ]}],
    }]}]}

    assert service.parse_receipts(response)[0].items == (service.ReceiptItem("Set for 2", "29.90", "1"),)


def test_quantity_column_identifies_subitems_merged_into_a_single_item_field():
    fields = [
        expense_field("ITEM", "Set for 2\nSoup\nRice", geometry(0.2, 0.25, 0.3, 0.15)),
        expense_field("PRICE", "29.90", geometry(0.7, 0.25, 0.1)),
        expense_field("QUANTITY", "1", geometry(0.55, 0.25, 0.02)),
    ]
    blocks = [
        word("Set", 0.2, 0.25), word("for", 0.27, 0.25), word("2", 0.34, 0.25),
        word("Soup", 0.2, 0.3), word("Rice", 0.2, 0.35),
        word("I", 0.55, 0.3, 0.02), word("1", 0.55, 0.35, 0.02),
    ]
    response = {"ExpenseDocuments": [{"Blocks": blocks, "LineItemGroups": [{
        "LineItems": [{"LineItemExpenseFields": fields}],
    }]}]}

    assert service.parse_receipts(response)[0].items == (service.ReceiptItem("Set for 2", "29.90", "1"),)


@pytest.mark.parametrize("with_geometry", [True, False])
def test_wrapped_item_without_components_keeps_its_full_name(with_geometry):
    fields = [
        expense_field("ITEM", "Chilled\nWintermelon Tea\nwith Osmanthus\nJelly",
                      geometry(0.2, 0.25, 0.32, 0.13) if with_geometry else None),
        expense_field("PRICE", "3.90", geometry(0.7, 0.25, 0.1) if with_geometry else None),
        expense_field("QUANTITY", "I", geometry(0.55, 0.25, 0.02) if with_geometry else None),
    ]
    blocks = [word("Chilled", 0.2, 0.25), word("Wintermelon Tea", 0.2, 0.29, 0.3),
              word("with Osmanthus", 0.2, 0.33, 0.3), word("Jelly", 0.2, 0.37)]
    response = {"ExpenseDocuments": [{"Blocks": blocks, "LineItemGroups": [{
        "LineItems": [{"LineItemExpenseFields": fields}],
    }]}]}

    assert service.parse_receipts(response)[0].items == (
        service.ReceiptItem("Chilled Wintermelon Tea with Osmanthus Jelly", "3.90", "1"),
    )


def test_unpriced_description_fragments_join_the_item_without_duplicating_existing_text():
    response = {"ExpenseDocuments": [{"LineItemGroups": [{"LineItems": [
        {"LineItemExpenseFields": [expense_field("ITEM", "Chilled\nWintermelon Tea"), expense_field("PRICE", "3.90")]},
        {"LineItemExpenseFields": [expense_field("ITEM", "Wintermelon Tea")]},
        {"LineItemExpenseFields": [expense_field("ITEM", "with Osmanthus")]},
        {"LineItemExpenseFields": [expense_field("ITEM", "Jelly")]},
        {"LineItemExpenseFields": [expense_field("ITEM", "Coffee"), expense_field("PRICE", "2.00")]},
    ]}]}]}

    assert service.parse_receipts(response)[0].items == (
        service.ReceiptItem("Chilled Wintermelon Tea with Osmanthus Jelly", "3.90"),
        service.ReceiptItem("Coffee", "2.00"),
    )


def test_numbers_inside_a_wrapped_name_are_not_mistaken_for_component_quantities():
    fields = [
        expense_field("ITEM", "Tea for\n2 people", geometry(0.2, 0.25, 0.3, 0.08)),
        expense_field("PRICE", "3.90", geometry(0.7, 0.25, 0.1)),
        expense_field("QUANTITY", "1", geometry(0.55, 0.25, 0.02)),
    ]
    blocks = [word("Tea", 0.2, 0.25), word("for", 0.27, 0.25), word("2", 0.2, 0.3, 0.02),
              word("people", 0.25, 0.3, 0.1)]
    response = {"ExpenseDocuments": [{"Blocks": blocks, "LineItemGroups": [{
        "LineItems": [{"LineItemExpenseFields": fields}],
    }]}]}

    assert service.parse_receipts(response)[0].items == (service.ReceiptItem("Tea for 2 people", "3.90", "1"),)


def test_continuations_and_components_on_other_pages_do_not_change_the_item_name():
    other_page = expense_field("ITEM", "Soup")
    other_page["PageNumber"] = 2
    response = {"ExpenseDocuments": [{"LineItemGroups": [{"LineItems": [
        {"LineItemExpenseFields": [expense_field("ITEM", "Chilled\nWintermelon Tea"), expense_field("PRICE", "3.90")]},
        {"LineItemExpenseFields": [other_page, expense_field("QUANTITY", "1")]},
    ]}]}]}

    assert service.parse_receipts(response)[0].items == (service.ReceiptItem("Chilled Wintermelon Tea", "3.90"),)


@pytest.mark.parametrize("raw, expected", [
    ("I", "1"), ("Ⅰ", "1"), ("Ｉ", "1"), ("i", "1"), ("l", "1"), ("|", "1"), ("IO", "10"), ("1.O", "1.0"),
    (" 2 ", "2"), ("4.00", "4.00"), ("0.5", "0.5"), ("-1", "-1"),
    ("unknown", ""), ("ONE", ""), ("1x", ""), ("", ""),
])
def test_quantities_are_numeric_and_common_digit_misreads_are_corrected(raw, expected):
    fields = [expense_field("ITEM", "Tea"), expense_field("PRICE", "2.00"), expense_field("QUANTITY", raw)]
    response = {"ExpenseDocuments": [{"LineItemGroups": [{"LineItems": [{"LineItemExpenseFields": fields}]}]}]}

    assert service.parse_receipts(response)[0].items[0].quantity == expected
