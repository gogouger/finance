import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from statistics import median

from fastapi import APIRouter, Query, Request

from .accounting import build_accounting_view
from .auth import require_owner
from .classification import effective_classifications
from .recurring import detect_recurring_costs


router = APIRouter()
CENT = Decimal("0.01")

MERCHANT_RULES = (
    ("CAPITAL ONE MOBILE PYMT", "Capital One payment"),
    ("CAPITAL ONE AUTOPAY PYMT", "Capital One payment"),
    ("COSTCO", "Costco"),
    ("KING SOOPERS", "King Soopers"),
    ("UNITED ", "United Airlines"),
    ("USAA INSURANCE", "USAA Insurance"),
    ("HOME DEPOT", "The Home Depot"),
    ("CORE ELECTRIC", "CORE Electric"),
    ("E 470", "E-470 tolls"),
    ("MINT MOBILE", "Mint Mobile"),
    ("SPOTIFY", "Spotify"),
)


def _money(value: Decimal | float | int) -> float:
    return float(Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP))


def _merchant(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value.strip())
    upper = cleaned.upper()
    for needle, label in MERCHANT_RULES:
        if needle in upper:
            return label
    if cleaned and cleaned == upper:
        return cleaned.title()
    return cleaned or "Unknown merchant"


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _shift_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _month_keys(start: date, end: date) -> list[str]:
    values = []
    cursor = _month_start(start)
    while cursor <= end:
        values.append(cursor.strftime("%Y-%m"))
        cursor = _shift_months(cursor, 1)
    return values


def _period_start(latest: date, months: int) -> date:
    if months == 0:
        return date.min
    return _shift_months(_month_start(latest), -(months - 1))


def _quarter(value: str) -> str:
    parsed = date.fromisoformat(value)
    return f"{parsed.year}-Q{(parsed.month - 1) // 3 + 1}"


def _yearly_metrics(rows: list[dict], latest: date) -> list[dict]:
    buckets: dict[str, dict] = defaultdict(
        lambda: {
            "raw_spending": Decimal("0"),
            "refunds": Decimal("0"),
            "net_spending": Decimal("0"),
            "card_payments": Decimal("0"),
            "transaction_count": 0,
            "months": set(),
        }
    )
    for item in rows:
        if item["status"] != "posted":
            continue
        bucket = buckets[item["date"][:4]]
        bucket["transaction_count"] += 1
        bucket["months"].add(item["date"][:7])
        amount = Decimal(str(item["amount"]))
        if item["accounting_type"] == "credit_card_payment":
            bucket["card_payments"] += abs(amount)
        elif item["accounting_type"] == "spending":
            bucket["raw_spending"] += amount
            bucket["net_spending"] += amount
        elif item["accounting_type"] == "refund":
            bucket["refunds"] += abs(amount)
            bucket["net_spending"] += amount

    result = []
    for year, bucket in sorted(buckets.items(), reverse=True):
        months_covered = len(bucket["months"])
        average = bucket["net_spending"] / Decimal(max(1, months_covered))
        result.append(
            {
                "year": int(year),
                "net_spending": _money(bucket["net_spending"]),
                "raw_spending": _money(bucket["raw_spending"]),
                "refunds": _money(bucket["refunds"]),
                "card_payments": _money(bucket["card_payments"]),
                "transaction_count": bucket["transaction_count"],
                "months_covered": months_covered,
                "average_per_observed_month": _money(average),
                "annualized_pace": _money(average * Decimal("12")),
                "complete_year": months_covered == 12 and int(year) < latest.year,
            }
        )
    return result


def _classification_summary(storage, owner: str, transactions: list[dict]) -> dict:
    effective = effective_classifications(storage, owner)
    uncertain = []
    for transaction in transactions:
        record = effective.get(transaction["transaction_id"], {})
        category = record.get("category") or {}
        primary = str(category.get("primary", "uncategorized")).upper()
        if primary not in {"OTHER", "UNCATEGORIZED"}:
            continue
        uncertain.append(
            {
                "transaction_id": transaction["transaction_id"],
                "date": transaction["date"],
                "merchant_name": _merchant(
                    record.get("merchant_name")
                    or transaction.get("merchant_name")
                    or transaction.get("name")
                    or ""
                ),
                "amount": _money(transaction.get("amount", 0)),
                "category": primary,
            }
        )
    uncertain.sort(key=lambda item: (abs(item["amount"]), item["date"]), reverse=True)
    return {
        "effective": effective,
        "review_count": len(uncertain),
        "review_items": uncertain[:30],
    }


def _anomalies(rows: list[dict], monthly_by_category: dict[str, dict[str, Decimal]]) -> list[dict]:
    spending = [item for item in rows if item["accounting_type"] == "spending"]
    latest_date = max(
        (date.fromisoformat(item["date"]) for item in spending), default=date.today()
    )
    recent_cutoff = latest_date - timedelta(days=45)
    merchant_history: dict[str, list[dict]] = defaultdict(list)
    for item in spending:
        merchant_history[item["merchant_name"].casefold()].append(item)

    results = []
    for item in spending:
        item_date = date.fromisoformat(item["date"])
        if item_date < recent_cutoff:
            continue
        prior = [
            row["amount"]
            for row in merchant_history[item["merchant_name"].casefold()]
            if row["date"] < item["date"] and row["amount"] > 0
        ]
        if len(prior) < 6:
            continue
        typical = Decimal(str(median(prior)))
        amount = Decimal(str(item["amount"]))
        threshold = max(Decimal("1000"), typical * Decimal("5"))
        if amount < threshold or amount - typical < Decimal("750"):
            continue
        results.append(
            {
                "type": "merchant_deviation",
                "title": "Higher than this merchant's usual charge",
                "merchant_name": _merchant(item["merchant_name"]),
                "date": item["date"],
                "amount": item["amount"],
                "explanation": (
                    f"This is ${_money(amount - typical):,.0f} above the median of "
                    f"{len(prior)} earlier charges at this merchant."
                ),
            }
        )
    months = sorted(monthly_by_category)
    if len(months) >= 4:
        latest = months[-2] if date.fromisoformat(f"{months[-1]}-01") == _month_start(date.today()) else months[-1]
        prior = [month for month in months if month < latest][-6:]
        for category, amount in monthly_by_category[latest].items():
            history = [
                monthly_by_category[month].get(category, Decimal("0"))
                for month in prior
                if monthly_by_category[month].get(category, Decimal("0")) > 0
            ]
            if len(history) < 4:
                continue
            baseline = Decimal(str(median(history)))
            if amount >= baseline * Decimal("2") and amount - baseline >= Decimal("500"):
                results.append(
                    {
                        "type": "category_spike",
                        "title": "Category increased",
                        "merchant_name": category.replace("_", " ").title(),
                        "date": f"{latest}-01",
                        "amount": _money(amount),
                        "explanation": f"More than twice the median of six prior active months (${_money(baseline):,.0f}).",
                    }
                )
    return sorted(
        results, key=lambda item: (item["date"], abs(item["amount"])), reverse=True
    )[:8]


@router.get("/api/private/spending/analytics")
def spending_analytics(
    request: Request,
    months: int = Query(default=12, ge=0, le=36),
) -> dict:
    owner = require_owner(request)
    storage = request.app.state.storage
    source_transactions = [
        item
        for item in storage.list_financial_records(owner, "transaction")
        if not item.get("removed")
    ]
    accounts = [
        item
        for item in storage.list_financial_records(owner, "account")
        if not item.get("removed")
    ]
    classifications = _classification_summary(storage, owner, source_transactions)
    accounting = build_accounting_view(
        source_transactions,
        storage.list_transaction_adjustments(owner),
        storage.list_provider_record_versions(owner, "transaction"),
        classifications["effective"],
        {item["account_id"] for item in accounts if item.get("type") == "credit"},
    )
    latest = max(
        (date.fromisoformat(item["date"]) for item in accounting["transactions"]),
        default=date.today(),
    )
    start = _period_start(latest, months)
    rows = [
        {**item, "merchant_name": _merchant(item["merchant_name"])}
        for item in accounting["transactions"]
        if date.fromisoformat(item["date"]) >= start and item["status"] == "posted"
    ]
    all_posted_rows = [
        {**item, "merchant_name": _merchant(item["merchant_name"])}
        for item in accounting["transactions"]
        if item["status"] == "posted"
    ]

    monthly: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(Decimal)
    )
    quarterly: dict[str, Decimal] = defaultdict(Decimal)
    annual: dict[str, Decimal] = defaultdict(Decimal)
    categories: dict[str, Decimal] = defaultdict(Decimal)
    merchants: dict[str, dict[str, Decimal | int]] = defaultdict(
        lambda: {"amount": Decimal("0"), "count": 0}
    )
    payments = Decimal("0")
    refunds = Decimal("0")
    raw_spending = Decimal("0")
    net_spending = Decimal("0")
    for item in rows:
        amount = Decimal(str(item["amount"]))
        if item["accounting_type"] == "credit_card_payment":
            payments += abs(amount)
            continue
        if item["accounting_type"] not in {"spending", "refund"}:
            continue
        category = item["category"]["primary"]
        analytic_amount = amount
        if item["accounting_type"] == "refund":
            refunds += abs(amount)
        else:
            raw_spending += amount
        net_spending += analytic_amount
        month = item["date"][:7]
        monthly[month][category] += analytic_amount
        quarterly[_quarter(item["date"])] += analytic_amount
        annual[item["date"][:4]] += analytic_amount
        categories[category] += analytic_amount
        merchants[item["merchant_name"]]["amount"] += analytic_amount
        merchants[item["merchant_name"]]["count"] += 1

    month_keys = _month_keys(start if start != date.min else min(
        (date.fromisoformat(item["date"]) for item in rows), default=latest
    ), latest)
    monthly_rows = [
        {
            "month": month,
            "net_spending": _money(sum(monthly[month].values(), Decimal("0"))),
            "categories": {
                key: _money(value)
                for key, value in sorted(monthly[month].items())
            },
        }
        for month in month_keys
    ]
    recurring = detect_recurring_costs(source_transactions, latest)
    liabilities = [
        item
        for item in storage.list_financial_records(owner, "liability")
        if not item.get("removed")
    ]
    period_months = max(1, len(month_keys))
    return {
        "currency": "USD",
        "data_quality": accounting["metrics"]["deduplication"],
        "period": {
            "months": months,
            "start": start.isoformat() if start != date.min else None,
            "end": latest.isoformat(),
        },
        "summary": {
            "raw_spending": _money(raw_spending),
            "refunds": _money(refunds),
            "net_spending": _money(net_spending),
            "average_monthly_spending": _money(net_spending / period_months),
            "credit_card_payments": _money(payments),
            "transaction_count": len(rows),
            "review_count": classifications["review_count"],
        },
        "monthly": monthly_rows,
        "quarterly": [
            {"period": key, "net_spending": _money(value)}
            for key, value in sorted(quarterly.items())
        ],
        "annual": [
            {"period": key, "net_spending": _money(value)}
            for key, value in sorted(annual.items())
        ],
        "yearly": _yearly_metrics(all_posted_rows, latest),
        "categories": [
            {"category": key, "net_spending": _money(value)}
            for key, value in sorted(
                categories.items(), key=lambda item: item[1], reverse=True
            )
        ],
        "merchants": [
            {
                "merchant_name": key,
                "net_spending": _money(value["amount"]),
                "transaction_count": value["count"],
            }
            for key, value in sorted(
                merchants.items(), key=lambda item: item[1]["amount"], reverse=True
            )[:30]
        ],
        "transactions": [
            {
                key: item[key]
                for key in (
                    "id",
                    "date",
                    "merchant_name",
                    "amount",
                    "accounting_type",
                    "category",
                    "adjustment",
                )
            }
            for item in sorted(
                rows, key=lambda item: (item["date"], item["id"]), reverse=True
            )
        ],
        "recurring": recurring,
        "review": {
            "count": classifications["review_count"],
            "items": classifications["review_items"],
        },
        "anomalies": _anomalies(rows, monthly),
        "liabilities": liabilities,
    }
