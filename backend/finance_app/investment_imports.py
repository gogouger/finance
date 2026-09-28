import csv
import hashlib
import json
import re
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
FIDELITY_POSITION_COLUMNS = {
    "Account number",
    "Account name",
    "Symbol",
    "Description",
    "Quantity",
    "Current value",
    "Cost basis total",
    "Total gain/loss dollar",
    "Total gain/loss percent",
    "Type",
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


def _signed_number(value: str, field: str, row_number: int, *, optional: bool = False) -> float | None:
    cleaned = value.strip().replace("$", "").replace(",", "").replace("+", "")
    if optional and not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError as error:
        raise ValueError(f"row {row_number}: {field} must be a number") from error


def _percent(value: str, field: str, row_number: int, *, optional: bool = False) -> float | None:
    cleaned = value.strip().replace("%", "")
    return _signed_number(cleaned, field, row_number, optional=optional)


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
    identities: set[tuple[str, str, str, float, float | None]] = set()
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
        quantity = _number(row["Quantity"], "Quantity", row_number)
        identity = (account_id, symbol, acquired_date, quantity, cost_basis)
        if identity in identities:
            raise ValueError(f"duplicate logical record at row {row_number}")
        identities.add(identity)
        records.append(
            {
                "kind": "tax_lot",
                "account_id": account_id,
                "symbol": symbol,
                "description": description,
                "quantity": quantity,
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


def _fidelity_position_effective_date(content: str) -> str:
    """Read Fidelity's stated report date rather than treating an import date as one."""
    match = re.search(
        r"Date downloaded\s+([A-Za-z]{3}-\d{1,2}-\d{4})",
        content,
    )
    if not match:
        raise ValueError("missing Fidelity Date downloaded footer")
    try:
        return datetime.strptime(match.group(1), "%b-%d-%Y").date().isoformat()
    except ValueError as error:
        raise ValueError("Fidelity Date downloaded is invalid") from error


def parse_fidelity_positions(content: str) -> list[dict]:
    """Parse Fidelity's position-level export without fabricating tax lots."""
    effective_date = _fidelity_position_effective_date(content)
    rows = _rows(content, FIDELITY_POSITION_COLUMNS)
    records = []
    identities: set[tuple[str, str]] = set()
    for row_number, row in enumerate(rows, start=2):
        # Fidelity appends disclosure rows after a blank line. They carry no
        # account number and are deliberately outside the position snapshot.
        account_id = (row.get("Account number") or "").strip()
        if not account_id:
            continue
        symbol = (row.get("Symbol") or "").strip().upper()
        description = (row.get("Description") or "").strip()
        account_name = (row.get("Account name") or "").strip()
        if not any((symbol, description, account_name)):
            continue
        if not symbol or not description or not account_name:
            raise ValueError(
                f"row {row_number}: account name, symbol, and description are required"
            )
        identity = (account_id, symbol)
        if identity in identities:
            raise ValueError(f"duplicate position at row {row_number}")
        identities.add(identity)
        description_key = description.casefold()
        records.append(
            {
                "kind": "holding",
                "account_id": account_id,
                "account_name": account_name,
                "security_id": f"fidelity_positions:{account_id}:{symbol}",
                "symbol": symbol,
                "description": description,
                "quantity": _number(
                    row.get("Quantity") or "", "Quantity", row_number, optional=True
                ),
                "institution_value": _number(
                    row.get("Current value") or "", "Current value", row_number
                ),
                "cost_basis": _number(
                    row.get("Cost basis total") or "", "Cost basis total", row_number, optional=True
                ),
                "cost_basis_status": (
                    "reported" if (row.get("Cost basis total") or "").strip() else "unknown"
                ),
                "reported_total_gain": _signed_number(
                    row.get("Total gain/loss dollar") or "",
                    "Total gain/loss dollar",
                    row_number,
                    optional=True,
                ),
                "reported_total_gain_percent": _percent(
                    row.get("Total gain/loss percent") or "",
                    "Total gain/loss percent",
                    row_number,
                    optional=True,
                ),
                "security_type": (
                    "cash equivalent"
                    if "held in money market" in description_key
                    else None
                ),
                "effective_date": effective_date,
                "currency": "USD",
                "iso_currency_code": "USD",
                "source": "fidelity_positions_csv",
            }
        )
    if not records:
        raise ValueError("the export contains no position rows")
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


def _record_identity(provider: str, record: dict, *, lot_ordinal: int = 0) -> str:
    if record["kind"] == "tax_lot":
        identity = {
            "provider": provider,
            "kind": record["kind"],
            "account_id": record["account_id"],
            "symbol": record["symbol"],
            "acquired_date": record["acquired_date"],
            # Fidelity can report more than one remaining lot for the same
            # symbol on the same day. The ordinal preserves those lots while
            # still allowing a later export to fill in an unknown basis.
            "lot_ordinal": lot_ordinal,
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
    lot_ordinals: dict[tuple[str, str, str], int] = {}
    for record in records:
        lot_ordinal = 0
        if record["kind"] == "tax_lot":
            lot_key = (
                record["account_id"],
                record["symbol"],
                record["acquired_date"],
            )
            lot_ordinal = lot_ordinals.get(lot_key, 0)
            lot_ordinals[lot_key] = lot_ordinal + 1
        record_id = _record_identity(provider, record, lot_ordinal=lot_ordinal)
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


@router.post("/api/private/investments/imports/fidelity-positions/preview")
def preview_fidelity_positions(payload: ImportPayload, request: Request) -> dict:
    require_owner(request)
    try:
        return _preview("fidelity_positions", parse_fidelity_positions(payload.content))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/api/private/investments/imports/fidelity-positions/commit")
def commit_fidelity_positions(payload: ImportPayload, request: Request) -> dict:
    owner = require_owner(request)
    try:
        records = parse_fidelity_positions(payload.content)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _commit(request, owner, "fidelity_positions", records)


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
