import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from .storage import EncryptedStorage


EXPECTED_COLUMNS = {
    "Transaction Date",
    "Posted Date",
    "Card No.",
    "Description",
    "Category",
    "Debit",
    "Credit",
}

CATEGORY_MAP = {
    "Dining": ("FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT"),
    "Entertainment": ("ENTERTAINMENT", "ENTERTAINMENT_OTHER_ENTERTAINMENT"),
    "Fee/Interest Charge": ("BANK_FEES", "BANK_FEES_INTEREST_CHARGE"),
    "Gas/Automotive": ("TRANSPORTATION", "TRANSPORTATION_OTHER_TRANSPORTATION"),
    "Health Care": ("MEDICAL", "MEDICAL_OTHER_MEDICAL"),
    "Insurance": ("GENERAL_SERVICES", "GENERAL_SERVICES_INSURANCE"),
    "Internet": ("RENT_AND_UTILITIES", "RENT_AND_UTILITIES_INTERNET_AND_CABLE"),
    "Merchandise": ("GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_OTHER"),
    "Other Services": ("GENERAL_SERVICES", "GENERAL_SERVICES_OTHER_GENERAL_SERVICES"),
    "Other Travel": ("TRAVEL", "TRAVEL_OTHER_TRAVEL"),
    "Payment/Credit": ("LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
    "Phone/Cable": ("RENT_AND_UTILITIES", "RENT_AND_UTILITIES_TELEPHONE"),
    "Professional Services": ("GENERAL_SERVICES", "GENERAL_SERVICES_OTHER_GENERAL_SERVICES"),
    "Utilities": ("RENT_AND_UTILITIES", "RENT_AND_UTILITIES_OTHER_UTILITIES"),
}


def _normalized_description(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _amount(row: dict[str, str], row_number: int) -> float:
    debit = row["Debit"].strip()
    credit = row["Credit"].strip()
    if bool(debit) == bool(credit):
        raise ValueError(
            f"row {row_number} must contain exactly one of Debit or Credit"
        )
    try:
        value = Decimal(debit or credit)
    except InvalidOperation as error:
        raise ValueError(f"row {row_number} contains an invalid amount") from error
    if value < 0:
        raise ValueError(f"row {row_number} contains a negative source amount")
    return float(value if debit else -value)


def _parsed_date(value: str, field: str, row_number: int) -> str:
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as error:
        raise ValueError(f"row {row_number} contains an invalid {field}") from error


def _fingerprint(transaction: dict) -> tuple[str, str, str]:
    return (
        transaction["date"],
        f"{Decimal(str(transaction['amount'])):.2f}",
        _normalized_description(transaction.get("name") or ""),
    )


def _source_id(row: dict[str, str], occurrence: int) -> str:
    canonical = json.dumps(
        {key: row[key].strip() for key in sorted(EXPECTED_COLUMNS)},
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(f"{canonical}:{occurrence}".encode()).hexdigest()
    return f"capital-one-csv:{digest}"


def import_capital_one_csv(
    storage: EncryptedStorage,
    connection: dict,
    source: Iterable[str],
    *,
    dry_run: bool = False,
) -> dict:
    if connection.get("provider") != "plaid" or connection.get("status") == "disconnected":
        raise ValueError("the target must be an active Plaid connection")
    if connection.get("connection_type") != "credit":
        raise ValueError("the target must be a credit-card connection")

    reader = csv.DictReader(source)
    if set(reader.fieldnames or []) != EXPECTED_COLUMNS:
        raise ValueError("the CSV columns do not match a Capital One transaction export")

    owner = connection["owner"]
    connection_id = connection["id"]
    accounts = [
        item
        for item in storage.list_financial_records(owner, "account")
        if item.get("connection_id") == connection_id
        and item.get("type") == "credit"
        and not item.get("removed")
    ]
    if len(accounts) != 1:
        raise ValueError("the Capital One connection must contain exactly one credit account")
    account_id = accounts[0]["account_id"]

    existing = Counter(
        _fingerprint(item)
        for item in storage.list_financial_records(owner, "transaction")
        if not item.get("removed")
    )
    occurrences: Counter[str] = Counter()
    imported = 0
    overlapped = 0
    earliest: str | None = None
    latest: str | None = None

    for row_number, row in enumerate(reader, 2):
        canonical = json.dumps(
            {key: row[key].strip() for key in sorted(EXPECTED_COLUMNS)},
            separators=(",", ":"),
            sort_keys=True,
        )
        occurrence = occurrences[canonical]
        occurrences[canonical] += 1
        posted_date = _parsed_date(row["Posted Date"], "Posted Date", row_number)
        authorized_date = _parsed_date(
            row["Transaction Date"], "Transaction Date", row_number
        )
        amount = _amount(row, row_number)
        description = re.sub(r"\s+", " ", row["Description"].strip())
        if not description:
            raise ValueError(f"row {row_number} is missing Description")
        category = row["Category"].strip()
        primary, detailed = CATEGORY_MAP.get(category, ("OTHER", "OTHER_OTHER"))
        transaction = {
            "account_id": account_id,
            "connection_id": connection_id,
            "transaction_id": _source_id(row, occurrence),
            "date": posted_date,
            "authorized_date": authorized_date,
            "name": description,
            "amount": amount,
            "pending": False,
            "iso_currency_code": "USD",
            "payment_channel": "other",
            "personal_finance_category": {
                "primary": primary,
                "detailed": detailed,
                "confidence_level": "UNKNOWN",
            },
            "source": "capital_one_csv",
            "source_category": category,
            "source_card_last4": row["Card No."].strip()[-4:],
        }
        fingerprint = _fingerprint(transaction)
        if existing[fingerprint] > 0:
            existing[fingerprint] -= 1
            overlapped += 1
            continue
        if not dry_run:
            storage.upsert_financial_record(
                owner,
                connection_id,
                "transaction",
                transaction["transaction_id"],
                transaction,
            )
        imported += 1
        earliest = min(earliest, posted_date) if earliest else posted_date
        latest = max(latest, posted_date) if latest else posted_date

    return {
        "connection_id": connection_id,
        "imported": imported,
        "overlap_skipped": overlapped,
        "date_start": earliest,
        "date_end": latest,
        "dry_run": dry_run,
    }


def _active_connection(storage: EncryptedStorage, connection_id: str) -> dict:
    matches = [
        item
        for item in storage.list_all_connections()
        if item.get("id") == connection_id and item.get("status") != "disconnected"
    ]
    if len(matches) != 1:
        raise ValueError("active connection not found")
    return matches[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a Capital One transaction CSV")
    parser.add_argument("--connection-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("csv_path", help="CSV path or - for standard input")
    args = parser.parse_args()
    encryption_key = os.environ.get("FINANCE_ENCRYPTION_KEY")
    if not encryption_key:
        raise SystemExit("FINANCE_ENCRYPTION_KEY is required")
    storage = EncryptedStorage(
        Path(os.environ.get("FINANCE_DATA_DIR", "/data")), encryption_key
    )
    storage.initialize()
    connection = _active_connection(storage, args.connection_id)
    if args.csv_path == "-":
        source = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8-sig", newline="")
        result = import_capital_one_csv(
            storage, connection, source, dry_run=args.dry_run
        )
    else:
        with Path(args.csv_path).open(encoding="utf-8-sig", newline="") as source:
            result = import_capital_one_csv(
                storage, connection, source, dry_run=args.dry_run
            )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
