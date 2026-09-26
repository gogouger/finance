from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Request
from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from .auth import require_owner


router = APIRouter()
CENT = Decimal("0.01")


class TransactionAdjustment(BaseModel):
    reimbursable: bool = False
    shared: bool = False
    business: bool = False
    excluded: bool = False
    personal_share_percent: int = Field(default=100, ge=0, le=100)

    @model_validator(mode="after")
    def shared_fraction_only_for_shared_transactions(self):
        if not self.shared and self.personal_share_percent != 100:
            raise ValueError("personal_share_percent requires shared=true")
        return self


def _money(value: Decimal | float | int) -> float:
    return float(Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP))


def _provider_category(
    transaction: dict, classification: dict | None = None
) -> tuple[str, str]:
    category = (classification or {}).get("category") or transaction.get(
        "personal_finance_category"
    ) or {}
    return str(category.get("primary", "UNCATEGORIZED")).upper(), str(
        category.get("detailed", "UNCATEGORIZED")
    ).upper()


def _is_transfer(transaction: dict) -> bool:
    primary, detailed = _provider_category(transaction)
    return primary in {"TRANSFER_IN", "TRANSFER_OUT"} or detailed.startswith(
        "TRANSFER_"
    )


def _is_card_payment(transaction: dict) -> bool:
    _, detailed = _provider_category(transaction)
    return detailed == "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"


def _matched_movements(transactions: list[dict]) -> dict[str, str]:
    matches: dict[str, str] = {}
    candidates = [
        item
        for item in transactions
        if not item.get("pending") and (_is_transfer(item) or _is_card_payment(item))
    ]
    for index, left in enumerate(candidates):
        left_id = left["transaction_id"]
        if left_id in matches:
            continue
        for right in candidates[index + 1 :]:
            right_id = right["transaction_id"]
            if right_id in matches or left.get("account_id") == right.get("account_id"):
                continue
            if Decimal(str(left.get("amount", 0))) != -Decimal(
                str(right.get("amount", 0))
            ):
                continue
            date_gap = abs(
                (date.fromisoformat(left["date"]) - date.fromisoformat(right["date"])).days
            )
            if date_gap > 3:
                continue
            movement_type = (
                "credit_card_payment"
                if _is_card_payment(left) or _is_card_payment(right)
                else "internal_transfer"
            )
            matches[left_id] = movement_type
            matches[right_id] = movement_type
            break
    return matches


def _refund_links(transactions: list[dict]) -> dict[str, str]:
    by_id = {item["transaction_id"]: item for item in transactions}
    links: dict[str, str] = {}
    for refund in transactions:
        amount = Decimal(str(refund.get("amount", 0)))
        if refund.get("pending") or amount >= 0:
            continue
        explicit_id = refund.get("original_transaction_id")
        if explicit_id in by_id:
            links[refund["transaction_id"]] = explicit_id
            continue
        merchant = (
            refund.get("merchant_name") or refund.get("name") or ""
        ).strip().casefold()
        if not merchant:
            continue
        refund_date = date.fromisoformat(refund["date"])
        candidates = [
            purchase
            for purchase in transactions
            if not purchase.get("pending")
            and Decimal(str(purchase.get("amount", 0))) == -amount
            and (
                purchase.get("merchant_name") or purchase.get("name") or ""
            ).strip().casefold()
            == merchant
            and 0
            <= (refund_date - date.fromisoformat(purchase["date"])).days
            <= 90
        ]
        if len(candidates) == 1:
            links[refund["transaction_id"]] = candidates[0]["transaction_id"]
    return links


def _personal_fraction(adjustment: dict) -> Decimal:
    if (
        adjustment["reimbursable"]
        or adjustment["business"]
        or adjustment["excluded"]
    ):
        return Decimal("0")
    if adjustment["shared"]:
        return Decimal(adjustment["personal_share_percent"]) / Decimal("100")
    return Decimal("1")


def build_accounting_view(
    transactions: list[dict],
    adjustments: list[dict] | None = None,
    provider_versions: list[dict] | None = None,
    classifications: dict[str, dict] | None = None,
    credit_account_ids: set[str] | None = None,
) -> dict:
    active = [item for item in transactions if not item.get("removed")]
    replaced_pending_ids = {
        item["pending_transaction_id"]
        for item in active
        if not item.get("pending") and item.get("pending_transaction_id")
    }
    visible = [
        item
        for item in active
        if item.get("transaction_id") not in replaced_pending_ids
    ]
    by_id = {item["transaction_id"]: item for item in visible}
    adjustments_by_id = {
        item["transaction_id"]: item for item in (adjustments or [])
    }
    versions_by_id: dict[str, list[dict]] = {}
    for version in provider_versions or []:
        versions_by_id.setdefault(version["source_id"], []).append(
            {
                "observed_at": version["observed_at"],
                "removed": version["removed"],
                "provider_record": version["provider_record"],
            }
        )
    matched_movements = _matched_movements(visible)
    refund_links = _refund_links(visible)
    classifications = classifications or {}
    credit_account_ids = credit_account_ids or set()

    rendered = []
    raw_spending = Decimal("0")
    provisional_spending = Decimal("0")
    refunds = Decimal("0")
    adjusted_spending = Decimal("0")
    income = Decimal("0")
    cash_inflows = Decimal("0")
    cash_outflows = Decimal("0")
    spending_by_category: dict[str, Decimal] = {}
    for source in visible:
        pending = bool(source.get("pending"))
        amount = Decimal(str(source.get("amount", 0)))
        source_id = source["transaction_id"]
        adjustment = {
            "reimbursable": False,
            "shared": False,
            "business": False,
            "excluded": False,
            "personal_share_percent": 100,
            **adjustments_by_id.get(source_id, {}),
        }
        original = by_id.get(refund_links.get(source_id, ""))
        accounting_type = matched_movements.get(source_id)
        if original is not None and amount < 0:
            accounting_type = "refund"
        elif accounting_type is None and _is_card_payment(source):
            accounting_type = "credit_card_payment"
        elif accounting_type is None and _is_transfer(source):
            accounting_type = "internal_transfer"
        elif (
            accounting_type is None
            and amount < 0
            and source.get("account_id") in credit_account_ids
        ):
            accounting_type = "refund"
        elif accounting_type is None:
            if pending:
                accounting_type = "pending"
            elif amount > 0:
                accounting_type = "spending"
            elif amount < 0:
                accounting_type = "income"
            else:
                accounting_type = "neutral"

        if not pending:
            if amount > 0:
                cash_outflows += amount
            elif amount < 0:
                cash_inflows += -amount

        category_source = original if original is not None and accounting_type == "refund" else source
        classification = classifications.get(category_source["transaction_id"])
        primary, detailed = _provider_category(category_source, classification)
        if accounting_type == "spending":
            if pending:
                provisional_spending += amount
            else:
                raw_spending += amount
                adjusted_spending += amount * _personal_fraction(adjustment)
                spending_by_category[primary] = spending_by_category.get(
                    primary, Decimal("0")
                ) + amount
        elif accounting_type == "pending" and amount > 0:
            provisional_spending += amount
        elif accounting_type == "refund":
            refunds += amount
            original_adjustment = {
                "reimbursable": False,
                "shared": False,
                "business": False,
                "excluded": False,
                "personal_share_percent": 100,
                **adjustments_by_id.get(
                    original["transaction_id"] if original is not None else source_id,
                    {},
                ),
            }
            adjusted_spending += amount * _personal_fraction(original_adjustment)
            spending_by_category[primary] = spending_by_category.get(
                primary, Decimal("0")
            ) + amount
        elif accounting_type == "income":
            income += -amount

        rendered_item = {
            "id": source_id,
            "date": source["date"],
            "cash_flow_date": source["date"],
            "merchant_name": (
                classifications.get(source_id, {}).get("merchant_name")
                or source.get("merchant_name")
                or source.get("name")
            ),
            "amount": _money(amount),
            "status": "pending" if pending else "posted",
            "accounting_type": accounting_type,
            "category": {"primary": primary, "detailed": detailed},
            "adjustment": {
                key: adjustment[key]
                for key in (
                    "reimbursable",
                    "shared",
                    "business",
                    "excluded",
                    "personal_share_percent",
                )
            },
            "provider_versions": versions_by_id.get(source_id, []),
        }
        if accounting_type == "refund" and original is not None:
            rendered_item["analytic_purchase_date"] = original["date"]
            rendered_item["refunds_transaction_id"] = original["transaction_id"]
        rendered.append(
            rendered_item
        )

    return {
        "currency": "USD",
        "transactions": sorted(rendered, key=lambda item: (item["date"], item["id"])),
        "metrics": {
            "finalized_spending": {
                "raw": _money(raw_spending),
                "adjusted": _money(adjusted_spending),
                "net_of_refunds": _money(raw_spending + refunds),
            },
            "provisional_spending": _money(provisional_spending),
            "income": _money(income),
            "cash_flow": {
                "inflows": _money(cash_inflows),
                "outflows": _money(cash_outflows),
                "net": _money(cash_inflows - cash_outflows),
            },
            "spending_by_category": {
                category: _money(amount)
                for category, amount in sorted(spending_by_category.items())
            },
        },
    }


@router.get("/api/private/transactions/accounting")
def transaction_accounting(request: Request) -> dict:
    owner = require_owner(request)
    transactions = request.app.state.storage.list_financial_records(
        owner, "transaction"
    )
    adjustments = request.app.state.storage.list_transaction_adjustments(owner)
    provider_versions = request.app.state.storage.list_provider_record_versions(
        owner, "transaction"
    )
    accounts = request.app.state.storage.list_financial_records(owner, "account")
    credit_account_ids = {
        item["account_id"]
        for item in accounts
        if not item.get("removed") and item.get("type") == "credit"
    }
    from .classification import effective_classifications

    return build_accounting_view(
        transactions,
        adjustments,
        provider_versions,
        effective_classifications(request.app.state.storage, owner),
        credit_account_ids,
    )


@router.patch("/api/private/transactions/{transaction_id}/adjustment")
def update_transaction_adjustment(
    transaction_id: str, payload: TransactionAdjustment, request: Request
) -> dict:
    owner = require_owner(request)
    transactions = request.app.state.storage.list_financial_records(
        owner, "transaction"
    )
    if not any(
        item.get("transaction_id") == transaction_id and not item.get("removed")
        for item in transactions
    ):
        raise HTTPException(status_code=404, detail="transaction not found")
    adjustment = {"transaction_id": transaction_id, **payload.model_dump()}
    request.app.state.storage.save_transaction_adjustment(
        owner, transaction_id, adjustment
    )
    return adjustment
