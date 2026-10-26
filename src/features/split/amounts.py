"""Money parsing shared by receipt extraction and bill allocation."""

from decimal import Decimal
import re
import unicodedata


def parse_amount(text):
    value = unicodedata.normalize("NFKC", str(text)).strip()
    negative = value.startswith("(") and value.endswith(")")
    if negative:
        value = value[1:-1].strip()
    value = re.sub(r"(?:SGD|S\s*\$|\$)", "", value, flags=re.IGNORECASE).strip()
    if not re.fullmatch(r"[+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?", value):
        return None
    amount = Decimal(value.replace(",", ""))
    return -abs(amount) if negative else amount
