import hashlib
import json
import os
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Request

from .auth import require_owner
from .assets import refresh_due_asset_valuations
from .plaid_provider import PlaidProviderError


router = APIRouter()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _reported_tax_lots(
    holding: dict, security: dict, timestamp: str
) -> list[tuple[str, dict]]:
    """Normalize institution-reported lots without inferring them from trades.

    `tax_lots` is optional in Plaid's holdings response.  An empty array means
    the connected institution did not furnish lots, which is materially
    different from a position with no acquisition history.
    """
    lots: list[tuple[str, dict]] = []
    for ordinal, lot in enumerate(holding.get("tax_lots") or []):
        acquired_at = lot.get("original_purchase_datetime")
        acquired_date = str(acquired_at)[:10] if acquired_at else None
        lot_identity = lot.get("institution_lot_id") or hashlib.sha256(
            json.dumps(
                {
                    "account_id": holding.get("account_id"),
                    "security_id": holding.get("security_id"),
                    "acquired_at": acquired_at,
                    "quantity": lot.get("quantity"),
                    "cost_basis": lot.get("cost_basis"),
                    "ordinal": ordinal,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        source_id = (
            f"{holding['account_id']}:{holding['security_id']}:{lot_identity}"
        )
        cost_basis = lot.get("cost_basis")
        lots.append(
            (
                source_id,
                {
                    "account_id": holding["account_id"],
                    "security_id": holding["security_id"],
                    "symbol": security.get("ticker_symbol")
                    or holding["security_id"],
                    "description": security.get("name"),
                    "quantity": lot.get("quantity"),
                    "cost_basis": cost_basis,
                    "cost_basis_status": (
                        "reported" if cost_basis is not None else "unknown"
                    ),
                    "acquired_date": acquired_date,
                    "effective_date": timestamp[:10],
                    "purchase_price": lot.get("purchase_price"),
                    "current_value": lot.get("current_value"),
                    "position_type": lot.get("position_type"),
                    "institution_lot_id": lot.get("institution_lot_id"),
                    "source_lot_id": source_id,
                    "currency": holding.get("iso_currency_code"),
                    "source": "plaid_reported_tax_lot",
                },
            )
        )
    return lots


def sync_connection(request: Request, connection: dict) -> dict:
    storage = request.app.state.storage
    provider = request.app.state.plaid
    owner = connection["owner"]
    connection_id = connection["id"]
    state = storage.get_sync_state(connection_id)
    timestamp = _now()

    accounts = provider.accounts_get(connection["access_token"])["accounts"]
    for account in accounts:
        if account.get("balances", {}).get("iso_currency_code") != "USD":
            continue
        normalized_account = {**account, "connection_id": connection_id}
        storage.upsert_financial_record(owner, connection_id, "account", account["account_id"], normalized_account)
        balance = {"connection_id": connection_id, "account_id": account["account_id"], **account["balances"], "observed_at": timestamp, "source": "plaid_cached"}
        fingerprint = hashlib.sha256(json.dumps({k: v for k, v in balance.items() if k != "observed_at"}, sort_keys=True).encode()).hexdigest()
        storage.upsert_financial_record(owner, connection_id, "balance", f"{account['account_id']}:{fingerprint}", balance)

    cursor = state.get("transactions_cursor")
    pages: list[dict] = []
    if "transactions" in connection.get("products", []):
        original_cursor = cursor
        for attempt in range(3):
            cursor = original_cursor
            pages = []
            try:
                while True:
                    page = provider.transactions_sync(connection["access_token"], cursor)
                    pages.append(page)
                    cursor = page["next_cursor"]
                    if not page["has_more"]:
                        break
                break
            except PlaidProviderError as error:
                if error.code != "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION" or attempt == 2:
                    raise
    for page in pages:
        for transaction in page["added"] + page["modified"]:
            if transaction.get("iso_currency_code") == "USD":
                storage.upsert_financial_record(owner, connection_id, "transaction", transaction["transaction_id"], transaction)
        for removed in page["removed"]:
            source_id = removed["transaction_id"]
            storage.upsert_financial_record(owner, connection_id, "transaction", source_id, {"transaction_id": source_id}, removed=True)

    freshness = {
        **state.get("freshness", {}),
        "accounts": timestamp,
        "balances": timestamp,
        "transactions": timestamp,
    }
    liability_count = 0
    if "liabilities" in connection.get("products", []):
        liabilities = provider.liabilities_get(connection["access_token"]).get(
            "liabilities", {}
        )
        for liability_type, values in liabilities.items():
            for liability in values or []:
                account_id = liability.get("account_id")
                if not account_id:
                    continue
                storage.upsert_financial_record(
                    owner,
                    connection_id,
                    "liability",
                    f"{liability_type}:{account_id}",
                    {
                        **liability,
                        "connection_id": connection_id,
                        "liability_type": liability_type,
                        "observed_at": timestamp,
                        "source": "plaid_cached",
                    },
                )
                liability_count += 1
        freshness["liabilities"] = timestamp
    investment_counts: dict[str, int] = {}
    if "investments" in connection.get("products", []):
        investments = provider.investments_holdings_get(connection["access_token"])
        securities_by_id = {
            security["security_id"]
            : security
            for security in investments.get("securities", [])
            if security.get("security_id")
        }
        for security in investments.get("securities", []):
            if security.get("iso_currency_code") == "USD":
                storage.upsert_financial_record(
                    owner,
                    connection_id,
                    "security",
                    security["security_id"],
                    security,
                )
        reported_lot_ids: set[str] = set()
        for holding in investments.get("holdings", []):
            if holding.get("iso_currency_code") == "USD":
                if holding.get("cost_basis") is None:
                    cost_basis_status = "missing"
                elif holding.get("cost_basis_as_of") and (
                    datetime.fromisoformat(timestamp).date()
                    - datetime.fromisoformat(holding["cost_basis_as_of"]).date()
                ).days > 45:
                    cost_basis_status = "stale"
                elif holding.get("cost_basis_as_of"):
                    cost_basis_status = "reported"
                else:
                    cost_basis_status = "freshness_unknown"
                normalized_holding = {
                    **holding,
                    "cost_basis_status": cost_basis_status,
                    "observed_at": timestamp,
                    "source": "plaid_cached",
                }
                storage.upsert_financial_record(
                    owner,
                    connection_id,
                    "holding",
                    f"{holding['account_id']}:{holding['security_id']}",
                    normalized_holding,
                )
                security = securities_by_id.get(holding["security_id"], {})
                for source_id, lot in _reported_tax_lots(
                    holding, security, timestamp
                ):
                    reported_lot_ids.add(source_id)
                    storage.upsert_financial_record(
                        owner,
                        connection_id,
                        "tax_lot",
                        source_id,
                        {**lot, "connection_id": connection_id},
                    )
        # A provider snapshot is authoritative for its own tax-lot records.
        # Do not touch manually imported Fidelity lots, which are stored under
        # a separate import connection and may fill an institution coverage gap.
        for existing_lot in storage.list_financial_records(owner, "tax_lot"):
            if (
                not existing_lot.get("removed")
                and existing_lot.get("connection_id") == connection_id
                and existing_lot.get("source") == "plaid_reported_tax_lot"
                and existing_lot.get("source_lot_id") not in reported_lot_ids
            ):
                storage.upsert_financial_record(
                    owner,
                    connection_id,
                    "tax_lot",
                    existing_lot["source_lot_id"],
                    existing_lot,
                    removed=True,
                )
        valuations = investments.get("valuation_history") or []
        if not valuations:
            values_by_account: dict[str, float] = {}
            for holding in investments.get("holdings", []):
                if holding.get("iso_currency_code") != "USD":
                    continue
                account_id = holding["account_id"]
                values_by_account[account_id] = values_by_account.get(account_id, 0) + float(
                    holding.get("institution_value") or 0
                )
            valuations = [
                {"date": timestamp[:10], "value": value, "account_id": account_id}
                for account_id, value in values_by_account.items()
            ]
        for valuation in valuations:
            storage.upsert_financial_record(
                owner,
                connection_id,
                "investment_valuation",
                f"{connection_id}:{valuation.get('account_id', 'portfolio')}:{valuation['date']}:{valuation.get('timing', 'close')}",
                {**valuation, "connection_id": connection_id, "currency": "USD"},
            )
        for symbol, observations in investments.get("benchmarks", {}).items():
            for observation in observations:
                storage.upsert_financial_record(
                    owner,
                    connection_id,
                    "benchmark_observation",
                    f"{symbol}:{observation['date']}",
                    {**observation, "symbol": symbol, "currency": "USD"},
                )
        activity_offset = 0
        while True:
            activity_response = provider.investments_transactions_get(
                connection["access_token"],
                "2010-01-01",
                timestamp[:10],
                offset=activity_offset,
                count=500,
            )
            activities = activity_response.get("investment_transactions", [])
            for activity in activities:
                if activity.get("iso_currency_code") == "USD":
                    storage.upsert_financial_record(
                        owner,
                        connection_id,
                        "investment_activity",
                        activity["investment_transaction_id"],
                        activity,
                    )
            activity_offset += len(activities)
            total = activity_response.get("total_investment_transactions")
            if not activities or total is None or activity_offset >= int(total):
                break
        freshness.update({"holdings": timestamp, "investment_activities": timestamp})
        investment_counts = {
            "holdings": len(storage.list_financial_records(owner, "holding")),
            "securities": len(storage.list_financial_records(owner, "security")),
            "investment_activities": len(
                storage.list_financial_records(owner, "investment_activity")
            ),
            "tax_lots": len(
                [
                    item
                    for item in storage.list_financial_records(owner, "tax_lot")
                    if not item["removed"]
                ]
            ),
        }

    state.update({"transactions_cursor": cursor, "freshness": freshness})
    storage.save_sync_state(connection_id, state)
    return {"connection_id": connection_id, "accounts": len(storage.list_financial_records(owner, "account")), "balances": len(storage.list_financial_records(owner, "balance")), "transactions": len([item for item in storage.list_financial_records(owner, "transaction") if not item["removed"]]), "liabilities": liability_count, **investment_counts, "freshness": state["freshness"]}


@router.post("/api/private/connections/{connection_id}/refresh")
def manual_refresh(connection_id: str, request: Request) -> dict:
    owner = require_owner(request)
    connection = request.app.state.storage.get_connection(owner, connection_id)
    if connection is None or connection["status"] == "disconnected":
        raise HTTPException(status_code=404, detail="active connection not found")
    state = request.app.state.storage.get_sync_state(connection_id)
    previous = state.get("last_manual_refresh")
    if previous and (datetime.now(UTC) - datetime.fromisoformat(previous)).total_seconds() < 900:
        raise HTTPException(status_code=429, detail="manual refresh available every 15 minutes", headers={"Retry-After": "900"})
    result = sync_connection(request, connection)
    state = request.app.state.storage.get_sync_state(connection_id)
    state["last_manual_refresh"] = _now()
    request.app.state.storage.save_sync_state(connection_id, state)
    return result


@router.post("/api/internal/nightly-reconcile")
def nightly_reconcile(request: Request, x_internal_key: str | None = Header(default=None)) -> dict:
    expected = os.environ.get("FINANCE_INTERNAL_KEY")
    if not expected or x_internal_key != expected:
        raise HTTPException(status_code=401, detail="invalid internal credential")
    connections = request.app.state.storage.list_all_connections()
    results = [sync_connection(request, item) for item in connections if item["status"] != "disconnected"]
    owners = sorted({item["owner"] for item in connections})
    asset_valuations = {
        owner: refresh_due_asset_valuations(request, owner) for owner in owners
    }
    raw_replies_purged = request.app.state.storage.purge_expired_email_reply_raw(
        datetime.now(UTC)
    )
    return {
        "connections": results,
        "asset_valuations": asset_valuations,
        "raw_email_replies_purged": raw_replies_purged,
    }


@router.post("/api/public/plaid/webhook")
async def plaid_webhook(request: Request) -> dict:
    body = await request.body()
    if not request.app.state.plaid.verify_webhook(body, request.headers.get("Plaid-Verification")):
        raise HTTPException(status_code=401, detail="invalid webhook signature")
    fingerprint = hashlib.sha256(body).hexdigest()
    if not request.app.state.storage.record_webhook_once(fingerprint):
        return {"accepted": True, "duplicate": True}
    payload = json.loads(body)
    connection = request.app.state.storage.find_connection_by_item(payload.get("item_id", ""))
    if connection is not None and connection["status"] != "disconnected":
        sync_connection(request, connection)
    return {"accepted": True, "duplicate": False}


@router.get("/api/private/data/summary")
def data_summary(request: Request) -> dict:
    owner = require_owner(request)
    connections = request.app.state.storage.list_connections(owner)
    freshness = {item["id"]: request.app.state.storage.get_sync_state(item["id"]).get("freshness", {}) for item in connections}
    return {"accounts": request.app.state.storage.list_financial_records(owner, "account"), "balances": request.app.state.storage.list_financial_records(owner, "balance"), "transactions": request.app.state.storage.list_financial_records(owner, "transaction"), "freshness": freshness}
