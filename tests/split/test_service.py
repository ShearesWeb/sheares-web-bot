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
    for field in expense_response["ExpenseDocuments"][0]["SummaryFields"]:
        if field["Type"]["Text"] == "TAX":
            field["LabelDetection"] = {"Text": "GST"}
    textract.add_response("analyze_expense", expense_response, {"Document": {"Bytes": receipt_image}})

    (receipt,) = service.analyze_receipt(receipt_image)

    assert receipt.items == (service.ReceiptItem("Noodles_[special]*", "8.00", "2"),)
    assert (receipt.vendor, receipt.subtotal, receipt.tax, receipt.total) == (
        "Test Restaurant", "8.00", "0.72", "8.72",
    )
    assert receipt.tax_label == "GST"


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


@pytest.mark.parametrize("blank", ["", "________"])
def test_blank_total_does_not_replace_a_readable_total(expense_response, blank):
    expense_response["ExpenseDocuments"][0]["SummaryFields"].append(expense_field("TOTAL", blank))

    assert service.parse_receipts(expense_response)[0].total == "8.72"


@pytest.mark.parametrize("kind", ["AMOUNT_DUE", "OTHER", "SUBTOTAL"])
def test_total_due_is_used_when_the_total_field_is_blank(expense_response, kind):
    summary = expense_response["ExpenseDocuments"][0]["SummaryFields"]
    summary[-1] = expense_field("TOTAL", "________")
    due = expense_field(kind, "8.72")
    due["LabelDetection"] = {"Text": "Total Due"}
    summary.append(due)

    receipt = service.parse_receipts(expense_response)[0]
    assert receipt.total == "8.72"
    assert receipt.subtotal == "8.00"


def test_full_total_is_preferred_over_a_remaining_amount_due(expense_response):
    expense_response["ExpenseDocuments"][0]["SummaryFields"].append(expense_field("AMOUNT_DUE", "2.00"))

    assert service.parse_receipts(expense_response)[0].total == "8.72"


def test_printed_total_due_can_be_read_from_ocr_lines(expense_response):
    document = expense_response["ExpenseDocuments"][0]
    document["SummaryFields"][-1] = expense_field("TOTAL", "________")
    document["Blocks"] = [
        {"BlockType": "LINE", "Text": "Total Due S$ 8.72"},
        {"BlockType": "LINE", "Text": "Total: ________"},
    ]

    assert service.parse_receipts(expense_response)[0].total == "8.72"


def test_total_fallback_does_not_use_subtotal_tax_or_tips(expense_response):
    document = expense_response["ExpenseDocuments"][0]
    document["SummaryFields"].pop()
    document["Blocks"] = [{"BlockType": "LINE", "Text": text} for text in (
        "Subtotal 8.00", "Tax 0.72", "Gratuity/Tips 0.00", "Total: ________",
    )]

    assert service.parse_receipts(expense_response)[0].total == ""


@pytest.mark.parametrize("price", ["0", "0.00", "S$ 0.00"])
def test_zero_priced_receipt_items_are_omitted(expense_response, price):
    document = expense_response["ExpenseDocuments"][0]
    document["LineItemGroups"][0]["LineItems"].append({"LineItemExpenseFields": [
        expense_field("ITEM", "Miscellaneous charges"), expense_field("PRICE", price),
    ]})

    receipt = service.parse_receipts(expense_response)[0]

    assert receipt.items == (service.ReceiptItem("Noodles_[special]*", "8.00", "2"),)
    assert receipt.total == "8.72"


@pytest.fixture
def booking_cart_response():
    # Reduced from image_2's actual Textract response: correct ITEM titles,
    # prices below the titles, and a first-item price misclassified as SUBTOTAL.
    blocks, rows = [], []
    for top in (0.130, 0.283, 0.437):
        rows.append({"LineItemExpenseFields": [
            expense_field("ITEM", "TOYOTAVIOS", geometry(0.033, top, 0.221, 0.015)),
            expense_field("PRICE", "S$ 81.75", geometry(0.040, top + 0.097, 0.150, 0.018)),
            expense_field("EXPENSE_ROW", (
                "TOYOTAVIOS\nDate: 14/09/2024\nStart time: 08: 30 Remove\n"
                "End time: 10: 10\nS$ 81.75 w/GST"
            ), geometry(0.033, top, 0.908, 0.116)),
        ]})
        blocks.append(word("TOYOTAVIOS", 0.033, top, 0.221))
    rows.append({"LineItemExpenseFields": [
        expense_field("ITEM", "Miscellaneous charges", geometry(0.035, 0.590, 0.384)),
        expense_field("PRICE", "S$ 0.00", geometry(0.838, 0.590, 0.125)),
    ]})
    blocks.append(word("Miscellaneous charges", 0.035, 0.590, 0.384))
    total = expense_field("TOTAL", "S$ 245.25")
    total["LabelDetection"] = {"Text": "Total Amount\n(Inclusive of GST)"}
    return {"ExpenseDocuments": [{
        "Blocks": blocks, "LineItemGroups": [{"LineItems": rows}],
        "SummaryFields": [
            expense_field("SUBTOTAL", "S$ 81.75", geometry(0.040, 0.227, 0.150, 0.018)),
            total, expense_field("OTHER", "S$ 520.97"),
        ],
    }]}


@pytest.mark.parametrize("miscellaneous", ["0.00", "5.00"])
def test_booking_cart_keeps_each_title_and_omits_zero_charges(booking_cart_response, miscellaneous):
    document = booking_cart_response["ExpenseDocuments"][0]
    document["LineItemGroups"][0]["LineItems"][-1]["LineItemExpenseFields"][1]["ValueDetection"]["Text"] = miscellaneous
    total = "245.25" if miscellaneous == "0.00" else "250.25"
    document["SummaryFields"][1]["ValueDetection"]["Text"] = total

    receipt = service.parse_receipts(booking_cart_response)[0]

    expected = (service.ReceiptItem("TOYOTAVIOS", "81.75"),) * 3
    if miscellaneous != "0.00":
        expected += (service.ReceiptItem("Miscellaneous charges", miscellaneous),)
    assert receipt.items == expected
    assert receipt.subtotal == ""
    assert receipt.total == total
    assert receipt.tax == ""  # w/GST is inclusive; no extra tax is invented.


@pytest.mark.parametrize("mismatch", ["missing_end_time", "different_page", "price_outside_row"])
def test_unaligned_booking_title_requires_a_matching_complete_row(booking_cart_response, mismatch):
    fields = booking_cart_response["ExpenseDocuments"][0]["LineItemGroups"][0]["LineItems"][0]["LineItemExpenseFields"]
    if mismatch == "missing_end_time":
        fields[2]["ValueDetection"]["Text"] = fields[2]["ValueDetection"]["Text"].replace("End time:", "Notes:")
    elif mismatch == "different_page":
        fields[2]["PageNumber"] = 2
    else:
        fields[2]["ValueDetection"]["Geometry"] = geometry(0.033, 0.130, 0.908, 0.030)

    receipt = service.parse_receipts(booking_cart_response)[0]

    assert receipt.items == (service.ReceiptItem("TOYOTAVIOS", "81.75"),) * 2


def test_real_unlabelled_subtotal_separate_from_item_prices_is_preserved(booking_cart_response):
    subtotal = booking_cart_response["ExpenseDocuments"][0]["SummaryFields"][0]
    subtotal["ValueDetection"] = {"Text": "245.25", "Geometry": geometry(0.80, 0.65, 0.17)}

    assert service.parse_receipts(booking_cart_response)[0].subtotal == "245.25"


@pytest.fixture
def booking_cart_ocr_response(booking_cart_response):
    document = booking_cart_response["ExpenseDocuments"][0]
    for top in (0.130, 0.283, 0.437):
        for text, offset in (
            ("TOYOTAVIOS", 0), ("Date: 14/09/2024", 0.026),
            ("Start time: 08:30", 0.051), ("End time: 10:10", 0.075),
            ("S$ 81.75 w/GST", 0.097),
        ):
            document["Blocks"].append({
                "BlockType": "LINE", "Text": text, "Page": 1,
                "Geometry": geometry(0.035, top + offset, 0.25, 0.014),
            })
    return booking_cart_response


@pytest.mark.parametrize("omitted", [(), (0,), (1,), (2,), (0, 1, 2), "all_groups"])
def test_booking_ocr_recovers_missing_rows_without_merging_identical_items(booking_cart_ocr_response, omitted):
    document = booking_cart_ocr_response["ExpenseDocuments"][0]
    if omitted == "all_groups":
        document["LineItemGroups"] = []
    else:
        group = document["LineItemGroups"][0]
        group["LineItems"] = [row for index, row in enumerate(group["LineItems"]) if index not in omitted]

    receipt = service.parse_receipts(booking_cart_ocr_response)[0]

    assert receipt.items == (service.ReceiptItem("TOYOTAVIOS", "81.75"),) * 3
    assert receipt.subtotal == ""
    assert receipt.total == "245.25"


@pytest.mark.parametrize("incomplete", ["missing_price", "partial_name", "missing_expense_row"])
def test_booking_ocr_recovers_incomplete_structured_rows(booking_cart_ocr_response, incomplete):
    rows = booking_cart_ocr_response["ExpenseDocuments"][0]["LineItemGroups"][0]["LineItems"]
    fields = rows[1]["LineItemExpenseFields"]
    if incomplete == "partial_name":
        fields[0]["ValueDetection"]["Text"] = "Start time: 12:45"
    else:
        fields.pop(1 if incomplete == "missing_price" else 2)

    receipt = service.parse_receipts(booking_cart_ocr_response)[0]

    assert receipt.items == (service.ReceiptItem("TOYOTAVIOS", "81.75"),) * 3


@pytest.mark.parametrize("missing", ["price", "title", "same_page_details"])
def test_booking_recovery_requires_a_visible_title_details_and_price(booking_cart_ocr_response, missing):
    document = booking_cart_ocr_response["ExpenseDocuments"][0]
    document["LineItemGroups"][0]["LineItems"].pop(1)
    lines = [block for block in document["Blocks"] if block["BlockType"] == "LINE"]
    if missing == "same_page_details":
        lines[8]["Page"] = 2
    else:
        document["Blocks"].remove(lines[9 if missing == "price" else 5])

    receipt = service.parse_receipts(booking_cart_ocr_response)[0]

    # The total alone must never be used to invent a third item.
    assert receipt.items == (service.ReceiptItem("TOYOTAVIOS", "81.75"),) * 2
    assert receipt.total == "245.25"


def test_recovered_bookings_preserve_other_paid_items_in_display_order(booking_cart_ocr_response):
    document = booking_cart_ocr_response["ExpenseDocuments"][0]
    document["LineItemGroups"][0]["LineItems"][-1]["LineItemExpenseFields"][1]["ValueDetection"]["Text"] = "5.00"
    document["SummaryFields"][1]["ValueDetection"]["Text"] = "250.25"

    receipt = service.parse_receipts(booking_cart_ocr_response)[0]

    assert receipt.items == (service.ReceiptItem("TOYOTAVIOS", "81.75"),) * 3 + (
        service.ReceiptItem("Miscellaneous charges", "5.00"),
    )
    assert receipt.total == "250.25"


def test_booking_recovery_preserves_detected_quantities(booking_cart_ocr_response):
    rows = booking_cart_ocr_response["ExpenseDocuments"][0]["LineItemGroups"][0]["LineItems"]
    rows[0]["LineItemExpenseFields"].append(expense_field("QUANTITY", "I"))
    rows[2]["LineItemExpenseFields"].append(expense_field("QUANTITY", "2"))

    receipt = service.parse_receipts(booking_cart_ocr_response)[0]

    assert receipt.items == (
        service.ReceiptItem("TOYOTAVIOS", "81.75", "1"),
        service.ReceiptItem("TOYOTAVIOS", "81.75"),
        service.ReceiptItem("TOYOTAVIOS", "81.75", "2"),
    )


def test_cart_upload_summary_keeps_booking_missing_from_structured_rows(
    services, textract, receipt_image, booking_cart_ocr_response, owner_profile,
):
    import features  # noqa: F401
    from core.router import router
    from features.split import repository
    from support import message_update, photo_update

    document = booking_cart_ocr_response["ExpenseDocuments"][0]
    document["LineItemGroups"][0]["LineItems"].pop(1)
    services.telegram.files["receipt"] = receipt_image
    textract.add_response("analyze_expense", booking_cart_ocr_response, {"Document": {"Bytes": receipt_image}})

    router.dispatch_update(message_update("/split"), services)
    router.dispatch_update(photo_update(), services)

    text = services.telegram.sent[-1]["text"]
    assert "Receipt 1:" in text
    for number in (1, 2, 3):
        assert f"{number}. TOYOTAVIOS - 81.75" in text
    assert text.count("TOYOTAVIOS - 81.75") == 3
    assert "Total: 245.25" in text
    assert "Miscellaneous charges" not in text
    expense, = repository.get_session(1, 10)["expenses"]
    assert len(expense["items"]) == 3
    assert expense["totalCents"] == 24525
