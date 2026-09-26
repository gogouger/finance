import csv
import json
from datetime import UTC, datetime
from io import StringIO
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .auth import require_fresh_owner
from .plaid_provider import PlaidProviderError


router = APIRouter()
SENSITIVE_KEY_PARTS = (
    "access_token",
    "client_secret",
    "credential",
    "password",
    "private_key",
    "recovery",
    "totp_secret",
)
COLLECTION_NAMES = {
    "account": "accounts",
    "balance": "balances",
    "transaction": "transactions",
    "security": "securities",
    "holding": "holdings",
    "investment_activity": "investment_activities",
    "investment_valuation": "investment_valuations",
    "benchmark_observation": "benchmark_observations",
    "tax_lot": "tax_lots",
    "household_asset": "household_assets",
}
ERASURE_CONFIRMATION = "PERMANENTLY ERASE MY FINANCE DATA"


class ErasureIntentRequest(BaseModel):
    pass


class ErasureConfirmation(BaseModel):
    confirmation_id: str = Field(min_length=20, max_length=200)
    confirmation: str = Field(min_length=1, max_length=120)


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _safe(item)
            for key, item in value.items()
            if not any(part in key.lower() for part in SENSITIVE_KEY_PARTS)
        }
    if isinstance(value, list):
        return [_safe(item) for item in value]
    return value


def _envelope(collection: str, source_id: str, data: dict, **metadata: Any) -> dict:
    return {
        "collection": collection,
        "source_id": source_id,
        **metadata,
        "data": _safe(data),
    }


def _export_document(request: Request, owner: str) -> dict:
    storage = request.app.state.storage
    records = []
    for connection in storage.list_connections(owner):
        records.append(
            _envelope("connections", connection["id"], connection)
        )
    for item in storage.list_financial_records_for_export(owner):
        collection = COLLECTION_NAMES.get(item["kind"], f"{item['kind']}s")
        records.append(
            _envelope(
                f"normalized/{collection}",
                item["source_id"],
                item["record"],
                connection_id=item["connection_id"],
                removed=item["removed"],
                updated_at=item["updated_at"],
            )
        )
    email_notes = [
        {
            key: reply[key]
            for key in (
                "id",
                "received_at",
                "raw_expires_at",
                "status",
                "validation_evidence",
                "note",
                "provenance",
                "state_changes_applied",
            )
            if key in reply
        }
        for reply in storage.list_email_replies(owner)
        if reply.get("note") is not None
    ]
    mcp_grants = [
        {
            key: grant[key]
            for key in (
                "id",
                "client_id",
                "client_name",
                "redirect_uri",
                "scopes",
                "status",
                "created_at",
                "revoked_at",
            )
            if key in grant
        }
        for grant in storage.list_mcp_grants(owner)
    ]
    groups = (
        ("classification_rules", storage.list_classification_rules(owner), "id"),
        ("transaction_classifications", storage.list_transaction_classifications(owner), "transaction_id"),
        ("transaction_adjustments", storage.list_transaction_adjustments(owner), "transaction_id"),
        ("recurring_obligations", storage.list_recurring_obligations(owner), "id"),
        ("scenarios", storage.list_scenarios(owner), "id"),
        ("email_notes", email_notes, "id"),
        ("mcp_grants", mcp_grants, "id"),
        ("audit_events", storage.list_audits(owner), "id"),
    )
    for collection, values, source_key in groups:
        records.extend(
            _envelope(collection, str(value[source_key]), value)
            for value in values
        )
    records.sort(key=lambda item: (item["collection"], item["source_id"]))
    return {
        "format": "finance-portable-records",
        "schema_version": 1,
        "currency": "USD",
        "generated_at": datetime.now(UTC).isoformat(),
        "documentation": "docs/data-export.md",
        "records": records,
    }


@router.get("/api/private/lifecycle/export.json")
def export_json(request: Request) -> JSONResponse:
    owner = require_fresh_owner(request)
    return JSONResponse(
        _export_document(request, owner),
        headers={"Content-Disposition": 'attachment; filename="finance-export.json"'},
    )


@router.get("/api/private/lifecycle/export.csv")
def export_csv(request: Request) -> Response:
    owner = require_fresh_owner(request)
    document = _export_document(request, owner)
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "schema_version",
            "collection",
            "source_id",
            "connection_id",
            "removed",
            "updated_at",
            "data_json",
        ],
        lineterminator="\n",
    )
    writer.writeheader()
    for item in document["records"]:
        writer.writerow(
            {
                "schema_version": document["schema_version"],
                "collection": item["collection"],
                "source_id": item["source_id"],
                "connection_id": item.get("connection_id", ""),
                "removed": str(item.get("removed", "")).lower(),
                "updated_at": item.get("updated_at", ""),
                "data_json": json.dumps(
                    item["data"], separators=(",", ":"), sort_keys=True
                ),
            }
        )
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="finance-export.csv"'},
    )


@router.post("/api/private/lifecycle/erasure-intents")
def create_erasure_intent(
    payload: ErasureIntentRequest, request: Request
) -> dict:
    owner = require_fresh_owner(request)
    intent = request.app.state.storage.create_erasure_intent(owner)
    return {
        "confirmation_id": intent["id"],
        "required_confirmation": ERASURE_CONFIRMATION,
        "expires_at": intent["expires_at"],
        "warning": "This permanently revokes provider access and erases local financial records, rules, scenarios, and audit history.",
    }


@router.delete("/api/private/lifecycle/data")
def erase_finance_data(payload: ErasureConfirmation, request: Request) -> dict:
    owner = require_fresh_owner(request)
    if payload.confirmation != ERASURE_CONFIRMATION:
        raise HTTPException(status_code=422, detail="exact confirmation phrase required")
    storage = request.app.state.storage
    if not storage.erasure_intent_is_valid(owner, payload.confirmation_id):
        raise HTTPException(status_code=409, detail="erasure intent is invalid or expired")
    for connection in storage.list_connections(owner):
        if connection.get("status") == "disconnected":
            continue
        try:
            request.app.state.plaid.remove_item(connection["access_token"])
        except PlaidProviderError as error:
            raise HTTPException(
                status_code=502,
                detail="provider access could not be revoked; no local data was erased",
            ) from error
    try:
        storage.erase_owner_data(owner, payload.confirmation_id)
    except KeyError as error:
        raise HTTPException(
            status_code=409, detail="erasure intent is invalid or expired"
        ) from error
    return {
        "erased": True,
        "provider_access_revoked": True,
        "retained": [
            "installation metadata",
            "non-identifying service configuration",
        ],
    }
