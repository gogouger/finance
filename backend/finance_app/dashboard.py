from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP
from statistics import median

from fastapi import APIRouter, Request

from .accounting import build_accounting_view
from .auth import require_owner


router = APIRouter()
CENT = Decimal("0.01")


def _money(value: Decimal | float | int) -> float:
    return float(Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP))


def _active(storage, owner: str, kind: str) -> list[dict]:
    return [
        item
        for item in storage.list_financial_records(owner, kind)
        if not item.get("removed")
    ]


def _record_key(record: dict) -> str:
    return f"{record.get('connection_id', 'legacy')}:{record['account_id']}"


def _latest_balances(records: list[dict]) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    history: dict[str, list[dict]] = {}
    for record in records:
        history.setdefault(_record_key(record), []).append(record)
    for values in history.values():
        values.sort(key=lambda item: item.get("observed_at", ""))
    return {key: values[-1] for key, values in history.items()}, history


def _freshness(timestamps: list[str]) -> dict:
    usable = [value for value in timestamps if value]
    if not usable:
        return {"status": "unavailable", "as_of": None}
    as_of = min(usable)
    try:
        age = datetime.now(UTC) - datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except ValueError:
        return {"status": "unknown", "as_of": as_of}
    if age.total_seconds() <= 48 * 60 * 60:
        status = "current"
    elif age.days <= 7:
        status = "delayed"
    else:
        status = "stale"
    return {"status": status, "as_of": as_of}


def _coverage(covered: int, total: int, sources: list[str]) -> dict:
    return {
        "covered": covered,
        "total": total,
        "percent": round(100 * covered / total, 1) if total else 0,
        "sources": sorted(set(sources)),
    }


def _metric(
    key: str,
    label: str,
    value: float,
    definition: str,
    *,
    inclusions: list[str],
    exclusions: list[str],
    timestamps: list[str],
    gaps: list[str],
    confidence: str,
    rationale: str,
    covered: int,
    total: int,
    sources: list[str],
) -> dict:
    return {
        "key": key,
        "label": label,
        "value": _money(value),
        "currency": "USD",
        "definition": definition,
        "inclusions": inclusions,
        "exclusions": exclusions,
        "freshness": _freshness(timestamps),
        "gaps": gaps,
        "confidence": {"level": confidence, "rationale": rationale},
        "source_coverage": _coverage(covered, total, sources),
    }


def _unusual_activity(accounting: dict, balance_history: dict[str, list[dict]]) -> list[dict]:
    spending = [
        item
        for item in accounting["transactions"]
        if item["accounting_type"] == "spending" and item["status"] == "posted"
    ]
    typical = median([item["amount"] for item in spending]) if spending else 0
    threshold = max(500, typical * 3)
    signals = [
        {
            "type": "unusual_charge",
            "title": "Charge is larger than recent typical spending",
            "amount": item["amount"],
            "date": item["date"],
            "merchant_name": item["merchant_name"],
            "explanation": (
                f"This posted charge is at least ${threshold:,.0f}, the larger of "
                "$500 or three times the median posted purchase in available history."
            ),
            "confidence": {
                "level": "medium",
                "rationale": "A deterministic size rule identified the charge; it does not establish fraud or error.",
            },
            "review_required": True,
        }
        for item in spending
        if item["amount"] >= threshold
    ]
    for account_key, observations in balance_history.items():
        if len(observations) < 2:
            continue
        previous, current = observations[-2:]
        change = float(current.get("current") or 0) - float(previous.get("current") or 0)
        material = max(500, abs(float(previous.get("current") or 0)) * 0.1)
        if abs(change) < material:
            continue
        signals.append(
            {
                "type": "material_balance_change",
                "title": "Account balance changed materially",
                "amount": _money(change),
                "date": str(current.get("observed_at", ""))[:10] or None,
                "account_reference": account_key.split(":", 1)[-1],
                "explanation": (
                    "The latest balance differs by at least $500 or 10% from the prior observation."
                ),
                "confidence": {
                    "level": "medium",
                    "rationale": "The change is provider-reported, but its cause has not been classified.",
                },
                "review_required": True,
            }
        )
    return sorted(signals, key=lambda item: (item.get("date") or "", item["type"]), reverse=True)


@router.get("/api/private/dashboard")
def financial_dashboard(request: Request) -> dict:
    owner = require_owner(request)
    storage = request.app.state.storage
    accounts = _active(storage, owner, "account")
    balances, balance_history = _latest_balances(_active(storage, owner, "balance"))
    account_by_key = {_record_key(item): item for item in accounts}
    assets = _active(storage, owner, "household_asset")
    holdings = _active(storage, owner, "holding")
    transactions = storage.list_financial_records(owner, "transaction")
    accounting = build_accounting_view(
        transactions,
        storage.list_transaction_adjustments(owner),
        storage.list_provider_record_versions(owner, "transaction"),
    )
    obligations = [
        item
        for item in storage.list_recurring_obligations(owner)
        if item.get("status") == "confirmed"
    ]

    balance_rows = [
        (account_by_key.get(key, {}), balance)
        for key, balance in balances.items()
    ]
    cash_rows = [row for row in balance_rows if row[0].get("type") == "depository"]
    credit_rows = [row for row in balance_rows if row[0].get("type") == "credit"]
    investment_rows = [row for row in balance_rows if row[0].get("type") == "investment"]
    cash = sum(float(balance.get("current") or 0) for _, balance in cash_rows)
    credit_debt = sum(max(0, float(balance.get("current") or 0)) for _, balance in credit_rows)
    investment_value = sum(float(balance.get("current") or 0) for _, balance in investment_rows)
    if not investment_rows and holdings:
        investment_value = sum(float(item.get("institution_value") or 0) for item in holdings)
    retirement_rows = [
        row
        for row in investment_rows
        if any(
            marker in str(row[0].get("subtype", "")).lower()
            for marker in ("401", "403", "ira", "roth", "retirement", "pension")
        )
    ]
    retirement_value = sum(float(balance.get("current") or 0) for _, balance in retirement_rows)
    household_asset_value = sum(float(item["valuation"]["amount"]) for item in assets)
    asset_debt = sum(float(item["ownership"]["debt_balance"]) for item in assets)
    debt = credit_debt + asset_debt
    net_worth = cash + investment_value + household_asset_value - debt
    monthly_factors = {"monthly": 1, "quarterly": 1 / 3, "annual": 1 / 12}
    true_monthly_cost = sum(
        float(item["amount"]) * monthly_factors[item["cadence"]]
        for item in obligations
    )

    balance_times = [item.get("observed_at", "") for item in balances.values()]
    transaction_times = [
        version["observed_at"]
        for version in storage.list_provider_record_versions(owner, "transaction")
    ]
    asset_times = [item["valuation"]["valued_at"] for item in assets]
    accounting_metrics = accounting["metrics"]
    source_connections = [
        connection["institution_name"]
        for connection in storage.list_connections(owner)
        if connection.get("status") != "disconnected"
    ]
    balance_sources = [*source_connections, "manual household assets"]
    transaction_sources = source_connections
    missing_recurring = [] if obligations else ["No recurring costs have been confirmed yet."]
    liability_gap = [
        "Other loans are excluded until normalized provider liability records are available."
    ]

    metrics = [
        _metric("net_worth", "Net worth", net_worth, "Cash, investments, and registered household assets minus known debts.", inclusions=["latest depository balances", "latest investment balances", "registered home and vehicle values", "credit-card balances", "registered asset debt"], exclusions=["selling costs", "unregistered assets", "unavailable loan liabilities"], timestamps=[*balance_times, *asset_times], gaps=liability_gap, confidence="medium", rationale="Connected balances and registered asset values are included; other loan liabilities may be incomplete.", covered=len(balances) + len(assets), total=len(accounts) + len(assets), sources=balance_sources),
        _metric("cash", "Cash", cash, "Latest current balances for connected depository accounts.", inclusions=["checking", "savings", "other depository accounts"], exclusions=["credit available", "investment cash inside brokerage accounts"], timestamps=[balance.get("observed_at", "") for _, balance in cash_rows], gaps=[] if cash_rows else ["No connected depository balances are available."], confidence="high" if cash_rows else "low", rationale="Computed from latest USD provider balance observations.", covered=len(cash_rows), total=len([item for item in accounts if item.get("type") == "depository"]), sources=transaction_sources),
        _metric("debt", "Known debt", debt, "Positive connected credit-card balances plus debt registered against household assets.", inclusions=["credit-card balances", "registered mortgage and vehicle debt"], exclusions=["unavailable student, personal, and other provider loan liabilities"], timestamps=[*balance_times, *asset_times], gaps=liability_gap, confidence="medium", rationale="Known balances are exact to their sources, but liability-source coverage is incomplete.", covered=len(credit_rows) + len([item for item in assets if item["ownership"]["debt_balance"] > 0]), total=len(credit_rows) + len([item for item in assets if item["ownership"]["debt_balance"] > 0]) + 1, sources=balance_sources),
        _metric("income", "Current income", accounting_metrics["income"], "Posted transaction inflows classified as income in available history.", inclusions=["posted income transactions"], exclusions=["transfers", "refunds", "pending income", "unconnected payroll history"], timestamps=transaction_times, gaps=[], confidence="medium", rationale="Provider transaction categories are used until the owner reviews classifications.", covered=len(transaction_sources), total=len(transaction_sources), sources=transaction_sources),
        _metric("raw_cash_flow", "Raw cash flow", accounting_metrics["cash_flow"]["net"], "All posted cash inflows minus all posted cash outflows, including transfers and card payments.", inclusions=["posted inflows", "posted outflows", "transfers", "credit-card payments"], exclusions=["pending transactions"], timestamps=transaction_times, gaps=[], confidence="high", rationale="Cash movement is taken directly from posted normalized transactions.", covered=len(transaction_sources), total=len(transaction_sources), sources=transaction_sources),
        _metric("raw_spending", "Raw spending", accounting_metrics["finalized_spending"]["raw"], "Posted purchase outflows before refunds or owner adjustments.", inclusions=["posted purchases"], exclusions=["income", "transfers", "credit-card payments", "pending transactions", "refund offsets"], timestamps=transaction_times, gaps=[], confidence="high", rationale="Deterministic accounting rules separate purchases from non-spending movements.", covered=len(transaction_sources), total=len(transaction_sources), sources=transaction_sources),
        _metric("adjusted_personal_spending", "Adjusted personal spending", accounting_metrics["finalized_spending"]["adjusted"], "Posted personal purchase spending after refunds and owner adjustments.", inclusions=["posted personal purchases", "linked refunds", "owner-confirmed personal shares"], exclusions=["transfers", "card payments", "pending purchases", "reimbursable, business, and excluded shares"], timestamps=transaction_times, gaps=[], confidence="high", rationale="Uses normalized accounting plus explicit owner adjustments; unreviewed provider categories remain visible separately.", covered=len(transaction_sources), total=len(transaction_sources), sources=transaction_sources),
        _metric("true_monthly_cost", "True monthly cost", true_monthly_cost, "Monthly equivalent of owner-confirmed recurring, quarterly, and annual obligations.", inclusions=["confirmed recurring obligations"], exclusions=["unconfirmed recurring proposals", "one-off spending"], timestamps=[item.get("updated_at", "") for item in obligations], gaps=missing_recurring, confidence="high" if obligations else "low", rationale="Only owner-confirmed obligations affect this metric.", covered=len(obligations), total=max(1, len(obligations)), sources=["confirmed recurring-cost records"] if obligations else []),
        _metric("credit_card_liabilities", "Credit-card liabilities", credit_debt, "Latest positive current balances on connected credit-card accounts.", inclusions=["connected credit-card current balances"], exclusions=["available credit", "pending charges", "unconnected cards"], timestamps=[balance.get("observed_at", "") for _, balance in credit_rows], gaps=[] if credit_rows else ["No connected credit-card balance is available."], confidence="high" if credit_rows else "low", rationale="Computed from latest USD provider balance observations.", covered=len(credit_rows), total=len([item for item in accounts if item.get("type") == "credit"]), sources=transaction_sources),
        _metric("investment_value", "Investment value", investment_value, "Latest connected investment-account balances, falling back to normalized holdings when balances are absent.", inclusions=["brokerage and retirement investment accounts"], exclusions=["unconnected accounts", "cost basis as a substitute for market value"], timestamps=balance_times, gaps=[] if investment_rows or holdings else ["No investment balances or holdings are available."], confidence="high" if investment_rows else ("medium" if holdings else "low"), rationale="Uses provider market values without inferring missing positions.", covered=len(investment_rows) or len(holdings), total=max(len(investment_rows), len(holdings), 1), sources=transaction_sources),
        _metric("retirement_value", "Retirement value", retirement_value, "Latest balances for investment accounts explicitly identified as retirement accounts.", inclusions=["401(k), 403(b), IRA, Roth, pension, and retirement subtypes"], exclusions=["taxable brokerage accounts", "unidentified investment accounts"], timestamps=[balance.get("observed_at", "") for _, balance in retirement_rows], gaps=[] if retirement_rows else ["No connected account is explicitly identified as a retirement account."], confidence="high" if retirement_rows else "low", rationale="No account is guessed to be retirement-only when its subtype is ambiguous.", covered=len(retirement_rows), total=max(len(investment_rows), 1), sources=transaction_sources),
        _metric("household_asset_value", "Household asset value", household_asset_value, "Latest registered gross values for homes and vehicles.", inclusions=["registered home values", "registered vehicle values"], exclusions=["selling costs", "debt", "unregistered property"], timestamps=asset_times, gaps=[] if assets else ["No home or vehicle has been registered."], confidence="medium" if assets else "low", rationale="Values retain their declared source and date; estimates are not presented as guaranteed sale proceeds.", covered=len(assets), total=max(len(assets), 1), sources=[item["valuation"]["source_label"] for item in assets]),
    ]

    raw_spending = accounting_metrics["finalized_spending"]["raw"]
    adjusted = accounting_metrics["finalized_spending"]["adjusted"]
    return {
        "currency": "USD",
        "generated_at": datetime.now(UTC).isoformat(),
        "metrics": metrics,
        "sections": {
            "cash_flow": accounting_metrics["cash_flow"],
            "adjusted_spending": {
                "value": adjusted,
                "raw_purchase_outflows": raw_spending,
                "excluded_from_personal": _money(raw_spending - adjusted),
                "context": "Observed adjusted personal spending, not a budget target or recommendation.",
            },
        },
        "spending_by_category": accounting_metrics["spending_by_category"],
        "unusual_activity": _unusual_activity(accounting, balance_history),
        "unusual_activity_method": {
            "definition": "Rule-based review signals for large posted charges and material balance changes.",
            "limitations": "Signals are not fraud determinations and can be legitimate. Empty results do not prove that all activity is expected.",
        },
    }
