from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .auth import require_owner


router = APIRouter()
CENT = Decimal("0.01")


def _decimal(value: Decimal | float | int) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _money(value: Decimal | float | int) -> float:
    return float(_decimal(value))


class Category(BaseModel):
    primary: str = Field(min_length=1, max_length=80)
    detailed: str = Field(min_length=1, max_length=80)

    @field_validator("primary", "detailed")
    @classmethod
    def normalize_category(cls, value: str) -> str:
        return value.strip().casefold().replace(" ", "_")


class Split(BaseModel):
    amount: Decimal = Field(gt=0)
    category: Category
    tags: list[str] = Field(default_factory=list, max_length=30)


class ClassificationInput(BaseModel):
    merchant_name: str | None = Field(default=None, min_length=1, max_length=160)
    category: Category
    tags: list[str] = Field(default_factory=list, max_length=30)
    splits: list[Split] = Field(default_factory=list, max_length=50)


class RuleMatch(BaseModel):
    merchant_contains: str = Field(min_length=2, max_length=160)

    @field_validator("merchant_contains")
    @classmethod
    def normalize_match(cls, value: str) -> str:
        return value.strip().casefold()


class RuleAssignment(BaseModel):
    merchant_name: str | None = Field(default=None, min_length=1, max_length=160)
    category: Category
    tags: list[str] = Field(default_factory=list, max_length=30)


class RuleInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    priority: int = Field(default=0, ge=-1000, le=1000)
    match: RuleMatch
    assignment: RuleAssignment


class RuleCreate(RuleInput):
    apply_historical: bool = False


class SuggestionInput(BaseModel):
    transaction_id: str = Field(min_length=1, max_length=160)
    category: Category
    tags: list[str] = Field(default_factory=list, max_length=30)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=1000)


class SuggestionDecision(BaseModel):
    decision: Literal["accept", "dismiss"]


def _tags(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip().casefold() for value in values if value.strip()))


def _transaction(storage, owner: str, transaction_id: str) -> dict:
    transaction = next(
        (
            item
            for item in storage.list_financial_records(owner, "transaction")
            if item.get("transaction_id") == transaction_id and not item.get("removed")
        ),
        None,
    )
    if transaction is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    return transaction


def _owner_classification(
    transaction_id: str, payload: ClassificationInput, source_amount: Decimal
) -> dict:
    if payload.splits:
        split_total = sum((_decimal(split.amount) for split in payload.splits), Decimal("0"))
        if split_total != source_amount:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"split amounts {split_total:.2f} must equal the source amount "
                    f"{source_amount:.2f}"
                ),
            )
    return {
        "transaction_id": transaction_id,
        "merchant_name": payload.merchant_name,
        "category": payload.category.model_dump(),
        "tags": _tags(payload.tags),
        "splits": [
            {
                "amount": _money(split.amount),
                "category": split.category.model_dump(),
                "tags": _tags(split.tags),
            }
            for split in payload.splits
        ],
        "confidence": 1.0,
        "provenance": "owner",
        "explanation": "Classified directly by the owner.",
        "needs_review": False,
    }


def _rule_matches(rule: dict, transaction: dict) -> bool:
    merchant = (transaction.get("merchant_name") or transaction.get("name") or "")
    return rule["match"]["merchant_contains"] in merchant.casefold()


def _assignment_identity(rule: dict) -> tuple:
    assignment = rule["assignment"]
    return (
        assignment.get("merchant_name"),
        assignment["category"]["primary"],
        assignment["category"]["detailed"],
        tuple(_tags(assignment.get("tags", []))),
    )


def _rule_explanation(rule: dict) -> str:
    return (
        f"Matched merchant containing '{rule['match']['merchant_contains']}' "
        f"at priority {rule['priority']}."
    )


def _classification_from_rule(transaction_id: str, rule: dict) -> dict:
    assignment = rule["assignment"]
    return {
        "transaction_id": transaction_id,
        "merchant_name": assignment.get("merchant_name"),
        "category": assignment["category"],
        "tags": _tags(assignment.get("tags", [])),
        "splits": [],
        "confidence": 0.98,
        "provenance": f"rule:{rule['id']}",
        "explanation": _rule_explanation(rule),
        "needs_review": False,
    }


def _matching_result(transaction: dict, rules: list[dict]) -> dict:
    matches = sorted(
        (rule for rule in rules if _rule_matches(rule, transaction)),
        key=lambda rule: (-rule["priority"], rule["order"], rule["id"]),
    )
    if not matches:
        return {"winner": None, "conflict": None}
    top = [rule for rule in matches if rule["priority"] == matches[0]["priority"]]
    identities = {_assignment_identity(rule) for rule in top}
    if len(identities) > 1:
        return {
            "winner": None,
            "conflict": {
                "transaction_id": transaction["transaction_id"],
                "reason": "Equal-priority rules propose different classifications.",
                "competing_rules": [
                    {
                        "id": rule["id"],
                        "name": rule["name"],
                        "priority": rule["priority"],
                        "assignment": rule["assignment"],
                    }
                    for rule in top
                ],
            },
        }
    return {"winner": top[0], "conflict": None}


def _preview(candidate: dict, transactions: list[dict], rules: list[dict]) -> dict:
    all_rules = [*rules, candidate]
    effects = []
    conflicts = []
    for transaction in transactions:
        if transaction.get("removed") or not _rule_matches(candidate, transaction):
            continue
        result = _matching_result(transaction, all_rules)
        if result["conflict"]:
            conflicts.append(result["conflict"])
        elif result["winner"]["id"] == candidate["id"]:
            effects.append(
                {
                    "transaction_id": transaction["transaction_id"],
                    "classification": _classification_from_rule(
                        transaction["transaction_id"], candidate
                    ),
                    "explanation": _rule_explanation(candidate),
                }
            )
    return {
        "effect_count": len(effects),
        "effects": effects,
        "conflicts": conflicts,
    }


def _candidate(payload: RuleInput, order: int) -> dict:
    record = payload.model_dump()
    record["assignment"]["tags"] = _tags(record["assignment"]["tags"])
    return {**record, "id": str(uuid4()), "order": order}


def _provider_classification(transaction: dict) -> dict:
    category = transaction.get("personal_finance_category") or {}
    return {
        "transaction_id": transaction["transaction_id"],
        "merchant_name": transaction.get("merchant_name") or transaction.get("name"),
        "category": {
            "primary": str(category.get("primary", "uncategorized")).casefold(),
            "detailed": str(category.get("detailed", "uncategorized")).casefold(),
        },
        "tags": [],
        "splits": [],
        "confidence": 0.55,
        "provenance": "provider",
        "explanation": "Imported provider category has not been confirmed.",
        "needs_review": True,
    }


def _effective_classification(
    transaction: dict, stored: dict | None, rules: list[dict]
) -> tuple[dict, dict | None]:
    if stored and stored.get("provenance") == "owner":
        return stored, None
    result = _matching_result(transaction, rules)
    if result["conflict"]:
        record = _provider_classification(transaction)
        record["explanation"] = result["conflict"]["reason"]
        return record, result["conflict"]
    if result["winner"]:
        return _classification_from_rule(
            transaction["transaction_id"], result["winner"]
        ), None
    return _provider_classification(transaction), None


def effective_classifications(storage, owner: str) -> dict[str, dict]:
    stored = {
        item["transaction_id"]: item
        for item in storage.list_transaction_classifications(owner)
    }
    rules = storage.list_classification_rules(owner)
    return {
        item["transaction_id"]: _effective_classification(
            item, stored.get(item["transaction_id"]), rules
        )[0]
        for item in storage.list_financial_records(owner, "transaction")
        if not item.get("removed")
    }


@router.put("/api/private/transactions/{transaction_id}/classification")
def classify_transaction(
    transaction_id: str, payload: ClassificationInput, request: Request
) -> dict:
    owner = require_owner(request)
    source = _transaction(request.app.state.storage, owner, transaction_id)
    record = _owner_classification(
        transaction_id, payload, _decimal(abs(Decimal(str(source.get("amount", 0)))))
    )
    request.app.state.storage.save_transaction_classification(
        owner, transaction_id, record
    )
    return record


@router.get("/api/private/transactions/classifications")
def list_transaction_classifications(request: Request) -> dict:
    owner = require_owner(request)
    stored = {
        item["transaction_id"]: item
        for item in request.app.state.storage.list_transaction_classifications(owner)
    }
    rules = request.app.state.storage.list_classification_rules(owner)
    transactions = request.app.state.storage.list_financial_records(owner, "transaction")
    return {
        "transactions": [
            {
                "transaction_id": item["transaction_id"],
                "merchant_name": item.get("merchant_name") or item.get("name"),
                "amount": _money(item.get("amount", 0)),
                "classification": _effective_classification(
                    item, stored.get(item["transaction_id"]), rules
                )[0],
            }
            for item in transactions
            if not item.get("removed")
        ]
    }


@router.post("/api/private/classification/rules/preview")
def preview_classification_rule(payload: RuleInput, request: Request) -> dict:
    owner = require_owner(request)
    storage = request.app.state.storage
    rules = storage.list_classification_rules(owner)
    candidate = _candidate(payload, len(rules) + 1)
    transactions = storage.list_financial_records(owner, "transaction")
    return _preview(candidate, transactions, rules)


@router.post("/api/private/classification/rules")
def create_classification_rule(payload: RuleCreate, request: Request) -> dict:
    owner = require_owner(request)
    storage = request.app.state.storage
    rules = storage.list_classification_rules(owner)
    candidate = _candidate(RuleInput(**payload.model_dump()), len(rules) + 1)
    transactions = storage.list_financial_records(owner, "transaction")
    preview = _preview(candidate, transactions, rules)
    storage.save_classification_rule(owner, candidate)
    historical_effect_count = 0
    if payload.apply_historical:
        existing = {
            item["transaction_id"]: item
            for item in storage.list_transaction_classifications(owner)
        }
        for effect in preview["effects"]:
            current = existing.get(effect["transaction_id"])
            if current and current.get("provenance") == "owner":
                continue
            storage.save_transaction_classification(
                owner, effect["transaction_id"], effect["classification"]
            )
            historical_effect_count += 1
    return {
        "rule": candidate,
        "historical_effect_count": historical_effect_count,
        "conflicts": preview["conflicts"],
    }


@router.get("/api/private/classification/rules")
def list_classification_rules(request: Request) -> dict:
    owner = require_owner(request)
    return {"rules": request.app.state.storage.list_classification_rules(owner)}


@router.post("/api/private/classification/suggestions")
def propose_classification(payload: SuggestionInput, request: Request) -> dict:
    owner = require_owner(request)
    _transaction(request.app.state.storage, owner, payload.transaction_id)
    suggestion = {
        **payload.model_dump(),
        "tags": _tags(payload.tags),
        "id": str(uuid4()),
        "provenance": "ai_suggestion",
        "applied": False,
        "status": "proposed",
    }
    request.app.state.storage.save_classification_suggestion(owner, suggestion)
    return suggestion


@router.get("/api/private/classification/review-queue")
def classification_review_queue(request: Request) -> dict:
    owner = require_owner(request)
    storage = request.app.state.storage
    stored = {
        item["transaction_id"]: item
        for item in storage.list_transaction_classifications(owner)
    }
    suggestions = {
        item["transaction_id"]: item
        for item in storage.list_classification_suggestions(owner)
        if item.get("status") == "proposed"
    }
    rules = storage.list_classification_rules(owner)
    items = []
    for transaction in storage.list_financial_records(owner, "transaction"):
        if transaction.get("removed"):
            continue
        effective, conflict = _effective_classification(
            transaction, stored.get(transaction["transaction_id"]), rules
        )
        suggestion = suggestions.get(transaction["transaction_id"])
        if conflict:
            reason = "rule_conflict"
        elif suggestion:
            reason = "ai_suggestion"
        elif effective["needs_review"]:
            reason = "low_confidence"
        else:
            continue
        item = {
            "transaction_id": transaction["transaction_id"],
            "merchant_name": transaction.get("merchant_name") or transaction.get("name"),
            "reason": reason,
            "confidence": effective["confidence"],
            "needs_review": True,
            "current_classification": effective,
        }
        if conflict:
            item["conflict"] = conflict
        if suggestion:
            item["suggestion"] = suggestion
        items.append(item)
    return {"items": sorted(items, key=lambda item: item["transaction_id"])}


@router.post(
    "/api/private/classification/suggestions/{suggestion_id}/decision"
)
def decide_classification_suggestion(
    suggestion_id: str, payload: SuggestionDecision, request: Request
) -> dict:
    owner = require_owner(request)
    storage = request.app.state.storage
    suggestion = next(
        (
            item
            for item in storage.list_classification_suggestions(owner)
            if item["id"] == suggestion_id
        ),
        None,
    )
    if suggestion is None:
        raise HTTPException(status_code=404, detail="suggestion not found")
    if suggestion.get("status") != "proposed":
        raise HTTPException(status_code=409, detail="suggestion already reviewed")
    suggestion["status"] = "accepted" if payload.decision == "accept" else "dismissed"
    suggestion["applied"] = payload.decision == "accept"
    if payload.decision == "accept":
        classification = {
            "transaction_id": suggestion["transaction_id"],
            "merchant_name": None,
            "category": suggestion["category"],
            "tags": suggestion["tags"],
            "splits": [],
            "confidence": 1.0,
            "provenance": "owner",
            "explanation": "Owner accepted AI suggestion.",
            "needs_review": False,
        }
        storage.save_transaction_classification(
            owner, suggestion["transaction_id"], classification
        )
    storage.replace_classification_suggestion(owner, suggestion)
    return suggestion
