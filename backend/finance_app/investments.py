from datetime import date
from math import prod

from fastapi import APIRouter, HTTPException, Query, Request

from .auth import require_owner
from .investment_scope import ownership_scope


router = APIRouter()


def _active_records(storage, owner: str, kind: str) -> list[dict]:
    return [
        item
        for item in storage.list_financial_records(owner, kind)
        if not item["removed"]
    ]


def _money(value: float) -> float:
    return round(value + 0.0, 2)


def _tax_treatment(account: dict | None) -> str:
    """Classify the account without pretending that a holding is itself taxable."""
    if ownership_scope(account) == "custodial":
        return "custodial"
    searchable = " ".join(
        str((account or {}).get(field) or "")
        for field in ("name", "official_name", "subtype")
    ).casefold()
    if "roth" in searchable:
        return "roth"
    if "health savings" in searchable or " hsa" in f" {searchable}":
        return "hsa"
    if any(marker in searchable for marker in ("401", "403", "ira", "pension", "retirement")):
        return "tax_deferred"
    return "taxable"


def _xirr(cash_flows: list[tuple[date, float]]) -> float | None:
    if not cash_flows or not any(value < 0 for _, value in cash_flows) or not any(
        value > 0 for _, value in cash_flows
    ):
        return None
    first_date = cash_flows[0][0]

    def present_value(rate: float) -> float:
        return sum(
            value / (1 + rate) ** ((flow_date - first_date).days / 365)
            for flow_date, value in cash_flows
        )

    low, high = -0.999999, 1.0
    while present_value(high) > 0 and high < 1_000_000:
        high *= 2
    if present_value(low) * present_value(high) > 0:
        return None
    for _ in range(160):
        midpoint = (low + high) / 2
        if present_value(midpoint) > 0:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2


@router.get("/api/private/investments/positions")
def investment_positions(request: Request) -> dict:
    owner = require_owner(request)
    storage = request.app.state.storage
    connections = storage.list_connections(owner)
    freshness = {
        connection["id"]: storage.get_sync_state(connection["id"]).get(
            "freshness", {}
        )
        for connection in connections
        if "investments" in connection.get("products", [])
    }
    securities = {
        security["security_id"]: security
        for security in _active_records(storage, owner, "security")
    }
    accounts_by_id = {
        account["account_id"]: account
        for account in _active_records(storage, owner, "account")
    }
    holdings_by_position: dict[tuple[str, str], dict] = {}
    for holding in _active_records(storage, owner, "holding"):
        security = securities.get(holding["security_id"], {})
        candidate = {
            **holding,
            "security_name": security.get("name") or holding.get("description"),
            "ticker_symbol": security.get("ticker_symbol") or holding.get("symbol"),
            "ownership_scope": ownership_scope(
                accounts_by_id.get(holding.get("account_id"))
            ),
        }
        position_key = (
            candidate["account_id"],
            candidate.get("ticker_symbol") or candidate["security_id"],
        )
        current = holdings_by_position.get(position_key)

        def preference(item: dict) -> tuple[str, int, int]:
            effective_date = str(
                item.get("effective_date") or item.get("observed_at") or ""
            )[:10]
            completeness = sum(
                item.get(field) is not None
                for field in ("quantity", "institution_value", "cost_basis")
            )
            provider_priority = int(item.get("source") == "plaid_cached")
            return effective_date, completeness, provider_priority

        if current is None or preference(candidate) > preference(current):
            holdings_by_position[position_key] = candidate
    tax_lots = _active_records(storage, owner, "tax_lot")
    holdings = list(holdings_by_position.values())
    for holding in holdings:
        if holding.get("cost_basis") is not None:
            continue
        matching_lots = [
            lot
            for lot in tax_lots
            if lot["account_id"] == holding["account_id"]
            and lot["symbol"] == holding.get("ticker_symbol")
        ]
        if not matching_lots:
            continue
        known_lots = [
            lot for lot in matching_lots if lot.get("cost_basis") is not None
        ]
        holding["cost_basis_lot_coverage"] = {
            "known": len(known_lots),
            "total": len(matching_lots),
        }
        if len(known_lots) != len(matching_lots):
            holding["cost_basis"] = None
            holding["cost_basis_status"] = "unknown"
            continue
        holding["cost_basis"] = _money(
            sum(float(lot["cost_basis"]) for lot in known_lots)
        )
        holding["cost_basis_status"] = "imported"
        holding["cost_basis_source"] = "fidelity_csv"
        holding["cost_basis_as_of"] = max(
            lot["effective_date"] for lot in known_lots
        )
    holdings.sort(key=lambda item: (item.get("security_name") or "", item["security_id"]))
    activities = [
        {
            **item,
            "ownership_scope": ownership_scope(
                accounts_by_id.get(item.get("account_id"))
            ),
        }
        for item in _active_records(storage, owner, "investment_activity")
    ]
    household_holdings = [
        item for item in holdings if item["ownership_scope"] == "household"
    ]
    custodial_holdings = [
        item for item in holdings if item["ownership_scope"] == "custodial"
    ]
    account_summaries = []
    for account_id in sorted({item["account_id"] for item in holdings}):
        account = accounts_by_id.get(account_id, {})
        account_holdings = [item for item in holdings if item["account_id"] == account_id]
        market_value = sum(float(item.get("institution_value") or 0) for item in account_holdings)
        known_basis_holdings = [item for item in account_holdings if item.get("cost_basis") is not None]
        known_basis_value = sum(float(item.get("institution_value") or 0) for item in known_basis_holdings)
        known_basis = sum(float(item["cost_basis"]) for item in known_basis_holdings)
        unrealized_gain = known_basis_value - known_basis
        treatment = _tax_treatment(account)
        taxable_gain = max(0, unrealized_gain) if treatment == "taxable" else 0
        account_summaries.append(
            {
                "account_id": account_id,
                "name": account.get("name") or account.get("official_name") or "Investment account",
                "subtype": account.get("subtype"),
                "ownership_scope": ownership_scope(account),
                "tax_treatment": treatment,
                "market_value": _money(market_value),
                "position_count": len(account_holdings),
                "known_cost_basis": _money(known_basis),
                "known_basis_market_value": _money(known_basis_value),
                "basis_coverage_percent": round(100 * known_basis_value / market_value, 1) if market_value else 0,
                "unrealized_gain_on_known_basis": _money(unrealized_gain),
                "unrealized_gain_percent": round(100 * unrealized_gain / known_basis, 1) if known_basis else None,
                "estimated_federal_tax_if_sold": {
                    "gain_subject_to_scenario": _money(taxable_gain),
                    "at_0_percent": 0,
                    "at_15_percent": _money(taxable_gain * 0.15),
                    "at_23_8_percent": _money(taxable_gain * 0.238),
                    "definition": "Illustrative federal long-term capital-gain scenarios on positive gains with known basis; not a tax return estimate.",
                    "exclusions": [
                        "state tax",
                        "short-term gains",
                        "income-dependent brackets",
                        "positions without cost basis",
                        "loss netting and carryovers",
                    ],
                },
            }
        )
    account_summaries.sort(key=lambda item: item["market_value"], reverse=True)
    return {
        "currency": "USD",
        "summary": {
            "household_market_value": _money(sum(float(item.get("institution_value") or 0) for item in household_holdings)),
            "household_position_count": len(household_holdings),
            "custodial_market_value": _money(sum(float(item.get("institution_value") or 0) for item in custodial_holdings)),
            "custodial_position_count": len(custodial_holdings),
            "custodial_definition": "UTMA and UGMA assets belong to their child beneficiaries and are excluded from household totals.",
        },
        "holdings": holdings,
        "account_summaries": account_summaries,
        "tax_lots": tax_lots,
        "activities": activities,
        "securities": list(securities.values()),
        "freshness": freshness,
    }


@router.get("/api/private/investments/performance")
def investment_performance(
    request: Request,
    start: date,
    end: date,
    benchmark: str = Query(default="SPY", pattern=r"^[A-Z][A-Z0-9.-]{0,9}$"),
) -> dict:
    owner = require_owner(request)
    if end <= start:
        raise HTTPException(status_code=422, detail="end must be after start")
    storage = request.app.state.storage

    valuation_totals: dict[tuple[str, str], float] = {}
    for item in _active_records(storage, owner, "investment_valuation"):
        if start <= date.fromisoformat(item["date"]) <= end:
            key = (item["date"], item.get("timing", "close"))
            valuation_totals[key] = valuation_totals.get(key, 0) + float(
                item["value"]
            )
    valuations = sorted(
        (
            {"date": valuation_date, "timing": timing, "value": value}
            for (valuation_date, timing), value in valuation_totals.items()
        ),
        key=lambda item: (item["date"], item["timing"] != "pre_flow"),
    )
    if (
        len(valuations) < 2
        or valuations[0]["date"] != start.isoformat()
        or valuations[-1]["date"] != end.isoformat()
    ):
        raise HTTPException(
            status_code=409,
            detail="complete beginning and ending valuations are unavailable",
        )

    raw_activities = [
        item
        for item in _active_records(storage, owner, "investment_activity")
        if start <= date.fromisoformat(item["date"]) <= end
    ]
    # Provider feeds and fallback CSV imports can describe the same cash flow.
    # Count the economic event once, preferring the direct provider record.
    activities_by_event: dict[tuple, dict] = {}
    for item in sorted(
        raw_activities,
        key=lambda value: str(value.get("source", "")).endswith("_csv"),
    ):
        event = (
            item.get("account_id"),
            item.get("date"),
            item.get("type"),
            item.get("subtype"),
            round(float(item.get("amount", 0)), 2),
        )
        activities_by_event.setdefault(event, item)
    activities = list(activities_by_event.values())
    contributions = sum(
        item["amount"] for item in activities if item.get("subtype") == "deposit"
    )
    withdrawals = sum(
        item["amount"]
        for item in activities
        if item.get("subtype") == "withdrawal"
    )
    dividends = sum(
        item["amount"]
        for item in activities
        if item.get("subtype") in {"dividend", "interest"}
    )
    fees = sum(
        item["amount"]
        for item in activities
        if item.get("type") == "fee" or item.get("subtype") == "fee"
    )
    valuation_changes = sum(
        item["amount"]
        for item in activities
        if item.get("type") == "adjustment"
    )
    external_by_date: dict[str, float] = {}
    for item in activities:
        if item.get("subtype") == "deposit":
            external_by_date[item["date"]] = external_by_date.get(item["date"], 0) + item["amount"]
        elif item.get("subtype") == "withdrawal":
            external_by_date[item["date"]] = external_by_date.get(item["date"], 0) - item["amount"]

    flow_boundary_dates = {
        item["date"] for item in valuations if item.get("timing") == "pre_flow"
    }
    missing_boundaries = sorted(set(external_by_date) - flow_boundary_dates)
    if missing_boundaries:
        raise HTTPException(
            status_code=409,
            detail="time-weighted return requires a pre-flow valuation on each external cash-flow date",
        )

    period_returns: list[float] = []
    previous_value = float(valuations[0]["value"])
    for valuation in valuations[1:]:
        current_value = float(valuation["value"])
        if valuation.get("timing") == "pre_flow":
            if previous_value:
                period_returns.append(current_value / previous_value - 1)
            previous_value = current_value + external_by_date.get(valuation["date"], 0)
        else:
            if previous_value:
                period_returns.append(current_value / previous_value - 1)
            previous_value = current_value
    time_weighted = prod(1 + value for value in period_returns) - 1

    beginning_value = float(valuations[0]["value"])
    ending_value = float(valuations[-1]["value"])
    market_performance = (
        ending_value
        - beginning_value
        - contributions
        + withdrawals
        - dividends
        + fees
        - valuation_changes
    )
    cash_flows = [(start, -beginning_value)]
    cash_flows.extend(
        (date.fromisoformat(flow_date), -amount)
        for flow_date, amount in sorted(external_by_date.items())
        if amount
    )
    cash_flows.append((end, ending_value))
    money_weighted = _xirr(cash_flows)

    benchmark_by_date = {
        item["date"]: item
        for item in _active_records(storage, owner, "benchmark_observation")
        if item.get("symbol") == benchmark
        and start <= date.fromisoformat(item["date"]) <= end
    }
    benchmark_points = sorted(benchmark_by_date.values(), key=lambda item: item["date"])
    if (
        len(benchmark_points) < 2
        or benchmark_points[0]["date"] != start.isoformat()
        or benchmark_points[-1]["date"] != end.isoformat()
    ):
        raise HTTPException(
            status_code=409,
            detail=f"{benchmark} benchmark does not cover the requested period",
        )
    benchmark_return = (
        benchmark_points[-1]["value"] / benchmark_points[0]["value"] - 1
    )

    return {
        "currency": "USD",
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "attribution": {
            "beginning_value": _money(beginning_value),
            "contributions": _money(contributions),
            "withdrawals": _money(withdrawals),
            "dividends": _money(dividends),
            "fees": _money(fees),
            "market_performance": _money(market_performance),
            "valuation_changes": _money(valuation_changes),
            "ending_value": _money(ending_value),
        },
        "returns": {
            "time_weighted_percent": round(time_weighted * 100, 4),
            "money_weighted_xirr_percent": (
                None if money_weighted is None else round(money_weighted * 100, 4)
            ),
            "time_weighted_definition": "Compounds portfolio subperiod returns after removing external cash flows.",
            "money_weighted_definition": "Annualized XIRR reflecting the timing and size of owner cash flows.",
        },
        "benchmark": {
            "symbol": benchmark,
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "return_percent": round(benchmark_return * 100, 4),
            "portfolio_excess_percent": round(
                (time_weighted - benchmark_return) * 100, 4
            ),
        },
    }
