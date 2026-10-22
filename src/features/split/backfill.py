"""Index previously completed bills so participants can see them through /bills."""

import argparse

from boto3.dynamodb.conditions import Attr
from botocore.exceptions import BotoCoreError, ClientError

from core.db import users_table
from features.split import ledger


def backfill(*, apply=False):
    """Dry run by default. Re-running preserves existing payment records."""
    params = {"FilterExpression": Attr("profile").begins_with(ledger.BILL_PREFIX), "ConsistentRead": True}
    found = indexed = 0
    while True:
        page = users_table().scan(**params)
        for bill in page.get("Items", []):
            if bill.get("ledgerVersion") == 1:
                continue
            found += 1
            if apply:
                if ledger.index_bill(bill) is None:
                    raise RuntimeError("A bill disappeared while indexing. Re-run the backfill.")
                indexed += 1
        if not page.get("LastEvaluatedKey"):
            break
        params["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return found, indexed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Save the indexes; without this flag only count older bills")
    args = parser.parse_args()
    try:
        found, indexed = backfill(apply=args.apply)
    except (BotoCoreError, ClientError, RuntimeError) as exc:
        parser.exit(1, f"Could not index saved bills: {exc}\n")
    if args.apply:
        print(f"Indexed {indexed} previously completed bills. Existing payments were preserved.")
    else:
        print(f"Found {found} previously completed bills to index. Run again with --apply to save the indexes.")


if __name__ == "__main__":
    main()
