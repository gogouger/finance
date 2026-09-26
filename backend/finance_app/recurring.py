import calendar
import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal, ROUND_HALF_UP
from statistics import median
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .auth import require_owner
from .merchant_identity import canonical_merchant


router = APIRouter()
CENT = Decimal("0.01")
CADENCES = (
    ("monthly", 25, 35),
    ("quarterly", 75, 105),
    ("annual", 330, 400),
)


class ObligationEdit(BaseModel):
    merchant_name: str | None = Field(default=None, min_length=1, max_length=160)
    amount: Decimal | None = Field(default=None, gt=0)
    cadence: Literal["monthly", "quarterly", "annual"] | None = None
    reason: str = Field(min_length=3, max_length=500)


def _money(value: Decimal | float | int) -> float:
    return float(Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP))


def _merchant(transaction: dict) -> str:
    return canonical_merchant(
        transaction.get("merchant_name") or transaction.get("name") or ""
    )


def _is_cost_candidate(transaction: dict, as_of: date) -> bool:
    if transaction.get("removed") or transaction.get("pending"):
        return False
    if Decimal(str(transaction.get("amount", 0))) <= 0:
        return False
    if date.fromisoformat(transaction["date"]) > as_of:
        return False
    category = transaction.get("personal_finance_category") or {}
    primary = str(category.get("primary", ""))
    detailed = str(category.get("detailed", ""))
    return not (
        primary in {"TRANSFER_IN", "TRANSFER_OUT"}
        or detailed.startswith("TRANSFER_")
        or detailed == "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"
    )


def _cadence(interval_days: float) -> str | None:
    return next(
        (
            name
            for name, minimum, maximum in CADENCES
            if minimum <= interval_days <= maximum
        ),
        None,
    )


def _proposal(group: list[dict]) -> dict | None:
    ordered = sorted(group, key=lambda item: (item["date"], item["transaction_id"]))
    if len(ordered) < 2:
        return None
    observed_dates = [date.fromisoformat(item["date"]) for item in ordered]
    intervals = [
        (right - left).days for left, right in zip(observed_dates, observed_dates[1:])
    ]
    median_interval = int(round(median(intervals)))
    cadence = _cadence(median_interval)
    if cadence is None or (cadence != "annual" and len(ordered) < 3):
        return None
    amounts = [Decimal(str(item["amount"])) for item in ordered]
    estimated = Decimal(str(median(amounts)))
    amount_spread = max(amounts) - min(amounts)
    if estimated <= 0 or amount_spread / estimated > Decimal("0.15"):
        return None
    merchant_name = _merchant(ordered[-1])
    proposal_id = hashlib.sha256(
        f"{merchant_name.casefold()}:{cadence}".encode()
    ).hexdigest()[:24]
    uncertainty = []
    if len(ordered) == 2:
        uncertainty.append(
            "Only two occurrences are available; another cycle would strengthen this estimate."
        )
    confidence = (
        Decimal("0.99")
        if len(ordered) >= 4 and amount_spread == 0
        else Decimal("0.82")
    )
    return {
        "id": proposal_id,
        "merchant_name": merchant_name,
        "cadence": cadence,
        "estimated_amount": _money(estimated),
        "status": "proposed",
        "affects_forecast": False,
        "confidence": float(confidence),
        "explanation": (
            f"{len(ordered)} charges from {merchant_name} recur about every "
            f"{median_interval} days with stable amounts."
        ),
        "uncertainty": uncertainty,
        "evidence": {
            "occurrence_count": len(ordered),
            "transaction_ids": [item["transaction_id"] for item in ordered],
            "observed_dates": [item["date"] for item in ordered],
            "median_interval_days": median_interval,
            "amount_range": {
                "minimum": _money(min(amounts)),
                "maximum": _money(max(amounts)),
            },
        },
    }


def detect_recurring_costs(transactions: list[dict], as_of: date) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for transaction in transactions:
        if not _is_cost_candidate(transaction, as_of):
            continue
        merchant = _merchant(transaction)
        if merchant:
            grouped.setdefault(merchant.casefold(), []).append(transaction)
    proposals = [
        proposal for group in grouped.values() if (proposal := _proposal(group))
    ]
    return sorted(
        proposals, key=lambda item: (item["merchant_name"].casefold(), item["id"])
    )


def _normalized_costs(obligations: list[dict]) -> dict:
    annual_multipliers = {
        "monthly": Decimal("12"),
        "quarterly": Decimal("4"),
        "annual": Decimal("1"),
    }
    annual = sum(
        Decimal(str(item["amount"])) * annual_multipliers[item["cadence"]]
        for item in obligations
        if item.get("status") == "confirmed"
    )
    return {
        "currency": "USD",
        "confirmed_cost_count": len(
            [item for item in obligations if item.get("status") == "confirmed"]
        ),
        "true_monthly_cost": _money(annual / Decimal("12")),
        "true_annual_cost": _money(annual),
    }


def _normalized_cost(obligation: dict) -> dict:
    annual = Decimal(str(obligation["amount"])) * {
        "monthly": Decimal("12"),
        "quarterly": Decimal("4"),
        "annual": Decimal("1"),
    }[obligation["cadence"]]
    return {"monthly": _money(annual / Decimal("12")), "annual": _money(annual)}


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _upcoming(obligations: list[dict], as_of: date) -> list[dict]:
    cadence_months = {"monthly": 1, "quarterly": 3, "annual": 12}
    upcoming = []
    for obligation in obligations:
        if obligation.get("status") != "confirmed":
            continue
        observed_dates = obligation.get("detection_evidence", {}).get(
            "observed_dates", []
        )
        if not observed_dates:
            continue
        due = date.fromisoformat(max(observed_dates))
        step = cadence_months[obligation["cadence"]]
        while due < as_of:
            due = _add_months(due, step)
        upcoming.append(
            {
                "date": due.isoformat(),
                "obligation_id": obligation["id"],
                "merchant_name": obligation["merchant_name"],
                "amount": _money(obligation["amount"]),
                "cadence": obligation["cadence"],
            }
        )
    return sorted(upcoming, key=lambda item: (item["date"], item["obligation_id"]))


@router.get("/api/private/recurring/costs")
def recurring_costs(
    request: Request,
    as_of: date = Query(default_factory=date.today),
) -> dict:
    owner = require_owner(request)
    storage = request.app.state.storage
    transactions = storage.list_financial_records(owner, "transaction")
    confirmed = storage.list_recurring_obligations(owner)
    confirmed_proposal_ids = {
        item["provenance"]["proposal_id"]
        for item in confirmed
        if item.get("provenance", {}).get("proposal_id")
    }
    return {
        "as_of": as_of.isoformat(),
        "proposals": [
            item
            for item in detect_recurring_costs(transactions, as_of)
            if item["id"] not in confirmed_proposal_ids
        ],
        "confirmed": [
            {**item, "normalized_cost": _normalized_cost(item)} for item in confirmed
        ],
        "upcoming_costs": _upcoming(confirmed, as_of),
        "metrics": _normalized_costs(confirmed),
    }


@router.post("/api/private/recurring/proposals/{proposal_id}/confirm")
def confirm_recurring_cost(proposal_id: str, request: Request) -> dict:
    owner = require_owner(request)
    storage = request.app.state.storage
    proposals = detect_recurring_costs(
        storage.list_financial_records(owner, "transaction"), date.today()
    )
    proposal = next((item for item in proposals if item["id"] == proposal_id), None)
    if proposal is None:
        raise HTTPException(status_code=404, detail="recurring proposal not found")
    if any(
        item.get("provenance", {}).get("proposal_id") == proposal_id
        for item in storage.list_recurring_obligations(owner)
    ):
        raise HTTPException(status_code=409, detail="recurring proposal already confirmed")
    now = datetime.now(UTC).isoformat()
    obligation = {
        "id": proposal_id,
        "merchant_name": proposal["merchant_name"],
        "cadence": proposal["cadence"],
        "amount": proposal["estimated_amount"],
        "status": "confirmed",
        "affects_forecast": True,
        "confidence": proposal["confidence"],
        "uncertainty": proposal["uncertainty"],
        "confirmed_at": now,
        "updated_at": now,
        "revision": 1,
        "edit_history": [],
        "detection_evidence": proposal["evidence"],
        "provenance": {
            "source": "detected_proposal",
            "proposal_id": proposal_id,
            "source_transaction_ids": proposal["evidence"]["transaction_ids"],
        },
    }
    storage.save_recurring_obligation(owner, obligation)
    return obligation


@router.patch("/api/private/recurring/obligations/{obligation_id}")
def edit_recurring_cost(
    obligation_id: str, payload: ObligationEdit, request: Request
) -> dict:
    owner = require_owner(request)
    storage = request.app.state.storage
    original = next(
        (
            item
            for item in storage.list_recurring_obligations(owner)
            if item["id"] == obligation_id
        ),
        None,
    )
    if original is None:
        raise HTTPException(status_code=404, detail="recurring obligation not found")

    requested = payload.model_dump(exclude={"reason"}, exclude_none=True)
    if "amount" in requested:
        requested["amount"] = _money(requested["amount"])
    changes = {
        field: {"before": original[field], "after": value}
        for field, value in requested.items()
        if original[field] != value
    }
    if not changes:
        raise HTTPException(status_code=422, detail="edit does not change the obligation")

    changed_at = datetime.now(UTC).isoformat()
    edited = {
        **original,
        **requested,
        "revision": original.get("revision", 1) + 1,
        "updated_at": changed_at,
        "edit_history": [
            *original.get("edit_history", []),
            {
                "revision": original.get("revision", 1) + 1,
                "actor": owner,
                "reason": payload.reason,
                "changed_at": changed_at,
                "changes": changes,
            },
        ],
    }
    storage.save_recurring_obligation(owner, edited)
    return edited
