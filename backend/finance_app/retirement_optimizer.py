from datetime import date
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator


router = APIRouter()


class ContributionRuleset(BaseModel):
    version: str = Field(min_length=1, max_length=100)
    effective_date: date
    workplace_employee_limit: float = Field(gt=0, le=1_000_000)
    ira_combined_limit: float = Field(gt=0, le=1_000_000)
    hsa_self_only_limit: float = Field(gt=0, le=1_000_000)
    hsa_family_limit: float = Field(gt=0, le=1_000_000)
    workplace_plan_allowed: bool = True
    roth_ira_allowed: bool = True
    traditional_ira_allowed: bool = True
    hsa_allowed: bool = True


class AccountEligibility(BaseModel):
    workplace_plan: bool
    roth_ira: bool
    traditional_ira: bool
    traditional_ira_deductible: bool = False
    hsa: bool


class RetirementOptimizationInputs(BaseModel):
    current_age: int = Field(ge=18, le=100)
    retirement_age: int = Field(ge=18, le=100)
    unrestricted_access_age: int = Field(ge=18, le=100)
    annual_income: float = Field(ge=0, le=100_000_000)
    annual_savings_budget: float = Field(ge=0, le=10_000_000)
    accessible_balance: float = Field(ge=0, le=1_000_000_000)
    annual_bridge_spending: float = Field(ge=0, le=100_000_000)
    annual_return_percent: float = Field(ge=-100, le=100)
    taxable_tax_drag_percent: float = Field(ge=0, le=20)
    current_ordinary_tax_rate_percent: float = Field(ge=0, le=60)
    retirement_ordinary_tax_rate_percent: float = Field(ge=0, le=60)
    capital_gains_tax_rate_percent: float = Field(ge=0, le=40)
    employer_match_rate_percent: float = Field(ge=0, le=500)
    employer_matchable_salary_percent: float = Field(ge=0, le=100)
    filing_status: Literal[
        "single", "married_filing_jointly", "married_filing_separately"
    ]
    hsa_coverage: Literal["none", "self_only", "family"]
    eligibility: AccountEligibility
    ruleset: ContributionRuleset

    @model_validator(mode="after")
    def validate_timeline(self):
        if self.retirement_age < self.current_age:
            raise ValueError("retirement age cannot precede current age")
        if self.annual_return_percent - self.taxable_tax_drag_percent < -100:
            raise ValueError(
                "effective taxable return cannot be below -100%"
            )
        return self


def _money(value: float) -> float:
    return round(value + 0.0, 2)


def _future_value_factor(rate: float, years: int) -> float:
    if years <= 0:
        return 0.0
    if rate == 0:
        return float(years)
    return ((1 + rate) ** years - 1) / rate


def optimize_retirement_contributions(
    inputs: RetirementOptimizationInputs,
) -> dict:
    workplace_eligible = (
        inputs.eligibility.workplace_plan
        and inputs.ruleset.workplace_plan_allowed
    )
    roth_ira_eligible = (
        inputs.eligibility.roth_ira and inputs.ruleset.roth_ira_allowed
    )
    traditional_ira_eligible = (
        inputs.eligibility.traditional_ira
        and inputs.ruleset.traditional_ira_allowed
    )
    hsa_eligible = inputs.eligibility.hsa and inputs.ruleset.hsa_allowed
    matchable_employee_contribution = 0.0
    if workplace_eligible:
        matchable_employee_contribution = min(
            inputs.annual_income
            * inputs.employer_matchable_salary_percent
            / 100,
            inputs.ruleset.workplace_employee_limit,
            inputs.annual_savings_budget,
        )
    employer_match = (
        matchable_employee_contribution
        * inputs.employer_match_rate_percent
        / 100
    )
    remaining = inputs.annual_savings_budget - matchable_employee_contribution
    allocations = {
        "traditional_401k": matchable_employee_contribution,
        "roth_401k": 0.0,
        "traditional_ira": 0.0,
        "roth_ira": 0.0,
        "hsa": 0.0,
        "taxable": 0.0,
    }

    bridge_years = max(
        inputs.unrestricted_access_age - inputs.retirement_age, 0
    )
    bridge_target = inputs.annual_bridge_spending * bridge_years
    accumulation_years = max(inputs.retirement_age - inputs.current_age, 0)
    accessible_return = (
        inputs.annual_return_percent - inputs.taxable_tax_drag_percent
    ) / 100
    projected_existing = inputs.accessible_balance * (
        1 + accessible_return
    ) ** accumulation_years
    accessible_shortfall = max(bridge_target - projected_existing, 0)
    contribution_factor = _future_value_factor(
        accessible_return, accumulation_years
    )
    required_accessible_contribution = (
        accessible_shortfall / contribution_factor
        if contribution_factor > 0
        else accessible_shortfall
    )
    bridge_contribution = min(remaining, required_accessible_contribution)
    allocations["taxable"] = bridge_contribution
    remaining -= bridge_contribution

    if hsa_eligible and inputs.hsa_coverage != "none":
        hsa_limit = (
            inputs.ruleset.hsa_family_limit
            if inputs.hsa_coverage == "family"
            else inputs.ruleset.hsa_self_only_limit
        )
        allocations["hsa"] = min(remaining, hsa_limit)
        remaining -= allocations["hsa"]
    else:
        hsa_limit = 0.0

    if workplace_eligible and remaining > 0:
        workplace_room = max(
            inputs.ruleset.workplace_employee_limit
            - allocations["traditional_401k"],
            0,
        )
        workplace_contribution = min(remaining, workplace_room)
        workplace_account = (
            "traditional_401k"
            if inputs.current_ordinary_tax_rate_percent
            > inputs.retirement_ordinary_tax_rate_percent
            else "roth_401k"
        )
        allocations[workplace_account] += workplace_contribution
        remaining -= workplace_contribution

    ira_account: str | None = None
    if traditional_ira_eligible and (
        inputs.eligibility.traditional_ira_deductible
        and inputs.current_ordinary_tax_rate_percent
        > inputs.retirement_ordinary_tax_rate_percent
    ):
        ira_account = "traditional_ira"
    elif roth_ira_eligible:
        ira_account = "roth_ira"
    elif traditional_ira_eligible:
        ira_account = "traditional_ira"
    if ira_account is not None and remaining > 0:
        ira_contribution = min(remaining, inputs.ruleset.ira_combined_limit)
        allocations[ira_account] = ira_contribution
        remaining -= ira_contribution

    deductible_contributions = (
        allocations["traditional_401k"] + allocations["hsa"]
    )
    if inputs.eligibility.traditional_ira_deductible:
        deductible_contributions += allocations["traditional_ira"]
    tax_savings = (
        deductible_contributions
        * inputs.current_ordinary_tax_rate_percent
        / 100
    )
    allocations["taxable"] += remaining + tax_savings
    projected_bridge_funding = projected_existing + (
        allocations["taxable"] * contribution_factor
    )
    projected_bridge_gap = max(
        bridge_target - projected_bridge_funding, 0
    )
    preferred_workplace_account = (
        "traditional_401k"
        if inputs.current_ordinary_tax_rate_percent
        > inputs.retirement_ordinary_tax_rate_percent
        else "roth_401k"
    )

    def bridge_contribution_at_return(return_percent: float) -> float:
        rate = (return_percent - inputs.taxable_tax_drag_percent) / 100
        existing = inputs.accessible_balance * (1 + rate) ** accumulation_years
        shortfall = max(bridge_target - existing, 0)
        factor = _future_value_factor(rate, accumulation_years)
        return shortfall / factor if factor > 0 else shortfall

    sensitivity = {
        "retirement_tax_rate_plus_5": {
            "assumption_percent": _money(
                min(inputs.retirement_ordinary_tax_rate_percent + 5, 60)
            ),
            "preferred_workplace_account": (
                "traditional_401k"
                if inputs.current_ordinary_tax_rate_percent
                > min(inputs.retirement_ordinary_tax_rate_percent + 5, 60)
                else "roth_401k"
            ),
        },
        "retirement_tax_rate_minus_5": {
            "assumption_percent": _money(
                max(inputs.retirement_ordinary_tax_rate_percent - 5, 0)
            ),
            "preferred_workplace_account": (
                "traditional_401k"
                if inputs.current_ordinary_tax_rate_percent
                > max(inputs.retirement_ordinary_tax_rate_percent - 5, 0)
                else "roth_401k"
            ),
        },
        "return_minus_2": {
            "assumption_percent": _money(
                max(inputs.annual_return_percent - 2, -100)
            ),
            "required_annual_accessible_contribution": _money(
                bridge_contribution_at_return(
                    max(inputs.annual_return_percent - 2, -100)
                )
            ),
        },
        "return_plus_2": {
            "assumption_percent": _money(
                min(inputs.annual_return_percent + 2, 100)
            ),
            "required_annual_accessible_contribution": _money(
                bridge_contribution_at_return(
                    min(inputs.annual_return_percent + 2, 100)
                )
            ),
        },
    }

    return {
        "model_version": "retirement-contribution-optimizer-v1",
        "ruleset": {
            "version": inputs.ruleset.version,
            "effective_date": inputs.ruleset.effective_date.isoformat(),
        },
        "allocation": {
            name: _money(value) for name, value in allocations.items()
        },
        "employer_match": _money(employer_match),
        "match_protected": _money(matchable_employee_contribution)
        >= _money(
            min(
                inputs.annual_income
                * inputs.employer_matchable_salary_percent
                / 100,
                inputs.ruleset.workplace_employee_limit,
            )
        ),
        "invested_tax_savings": _money(tax_savings),
        "bridge": {
            "years": bridge_years,
            "target_at_retirement": _money(bridge_target),
            "projected_existing_at_retirement": _money(projected_existing),
            "required_annual_accessible_contribution": _money(
                required_accessible_contribution
            ),
            "planned_annual_accessible_contribution": _money(
                allocations["taxable"]
            ),
            "projected_funding_gap_at_retirement": _money(
                projected_bridge_gap
            ),
            "status": (
                "funded" if projected_bridge_gap <= 0.01 else "underfunded"
            ),
        },
        "contribution_limits": {
            "workplace_employee": _money(
                inputs.ruleset.workplace_employee_limit
                if workplace_eligible
                else 0
            ),
            "ira_combined": _money(
                inputs.ruleset.ira_combined_limit
                if (
                    roth_ira_eligible or traditional_ira_eligible
                )
                else 0
            ),
            "hsa": _money(hsa_limit),
        },
        "account_eligibility": {
            "workplace_plan": {
                "eligible": workplace_eligible,
                "reason": (
                    "eligible under selected ruleset"
                    if workplace_eligible
                    else f"not allowed by ruleset {inputs.ruleset.version}"
                ),
            },
            "roth_ira": {
                "eligible": roth_ira_eligible,
                "reason": (
                    "eligible under selected ruleset"
                    if roth_ira_eligible
                    else f"not allowed by ruleset {inputs.ruleset.version}"
                ),
            },
            "traditional_ira": {
                "eligible": traditional_ira_eligible,
                "reason": (
                    "eligible under selected ruleset"
                    if traditional_ira_eligible
                    else f"not allowed by ruleset {inputs.ruleset.version}"
                ),
            },
            "hsa": {
                "eligible": hsa_eligible,
                "reason": (
                    "eligible under selected ruleset"
                    if hsa_eligible
                    else f"not allowed by ruleset {inputs.ruleset.version}"
                ),
            },
        },
        "recommendation": {
            "preferred_workplace_account": preferred_workplace_account,
            "disclaimer": "Educational planning output, not professional financial, tax, or legal advice.",
        },
        "sensitivity": sensitivity,
        "drivers": [
            "Protecting the available employer match is the first allocation priority.",
            "Accessible taxable assets are reserved for the early-retirement bridge before discretionary retirement contributions.",
            "The current and retirement ordinary tax rate assumptions drive the traditional-versus-Roth preference.",
            "Return assumptions change how much annual accessible saving the bridge requires.",
        ],
    }


@router.post("/api/public/retirement/optimize")
def retirement_optimization(inputs: RetirementOptimizationInputs) -> dict:
    return optimize_retirement_contributions(inputs)
