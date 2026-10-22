"""Run receipt samples through live Textract without sending Telegram messages."""

import argparse
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from features.split.handlers import receipt_messages
from features.split.service import analyze_receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="*", type=Path, help="JPEG/PNG paths; defaults to this feature's images folder")
    args = parser.parse_args()
    images = args.images or sorted(
        path for path in (Path(__file__).parent / "images").glob("*")
        if path.suffix.lower() in (".png", ".jpg", ".jpeg")
    )
    if not images:
        parser.error("No receipt images found. Provide one or more JPEG/PNG paths.")
    for path in images:
        print(f"\n--- {path.name} ---")
        try:
            receipts = analyze_receipt(path.read_bytes())
        except (OSError, ValueError, BotoCoreError, ClientError) as exc:
            parser.exit(1, f"Could not analyze {path.name}: {exc}\n")
        if any(receipt.items for receipt in receipts):
            print("\n".join(receipt_messages(receipts)))
        else:
            print("No receipt items detected.")


if __name__ == "__main__":
    main()
