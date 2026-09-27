import hashlib
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from statistics import median
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from .accounting import build_accounting_view
from .auth import require_owner
from .classification import effective_classifications


router = APIRouter()
CENT = Decimal("0.01")
FeedbackAction = Literal[
    "useful",
    "not_useful",
    "essential",
    "later",
    "incorrectly_categorized",
]


class RecommendationFeedback(BaseModel):
    action: FeedbackAction
    note: str | None = Field(default=None, max_length=500)
    snoozed_until: date | None = None

    @model_validator(mode="after")
    def validate_snooze(self):
        if self.action == "later" and self.snoozed_until is None:
            raise ValueError("snoozed_until is required when reviewing later")
        return self


def _money(value: Decimal | float | int) -> float:
    return float(Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP))


def _opportunity_id(kind: str, subject: str) -> str:
    return hashlib.sha256(f"{kind}:{subject.casefold()}".encode()).hexdigest()[:24]


def _future_value(annual_saving: float, years: int = 10, rate: float = 0.07) -> float:
    if annual_saving <= 0:
        return 0.0
    return annual_saving * (((1 + rate) ** years - 1) / rate)


def _opportunity(
    *,
    kind: str,
    subject: str,
    title: str,
    explanation: str,
    action: str,
    monthly_impact: float,
    confidence: str,
    confidence_reason: str,
    evidence: list[dict],
) -> dict:
    annual_impact = monthly_impact * 12
    return {
        "id": _opportunity_id(kind, subject),
        "kind": kind,
        "subject": subject,
        "title": title,
        "explanation": explanation,
        "suggested_action": action,
        "estimated_impact": {
            "monthly": _money(monthly_impact),
            "annual": _money(annual_impact),
            "ten_year_investment_value": _money(_future_value(annual_impact)),
            "investment_assumption": "Illustrative 7% annual return for 10 years; not a forecast.",
        },
        "confidence": {"level": confidence, "rationale": confidence_reason},
        "evidence": evidence,
        "advisory_only": True,
    }


def _fee_opportunities(rows: list[dict], as_of: date) -> list[dict]:
    cutoff = as_of - timedelta(days=365)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        category = row.get("category") or {}
        primary = str(category.get("primary", "")).upper()
        detailed = str(category.get("detailed", "")).upper()
        if (
            row.get("accounting_type") == "spending"
            and date.fromisoformat(row["date"]) >= cutoff
            and (primary == "BANK_FEES" or "BANK_FEES" in detailed)
        ):
            grouped[str(row.get("merchant_name") or "Bank fee")].append(row)
    return [
        _opportunity(
            kind="fees",
            subject=merchant,
            title=f"Review {merchant} fees",
            explanation="These are explicitly categorized as bank fees. The estimate uses only posted charges from the last 12 months.",
            action="Check whether the fee can be avoided, waived, or replaced; no account change is made automatically.",
            monthly_impact=sum(float(row["amount"]) for row in items) / 12,
            confidence="high",
            confidence_reason="The provider or owner classification explicitly identifies these posted transactions as bank fees.",
            evidence=[
                {
                    "date": row["date"],
                    "merchant_name": row.get("merchant_name"),
                    "amount": _money(row["amount"]),
                    "transaction_id": row.get("id"),
                }
                for row in sorted(items, key=lambda item: item["date"], reverse=True)[:6]
            ],
        )
        for merchant, items in grouped.items()
        if sum(float(row["amount"]) for row in items) >= 12
    ]


def _category_growth_opportunities(rows: list[dict], as_of: date) -> list[dict]:
    last_complete_month = (as_of.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    monthly: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        if row.get("accounting_type") not in {"spending", "refund"}:
            continue
        month = str(row["date"])[:7]
        if month > last_complete_month:
            continue
        category = str((row.get("category") or {}).get("primary") or "UNCATEGORIZED")
        monthly[month][category] += float(row["amount"])
    months = sorted(monthly)
    if len(months) < 6:
        return []
    recent_months = months[-3:]
    prior_months = months[-6:-3]
    categories = set().union(*(monthly[month] for month in months[-6:]))
    opportunities = []
    for category in categories:
        recent = sum(max(monthly[month].get(category, 0), 0) for month in recent_months) / 3
        prior = sum(max(monthly[month].get(category, 0), 0) for month in prior_months) / 3
        increase = recent - prior
        if prior < 100 or increase < 100 or recent < prior * 1.25:
            continue
        readable = category.replace("_", " ").title()
        opportunities.append(
            _opportunity(
                kind="category_growth",
                subject=category,
                title=f"Understand the rise in {readable}",
                explanation=f"The latest three complete months averaged {_money(recent):,.0f} per month, compared with {_money(prior):,.0f} in the preceding three. This is a review prompt, not a budget judgment.",
                action="Open the category transactions and decide whether the change is expected, essential, miscategorized, or worth reducing.",
                monthly_impact=increase,
                confidence="medium",
                confidence_reason="Six complete months show a sustained category-level change, but the reason still requires owner review.",
                evidence=[
                    {
                        "period": "recent_three_complete_months",
                        "months": recent_months,
                        "monthly_average": _money(recent),
                    },
                    {
                        "period": "preceding_three_complete_months",
                        "months": prior_months,
                        "monthly_average": _money(prior),
                    },
                ],
            )
        )
    return opportunities


def _recurring_increase_opportunities(
    rows: list[dict], obligations: list[dict]
) -> list[dict]:
    by_merchant: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("accounting_type") == "spending":
            by_merchant[str(row.get("merchant_name") or "").casefold()].append(row)
    opportunities = []
    multiplier = {"monthly": 12, "quarterly": 4, "annual": 1}
    for obligation in obligations:
        if obligation.get("status") != "confirmed":
            continue
        items = sorted(
            by_merchant.get(str(obligation.get("merchant_name") or "").casefold(), []),
            key=lambda item: item["date"],
        )
        if len(items) < 3:
            continue
        latest = float(items[-1]["amount"])
        prior_values = [float(item["amount"]) for item in items[:-1]]
        typical = float(median(prior_values))
        increase = latest - typical
        if typical <= 0 or latest < typical * 1.1 or increase < 2:
            continue
        cadence = obligation["cadence"]
        annual_increase = increase * multiplier[cadence]
        opportunities.append(
            _opportunity(
                kind="recurring_increase",
                subject=str(obligation["id"]),
                title=f"{obligation['merchant_name']} costs more than before",
                explanation=f"The latest {cadence} charge is {_money(latest):,.0f}, compared with a {_money(typical):,.0f} median across {len(prior_values)} earlier charges.",
                action="Review the renewal or plan before the next charge. Keeping it is a valid owner decision.",
                monthly_impact=annual_increase / 12,
                confidence="medium" if cadence == "annual" else "high",
                confidence_reason="This compares a confirmed recurring obligation with its own posted charge history.",
                evidence=[
                    {"date": items[-1]["date"], "amount": _money(latest)},
                    {"earlier_charge_count": len(prior_values), "median_amount": _money(typical)},
                ],
            )
        )
    return opportunities


def build_spending_opportunities(
    rows: list[dict],
    obligations: list[dict],
    feedback: list[dict],
    *,
    as_of: date,
) -> dict:
    feedback_by_id = {
        item["opportunity_id"]: item for item in feedback if not item.get("removed")
    }
    candidates = [
        *_fee_opportunities(rows, as_of),
        *_recurring_increase_opportunities(rows, obligations),
        *_category_growth_opportunities(rows, as_of),
    ]
    active = []
    reviewed = []
    for item in candidates:
        owner_feedback = feedback_by_id.get(item["id"])
        enriched = {**item, "owner_feedback": owner_feedback}
        if owner_feedback is None:
            active.append(enriched)
            continue
        action = owner_feedback["action"]
        snoozed_until = owner_feedback.get("snoozed_until")
        if action == "later" and snoozed_until and date.fromisoformat(snoozed_until) <= as_of:
            active.append(enriched)
        else:
            reviewed.append(enriched)
    active.sort(key=lambda item: item["estimated_impact"]["annual"], reverse=True)
    reviewed.sort(
        key=lambda item: (item.get("owner_feedback") or {}).get(
            "feedback_updated_at", ""
        ),
        reverse=True,
    )
    return {
        "currency": "USD",
        "as_of": as_of.isoformat(),
        "opportunities": active,
        "reviewed": reviewed,
        "method": {
            "definition": "Evidence-backed review prompts from explicit fees, confirmed recurring-cost increases, and sustained category changes in the household's own history.",
            "boundaries": "No service is cancelled and no money is moved. Estimated impact is the observed increase or fee amount, not a promise that it can all be eliminated.",
        },
    }


def _current_recommendations(request: Request, owner: str) -> dict:
    storage = request.app.state.storage
    transactions = storage.list_financial_records(owner, "transaction")
    accounts = storage.list_financial_records(owner, "account")
    accounting = build_accounting_view(
        transactions,
        storage.list_transaction_adjustments(owner),
        storage.list_provider_record_versions(owner, "transaction"),
        effective_classifications(storage, owner),
        {
            item["account_id"]
            for item in accounts
            if not item.get("removed") and item.get("type") == "credit"
        },
    )
    latest = max(
        (date.fromisoformat(item["date"]) for item in accounting["transactions"]),
        default=date.today(),
    )
    return build_spending_opportunities(
        accounting["transactions"],
        storage.list_recurring_obligations(owner),
        storage.list_financial_records(owner, "recommendation_feedback"),
        as_of=latest,
    )


@router.get("/api/private/recommendations")
def recommendations(request: Request) -> dict:
    owner = require_owner(request)
    return _current_recommendations(request, owner)


@router.post("/api/private/recommendations/{opportunity_id}/feedback")
def save_recommendation_feedback(
    opportunity_id: str,
    payload: RecommendationFeedback,
    request: Request,
) -> dict:
    owner = require_owner(request)
    current = _current_recommendations(request, owner)
    known = {
        item["id"]: item
        for item in [*current["opportunities"], *current["reviewed"]]
    }
    if opportunity_id not in known:
        raise HTTPException(status_code=404, detail="recommendation not found")
    now = datetime.now(UTC).isoformat()
    record = {
        "id": opportunity_id,
        "opportunity_id": opportunity_id,
        "kind": known[opportunity_id]["kind"],
        "subject": known[opportunity_id]["subject"],
        **payload.model_dump(mode="json"),
        "feedback_updated_at": now,
    }
    request.app.state.storage.upsert_financial_record(
        owner,
        "owner-feedback",
        "recommendation_feedback",
        opportunity_id,
        record,
    )
    return record
