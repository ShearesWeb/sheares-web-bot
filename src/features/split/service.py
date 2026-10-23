from dataclasses import dataclass
from decimal import Decimal
from functools import cache
import math
import re
import unicodedata

import boto3
from botocore.config import Config

from features.split.amounts import parse_amount

MAX_RECEIPT_BYTES = 10 * 1024 * 1024
TOTAL_LABELS = {
    "total": 1, "totalamount": 1, "grandtotal": 2, "totaldue": 2,
    "amountdue": 2, "totalpayable": 2, "amountpayable": 2,
}
TOTAL_LINE = re.compile(
    r"\s*(total\s*due|grand\s*total|amount\s*due|total\s*amount|total\s*payable|amount\s*payable|total)"
    r"\s*:?\s*(.+?)\s*", re.IGNORECASE,
)


@dataclass(frozen=True)
class ReceiptItem:
    name: str
    price: str
    quantity: str = ""

    def __post_init__(self):
        object.__setattr__(self, "quantity", _normalize_quantity(self.quantity))
        object.__setattr__(self, "price", _format_amount(self.price))


@dataclass(frozen=True)
class Receipt:
    items: tuple[ReceiptItem, ...]
    vendor: str = ""
    subtotal: str = ""
    service_charge: str = ""
    tax: str = ""
    total: str = ""
    service_charge_label: str = ""
    tax_label: str = ""

    def __post_init__(self):
        object.__setattr__(self, "items", tuple(item for item in self.items if parse_amount(item.price) != 0))
        for field in ("subtotal", "service_charge", "tax", "total"):
            object.__setattr__(self, field, _format_amount(getattr(self, field)))
        object.__setattr__(self, "subtotal", _subtotal_before_tax(self))


@cache
def _textract():
    return boto3.client("textract", config=Config(
        connect_timeout=2, read_timeout=8, retries={"total_max_attempts": 1},
    ))


def analyze_receipt(image_bytes):
    if len(image_bytes) > MAX_RECEIPT_BYTES:
        raise ValueError("Please upload a receipt image smaller than 10 MB.")
    if not image_bytes.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
        raise ValueError("Please upload a JPEG or PNG image of your receipt.")
    response = _textract().analyze_expense(Document={"Bytes": image_bytes})
    return parse_receipts(response)


def _fields(fields):
    return {
        field.get("Type", {}).get("Text", ""): " ".join(field.get("ValueDetection", {}).get("Text", "").split())
        for field in fields
    }


def _summary_fields(fields, blocks, item_prices=()):
    summary, labelled_totals = {}, []
    for field in fields:
        kind = field.get("Type", {}).get("Text", "")
        value = " ".join(field.get("ValueDetection", {}).get("Text", "").split())
        if not value or (kind in ("TOTAL", "AMOUNT_DUE") and parse_amount(value) is None):
            # Receipts can have a printed Total Due followed by an empty Total
            # line for handwritten tips. The empty field must not replace it.
            continue
        printed_label = " ".join(field.get("LabelDetection", {}).get("Text", "").split()).rstrip(":：").strip()
        if kind == "SUBTOTAL" and not printed_label and any(
            field.get("PageNumber", 1) == price.get("PageNumber", 1)
            and _inside(field.get("ValueDetection", {}).get("Geometry", {}), price.get("ValueDetection", {}).get("Geometry", {}))
            for price in item_prices
        ):
            # A cart's first item price can be misclassified as an unlabelled
            # subtotal. It is not a summary when it occupies the item price.
            continue
        label = re.sub(r"[^a-z]", "", printed_label.casefold())
        if label in TOTAL_LABELS:
            if parse_amount(value) is not None:
                labelled_totals.append((TOTAL_LABELS[label], value))
            # Textract can label Total Due as SUBTOTAL. Its printed label
            # identifies the total and must not overwrite the real subtotal.
            if kind not in ("TOTAL", "AMOUNT_DUE"):
                continue
        summary[kind] = value
        if kind in ("TAX", "SERVICE_CHARGE"):
            summary[f"{kind}_LABEL"] = printed_label
    if "TOTAL" in summary:
        return summary
    if "AMOUNT_DUE" in summary:
        summary["TOTAL"] = summary["AMOUNT_DUE"]
        return summary
    if not labelled_totals:
        # AnalyzeExpense includes OCR lines as well as summary fields. Accept
        # an explicitly printed total when its summary classification is absent.
        for block in blocks:
            if block.get("BlockType") != "LINE":
                continue
            match = TOTAL_LINE.fullmatch(block.get("Text", ""))
            if match and parse_amount(match[2]) is not None:
                label = re.sub(r"[^a-z]", "", match[1].casefold())
                labelled_totals.append((TOTAL_LABELS[label], match[2]))
    if labelled_totals:
        summary["TOTAL"] = max(labelled_totals, key=lambda candidate: candidate[0])[1]
    return summary


def _format_amount(text):
    amount = parse_amount(text)
    if amount is not None:
        return f"{amount:.2f}"
    return re.sub(r"S\s*\$", "", str(text), flags=re.IGNORECASE).strip()


def _subtotal_before_tax(receipt):
    total = parse_amount(receipt.total)
    tax = parse_amount(receipt.tax) if receipt.tax else Decimal("0")
    service_charge = parse_amount(receipt.service_charge) if receipt.service_charge else Decimal("0")
    if total is None or tax is None or service_charge is None or tax + service_charge == 0:
        return receipt.subtotal

    before_tax = total - tax - service_charge
    item_amounts = [parse_amount(item.price) for item in receipt.items]
    item_subtotal = (
        sum(item_amounts, Decimal("0"))
        if item_amounts and all(amount is not None for amount in item_amounts) else None
    )
    # Keep a consistent printed subtotal or item sum, including receipts that
    # round the final payment by up to five cents. Tax-inclusive item prices
    # equal the total, so their pre-tax subtotal must be derived instead.
    for candidate in (parse_amount(receipt.subtotal), item_subtotal):
        if candidate is not None and candidate != total and abs(candidate - before_tax) <= Decimal("0.05"):
            return f"{candidate:.2f}"
    return f"{before_tax:.2f}"


def _normalize_quantity(text):
    # These glyphs are commonly confused with digits in the quantity column.
    value = unicodedata.normalize("NFKC", str(text)).strip()
    value = value.translate(str.maketrans({"I": "1", "i": "1", "l": "1", "|": "1", "O": "0", "o": "0"}))
    return value if re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", value) else ""


def _box(geometry):
    return geometry.get("BoundingBox", {})


def _inside(geometry, region):
    box, bounds = _box(geometry), _box(region)
    if not box or not bounds:
        return False
    x, y = box["Left"] + box["Width"] / 2, box["Top"] + box["Height"] / 2
    return (
        bounds["Left"] <= x <= bounds["Left"] + bounds["Width"]
        and bounds["Top"] <= y <= bounds["Top"] + bounds["Height"]
    )


def _points(geometry):
    if geometry.get("Polygon"):
        return [(point["X"], point["Y"]) for point in geometry["Polygon"]]
    box = _box(geometry)
    return [
        (box["Left"] + x * box["Width"], box["Top"] + y * box["Height"])
        for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))
    ]


def _price_axis(geometry):
    polygon = geometry.get("Polygon", [])
    if len(polygon) >= 2:
        dx, dy = polygon[1]["X"] - polygon[0]["X"], polygon[1]["Y"] - polygon[0]["Y"]
        length = math.hypot(dx, dy)
        if length:
            return dx / length, dy / length
    return 1, 0


def _same_row(geometry, price_geometry):
    # Project onto the normal of the price's baseline so tilted receipts work too.
    axis_x, axis_y = _price_axis(price_geometry)
    ranges = []
    for region in (geometry, price_geometry):
        projections = [-axis_y * x + axis_x * y for x, y in _points(region)]
        ranges.append((min(projections), max(projections)))
    (top, bottom), (price_top, price_bottom) = ranges
    overlap = min(bottom, price_bottom) - max(top, price_top)
    height = min(bottom - top, price_bottom - price_top)
    return height > 0 and overlap >= height * 0.3


def _description_from_line(text, description):
    # A LINE may span description, quantity and price when WORD blocks are unavailable.
    tokens = text.split()
    description = f" {' '.join(description.split())} "
    best = ""
    for start in range(len(tokens)):
        for end in range(len(tokens), start, -1):
            candidate = " ".join(tokens[start:end])
            if f" {candidate} " in description:
                if len(candidate) > len(best):
                    best = candidate
                break
    return best


def _has_subitems(fields, following, blocks):
    name = fields.get("ITEM") or fields.get("EXPENSE_ROW") or {}
    page = name.get("PageNumber", 1)
    for child in following:
        child_name = child.get("ITEM") or child.get("EXPENSE_ROW") or {}
        values = _fields(child.values())
        if (
            child_name.get("ValueDetection", {}).get("Text", "").strip()
            and child_name.get("PageNumber", 1) == page
            and (values.get("QUANTITY") or values.get("UNIT_PRICE"))
        ):
            return True

    # Textract may combine a set and its components into one ITEM. Look for
    # additional quantities below its priced row in the same quantity column.
    name_geometry = name.get("ValueDetection", {}).get("Geometry", {})
    price_geometry = fields["PRICE"].get("ValueDetection", {}).get("Geometry", {})
    quantity_geometry = fields.get("QUANTITY", {}).get("ValueDetection", {}).get("Geometry", {})
    if not all(_box(region) for region in (name_geometry, price_geometry, quantity_geometry)):
        return False
    axis_x, axis_y = _price_axis(price_geometry)

    def limits(region, axis):
        values = [x * axis[0] + y * axis[1] for x, y in _points(region)]
        return min(values), max(values)

    column_left, column_right = limits(quantity_geometry, (axis_x, axis_y))
    _, price_bottom = limits(price_geometry, (-axis_y, axis_x))
    _, name_bottom = limits(name_geometry, (-axis_y, axis_x))
    for block in blocks:
        if block.get("BlockType") != "WORD" or block.get("Page", 1) != page:
            continue
        region = block.get("Geometry", {})
        if not _box(region) or not _normalize_quantity(block.get("Text", "")):
            continue
        left, right = limits(region, (axis_x, axis_y))
        top, bottom = limits(region, (-axis_y, axis_x))
        center_y = (top + bottom) / 2
        if min(right, column_right) > max(left, column_left) and price_bottom < center_y <= name_bottom:
            return True
    return False


def _is_booking_item(fields):
    item, price, row = (fields.get(kind, {}) for kind in ("ITEM", "PRICE", "EXPENSE_ROW"))
    name = " ".join(item.get("ValueDetection", {}).get("Text", "").split())
    text = " ".join(row.get("ValueDetection", {}).get("Text", "").split())
    if not name or not re.match(
        re.escape(name) + r"\s+date\s*:.*?\bstart\s*time\s*:.*?\bend\s*time\s*:", text, re.IGNORECASE,
    ):
        return False
    if not item.get("PageNumber", 1) == price.get("PageNumber", 1) == row.get("PageNumber", 1):
        return False
    item_region, price_region, row_region = (
        field.get("ValueDetection", {}).get("Geometry", {}) for field in (item, price, row)
    )
    if not _inside(item_region, row_region) or not _inside(price_region, row_region):
        return False
    item_box, price_box = _box(item_region), _box(price_region)
    return (
        item_box["Top"] + item_box["Height"] <= price_box["Top"]
        and abs(item_box["Left"] - price_box["Left"]) <= max(item_box["Height"] * 2, 0.02)
    )


def _priced_item_name(fields, blocks, *, has_subitems):
    name_field = fields.get("ITEM") or fields.get("EXPENSE_ROW")
    if not name_field:
        return ""
    name = name_field.get("ValueDetection", {})
    # Booking carts put the title above date/time details and the price. Keep
    # Textract's ITEM title only when the enclosing row confirms that layout.
    if not has_subitems and _is_booking_item(fields):
        return " ".join(name.get("Text", "").split())
    price = fields["PRICE"].get("ValueDetection", {})
    name_geometry, price_geometry = name.get("Geometry", {}), price.get("Geometry", {})
    if _box(name_geometry) and _box(price_geometry):
        page = name_field.get("PageNumber", 1)
        excluded = [
            fields[kind].get("ValueDetection", {}).get("Geometry", {})
            for kind in ("PRICE", "UNIT_PRICE", "QUANTITY") if kind in fields
        ]
        candidates = [
            block for block in blocks
            if block.get("BlockType") in ("WORD", "LINE")
            and block.get("Page", 1) == page
            and _inside(block.get("Geometry", {}), name_geometry)
            and not any(_inside(block.get("Geometry", {}), region) for region in excluded)
        ]
        # WORD blocks let us remove quantity/price columns even if the OCR LINE spans the whole row.
        words = [block for block in candidates if block["BlockType"] == "WORD"]
        candidates = words or candidates
        if candidates:
            aligned = [block for block in candidates if _same_row(block["Geometry"], price_geometry)]
            if not aligned:
                return ""
            if not has_subitems and "ITEM" in fields:
                return " ".join(name.get("Text", "").split())
            axis_x, axis_y = _price_axis(price_geometry)
            aligned.sort(key=lambda block: (
                _box(block["Geometry"])["Left"] * axis_x + _box(block["Geometry"])["Top"] * axis_y
            ))
            if words:
                return " ".join(block.get("Text", "").strip() for block in aligned).strip()
            return " ".join(
                _description_from_line(block.get("Text", ""), name.get("Text", ""))
                for block in aligned
            ).strip()
    if not has_subitems and "ITEM" in fields:
        return " ".join(name.get("Text", "").split())
    # With sub-items but no OCR geometry, the first line is the parent title.
    return next((line.strip() for line in name.get("Text", "").splitlines() if line.strip()), "")


def _with_continuations(name, following, page):
    for fields in following:
        field = fields.get("ITEM", {})
        if field.get("PageNumber", 1) != page:
            continue
        continuation = " ".join(field.get("ValueDetection", {}).get("Text", "").split())
        if continuation and f" {continuation} " not in f" {name} ":
            name = f"{name} {continuation}"
    return name


def _booking_amount(text):
    return parse_amount(re.sub(r"\s*w\s*/\s*GST\s*$", "", text, flags=re.IGNORECASE))


def _booking_rows(blocks):
    """Read complete booking cards even when LineItemGroups omits a row."""
    lines = sorted((
        block for block in blocks
        if block.get("BlockType") == "LINE" and _box(block.get("Geometry", {}))
        and block.get("Text", "").strip()
    ), key=lambda block: (block.get("Page", 1), _box(block["Geometry"])["Top"]))
    labels = (r"date\s*:", r"start\s*time\s*:", r"end\s*time\s*:")
    if not all(any(re.match(pattern, line["Text"].strip(), re.IGNORECASE) for line in lines) for pattern in labels):
        return []
    bookings = []
    for index, title in enumerate(lines):
        page, title_box = title.get("Page", 1), _box(title["Geometry"])
        tolerance = max(title_box["Height"] * 2, 0.02)
        following = [
            line for line in lines[index + 1:]
            if line.get("Page", 1) == page
            and abs(_box(line["Geometry"])["Left"] - title_box["Left"]) <= tolerance
            and title_box["Top"] < _box(line["Geometry"])["Top"]
            <= title_box["Top"] + max(title_box["Height"] * 10, 0.1)
        ]
        if len(following) < 4:
            continue
        date, start, end, price = following[:4]
        if not all(re.match(pattern, line["Text"].strip(), re.IGNORECASE) for pattern, line in zip(labels, (date, start, end))):
            continue
        amount = _booking_amount(price["Text"])
        name = " ".join(title["Text"].split())
        if amount is None or _booking_amount(name) is not None:
            continue
        bounds = [_box(line["Geometry"]) for line in (title, date, start, end, price)]
        left = min(box["Left"] for box in bounds)
        right = max(box["Left"] + box["Width"] for box in bounds)
        bottom = max(box["Top"] + box["Height"] for box in bounds)
        region = {"BoundingBox": {
            "Left": left, "Top": title_box["Top"],
            "Width": right - left, "Height": bottom - title_box["Top"],
        }}
        price_field = {
            "Type": {"Text": "PRICE"}, "PageNumber": page,
            "ValueDetection": {"Text": str(amount), "Geometry": price["Geometry"]},
        }
        bookings.append((ReceiptItem(name, str(amount)), price_field, region))
    return bookings


def parse_receipts(response):
    receipts = []
    for document in response.get("ExpenseDocuments", []):
        blocks = document.get("Blocks", [])
        bookings = _booking_rows(blocks)
        items = [(item, price) for item, price, _ in bookings]
        item_prices = [price for _, price, _ in bookings]
        for group in document.get("LineItemGroups", []):
            entries = [
                {
                    field.get("Type", {}).get("Text", ""): field
                    for field in line.get("LineItemExpenseFields", [])
                }
                for line in group.get("LineItems", [])
            ]
            for index, raw_fields in enumerate(entries):
                fields = _fields(raw_fields.values())
                # Unpriced lines are set components or description continuations, not billable items.
                if not fields.get("PRICE"):
                    continue
                item_prices.append(raw_fields["PRICE"])
                price_field = raw_fields["PRICE"]
                booking_index = next((
                    index for index, (_, card_price, region) in enumerate(bookings)
                    if price_field.get("PageNumber", 1) == card_price.get("PageNumber", 1)
                    and _inside(price_field.get("ValueDetection", {}).get("Geometry", {}), region)
                ), None)
                if booking_index is not None:
                    # Use each printed card once, including cards that Textract
                    # splits into several rows or gives a partial description.
                    item, card_price = items[booking_index]
                    if not item.quantity and fields.get("QUANTITY"):
                        items[booking_index] = (ReceiptItem(item.name, item.price, fields["QUANTITY"]), card_price)
                    continue
                following = []
                for candidate in entries[index + 1:]:
                    if _fields(candidate.values()).get("PRICE"):
                        break
                    following.append(candidate)
                has_subitems = _has_subitems(raw_fields, following, blocks)
                name = _priced_item_name(raw_fields, blocks, has_subitems=has_subitems)
                if name:
                    if not has_subitems and "ITEM" in raw_fields:
                        name = _with_continuations(name, following, raw_fields["ITEM"].get("PageNumber", 1))
                    # PRICE is the line total; UNIT_PRICE is a different field.
                    items.append((ReceiptItem(name, fields["PRICE"], fields.get("QUANTITY", "")), price_field))
        if bookings:
            items.sort(key=lambda entry: (
                entry[1].get("PageNumber", 1),
                _box(entry[1].get("ValueDetection", {}).get("Geometry", {})).get("Top", 1),
            ))
        summary = _summary_fields(document.get("SummaryFields", []), blocks, item_prices)
        receipts.append(Receipt(
            items=tuple(item for item, _ in items), vendor=summary.get("VENDOR_NAME", ""),
            subtotal=summary.get("SUBTOTAL", ""), service_charge=summary.get("SERVICE_CHARGE", ""),
            tax=summary.get("TAX", ""), total=summary.get("TOTAL", ""),
            service_charge_label=summary.get("SERVICE_CHARGE_LABEL", ""),
            tax_label=summary.get("TAX_LABEL", ""),
        ))
    return receipts
