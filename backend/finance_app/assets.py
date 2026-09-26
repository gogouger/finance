from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator

from .auth import require_owner
from .valuation_provider import (
    ValuationProviderRateLimited,
    ValuationProviderUnavailable,
    create_valuation_provider,
)


router = APIRouter()
CENT = Decimal("0.01")


def _money(value: Decimal | float | int) -> float:
    return float(Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP))


def _observation_with_freshness(observation: dict) -> dict:
    rendered = deepcopy(observation)
    fresh_until = rendered.pop("fresh_until", rendered["observed_at"])
    status_value = (
        "fresh"
        if datetime.fromisoformat(fresh_until.replace("Z", "+00:00"))
        > datetime.now(UTC)
        else "stale"
    )
    rendered["freshness"] = {
        "status": status_value,
        "fresh_until": fresh_until,
    }
    return rendered


class Ownership(BaseModel):
    owned_outright: bool
    debt_balance: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def outright_assets_have_no_debt(self):
        if self.owned_outright and self.debt_balance != 0:
            raise ValueError("an outright-owned asset cannot have a debt balance")
        return self


class Valuation(BaseModel):
    amount: float = Field(ge=0)
    valued_at: str = Field(min_length=1)
    source_label: str = Field(min_length=1, max_length=120)

    @field_validator("valued_at")
    @classmethod
    def valued_at_is_timestamp(cls, value: str) -> str:
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("valued_at must be an ISO 8601 timestamp") from error
        if timestamp.tzinfo is None:
            raise ValueError("valued_at must include a timezone")
        return value


class AssetCreate(BaseModel):
    kind: Literal["home", "vehicle"]
    name: str = Field(min_length=1, max_length=120)
    identifiers: dict[str, str] = Field(default_factory=dict)
    purchase_price: float = Field(ge=0)
    ownership: Ownership
    valuation: Valuation
    selling_cost_percent: float = Field(default=0, ge=0, le=100)
    annual_costs: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_cost_categories(self):
        allowed = {
            "home": {
                "property_tax",
                "hoa",
                "insurance",
                "utilities",
                "maintenance",
                "improvements",
            },
            "vehicle": {
                "insurance",
                "registration",
                "energy",
                "maintenance",
                "repairs",
                "transactions",
            },
        }[self.kind]
        unexpected = set(self.annual_costs) - allowed
        if unexpected:
            raise ValueError(
                f"unsupported {self.kind} cost categories: {', '.join(sorted(unexpected))}"
            )
        if any(value < 0 for value in self.annual_costs.values()):
            raise ValueError("annual costs cannot be negative")
        return self


class CostLinkCreate(BaseModel):
    transaction_id: str = Field(min_length=1)


class CostLinkUpdate(BaseModel):
    category: Literal[
        "property_tax",
        "insurance",
        "utilities",
        "maintenance",
        "capital_improvement",
        "registration",
        "energy",
        "repairs",
        "other",
    ]
    confirm_capital_improvement: bool = False


def _render(asset: dict) -> dict:
    value = Decimal(str(asset["valuation"]["amount"]))
    debt = Decimal(str(asset["ownership"]["debt_balance"]))
    selling_cost = value * Decimal(str(asset["selling_cost_percent"])) / Decimal("100")
    annual_costs = {
        category: _money(amount)
        for category, amount in asset["annual_costs"].items()
    }
    rendered = {
        **asset,
        "valuation": {"currency": "USD", **asset["valuation"]},
        "ownership": {
            **asset["ownership"],
            "gross_equity": _money(value - debt),
            "estimated_selling_cost": _money(selling_cost),
            "net_equity_after_sale": _money(value - debt - selling_cost),
        },
    }
    valued_at = datetime.fromisoformat(
        asset["valuation"]["valued_at"].replace("Z", "+00:00")
    )
    age_days = (datetime.now(UTC) - valued_at.astimezone(UTC)).days
    rendered["provenance"] = {
        "source_label": asset["valuation"]["source_label"],
        "observed_at": asset["valuation"]["valued_at"],
        "freshness": "current" if age_days <= 45 else "stale",
    }
    rendered["valuation_observations"] = [
        _observation_with_freshness(item)
        for item in asset.get("valuation_observations", [])
    ]
    if "valuation_automation" in asset:
        rendered["valuation_automation"] = deepcopy(asset["valuation_automation"])
        rendered["valuation_automation"]["latest_estimates"] = [
            _observation_with_freshness(item)
            for item in asset["valuation_automation"].get("latest_estimates", [])
        ]
    linked_total = sum(
        Decimal(str(item["amount"])) for item in asset.get("cost_links", [])
    )
    if asset["kind"] == "vehicle":
        rendered["cost_summary"] = {
            "depreciation_to_date": _money(
                max(Decimal("0"), Decimal(str(asset["purchase_price"])) - value)
            ),
            "annual_operating_costs": annual_costs,
            "annual_operating_total": _money(sum(annual_costs.values())),
            "linked_transaction_total": _money(linked_total),
        }
    else:
        rendered["cost_summary"] = {
            "annual_ownership_costs": annual_costs,
            "annual_ownership_total": _money(sum(annual_costs.values())),
            "linked_transaction_total": _money(linked_total),
        }
    return rendered


def _assets(request: Request, owner: str) -> list[dict]:
    return [
        item
        for item in request.app.state.storage.list_financial_records(
            owner, "household_asset"
        )
        if not item["removed"]
    ]


def _asset(request: Request, owner: str, asset_id: str) -> dict:
    asset = next(
        (item for item in _assets(request, owner) if item["id"] == asset_id), None
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    return asset


def _save_asset(request: Request, owner: str, asset: dict) -> None:
    stored = {key: value for key, value in asset.items() if key != "removed"}
    request.app.state.storage.upsert_financial_record(
        owner,
        "manual-assets",
        "household_asset",
        asset["id"],
        stored,
    )


def _valuation_provider(request: Request):
    if not hasattr(request.app.state, "valuation_provider"):
        request.app.state.valuation_provider = create_valuation_provider()
    return request.app.state.valuation_provider


@router.post(
    "/api/private/assets", status_code=status.HTTP_201_CREATED
)
def create_asset(payload: AssetCreate, request: Request) -> dict:
    owner = require_owner(request)
    asset_id = str(uuid4())
    asset = {
        "id": asset_id,
        **payload.model_dump(),
        "cost_links": [],
        "valuation_history": [payload.valuation.model_dump()],
    }
    request.app.state.storage.upsert_financial_record(
        owner,
        "manual-assets",
        "household_asset",
        asset_id,
        asset,
    )
    return _render({**asset, "removed": False})


@router.get("/api/private/assets")
def list_assets(request: Request) -> dict:
    owner = require_owner(request)
    return {
        "currency": "USD",
        "assets": [_render(asset) for asset in _assets(request, owner)],
    }


@router.get("/api/private/assets/valuation-provider/status")
def valuation_provider_status(request: Request) -> dict:
    require_owner(request)
    return _valuation_provider(request).terms()


def _mcp_safe_asset(asset: dict) -> dict:
    rendered = _render(asset)
    rendered.pop("identifiers", None)
    for observation in rendered.get("valuation_observations", []):
        observation.get("source", {}).pop("url", None)
    for observation in rendered.get("valuation_automation", {}).get(
        "latest_estimates", []
    ):
        observation.get("source", {}).pop("url", None)
    return rendered


@router.get("/api/private/assets/mcp-safe")
def list_assets_for_mcp(request: Request) -> dict:
    owner = require_owner(request)
    return {
        "currency": "USD",
        "privacy": {
            "identifiers_included": False,
            "source_urls_included": False,
        },
        "assets": [_mcp_safe_asset(asset) for asset in _assets(request, owner)],
    }


@router.post("/api/private/assets/{asset_id}/cost-links")
def link_asset_transaction(
    asset_id: str, payload: CostLinkCreate, request: Request
) -> dict:
    owner = require_owner(request)
    asset = _asset(request, owner, asset_id)
    transaction = next(
        (
            item
            for item in request.app.state.storage.list_financial_records(
                owner, "transaction"
            )
            if not item["removed"]
            and item.get("transaction_id") == payload.transaction_id
        ),
        None,
    )
    if transaction is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    if any(
        item["transaction_id"] == payload.transaction_id
        for item in asset["cost_links"]
    ):
        raise HTTPException(status_code=409, detail="transaction is already linked")
    link = {
        "transaction_id": payload.transaction_id,
        "transaction_date": transaction["date"],
        "merchant_name": transaction.get("merchant_name") or transaction.get("name"),
        "amount": _money(transaction["amount"]),
        "category": "maintenance",
        "classification": "defaulted",
    }
    asset["cost_links"].append(link)
    _save_asset(request, owner, asset)
    return link


@router.patch("/api/private/assets/{asset_id}/valuation")
def update_asset_valuation(
    asset_id: str, payload: Valuation, request: Request
) -> dict:
    owner = require_owner(request)
    asset = _asset(request, owner, asset_id)
    valuation = payload.model_dump()
    asset["valuation"] = valuation
    asset.setdefault("valuation_history", []).append(valuation)
    _save_asset(request, owner, asset)
    return _render(asset)


@router.post("/api/private/assets/{asset_id}/valuations/refresh")
def refresh_asset_valuations(asset_id: str, request: Request) -> dict:
    owner = require_owner(request)
    asset = _asset(request, owner, asset_id)
    provider = _valuation_provider(request)
    now = datetime.now(UTC)
    automation = asset.get("valuation_automation", {})
    cache_expires_at = automation.get("cache_expires_at")
    if cache_expires_at and datetime.fromisoformat(cache_expires_at) > now:
        return {
            "status": "cached",
            "cache": {"status": "hit", "expires_at": cache_expires_at},
            "terms": provider.terms(),
            "estimates": [
                _observation_with_freshness(item)
                for item in automation.get("latest_estimates", [])
            ],
        }

    try:
        estimates = provider.fetch(asset["kind"], asset.get("identifiers", {}))
    except (ValuationProviderUnavailable, ValuationProviderRateLimited) as error:
        automation.update(
            {
                "status": "unavailable",
                "last_attempt_at": now.isoformat(),
                "warning": str(error),
            }
        )
        asset["valuation_automation"] = automation
        _save_asset(request, owner, asset)
        return {
            "status": "unavailable",
            "cache": {"status": "miss"},
            "terms": provider.terms(),
            "warning": str(error),
            "fallback": {
                "status": "manual_value_retained",
                "valuation": _render(asset)["valuation"],
                "freshness": _render(asset)["provenance"]["freshness"],
            },
            "estimates": asset.get("valuation_observations", []),
        }

    expires_at = datetime.fromtimestamp(
        now.timestamp() + provider.cache_seconds, tz=UTC
    ).isoformat()
    estimates = [{**item, "fresh_until": expires_at} for item in estimates]
    asset.setdefault("valuation_observations", []).extend(estimates)
    automation = {
        "status": "available",
        "last_refreshed_at": now.isoformat(),
        "cache_expires_at": expires_at,
        "latest_estimates": estimates,
    }
    asset["valuation_automation"] = automation
    _save_asset(request, owner, asset)
    return {
        "status": "refreshed",
        "cache": {"status": "miss", "expires_at": expires_at},
        "terms": provider.terms(),
        "estimates": [_observation_with_freshness(item) for item in estimates],
    }


@router.patch("/api/private/assets/{asset_id}/cost-links/{transaction_id}")
def update_asset_cost_link(
    asset_id: str,
    transaction_id: str,
    payload: CostLinkUpdate,
    request: Request,
) -> dict:
    owner = require_owner(request)
    asset = _asset(request, owner, asset_id)
    link = next(
        (
            item
            for item in asset["cost_links"]
            if item["transaction_id"] == transaction_id
        ),
        None,
    )
    if link is None:
        raise HTTPException(status_code=404, detail="cost link not found")
    if (
        payload.category == "capital_improvement"
        and not payload.confirm_capital_improvement
    ):
        raise HTTPException(
            status_code=422,
            detail="capital improvements require explicit owner confirmation",
        )
    link["category"] = payload.category
    link["classification"] = "owner_confirmed"
    _save_asset(request, owner, asset)
    return link
