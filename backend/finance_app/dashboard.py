from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from statistics import median

from fastapi import APIRouter, Request

from .accounting import build_accounting_view
from .assets import effective_asset_valuation
from .auth import require_owner
from .classification import effective_classifications
from .investment_scope import is_custodial_account
from .recommendations import build_spending_opportunities


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


def _shift_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


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
    latest = max(
        (date.fromisoformat(item["date"]) for item in spending), default=date.today()
    )
    recent_cutoff = latest - timedelta(days=45)
    histories: dict[str, list[dict]] = defaultdict(list)
    for item in spending:
        histories[str(item.get("merchant_name") or "").casefold()].append(item)
    signals = []
    for item in spending:
        if date.fromisoformat(item["date"]) < recent_cutoff:
            continue
        prior = [
            row["amount"]
            for row in histories[str(item.get("merchant_name") or "").casefold()]
            if row["date"] < item["date"] and row["amount"] > 0
        ]
        if len(prior) < 6:
            continue
        typical = median(prior)
        threshold = max(1000, typical * 5)
        if item["amount"] < threshold or item["amount"] - typical < 750:
            continue
        signals.append(
            {
                "type": "merchant_deviation",
                "title": "Charge is higher than this merchant's usual amount",
                "amount": item["amount"],
                "date": item["date"],
                "merchant_name": item["merchant_name"],
                "explanation": (
                    f"Compared with the median of {len(prior)} earlier charges "
                    "at this same merchant."
                ),
                "confidence": {
                    "level": "medium",
                    "rationale": "The merchant has enough prior history for comparison; this is still only a review prompt.",
                },
                "review_required": True,
            }
        )
    for account_key, observations in balance_history.items():
        if len(observations) < 2:
            continue
        previous, current = observations[-2:]
        change = float(current.get("current") or 0) - float(previous.get("current") or 0)
        material = max(5000, abs(float(previous.get("current") or 0)) * 0.25)
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
                    "The latest balance differs by at least $5,000 or 25% from the prior observation."
                ),
                "confidence": {
                    "level": "medium",
                    "rationale": "The change is provider-reported, but its cause has not been classified.",
                },
                "review_required": True,
            }
        )
    return sorted(
        signals, key=lambda item: (item.get("date") or "", item["type"]), reverse=True
    )[:8]


def _billing_alerts(transactions: list[dict], accounts: list[dict]) -> list[dict]:
    credit_ids = {
        item["account_id"] for item in accounts if item.get("type") == "credit"
    }
    alerts = []
    for item in transactions:
        merchant = str(item.get("merchant_name") or item.get("name") or "")
        if (
            not item.get("removed")
            and not item.get("pending")
            and item.get("account_id") in credit_ids
            and "rentcast" in merchant.casefold()
            and float(item.get("amount") or 0) > 0
        ):
            alerts.append(
                {
                    "type": "unexpected_provider_charge",
                    "provider": "RentCast",
                    "date": item.get("date"),
                    "amount": _money(item["amount"]),
                    "currency": "USD",
                    "message": "RentCast charged a connected credit card even though the app is configured to remain within the free allowance.",
                    "review_required": True,
                }
            )
    return sorted(alerts, key=lambda item: item.get("date") or "", reverse=True)


def _net_worth_attribution(
    *,
    account_by_key: dict[str, dict],
    balance_history: dict[str, list[dict]],
    assets: list[dict],
    comparison_date: date,
    ending_net_worth: float,
    operating_surplus: float,
) -> dict:
    opening_cash = 0.0
    opening_investments = 0.0
    opening_cards = 0.0
    expected = 0
    covered = 0
    sources = []
    for key, account in account_by_key.items():
        account_type = account.get("type")
        if account_type not in {"depository", "credit", "investment"}:
            continue
        if account_type == "investment" and is_custodial_account(account):
            continue
        expected += 1
        eligible = [
            item
            for item in balance_history.get(key, [])
            if str(item.get("observed_at", ""))[:10]
            and date.fromisoformat(str(item["observed_at"])[:10])
            <= comparison_date
        ]
        if not eligible:
            continue
        observation = max(eligible, key=lambda item: item.get("observed_at", ""))
        value = float(observation.get("current") or 0)
        covered += 1
        sources.append(account.get("name") or key.split(":", 1)[-1])
        if account_type == "depository":
            opening_cash += value
        elif account_type == "credit":
            opening_cards += max(value, 0)
        else:
            opening_investments += value

    opening_assets = 0.0
    opening_asset_debt = 0.0
    for asset in assets:
        expected += 1
        history = [*asset.get("valuation_history", []), asset.get("valuation", {})]
        eligible = [
            item
            for item in history
            if item.get("valued_at")
            and date.fromisoformat(str(item["valued_at"])[:10])
            <= comparison_date
        ]
        if not eligible:
            continue
        valuation = max(eligible, key=lambda item: item.get("valued_at", ""))
        opening_assets += float(valuation.get("amount") or 0)
        opening_asset_debt += float(asset["ownership"].get("debt_balance") or 0)
        covered += 1
        sources.append(asset.get("name") or asset.get("kind", "household asset"))

    coverage = _coverage(covered, expected, sources)
    opening_net_worth = (
        opening_cash
        + opening_investments
        + opening_assets
        - opening_cards
        - opening_asset_debt
    )
    change = ending_net_worth - opening_net_worth
    residual = change - operating_surplus
    available = expected > 0 and covered == expected
    return {
        "available": available,
        "period": {
            "label": "One year",
            "start": comparison_date.isoformat(),
        },
        "opening_net_worth": _money(opening_net_worth) if available else None,
        "ending_net_worth": _money(ending_net_worth),
        "change": _money(change) if available else None,
        "direction": (
            "stronger" if change > 0 else "weaker" if change < 0 else "unchanged"
        )
        if available
        else "not_yet_measurable",
        "drivers": [
            {
                "key": "operating_surplus",
                "label": "Income minus personal spending",
                "value": _money(operating_surplus),
                "definition": "Classified trailing-12-month income minus adjusted personal spending; transfers and refunds are not income.",
            },
            {
                "key": "valuation_and_balance_change",
                "label": "Market, valuation, and liability change",
                "value": _money(residual) if available else None,
                "definition": "The reconciled remainder after household operating surplus, including investment returns, asset revaluations, liability changes, and any timing differences.",
            },
        ],
        "reconciliation_difference": 0.0 if available else None,
        "coverage": coverage,
        "limitations": [
            "Asset debt uses the currently registered debt balance because historical loan balances are not yet stored."
        ]
        + ([] if available else ["A full one-year opening snapshot is not available for every current household account and asset."]),
    }


def _retirement_readiness(scenarios: list[dict]) -> dict:
    supported = [
        item
        for item in scenarios
        if item.get("calculator") in {"retirement_comparison", "retirement_uncertainty"}
    ]
    if not supported:
        return {
            "available": False,
            "status": "scenario_required",
            "explanation": "Save a Retirement comparison to connect the dashboard to an explicit retirement age, spending target, tax assumptions, and risk range.",
            "action_href": "/retirement",
        }
    scenario = max(supported, key=lambda item: item.get("created_at", ""))
    output = scenario.get("output") or {}
    if scenario["calculator"] == "retirement_comparison":
        uncertainty = output.get("uncertainty") or {}
        bridge = output.get("optimized_mix", {}).get("bridge", {})
        first_failure = uncertainty.get("first_failure_age_distribution") or {}
        return {
            "available": bool(uncertainty),
            "status": "modeled" if uncertainty else "scenario_needs_refresh",
            "scenario_id": scenario["id"],
            "scenario_name": scenario.get("name", "Retirement comparison"),
            "created_at": scenario.get("created_at"),
            "retirement_age": output.get("retirement_age"),
            "success_probability_percent": uncertainty.get("success_probability_percent"),
            "stress_success_probability_percent": (uncertainty.get("stress_case") or {}).get("success_probability_percent"),
            "bridge_required": (output.get("bridge") or {}).get("required_spending"),
            "bridge_projected": bridge.get("projected_accessible_at_retirement"),
            "bridge_gap": bridge.get("projected_gap"),
            "first_failure_age_median": first_failure.get("p50"),
            "explanation": "Readiness uses the latest saved Retirement comparison. It is scenario-based and does not silently replace its balances or assumptions with current dashboard data.",
            "action_href": "/retirement",
        }
    baseline = (output.get("cases") or [{}])[0]
    first_failure = baseline.get("first_failure_age_distribution") or {}
    return {
        "available": bool(baseline),
        "status": "modeled" if baseline else "scenario_needs_refresh",
        "scenario_id": scenario["id"],
        "scenario_name": scenario.get("name", "Retirement uncertainty"),
        "created_at": scenario.get("created_at"),
        "retirement_age": (scenario.get("inputs") or {}).get("retirement_age"),
        "success_probability_percent": baseline.get("success_probability_percent"),
        "stress_success_probability_percent": None,
        "bridge_required": None,
        "bridge_projected": None,
        "bridge_gap": None,
        "first_failure_age_median": first_failure.get("p50"),
        "explanation": "Readiness uses the latest saved retirement uncertainty scenario and preserves its dated inputs.",
        "action_href": "/retirement",
    }


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
    classifications = effective_classifications(storage, owner)
    credit_account_ids = {
        item["account_id"] for item in accounts if item.get("type") == "credit"
    }
    full_accounting = build_accounting_view(
        transactions,
        storage.list_transaction_adjustments(owner),
        storage.list_provider_record_versions(owner, "transaction"),
        classifications,
        credit_account_ids,
    )
    latest_transaction_date = max(
        (
            date.fromisoformat(item["date"])
            for item in transactions
            if not item.get("removed") and item.get("date")
        ),
        default=date.today(),
    )
    reporting_start = _shift_months(latest_transaction_date, -11)
    period_transactions = [
        item
        for item in transactions
        if item.get("date") and date.fromisoformat(item["date"]) >= reporting_start
    ]
    accounting = build_accounting_view(
        period_transactions,
        storage.list_transaction_adjustments(owner),
        storage.list_provider_record_versions(owner, "transaction"),
        classifications,
        credit_account_ids,
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
    all_investment_rows = [row for row in balance_rows if row[0].get("type") == "investment"]
    custodial_investment_rows = [
        row for row in all_investment_rows if is_custodial_account(row[0])
    ]
    investment_rows = [
        row for row in all_investment_rows if not is_custodial_account(row[0])
    ]
    cash = sum(float(balance.get("current") or 0) for _, balance in cash_rows)
    credit_debt = sum(max(0, float(balance.get("current") or 0)) for _, balance in credit_rows)
    investment_value = sum(float(balance.get("current") or 0) for _, balance in investment_rows)
    custodial_investment_value = sum(
        float(balance.get("current") or 0)
        for _, balance in custodial_investment_rows
    )
    if not all_investment_rows and holdings:
        accounts_by_id = {item["account_id"]: item for item in accounts}
        investment_value = sum(
            float(item.get("institution_value") or 0)
            for item in holdings
            if not is_custodial_account(accounts_by_id.get(item.get("account_id")))
        )
        custodial_investment_value = sum(
            float(item.get("institution_value") or 0)
            for item in holdings
            if is_custodial_account(accounts_by_id.get(item.get("account_id")))
        )
    retirement_rows = [
        row
        for row in investment_rows
        if any(
            marker in str(row[0].get("subtype", "")).lower()
            for marker in ("401", "403", "ira", "roth", "retirement", "pension")
        )
    ]
    retirement_value = sum(float(balance.get("current") or 0) for _, balance in retirement_rows)
    household_asset_value = sum(
        float(effective_asset_valuation(item)["amount"]) for item in assets
    )
    asset_debt = sum(float(item["ownership"]["debt_balance"]) for item in assets)
    net_worth = (
        cash
        + investment_value
        + household_asset_value
        - asset_debt
        - credit_debt
    )
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
        _metric("net_worth", "Net worth", net_worth, "Cash, household investments, and registered household assets minus current card balances and registered asset debt.", inclusions=["latest depository balances", "latest household investment balances", "registered home and vehicle values", "current credit-card balances", "registered asset debt"], exclusions=["children's custodial accounts (UTMA/UGMA)", "selling costs", "unregistered assets", "unavailable loan liabilities"], timestamps=[*balance_times, *asset_times], gaps=liability_gap, confidence="medium", rationale="Children's custodial investments are tracked separately; card balances are included only as a point-in-time liability.", covered=len(balances) + len(assets), total=len(accounts) + len(assets), sources=balance_sources),
        _metric("cash", "Cash", cash, "Latest current balances for connected depository accounts.", inclusions=["checking", "savings", "other depository accounts"], exclusions=["credit available", "investment cash inside brokerage accounts"], timestamps=[balance.get("observed_at", "") for _, balance in cash_rows], gaps=[] if cash_rows else ["No connected depository balances are available."], confidence="high" if cash_rows else "low", rationale="Computed from latest USD provider balance observations.", covered=len(cash_rows), total=len([item for item in accounts if item.get("type") == "depository"]), sources=transaction_sources),
        _metric("debt", "Registered asset debt", asset_debt, "Debt explicitly registered against household assets, kept separate from transient credit-card balances.", inclusions=["registered mortgage and vehicle debt"], exclusions=["current credit-card balances", "unavailable student, personal, and other provider loan liabilities"], timestamps=asset_times, gaps=liability_gap, confidence="medium", rationale="This is structural debt attached to registered assets; current card balances appear as a separate snapshot metric.", covered=len([item for item in assets if item["ownership"]["debt_balance"] > 0]), total=max(1, len(assets)), sources=[item["valuation"]["source_label"] for item in assets]),
        _metric("income", "Income · trailing 12 months", accounting_metrics["income"], "Posted transaction inflows classified as income during the trailing 12 calendar months.", inclusions=["posted income transactions in the reporting period"], exclusions=["older history", "transfers", "refunds", "pending income", "unconnected payroll history"], timestamps=transaction_times, gaps=[], confidence="medium", rationale="Provider transaction categories are used until the owner reviews classifications.", covered=len(transaction_sources), total=len(transaction_sources), sources=transaction_sources),
        _metric("raw_cash_flow", "Observed bank movement · trailing 12 months", accounting_metrics["cash_flow"]["net"], "Credits minus debits observed on connected bank accounts; this is not household profit or loss.", inclusions=["posted depository credits", "posted depository debits", "bank-side investment transfers and card payments"], exclusions=["investment returns", "changes in brokerage value", "credit-card purchases", "older history", "pending transactions"], timestamps=transaction_times, gaps=[] if cash_rows else ["No connected depository account is available, so bank movement is not measurable yet."], confidence="high" if cash_rows else "low", rationale="A bank debit can move value to a brokerage or pay a card rather than reduce household net worth. The movement bridge separates those uses.", covered=len(cash_rows), total=max(1, len([item for item in accounts if item.get("type") == "depository"])), sources=transaction_sources),
        _metric("raw_spending", "Raw spending · trailing 12 months", accounting_metrics["finalized_spending"]["raw"], "Posted purchase outflows during the trailing 12 calendar months before refunds or owner adjustments.", inclusions=["posted purchases in the reporting period"], exclusions=["older history", "income", "transfers", "credit-card payments", "pending transactions", "refund offsets"], timestamps=transaction_times, gaps=[], confidence="high", rationale="Deterministic accounting rules separate purchases from non-spending movements.", covered=len(transaction_sources), total=len(transaction_sources), sources=transaction_sources),
        _metric("adjusted_personal_spending", "Personal spending · trailing 12 months", accounting_metrics["finalized_spending"]["adjusted"], "Posted personal purchase spending during the trailing 12 calendar months after refunds and owner adjustments.", inclusions=["posted personal purchases", "linked refunds", "owner-confirmed personal shares"], exclusions=["older history", "transfers", "card payments", "pending purchases", "reimbursable, business, and excluded shares"], timestamps=transaction_times, gaps=[], confidence="high", rationale="Uses normalized accounting plus explicit owner adjustments; unreviewed provider categories remain visible separately.", covered=len(transaction_sources), total=len(transaction_sources), sources=transaction_sources),
        _metric("true_monthly_cost", "True monthly cost", true_monthly_cost, "Monthly equivalent of owner-confirmed recurring, quarterly, and annual obligations.", inclusions=["confirmed recurring obligations"], exclusions=["unconfirmed recurring proposals", "one-off spending"], timestamps=[item.get("updated_at", "") for item in obligations], gaps=missing_recurring, confidence="high" if obligations else "low", rationale="Only owner-confirmed obligations affect this metric.", covered=len(obligations), total=max(1, len(obligations)), sources=["confirmed recurring-cost records"] if obligations else []),
        _metric("credit_card_liabilities", "Current card balance", credit_debt, "Latest provider-reported credit-card balance—a transient point-in-time amount, not long-term debt.", inclusions=["connected credit-card current balances"], exclusions=["available credit", "pending charges", "unconnected cards", "mortgage and other structural debt"], timestamps=[balance.get("observed_at", "") for _, balance in credit_rows], gaps=[] if credit_rows else ["No connected credit-card balance is available."], confidence="high" if credit_rows else "low", rationale="This changes as charges post and payments settle; it is shown as a current liability snapshot.", covered=len(credit_rows), total=len([item for item in accounts if item.get("type") == "credit"]), sources=transaction_sources),
        _metric("investment_value", "Household investment value", investment_value, "Latest connected investment balances belonging to the household, falling back to normalized holdings when balances are absent.", inclusions=["household brokerage and retirement investment accounts"], exclusions=["children's custodial accounts (UTMA/UGMA)", "unconnected accounts", "cost basis as a substitute for market value"], timestamps=[balance.get("observed_at", "") for _, balance in investment_rows], gaps=[] if investment_rows or holdings else ["No household investment balances or holdings are available."], confidence="high" if investment_rows else ("medium" if holdings else "low"), rationale="Uses provider market values without treating children's assets as the owner's assets.", covered=len(investment_rows) or len(holdings), total=max(len(all_investment_rows), len(holdings), 1), sources=transaction_sources),
        _metric("custodial_investment_value", "Children's custodial investments", custodial_investment_value, "Latest connected UTMA and UGMA balances, shown separately because the assets belong to their child beneficiaries.", inclusions=["UTMA accounts", "UGMA accounts"], exclusions=["household net worth", "household investment value", "retirement value"], timestamps=[balance.get("observed_at", "") for _, balance in custodial_investment_rows], gaps=[] if custodial_investment_rows else ["No connected custodial investment account is available."], confidence="high" if custodial_investment_rows else "low", rationale="Account subtype and name identify custodial ownership; the records remain visible without inflating household totals.", covered=len(custodial_investment_rows), total=max(len(custodial_investment_rows), 1), sources=transaction_sources),
        _metric("retirement_value", "Retirement value", retirement_value, "Latest balances for household investment accounts explicitly identified as retirement accounts.", inclusions=["401(k), 403(b), IRA, Roth, pension, and retirement subtypes"], exclusions=["children's custodial accounts (UTMA/UGMA)", "taxable brokerage accounts", "unidentified investment accounts"], timestamps=[balance.get("observed_at", "") for _, balance in retirement_rows], gaps=[] if retirement_rows else ["No connected household account is explicitly identified as a retirement account."], confidence="high" if retirement_rows else "low", rationale="No custodial or ambiguous account is guessed to be the owner's retirement asset.", covered=len(retirement_rows), total=max(len(investment_rows), 1), sources=transaction_sources),
        _metric("household_asset_value", "Household asset value", household_asset_value, "Latest registered gross values for homes and vehicles.", inclusions=["registered home values", "registered vehicle values"], exclusions=["selling costs", "debt", "unregistered property"], timestamps=asset_times, gaps=[] if assets else ["No home or vehicle has been registered."], confidence="medium" if assets else "low", rationale="Values retain their declared source and date; estimates are not presented as guaranteed sale proceeds.", covered=len(assets), total=max(len(assets), 1), sources=[item["valuation"]["source_label"] for item in assets]),
    ]

    raw_spending = accounting_metrics["finalized_spending"]["raw"]
    adjusted = accounting_metrics["finalized_spending"]["adjusted"]
    operating_surplus = _money(accounting_metrics["income"] - adjusted)
    net_worth_change = _net_worth_attribution(
        account_by_key=account_by_key,
        balance_history=balance_history,
        assets=assets,
        comparison_date=latest_transaction_date - timedelta(days=365),
        ending_net_worth=net_worth,
        operating_surplus=operating_surplus,
    )
    recommendations = build_spending_opportunities(
        full_accounting["transactions"],
        obligations,
        storage.list_financial_records(owner, "recommendation_feedback"),
        as_of=latest_transaction_date,
    )
    retirement_readiness = _retirement_readiness(storage.list_scenarios(owner))
    return {
        "currency": "USD",
        "generated_at": datetime.now(UTC).isoformat(),
        "data_quality": accounting_metrics["deduplication"],
        "reporting_period": {
            "label": "Trailing 12 months",
            "start": reporting_start.isoformat(),
            "end": latest_transaction_date.isoformat(),
        },
        "metrics": metrics,
        "net_worth_change": net_worth_change,
        "retirement_readiness": retirement_readiness,
        "recommendations": recommendations,
        "sections": {
            "cash_flow": {
                **accounting_metrics["cash_flow"],
                "operating_surplus": operating_surplus,
                "available": bool(cash_rows),
                "context": (
                    "Observed bank movement is not profit or loss. Brokerage funding and card payments reduce bank cash while moving value or settling purchases counted elsewhere."
                    if cash_rows
                    else "Connect a bank account to measure movement. Household operating surplus remains a separate income-versus-spending measure."
                ),
            },
            "adjusted_spending": {
                "value": adjusted,
                "raw_purchase_outflows": raw_spending,
                "excluded_from_personal": _money(raw_spending - adjusted),
                "context": "Observed adjusted personal spending, not a budget target or recommendation.",
            },
        },
        "spending_by_category": accounting_metrics["spending_by_category"],
        "unusual_activity": _unusual_activity(full_accounting, balance_history),
        "billing_alerts": _billing_alerts(transactions, accounts),
        "unusual_activity_method": {
            "definition": "Recent charges are compared with earlier charges at the same merchant; balance changes use a high materiality threshold.",
            "limitations": "A charge needs at least six earlier merchant-specific observations and must exceed both $1,000 and a strong merchant-relative threshold. Same-day repeats are not called duplicates without stronger provider evidence, and signals are not fraud determinations.",
        },
    }
