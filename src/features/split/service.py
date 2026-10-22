from dataclasses import dataclass
from decimal import Decimal
from functools import cache
import math
import re
import unicodedata

import boto3
from botocore.config import Config

MAX_RECEIPT_BYTES = 10 * 1024 * 1024


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

    def __post_init__(self):
        for field in ("subtotal", "service_charge", "tax", "total"):
            object.__setattr__(self, field, _format_amount(getattr(self, field)))
        object.__setattr__(self, "subtotal", _subtotal_before_tax(self))


@cache
def _textract():
    return boto3.client("textract", config=Config(
        connect_timeout=3, read_timeout=20, retries={"total_max_attempts": 1},
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


def _amount(text):
    value = unicodedata.normalize("NFKC", str(text)).strip()
    negative = value.startswith("(") and value.endswith(")")
    if negative:
        value = value[1:-1].strip()
    value = re.sub(r"(?:SGD|S\s*\$|\$)", "", value, flags=re.IGNORECASE).strip()
    if not re.fullmatch(r"[+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?", value):
        return None
    amount = Decimal(value.replace(",", ""))
    return -abs(amount) if negative else amount


def _format_amount(text):
    amount = _amount(text)
    if amount is not None:
        return f"{amount:.2f}"
    return re.sub(r"S\s*\$", "", str(text), flags=re.IGNORECASE).strip()


def _subtotal_before_tax(receipt):
    total = _amount(receipt.total)
    tax = _amount(receipt.tax) if receipt.tax else Decimal("0")
    service_charge = _amount(receipt.service_charge) if receipt.service_charge else Decimal("0")
    if total is None or tax is None or service_charge is None or tax + service_charge == 0:
        return receipt.subtotal

    before_tax = total - tax - service_charge
    item_amounts = [_amount(item.price) for item in receipt.items]
    item_subtotal = (
        sum(item_amounts, Decimal("0"))
        if item_amounts and all(amount is not None for amount in item_amounts) else None
    )
    # Keep a consistent printed subtotal or item sum, including receipts that
    # round the final payment by up to five cents. Tax-inclusive item prices
    # equal the total, so their pre-tax subtotal must be derived instead.
    for candidate in (_amount(receipt.subtotal), item_subtotal):
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


def _priced_item_name(fields, blocks, *, has_subitems):
    name_field = fields.get("ITEM") or fields.get("EXPENSE_ROW")
    if not name_field:
        return ""
    name = name_field.get("ValueDetection", {})
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


def parse_receipts(response):
    receipts = []
    for document in response.get("ExpenseDocuments", []):
        items = []
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
                following = []
                for candidate in entries[index + 1:]:
                    if _fields(candidate.values()).get("PRICE"):
                        break
                    following.append(candidate)
                has_subitems = _has_subitems(raw_fields, following, document.get("Blocks", []))
                name = _priced_item_name(raw_fields, document.get("Blocks", []), has_subitems=has_subitems)
                if name:
                    if not has_subitems and "ITEM" in raw_fields:
                        name = _with_continuations(name, following, raw_fields["ITEM"].get("PageNumber", 1))
                    # PRICE is the line total; UNIT_PRICE is a different field.
                    items.append(ReceiptItem(name, fields["PRICE"], fields.get("QUANTITY", "")))
        summary = _fields(document.get("SummaryFields", []))
        receipts.append(Receipt(
            items=tuple(items), vendor=summary.get("VENDOR_NAME", ""),
            subtotal=summary.get("SUBTOTAL", ""), service_charge=summary.get("SERVICE_CHARGE", ""),
            tax=summary.get("TAX", ""), total=summary.get("TOTAL", ""),
        ))
    return receipts
