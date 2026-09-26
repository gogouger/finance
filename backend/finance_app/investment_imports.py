import csv
import hashlib
import json
from datetime import UTC, date, datetime
from io import StringIO

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import require_owner


router = APIRouter()


class ImportPayload(BaseModel):
    content: str = Field(min_length=1, max_length=2_000_000)


FIDELITY_COLUMNS = {
    "Account Number",
    "Symbol",
    "Description",
    "Quantity",
    "Cost Basis Total",
    "Date Acquired",
    "As Of Date",
}
VESTWELL_COLUMNS = {
    "Record Type",
    "Account ID",
    "Symbol",
    "Description",
    "Quantity",
    "Market Value",
    "Cost Basis",
    "Effective Date",
    "Activity Type",
    "Amount",
}
VESTWELL_ACTIVITY_TYPES = {
    "buy",
    "deposit",
    "dividend",
    "fee",
    "interest",
    "sell",
    "withdrawal",
}


def _number(value: str, field: str, row_number: int, *, optional: bool = False) -> float | None:
    cleaned = value.strip().replace("$", "").replace(",", "")
    if optional and not cleaned:
        return None
    try:
        result = float(cleaned)
    except ValueError as error:
        raise ValueError(f"row {row_number}: {field} must be a number") from error
    if result < 0:
        raise ValueError(f"row {row_number}: {field} cannot be negative")
    return result


def _date(value: str, field: str, row_number: int) -> str:
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as error:
        raise ValueError(f"row {row_number}: {field} must use YYYY-MM-DD") from error


def _rows(content: str, required: set[str]) -> list[dict[str, str]]:
    reader = csv.DictReader(StringIO(content.lstrip("\ufeff")))
    present = set(reader.fieldnames or [])
    missing = sorted(required - present)
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")
    rows = list(reader)
    if not rows:
        raise ValueError("the export contains no data rows")
    return rows


def parse_fidelity(content: str) -> list[dict]:
    records = []
    identities: set[tuple[str, str, str]] = set()
    for row_number, row in enumerate(_rows(content, FIDELITY_COLUMNS), start=2):
        account_id = row["Account Number"].strip()
        symbol = row["Symbol"].strip().upper()
        description = row["Description"].strip()
        if not account_id or not symbol or not description:
            raise ValueError(
                f"row {row_number}: account, symbol, and description are required"
            )
        cost_basis = _number(
            row["Cost Basis Total"], "Cost Basis Total", row_number, optional=True
        )
        acquired_date = _date(row["Date Acquired"], "Date Acquired", row_number)
        identity = (account_id, symbol, acquired_date)
        if identity in identities:
            raise ValueError(f"duplicate logical record at row {row_number}")
        identities.add(identity)
        records.append(
            {
                "kind": "tax_lot",
                "account_id": account_id,
                "symbol": symbol,
                "description": description,
                "quantity": _number(row["Quantity"], "Quantity", row_number),
                "cost_basis": cost_basis,
                "cost_basis_status": (
                    "reported" if cost_basis is not None else "unknown"
                ),
                "acquired_date": acquired_date,
                "effective_date": _date(
                    row["As Of Date"], "As Of Date", row_number
                ),
                "currency": "USD",
                "source": "fidelity_csv",
            }
        )
    return records


def parse_vestwell(content: str) -> list[dict]:
    records = []
    for row_number, row in enumerate(_rows(content, VESTWELL_COLUMNS), start=2):
        record_type = row["Record Type"].strip().upper()
        account_id = row["Account ID"].strip()
        symbol = row["Symbol"].strip().upper()
        description = row["Description"].strip()
        effective_date = _date(
            row["Effective Date"], "Effective Date", row_number
        )
        if not account_id or not description:
            raise ValueError(f"row {row_number}: account and description are required")
        if record_type == "HOLDING":
            if not symbol:
                raise ValueError(f"row {row_number}: holdings require a symbol")
            cost_basis = _number(
                row["Cost Basis"], "Cost Basis", row_number, optional=True
            )
            records.append(
                {
                    "kind": "holding",
                    "account_id": account_id,
                    "security_id": f"vestwell:{symbol}",
                    "symbol": symbol,
                    "description": description,
                    "quantity": _number(row["Quantity"], "Quantity", row_number),
                    "institution_value": _number(
                        row["Market Value"], "Market Value", row_number
                    ),
                    "cost_basis": cost_basis,
                    "cost_basis_status": (
                        "reported" if cost_basis is not None else "unknown"
                    ),
                    "effective_date": effective_date,
                    "currency": "USD",
                    "iso_currency_code": "USD",
                    "source": "vestwell_csv",
                }
            )
        elif record_type == "ACTIVITY":
            activity_type = row["Activity Type"].strip().lower()
            if activity_type not in VESTWELL_ACTIVITY_TYPES:
                raise ValueError(
                    f"row {row_number}: unsupported Activity Type {activity_type!r}"
                )
            records.append(
                {
                    "kind": "investment_activity",
                    "account_id": account_id,
                    "symbol": symbol,
                    "description": description,
                    "date": effective_date,
                    "effective_date": effective_date,
                    "type": "fee" if activity_type == "fee" else "cash",
                    "subtype": activity_type,
                    "amount": _number(row["Amount"], "Amount", row_number),
                    "currency": "USD",
                    "iso_currency_code": "USD",
                    "source": "vestwell_csv",
                }
            )
        else:
            raise ValueError(
                f"row {row_number}: Record Type must be HOLDING or ACTIVITY"
            )
    return records


def _preview(provider: str, records: list[dict]) -> dict:
    return {
        "provider": provider,
        "valid": True,
        "counts": {
            "tax_lots": sum(item["kind"] == "tax_lot" for item in records),
            "holdings": sum(item["kind"] == "holding" for item in records),
            "activities": sum(item["kind"] == "investment_activity" for item in records),
        },
        "records": records,
    }


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _record_identity(provider: str, record: dict) -> str:
    if record["kind"] == "tax_lot":
        identity = {
            "provider": provider,
            "kind": record["kind"],
            "account_id": record["account_id"],
            "symbol": record["symbol"],
            "acquired_date": record["acquired_date"],
        }
    elif record["kind"] == "holding":
        identity = {
            "provider": provider,
            "kind": record["kind"],
            "account_id": record["account_id"],
            "symbol": record["symbol"],
        }
    elif record["kind"] == "investment_activity":
        identity = {
            "provider": provider,
            "kind": record["kind"],
            "account_id": record["account_id"],
            "symbol": record["symbol"],
            "date": record["date"],
            "subtype": record["subtype"],
            "amount": record["amount"],
        }
    else:
        identity = {"provider": provider, **record}
    return _fingerprint(identity)


def _commit(request: Request, owner: str, provider: str, records: list[dict]) -> dict:
    storage = request.app.state.storage
    batch_id = _fingerprint({"provider": provider, "records": records})
    existing = {
        item.get("import_record_id")
        for kind in {item["kind"] for item in records}
        for item in storage.list_financial_records(owner, kind)
        if not item["removed"]
    }
    created = 0
    unchanged = 0
    for record in records:
        record_id = _record_identity(provider, record)
        stored = {
            **record,
            "import_record_id": record_id,
            "import_batch_id": batch_id,
        }
        if record_id in existing:
            unchanged += 1
        else:
            created += 1
        storage.upsert_financial_record(
            owner,
            f"import:{provider}",
            record["kind"],
            record_id,
            stored,
        )
    storage.append_audit(
        owner,
        {
            "action": f"investment_import.{provider}.committed",
            "resource_type": "investment_import",
            "resource_id": batch_id,
            "outcome": "success",
            "occurred_at": datetime.now(UTC).isoformat(),
        },
    )
    return {
        "provider": provider,
        "import_batch_id": batch_id,
        "result": {"created": created, "unchanged": unchanged},
    }


@router.post("/api/private/investments/imports/fidelity/preview")
def preview_fidelity(payload: ImportPayload, request: Request) -> dict:
    require_owner(request)
    try:
        return _preview("fidelity", parse_fidelity(payload.content))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/api/private/investments/imports/fidelity/commit")
def commit_fidelity(payload: ImportPayload, request: Request) -> dict:
    owner = require_owner(request)
    try:
        records = parse_fidelity(payload.content)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _commit(request, owner, "fidelity", records)


@router.post("/api/private/investments/imports/vestwell/preview")
def preview_vestwell(payload: ImportPayload, request: Request) -> dict:
    require_owner(request)
    try:
        return _preview("vestwell", parse_vestwell(payload.content))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/api/private/investments/imports/vestwell/commit")
def commit_vestwell(payload: ImportPayload, request: Request) -> dict:
    owner = require_owner(request)
    try:
        records = parse_vestwell(payload.content)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _commit(request, owner, "vestwell", records)
