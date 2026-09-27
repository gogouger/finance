import math
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

from .early_retirement import (
    EarlyAccessRuleset,
    EarlyRetirementInputs,
    compare_early_access_strategies,
)


router = APIRouter()

StrategyKey = Literal["taxable", "roth", "traditional", "hsa"]


class RetirementComparisonInputs(BaseModel):
    current_age: int = Field(ge=18, le=100)
    retirement_age: int = Field(ge=19, le=100)
    end_age: int = Field(ge=20, le=120)
    annual_take_home_sacrifice: float = Field(ge=0, le=10_000_000)
    annual_retirement_spending: float = Field(ge=0, le=100_000_000)
    taxable_balance: float = Field(ge=0, le=1_000_000_000)
    taxable_basis: float = Field(ge=0, le=1_000_000_000)
    traditional_balance: float = Field(ge=0, le=1_000_000_000)
    workplace_plan_balance: float = Field(ge=0, le=1_000_000_000)
    roth_balance: float = Field(ge=0, le=1_000_000_000)
    roth_contribution_basis: float = Field(ge=0, le=1_000_000_000)
    hsa_balance: float = Field(ge=0, le=1_000_000_000)
    annual_return_percent: float = Field(ge=-100, le=100)
    taxable_tax_drag_percent: float = Field(ge=0, le=20)
    inflation_percent: float = Field(ge=-10, le=30)
    current_ordinary_tax_rate_percent: float = Field(ge=0, le=60)
    retirement_ordinary_tax_rate_percent: float = Field(ge=0, le=60)
    capital_gains_tax_rate_percent: float = Field(ge=0, le=40)
    employer_match: float = Field(default=0, ge=0, le=10_000_000)
    traditional_contribution_limit: float = Field(gt=0, le=1_000_000)
    roth_contribution_limit: float = Field(gt=0, le=1_000_000)
    hsa_contribution_limit: float = Field(gt=0, le=1_000_000)
    qualified_hsa_spending_percent: float = Field(default=15, ge=0, le=100)
    annual_conversion_amount: float = Field(default=0, ge=0, le=100_000_000)
    sepp_annual_distribution: float = Field(default=0, ge=0, le=100_000_000)
    ruleset: EarlyAccessRuleset

    @model_validator(mode="after")
    def validate_timeline_and_basis(self):
        if self.retirement_age <= self.current_age:
            raise ValueError("retirement age must be after current age")
        if self.end_age <= self.retirement_age:
            raise ValueError("end age must be after retirement age")
        if self.taxable_basis > self.taxable_balance:
            raise ValueError("taxable basis cannot exceed taxable balance")
        if self.roth_contribution_basis > self.roth_balance:
            raise ValueError("Roth contribution basis cannot exceed Roth balance")
        if self.annual_return_percent - self.taxable_tax_drag_percent < -100:
            raise ValueError("effective taxable return cannot be below -100%")
        return self


def _money(value: float) -> float:
    return round(value + 0.0, 2)


def _after_tax_value(
    key: StrategyKey,
    *,
    balance: float,
    basis: float,
    age: int,
    inputs: RetirementComparisonInputs,
) -> float:
    if key == "taxable":
        gain = max(balance - basis, 0)
        return balance - gain * inputs.capital_gains_tax_rate_percent / 100
    if key == "traditional":
        penalty = (
            inputs.ruleset.early_withdrawal_penalty_percent / 100
            if age < inputs.ruleset.unrestricted_access_age
            else 0
        )
        return balance * max(
            1 - inputs.retirement_ordinary_tax_rate_percent / 100 - penalty,
            0,
        )
    if key == "roth" and age < inputs.ruleset.unrestricted_access_age:
        earnings = max(balance - basis, 0)
        return basis + earnings * max(
            1
            - inputs.retirement_ordinary_tax_rate_percent / 100
            - inputs.ruleset.early_withdrawal_penalty_percent / 100,
            0,
        )
    if key == "hsa":
        qualified = inputs.qualified_hsa_spending_percent / 100
        nonqualified = balance * (1 - qualified)
        penalty = (
            inputs.ruleset.hsa_nonqualified_penalty_percent / 100
            if age < 65
            else 0
        )
        return balance * qualified + nonqualified * max(
            1
            - inputs.retirement_ordinary_tax_rate_percent / 100
            - penalty,
            0,
        )
    return balance


def _contribution_plan(
    key: StrategyKey, inputs: RetirementComparisonInputs
) -> tuple[float, float, float]:
    sacrifice = inputs.annual_take_home_sacrifice
    if key == "taxable":
        return sacrifice, 0.0, 0.0
    if key == "roth":
        primary = min(sacrifice, inputs.roth_contribution_limit)
        return primary, sacrifice - primary, 0.0

    current_tax_rate = inputs.current_ordinary_tax_rate_percent / 100
    gross_equivalent = (
        sacrifice / (1 - current_tax_rate)
        if current_tax_rate < 1
        else sacrifice
    )
    limit = (
        inputs.traditional_contribution_limit
        if key == "traditional"
        else inputs.hsa_contribution_limit
    )
    primary = min(gross_equivalent, limit)
    take_home_used = primary * (1 - current_tax_rate)
    overflow = max(sacrifice - take_home_used, 0)
    match = inputs.employer_match if key == "traditional" else 0.0
    return primary, overflow, match


def _project_strategy(
    key: StrategyKey, inputs: RetirementComparisonInputs
) -> dict:
    primary_contribution, overflow_contribution, match = _contribution_plan(
        key, inputs
    )
    balance = 0.0
    basis = 0.0
    overflow_balance = 0.0
    overflow_basis = 0.0
    sheltered_return = inputs.annual_return_percent / 100
    taxable_return = (
        inputs.annual_return_percent - inputs.taxable_tax_drag_percent
    ) / 100
    years = []
    for age in range(inputs.current_age + 1, inputs.retirement_age + 1):
        balance += primary_contribution + match
        if key in ("taxable", "roth"):
            basis += primary_contribution
        if key == "traditional":
            basis = 0.0
        overflow_balance += overflow_contribution
        overflow_basis += overflow_contribution
        balance *= 1 + (taxable_return if key == "taxable" else sheltered_return)
        overflow_balance *= 1 + taxable_return
        primary_after_tax = _after_tax_value(
            key,
            balance=balance,
            basis=basis,
            age=age,
            inputs=inputs,
        )
        overflow_gain = max(overflow_balance - overflow_basis, 0)
        overflow_after_tax = overflow_balance - (
            overflow_gain * inputs.capital_gains_tax_rate_percent / 100
        )
        years.append(
            {
                "age": age,
                "headline_balance": _money(balance + overflow_balance),
                "after_tax_value": _money(
                    primary_after_tax + overflow_after_tax
                ),
                "primary_balance": _money(balance),
                "taxable_overflow": _money(overflow_balance),
                "accessible_basis": _money(
                    basis + overflow_basis
                    if key in ("taxable", "roth")
                    else overflow_basis
                ),
            }
        )

    labels = {
        "taxable": "Taxable brokerage",
        "roth": "Roth account",
        "traditional": "Traditional 401(k)",
        "hsa": "HSA",
    }
    caveats = {
        "taxable": "Accessible at any age; the model taxes gains and reduces returns by tax drag.",
        "roth": "Contribution basis is accessible first; early earnings may create tax and penalty.",
        "traditional": "Current tax savings buy a larger contribution; early access may create income tax and penalty.",
        "hsa": "Tax-free value assumes the displayed share is used for qualified medical expenses.",
    }
    final = years[-1]
    return {
        "key": key,
        "label": labels[key],
        "annual_take_home_cost": _money(inputs.annual_take_home_sacrifice),
        "annual_primary_contribution": _money(primary_contribution),
        "annual_taxable_overflow": _money(overflow_contribution),
        "employer_match": _money(match),
        "years": years,
        "at_retirement": final,
        "caveat": caveats[key],
    }


def compare_retirement_choices(inputs: RetirementComparisonInputs) -> dict:
    strategies = [
        _project_strategy(key, inputs)
        for key in ("taxable", "roth", "traditional", "hsa")
    ]
    by_key = {strategy["key"]: strategy for strategy in strategies}
    accumulation_years = inputs.retirement_age - inputs.current_age
    sheltered_growth = (1 + inputs.annual_return_percent / 100) ** (
        accumulation_years
    )
    taxable_growth = (
        1
        + (
            inputs.annual_return_percent
            - inputs.taxable_tax_drag_percent
        )
        / 100
    ) ** accumulation_years
    retirement_spending = inputs.annual_retirement_spending * (
        1 + inputs.inflation_percent / 100
    ) ** accumulation_years
    bridge_years = max(
        math.ceil(inputs.ruleset.unrestricted_access_age)
        - inputs.retirement_age,
        0,
    )
    bridge_required = sum(
        retirement_spending * (1 + inputs.inflation_percent / 100) ** year
        for year in range(bridge_years)
    )
    existing_taxable = inputs.taxable_balance * taxable_growth
    existing_taxable_gain = max(
        existing_taxable - inputs.taxable_basis,
        0,
    )
    existing_taxable_after_tax = existing_taxable - (
        existing_taxable_gain
        * inputs.capital_gains_tax_rate_percent
        / 100
    )
    existing_roth_basis = inputs.roth_contribution_basis
    taxable_projection = by_key["taxable"]["at_retirement"]
    accessible_at_retirement = (
        existing_taxable_after_tax
        + existing_roth_basis
        + taxable_projection["after_tax_value"]
    )
    traditional_projection = by_key["traditional"]["at_retirement"]
    roth_projection = by_key["roth"]["at_retirement"]
    early_inputs = EarlyRetirementInputs(
        retirement_age=inputs.retirement_age,
        end_age=min(
            inputs.end_age,
            max(
                math.ceil(inputs.ruleset.unrestricted_access_age) + 1,
                inputs.retirement_age + 1,
            ),
        ),
        annual_spending=retirement_spending,
        taxable_balance=existing_taxable
        + taxable_projection["headline_balance"],
        taxable_basis=min(
            inputs.taxable_basis
            + taxable_projection["accessible_basis"],
            existing_taxable + taxable_projection["headline_balance"],
        ),
        traditional_balance=(
            inputs.traditional_balance * sheltered_growth
            + traditional_projection["headline_balance"]
        ),
        workplace_plan_balance=(
            inputs.workplace_plan_balance * sheltered_growth
            + traditional_projection["primary_balance"]
        ),
        roth_contribution_basis=(
            inputs.roth_contribution_basis
            + roth_projection["accessible_basis"]
        ),
        annual_conversion_amount=inputs.annual_conversion_amount,
        separated_from_employer_age=inputs.retirement_age,
        sepp_annual_distribution=inputs.sepp_annual_distribution,
        ordinary_tax_rate_percent=inputs.retirement_ordinary_tax_rate_percent,
        capital_gains_tax_rate_percent=inputs.capital_gains_tax_rate_percent,
        annual_return_percent=inputs.annual_return_percent,
        ruleset=inputs.ruleset,
    )
    early_access = compare_early_access_strategies(early_inputs)
    ranked = sorted(
        strategies,
        key=lambda item: item["at_retirement"]["after_tax_value"],
        reverse=True,
    )
    return {
        "model_version": "retirement-comparison-v1",
        "currency": "USD",
        "retirement_age": inputs.retirement_age,
        "comparison_basis": {
            "annual_take_home_sacrifice": _money(
                inputs.annual_take_home_sacrifice
            ),
            "accumulation_years": accumulation_years,
            "ranking_metric": "Estimated spendable value at retirement after modeled taxes and early-access penalty.",
        },
        "strategies": strategies,
        "ranking": [
            {
                "key": item["key"],
                "label": item["label"],
                "after_tax_value": item["at_retirement"][
                    "after_tax_value"
                ],
            }
            for item in ranked
        ],
        "bridge": {
            "years": bridge_years,
            "annual_spending_at_retirement": _money(retirement_spending),
            "required_spending": _money(bridge_required),
            "accessible_at_retirement": _money(accessible_at_retirement),
            "funding_path": "existing flexible assets plus directing the modeled annual sacrifice to taxable brokerage",
            "existing_gap": _money(
                max(bridge_required - accessible_at_retirement, 0)
            ),
        },
        "early_access": early_access,
        "drivers": [
            "Every strategy uses the same annual reduction in take-home cash.",
            "Current tax savings increase traditional and HSA contribution capacity, subject to the displayed limits.",
            "The retirement-age comparison values taxes and early-access penalties instead of comparing headline balances.",
            "The bridge separates money available before unrestricted retirement-account access from later wealth.",
        ],
        "disclaimer": "Educational planning output, not professional financial, tax, or legal advice. Account eligibility and future law can change.",
    }


@router.post("/api/public/retirement/compare")
def retirement_comparison(inputs: RetirementComparisonInputs) -> dict:
    return compare_retirement_choices(inputs)
