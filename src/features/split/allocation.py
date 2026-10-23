"""Split frozen bill expenses using exact amounts, percentages, or item owners."""

from decimal import Decimal
from fractions import Fraction
import re

from features.split import billing
from features.split.amounts import parse_amount

METHODS = {"equal": "Equal", "exact": "Exact amounts", "percentage": "Percentages", "item": "By item"}


def _participants(users, amounts):
    return [{
        "userID": str(user["telegramID"]), "username": user.get("username", ""),
        "firstName": user.get("firstName", ""), "amountCents": int(amount),
    } for user, amount in zip(users, amounts)]


def _distribute(total, weights):
    """Largest remainders conserve cents; equal remainders use participant order."""
    weight = sum(weights)
    if weight <= 0 or any(value < 0 for value in weights):
        raise ValueError("The selected items must have a positive combined price.")
    quotas = [Fraction(total) * value / weight for value in weights]
    amounts = [value.numerator // value.denominator for value in quotas]
    order = sorted(range(len(weights)), key=lambda index: quotas[index] - amounts[index], reverse=True)
    for index in order[:total - sum(amounts)]:
        amounts[index] += 1
    return amounts, any(value.denominator != 1 for value in quotas)


def _person_index(reference, users):
    if re.fullmatch(r"[0-9]+", reference):
        number = int(reference)
        if 1 <= number <= len(users):
            return number - 1
    elif reference.startswith("@"):
        matches = [index for index, user in enumerate(users)
                   if user.get("username", "").casefold() == reference[1:].casefold()]
        if len(matches) == 1:
            return matches[0]
    raise ValueError(f"Unknown or ambiguous person: {reference}. Use a person number from the list.")


def _rows(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or len(lines) > billing.MAX_ITEMS:
        raise ValueError("Send your allocations together, one per line, using the examples above.")
    for line in lines:
        match = re.fullmatch(r"(\S+)\s+-\s*(.+)", line)
        if not match:
            raise ValueError("Use a space before the dash, for example: 1 - 20.00")
        yield match[1], match[2].strip()


def _exact_amount(value):
    amount = parse_amount(value)
    if amount is None or amount < 0 or amount > Decimal(billing.MAX_CENTS) / 100 or amount * 100 != (amount * 100).to_integral_value():
        raise ValueError("Enter nonnegative amounts with at most two decimal places, for example: 1 - 20.00")
    return int(amount * 100)


def _percentage(value):
    if not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,4})?\s*%?", value):
        raise ValueError("Enter percentages from 0 to 100 with up to four decimal places, for example: 1 - 60%")
    amount = Decimal(value.rstrip("%").strip())
    if amount > 100:
        raise ValueError("Each percentage must be between 0 and 100.")
    return amount


def _person_values(text, users, parser):
    values = {}
    for reference, value in _rows(text):
        index = _person_index(reference, users)
        if index in values:
            raise ValueError(f"Person {index + 1} was listed more than once. List each person once.")
        values[index] = parser(value)
    missing = [str(index + 1) for index in range(len(users)) if index not in values]
    if missing:
        raise ValueError("Missing people: " + ", ".join(missing) + ". Include everyone, using 0 for no share.")
    return [values[index] for index in range(len(users))]


def item_catalog(expenses):
    """Stable global item numbers follow the displayed text-first, receipt-last order."""
    ordered = sorted(enumerate(expenses), key=lambda pair: pair[1]["source"] == "receipt")
    items = []
    for batch_index, batch in ordered:
        for item_index, item in enumerate(batch["items"]):
            amount = parse_amount(item.get("price", ""))
            if amount is None or amount < 0 or amount > Decimal(billing.MAX_CENTS) / 100 or amount * 100 != (amount * 100).to_integral_value():
                raise ValueError("Splitting by item needs a readable, nonnegative price on every item. Use exact amounts, percentages, or equal splitting for this bill.")
            items.append({
                "number": len(items) + 1, "batchIndex": batch_index, "itemIndex": item_index,
                "name": item["name"], "quantity": item.get("quantity", ""),
                "priceCents": int(amount * 100),
            })
        if not batch["items"] or sum(item["priceCents"] for item in items if item["batchIndex"] == batch_index) <= 0:
            raise ValueError("Splitting by item needs positive priced items in each addition. Use another split method for this bill.")
    return items


def _by_item(expenses, users, text):
    catalog = item_catalog(expenses)
    owners = {}
    for reference, value in _rows(text):
        if not re.fullmatch(r"[0-9]+", reference) or not 1 <= int(reference) <= len(catalog):
            raise ValueError(f"Unknown item: {reference}. Use an item number from the list.")
        number = int(reference)
        if number in owners:
            raise ValueError(f"Item {number} was assigned twice. Put everyone sharing it on one line.")
        people = [_person_index(person, users) for person in re.split(r"[\s,;]+", value) if person]
        if not people:
            raise ValueError(f"Assign item {number} to at least one person.")
        if len(people) != len(set(people)):
            raise ValueError(f"Item {number} lists the same person twice. List each person once per item.")
        owners[number] = people
    missing = [str(item["number"]) for item in catalog if item["number"] not in owners]
    if missing:
        raise ValueError("Assign every item. Missing items: " + ", ".join(missing))
    return _item_amounts(expenses, users, catalog, owners)


def _item_amounts(expenses, users, catalog, owners):
    amounts = [0] * len(users)
    rounded = False
    for batch_index, batch in enumerate(expenses):
        weights = [Fraction(0)] * len(users)
        for item in catalog:
            if item["batchIndex"] != batch_index:
                continue
            people = owners[item["number"]]
            for index in people:
                weights[index] += Fraction(item["priceCents"], len(people))
        shares, batch_rounded = _distribute(int(batch["totalCents"]), weights)
        amounts = [amount + share for amount, share in zip(amounts, shares)]
        rounded |= batch_rounded
    assignments = [{
        "itemNumber": item["number"], "batchIndex": item["batchIndex"], "itemIndex": item["itemIndex"],
        "userIDs": [str(users[index]["telegramID"]) for index in owners[item["number"]]],
    } for item in catalog]
    return amounts, {"itemAssignments": assignments, "receiptAdjustment": "proportional", "rounded": rounded}


def calculate_item_choices(expenses, users, choices):
    """Allocate a saved checklist using participant IDs, independent of usernames."""
    ids = [str(user["telegramID"]) for user in users]
    if not 1 <= len(ids) <= billing.MAX_PEOPLE or len(ids) != len(set(ids)):
        raise ValueError("Select between 1 and 50 distinct people.")
    catalog = item_catalog(expenses)
    indices = {user_id: index for index, user_id in enumerate(ids)}
    owners = {}
    for item in catalog:
        selected = choices.get(str(item["number"]), {})
        if not selected:
            raise ValueError("Assign every item before finishing the split.")
        if any(user_id not in indices for user_id in selected):
            raise ValueError("Items can only be assigned to the selected people.")
        owners[item["number"]] = [index for user_id, index in indices.items() if selected.get(user_id)]
        if not owners[item["number"]]:
            raise ValueError("Assign every item before finishing the split.")
    amounts, details = _item_amounts(expenses, users, catalog, owners)
    return _participants(users, amounts), details


def calculate(expenses, users, method, text=""):
    ids = [str(user["telegramID"]) for user in users]
    if not 1 <= len(ids) <= billing.MAX_PEOPLE or len(ids) != len(set(ids)):
        raise ValueError("Select between 1 and 50 distinct people.")
    total = billing.total_cents(expenses)
    if total <= 0 or method not in METHODS:
        raise ValueError("Choose a split method for a bill with a positive total.")
    if method == "equal":
        return billing.split_equally(total, users), {"rounded": bool(total % len(users))}
    if method == "exact":
        amounts = _person_values(text, users, _exact_amount)
        entered = sum(amounts)
        if entered != total:
            difference = total - entered
            description = f"{billing.money(difference)} remains" if difference > 0 else f"{billing.money(-difference)} too much"
            raise ValueError(f"Your amounts add up to {billing.money(entered)}, but the bill is {billing.money(total)} ({description}). Resend all amounts together.")
        details = {"rounded": False}
    elif method == "percentage":
        percentages = _person_values(text, users, _percentage)
        if sum(percentages) != 100:
            raise ValueError(f"Your percentages add up to {sum(percentages):f}%. They must add up to 100%. Resend all percentages together.")
        amounts, rounded = _distribute(total, [Fraction(value) for value in percentages])
        details = {"percentages": {user_id: f"{value:f}" for user_id, value in zip(ids, percentages)}, "rounded": rounded}
    else:
        amounts, details = _by_item(expenses, users, text)
    return _participants(users, amounts), details
