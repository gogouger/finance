from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .auth import require_owner
from .early_retirement import EarlyRetirementInputs, compare_early_access_strategies
from .housing import HousingInputs, calculate_housing
from .household import HouseholdPlan, project_household
from .retirement import RetirementInputs, calculate_retirement
from .retirement_optimizer import (
    RetirementOptimizationInputs,
    optimize_retirement_contributions,
)
from .retirement_uncertainty import (
    RetirementUncertaintyInputs,
    simulate_retirement_uncertainty,
)


router = APIRouter()
RULESETS = {
    "housing": "housing-core-2026-09-25",
    "housing_advanced": "housing-complete-2026-09-25",
    "retirement": "retirement-baseline-2026-09-25",
    "household": "household-life-events-v1",
    "retirement_uncertainty": "retirement-uncertainty-v1",
}
ScenarioCalculator = Literal[
    "housing",
    "housing_advanced",
    "retirement",
    "retirement_optimizer",
    "early_retirement",
    "household",
    "retirement_uncertainty",
]
DIRECT_IDENTIFIER_KEYS = {
    "account_number",
    "address",
    "email",
    "full_name",
    "name",
    "owner",
    "parcel_id",
    "phone",
}


class ScenarioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    calculator: ScenarioCalculator
    inputs: dict[str, Any]


class ScenarioClone(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    inputs: dict[str, Any] | None = None


class ScenarioComparison(BaseModel):
    baseline_id: str
    variant_id: str


class ShareCreate(BaseModel):
    expires_in_hours: int = Field(ge=1, le=24 * 30)


def calculate_scenario(calculator: str, inputs: dict[str, Any]) -> dict[str, Any]:
    if calculator in {"housing", "housing_advanced"}:
        return calculate_housing(HousingInputs.model_validate(inputs))
    if calculator == "retirement":
        return calculate_retirement(RetirementInputs.model_validate(inputs))
    if calculator == "retirement_optimizer":
        return optimize_retirement_contributions(
            RetirementOptimizationInputs.model_validate(inputs)
        )
    if calculator == "early_retirement":
        return compare_early_access_strategies(
            EarlyRetirementInputs.model_validate(inputs)
        )
    if calculator == "household":
        return project_household(HouseholdPlan.model_validate(inputs))
    if calculator == "retirement_uncertainty":
        return simulate_retirement_uncertainty(
            RetirementUncertaintyInputs.model_validate(inputs)
        )
    raise ValueError(f"unsupported scenario calculator: {calculator}")


def _ruleset_version(calculator: str, output: dict[str, Any]) -> str:
    ruleset = output.get("ruleset")
    if isinstance(ruleset, dict) and isinstance(ruleset.get("version"), str):
        return ruleset["version"]
    return str(output.get("model_version") or RULESETS[calculator])


def _scenario_record(payload: ScenarioCreate) -> dict[str, Any]:
    inputs = payload.inputs.copy()
    output = calculate_scenario(payload.calculator, inputs)
    return {
        "name": payload.name,
        "calculator": payload.calculator,
        "inputs": inputs,
        "output": output,
        "ruleset_version": _ruleset_version(payload.calculator, output),
    }


def _get_owned(request: Request, scenario_id: str) -> dict[str, Any]:
    scenario = request.app.state.storage.get_scenario(
        require_owner(request), scenario_id
    )
    if scenario is None:
        raise HTTPException(status_code=404, detail="scenario not found")
    return scenario


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact(item)
            for key, item in value.items()
            if key.lower() not in DIRECT_IDENTIFIER_KEYS
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _outcomes(scenario: dict[str, Any]) -> dict[str, float]:
    output = scenario["output"]
    calculator = scenario["calculator"]
    if calculator in {"housing", "housing_advanced"}:
        final = output["years"][-1]
        keys = ("buyer_net_wealth", "renter_investments", "buyer_advantage")
        return {key: final[key] for key in keys}
    if calculator == "retirement":
        final = output["years"][-1]
        keys = ("total_balance", "spendable_after_tax", "unmet_spending")
        return {key: final[key] for key in keys}
    if calculator == "retirement_optimizer":
        return {
            "employer_match": output["employer_match"],
            "bridge_gap": output["bridge"]["projected_funding_gap_at_retirement"],
            "taxable_allocation": output["allocation"]["taxable"],
        }
    if calculator == "early_retirement":
        eligible = [item for item in output["strategies"] if item["eligible"]]
        return {
            "best_spendable_value": max(
                (item["spendable_value"] for item in eligible), default=0
            ),
            "eligible_strategy_count": float(len(eligible)),
        }
    if calculator == "household":
        return {
            key: value for key, value in output["cumulative"].items()
        }
    baseline = output["cases"][0]
    return {
        "success_probability_percent": baseline["success_probability_percent"],
        "median_ending_balance": baseline["ending_balance_distribution"]["p50"],
    }


@router.get("/api/private/scenarios")
def list_scenarios(request: Request) -> list[dict[str, Any]]:
    return request.app.state.storage.list_scenarios(require_owner(request))


@router.post("/api/private/scenarios")
def save_scenario(payload: ScenarioCreate, request: Request) -> dict[str, Any]:
    return request.app.state.storage.save_scenario(
        require_owner(request), _scenario_record(payload)
    )


@router.post("/api/private/scenarios/compare")
def compare_scenarios(
    payload: ScenarioComparison, request: Request
) -> dict[str, Any]:
    baseline = _get_owned(request, payload.baseline_id)
    variant = _get_owned(request, payload.variant_id)
    if baseline["calculator"] != variant["calculator"]:
        raise HTTPException(status_code=422, detail="calculator types must match")
    input_changes = {
        key: {"baseline": baseline["inputs"].get(key), "variant": variant["inputs"].get(key)}
        for key in sorted(set(baseline["inputs"]) | set(variant["inputs"]))
        if baseline["inputs"].get(key) != variant["inputs"].get(key)
    }
    baseline_outcomes = _outcomes(baseline)
    variant_outcomes = _outcomes(variant)
    return {
        "baseline_id": baseline["id"],
        "variant_id": variant["id"],
        "input_changes": input_changes,
        "outcome_changes": {
            key: round(variant_outcomes[key] - baseline_outcomes[key], 2)
            for key in baseline_outcomes
        },
    }


@router.get("/api/private/scenarios/{scenario_id}")
def get_scenario(scenario_id: str, request: Request) -> dict[str, Any]:
    return _get_owned(request, scenario_id)


@router.post("/api/private/scenarios/{scenario_id}/clone")
def clone_scenario(
    scenario_id: str, payload: ScenarioClone, request: Request
) -> dict[str, Any]:
    original = _get_owned(request, scenario_id)
    inputs = original["inputs"] if payload.inputs is None else payload.inputs.copy()
    output = calculate_scenario(original["calculator"], inputs)
    clone = {
        "name": payload.name,
        "calculator": original["calculator"],
        "inputs": inputs,
        "output": output,
        "ruleset_version": _ruleset_version(original["calculator"], output),
        "cloned_from": original["id"],
    }
    return request.app.state.storage.save_scenario(require_owner(request), clone)


@router.post("/api/private/scenarios/{scenario_id}/shares")
def share_scenario(
    scenario_id: str, payload: ShareCreate, request: Request
) -> dict[str, str]:
    scenario = _get_owned(request, scenario_id)
    public_copy = _redact(
        {
            "calculator": scenario["calculator"],
            "inputs": scenario["inputs"],
            "output": scenario["output"],
            "ruleset_version": scenario["ruleset_version"],
        }
    )
    expires_at = datetime.now(UTC) + timedelta(hours=payload.expires_in_hours)
    token = request.app.state.storage.create_share(
        scenario_id, public_copy, expires_at
    )
    return {"token": token, "path": f"/api/public/shares/{token}"}


@router.get("/api/public/shares/{token}")
def read_share(token: str, request: Request) -> dict[str, Any]:
    payload = request.app.state.storage.get_share(token)
    if payload is None:
        raise HTTPException(status_code=404, detail="share not found")
    return payload


@router.delete("/api/private/shares/{token}", status_code=204)
def revoke_share(token: str, request: Request) -> Response:
    if not request.app.state.storage.revoke_share(require_owner(request), token):
        raise HTTPException(status_code=404, detail="share not found")
    return Response(status_code=204)
