import hashlib
import json
import os
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Request

from .auth import require_owner
from .plaid_provider import PlaidProviderError


router = APIRouter()


def _now() -> str:
    return datetime.now(UTC).isoformat()


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
        for security in investments.get("securities", []):
            if security.get("iso_currency_code") == "USD":
                storage.upsert_financial_record(
                    owner,
                    connection_id,
                    "security",
                    security["security_id"],
                    security,
                )
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
        valuations = investments.get("valuation_history") or [
            {
                "date": timestamp[:10],
                "value": sum(
                    float(holding.get("institution_value") or 0)
                    for holding in investments.get("holdings", [])
                    if holding.get("iso_currency_code") == "USD"
                ),
            }
        ]
        for valuation in valuations:
            storage.upsert_financial_record(
                owner,
                connection_id,
                "investment_valuation",
                f"{connection_id}:{valuation['date']}:{valuation.get('timing', 'close')}",
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
    results = [sync_connection(request, item) for item in request.app.state.storage.list_all_connections() if item["status"] != "disconnected"]
    raw_replies_purged = request.app.state.storage.purge_expired_email_reply_raw(
        datetime.now(UTC)
    )
    return {
        "connections": results,
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
