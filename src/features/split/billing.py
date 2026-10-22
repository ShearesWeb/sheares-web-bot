"""Expense collection and allocation rules; amounts are stored as integer cents."""

from decimal import Decimal
from html import escape
import re

from features.split import service

MAX_ITEMS = 200
MAX_BATCHES = 50
MAX_PEOPLE = 50
MAX_CENTS = 100_000_000


def money(cents):
    return f"{Decimal(int(cents)) / 100:.2f}"


def _cents(value):
    amount = service._amount(value)
    if amount is None or amount <= 0 or amount * 100 != (amount * 100).to_integral_value():
        raise ValueError("Amounts must be positive numbers with at most two decimal places.")
    cents = int(amount * 100)
    if cents > MAX_CENTS:
        raise ValueError("That amount is too large. Please check the price.")
    return cents


def parse_expenses(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or len(lines) > 25:
        raise ValueError("Send between 1 and 25 expenses per message, one per line.")
    items = []
    for line in lines:
        match = re.fullmatch(r"(.+?)\s+-\s*(.+)", line)
        if not match or not match[1].strip() or len(match[1].strip()) > 200:
            raise ValueError("Use one expense per line, for example:\nFong Seng - 36.41\nAl Amaan - 74.86")
        cents = _cents(match[2])
        items.append({"name": match[1].strip(), "price": money(cents), "quantity": ""})
    return [{"source": "text", "items": items, "totalCents": sum(_cents(item["price"]) for item in items)}]


def receipt_expenses(receipts):
    expenses = []
    for receipt in receipts:
        if not receipt.items:
            continue
        total = service._amount(receipt.total)
        if total is None:
            if receipt.tax or receipt.service_charge:
                raise ValueError("I couldn't read the receipt's final total. Add it as text, for example: Receipt - 33.80")
            prices = [service._amount(item.price) for item in receipt.items]
            if any(price is None for price in prices):
                raise ValueError("I couldn't read all the prices. Add the final amount as text, for example: Receipt - 33.80")
            total = sum(prices, Decimal("0"))
        expenses.append({
            "source": "receipt", "totalCents": _cents(str(total)),
            "items": [{"name": item.name[:200], "price": item.price, "quantity": item.quantity} for item in receipt.items],
            "subtotal": receipt.subtotal, "tax": receipt.tax, "serviceCharge": receipt.service_charge,
        })
    return expenses


def validate_capacity(existing, added):
    expenses = [*existing, *added]
    if len(expenses) > MAX_BATCHES or sum(len(batch["items"]) for batch in expenses) > MAX_ITEMS:
        raise ValueError("This bill is full (200 items or 50 additions). Send /done to split it.")
    if total_cents(expenses) > MAX_CENTS:
        raise ValueError("This bill's total is too large. Please check the amounts.")


def total_cents(expenses):
    return sum(int(batch["totalCents"]) for batch in expenses)


def parse_handles(text):
    handles = []
    for token in re.split(r"[\s,;]+", text.strip()):
        name = token.lstrip("@").casefold()
        if not re.fullmatch(r"[a-z0-9_]{1,32}", name):
            raise ValueError("Send Telegram handles separated by spaces, commas, or new lines, for example: @alice @bob")
        if name not in handles:
            handles.append(name)
    if not handles or len(handles) > MAX_PEOPLE:
        raise ValueError("Enter between 1 and 50 Telegram handles.")
    return handles


def split_equally(total, users):
    unique = {str(user["telegramID"]): user for user in users}
    if not unique or len(unique) > MAX_PEOPLE or total <= 0:
        raise ValueError("Select between 1 and 50 people for a bill with a positive total.")
    base, remainder = divmod(int(total), len(unique))
    return [{
        "userID": user_id, "username": user.get("username", ""), "firstName": user.get("firstName", ""),
        "amountCents": base + (index < remainder),
    } for index, (user_id, user) in enumerate(unique.items())]


def receipt_lines(receipt, number, *, html=False):
    render = escape if html else str
    lines = [f"Receipt {number}:"]
    for index, item in enumerate(receipt["items"], 1):
        quantity = f" (QTY: {render(item['quantity'])})" if item.get("quantity") else ""
        price = render(item.get("price") or "price not detected")
        lines.append(f"{index}. {render(item['name'])}{quantity} - {price}")
    total = receipt.get("total", "")
    if not total and "totalCents" in receipt:
        total = money(receipt["totalCents"])
    summary = [
        ("Subtotal", receipt.get("subtotal", "")), ("Service charge", receipt.get("serviceCharge", "")),
        ("Tax", receipt.get("tax", "")), ("Total", total),
    ]
    summary_lines = [f"{label}: {render(value)}" for label, value in summary if value]
    if summary_lines:
        lines.extend(["", *summary_lines])
    return lines


def expense_lines(expenses, *, html=False):
    render = escape if html else str
    lines = [
        f"{render(item['name'])} - {render(item['price'])}"
        for batch in expenses if batch["source"] == "text" for item in batch["items"]
    ]
    receipts = [batch for batch in expenses if batch["source"] == "receipt"]
    for number, receipt in enumerate(receipts, 1):
        if lines:
            lines.append("")
        lines.extend(receipt_lines(receipt, number, html=html))
    return lines


def expense_summary_lines(expenses, *, html=False):
    total = f"Total: {money(total_cents(expenses))}"
    return [*expense_lines(expenses, html=html), "", "--------------------", f"<b>{total}</b>" if html else total]


def bill_lines(bill, current_users):
    lines = ["<b>Splitting Bill</b>", "", f"<b>Total: {money(bill['totalCents'])}</b>", "", "Each person pays:"]
    for person in bill["participants"]:
        user = current_users.get(str(person["userID"])) or person
        label = f"@{user['username']}" if user.get("username") else (user.get("firstName") or f"User {person['userID']}")
        lines.append(f'<a href="tg://user?id={int(person["userID"])}">{escape(label)}</a> - {money(person["amountCents"])}')
    rounded = bill.get("allocation", {}).get("rounded", (
        bill.get("splitMethod", "equal") == "equal" and int(bill["totalCents"]) % len(bill["participants"])
    ))
    if rounded:
        lines.extend(["", "The remaining cents are distributed so the shares add up to the total."])
    lines.extend(["", *expense_lines(bill["expenses"], html=True)])
    return lines
