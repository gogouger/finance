import math
from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator


router = APIRouter()


STRATEGIES = (
    "penalized_traditional",
    "taxable_bridge",
    "roth_contribution_basis",
    "roth_conversion_ladder",
    "rule_of_55",
    "sepp_72t",
)


class EarlyAccessRuleset(BaseModel):
    version: str = Field(min_length=1, max_length=100)
    effective_date: date
    unrestricted_access_age: float = Field(ge=50, le=75)
    early_withdrawal_penalty_percent: float = Field(ge=0, le=100)
    hsa_nonqualified_penalty_percent: float = Field(default=20, ge=0, le=100)
    conversion_wait_years: int = Field(ge=0, le=20)
    rule_of_55_min_separation_age: int = Field(ge=50, le=65)
    sepp_minimum_years: int = Field(ge=1, le=20)


class EarlyRetirementInputs(BaseModel):
    retirement_age: int = Field(ge=18, le=100)
    end_age: int = Field(ge=19, le=120)
    annual_spending: float = Field(ge=0, le=100_000_000)
    taxable_balance: float = Field(ge=0, le=1_000_000_000)
    taxable_basis: float = Field(ge=0, le=1_000_000_000)
    traditional_balance: float = Field(ge=0, le=1_000_000_000)
    workplace_plan_balance: float = Field(ge=0, le=1_000_000_000)
    roth_contribution_basis: float = Field(ge=0, le=1_000_000_000)
    annual_conversion_amount: float = Field(ge=0, le=100_000_000)
    separated_from_employer_age: int = Field(ge=18, le=100)
    sepp_annual_distribution: float = Field(ge=0, le=100_000_000)
    ordinary_tax_rate_percent: float = Field(ge=0, le=60)
    capital_gains_tax_rate_percent: float = Field(ge=0, le=40)
    annual_return_percent: float = Field(ge=-100, le=100)
    ruleset: EarlyAccessRuleset

    @model_validator(mode="after")
    def validate_timeline_and_basis(self):
        if self.end_age <= self.retirement_age:
            raise ValueError("end age must be after retirement age")
        if self.taxable_basis > self.taxable_balance:
            raise ValueError("taxable basis cannot exceed taxable balance")
        return self


def _money(value: float) -> float:
    return round(value + 0.0, 2)


def _year_row(
    *,
    age: int,
    opening: float,
    withdrawal: float,
    ordinary_tax: float = 0,
    capital_gains_tax: float = 0,
    penalty: float = 0,
    spending: float,
    closing: float,
) -> dict:
    spendable = max(
        withdrawal - ordinary_tax - capital_gains_tax - penalty, 0
    )
    return {
        "age": age,
        "accessible_opening_balance": _money(opening),
        "gross_withdrawal": _money(withdrawal),
        "ordinary_tax": _money(ordinary_tax),
        "capital_gains_tax": _money(capital_gains_tax),
        "penalty": _money(penalty),
        "spendable": _money(spendable),
        "unmet_spending": _money(max(spending - spendable, 0)),
        "accessible_closing_balance": _money(closing),
    }


def _strategy_result(
    strategy: str,
    years: list[dict],
    *,
    eligible: bool = True,
    failure_reason: str | None = None,
) -> dict:
    if failure_reason is None:
        failed_year = next(
            (year for year in years if year["unmet_spending"] > 0), None
        )
        if failed_year is not None:
            failure_reason = (
                f"Accessible funds do not cover spending at age "
                f"{failed_year['age']}."
            )
    total_spendable = _money(sum(year["spendable"] for year in years))
    return {
        "strategy": strategy,
        "eligible": eligible,
        "failure_reason": failure_reason,
        "years": years,
        "total_spendable": total_spendable,
        "spendable_value": total_spendable,
    }


def _simulate_penalized_traditional(
    inputs: EarlyRetirementInputs,
) -> dict:
    balance = inputs.traditional_balance
    ordinary_rate = inputs.ordinary_tax_rate_percent / 100
    penalty_rate = inputs.ruleset.early_withdrawal_penalty_percent / 100
    growth_rate = inputs.annual_return_percent / 100
    years = []
    for age in range(inputs.retirement_age, inputs.end_age):
        opening = balance
        applied_penalty_rate = (
            penalty_rate
            if age < inputs.ruleset.unrestricted_access_age
            else 0
        )
        net_rate = 1 - ordinary_rate - applied_penalty_rate
        gross_needed = (
            inputs.annual_spending / net_rate if net_rate > 0 else 0
        )
        withdrawal = min(balance, gross_needed)
        balance = max(balance - withdrawal, 0) * (1 + growth_rate)
        years.append(
            _year_row(
                age=age,
                opening=opening,
                withdrawal=withdrawal,
                ordinary_tax=withdrawal * ordinary_rate,
                penalty=withdrawal * applied_penalty_rate,
                spending=inputs.annual_spending,
                closing=balance,
            )
        )
    result = _strategy_result("penalized_traditional", years)
    result["constraints"] = {
        "penalty_applies_before_age": inputs.ruleset.unrestricted_access_age,
        "penalty_percent": _money(
            inputs.ruleset.early_withdrawal_penalty_percent
        ),
    }
    return result


def _simulate_taxable_bridge(inputs: EarlyRetirementInputs) -> dict:
    balance = inputs.taxable_balance
    basis = inputs.taxable_basis
    gains_rate = inputs.capital_gains_tax_rate_percent / 100
    growth_rate = inputs.annual_return_percent / 100
    years = []
    for age in range(inputs.retirement_age, inputs.end_age):
        opening = balance
        gain_ratio = max(balance - basis, 0) / balance if balance > 0 else 0
        effective_tax_rate = gain_ratio * gains_rate
        gross_needed = (
            inputs.annual_spending / (1 - effective_tax_rate)
            if effective_tax_rate < 1
            else 0
        )
        withdrawal = min(balance, gross_needed)
        tax = withdrawal * effective_tax_rate
        basis_withdrawn = (
            min(basis, withdrawal * min(basis / balance, 1))
            if balance > 0
            else 0
        )
        balance = max(balance - withdrawal, 0) * (1 + growth_rate)
        basis = max(basis - basis_withdrawn, 0)
        years.append(
            _year_row(
                age=age,
                opening=opening,
                withdrawal=withdrawal,
                capital_gains_tax=tax,
                spending=inputs.annual_spending,
                closing=balance,
            )
        )
    result = _strategy_result("taxable_bridge", years)
    result["constraints"] = {
        "limited_to_taxable_balance": True,
        "capital_gains_tax_applies_to_gains_only": True,
    }
    return result


def _simulate_roth_basis(inputs: EarlyRetirementInputs) -> dict:
    balance = inputs.roth_contribution_basis
    growth_rate = inputs.annual_return_percent / 100
    years = []
    for age in range(inputs.retirement_age, inputs.end_age):
        opening = balance
        withdrawal = min(balance, inputs.annual_spending)
        balance = max(balance - withdrawal, 0) * (1 + growth_rate)
        years.append(
            _year_row(
                age=age,
                opening=opening,
                withdrawal=withdrawal,
                spending=inputs.annual_spending,
                closing=balance,
            )
        )
    result = _strategy_result("roth_contribution_basis", years)
    result["constraints"] = {
        "contribution_basis_only": True,
        "earnings_included": False,
    }
    return result


def _simulate_conversion_ladder(inputs: EarlyRetirementInputs) -> dict:
    traditional_balance = inputs.traditional_balance
    accessible_balance = 0.0
    ordinary_rate = inputs.ordinary_tax_rate_percent / 100
    growth_rate = inputs.annual_return_percent / 100
    releases: dict[int, float] = {}
    years = []
    for age in range(inputs.retirement_age, inputs.end_age):
        accessible_balance += releases.pop(age, 0)
        opening = accessible_balance
        conversion = min(
            traditional_balance, inputs.annual_conversion_amount
        )
        traditional_balance -= conversion
        releases[age + inputs.ruleset.conversion_wait_years] = conversion
        withdrawal = min(accessible_balance, inputs.annual_spending)
        accessible_balance = max(accessible_balance - withdrawal, 0) * (
            1 + growth_rate
        )
        traditional_balance *= 1 + growth_rate
        row = _year_row(
            age=age,
            opening=opening,
            withdrawal=withdrawal,
            spending=inputs.annual_spending,
            closing=accessible_balance,
        )
        row["conversion_amount"] = _money(conversion)
        row["conversion_tax"] = _money(conversion * ordinary_rate)
        row["release_age"] = age + inputs.ruleset.conversion_wait_years
        years.append(row)
    first_release_age = (
        inputs.retirement_age + inputs.ruleset.conversion_wait_years
    )
    failure_reason = None
    if inputs.annual_spending > 0 and first_release_age > inputs.retirement_age:
        failure_reason = (
            f"Conversions started at age {inputs.retirement_age} are unavailable "
            f"until age {first_release_age}; a separate bridge is required."
        )
    result = _strategy_result(
        "roth_conversion_ladder",
        years,
        failure_reason=failure_reason,
    )
    result["constraints"] = {
        "conversion_wait_years": inputs.ruleset.conversion_wait_years,
        "first_release_age": first_release_age,
    }
    result["total_conversion_tax"] = _money(
        sum(year["conversion_tax"] for year in years)
    )
    result["net_spendable_after_all_taxes"] = _money(
        result["total_spendable"] - result["total_conversion_tax"]
    )
    result["spendable_value"] = result["net_spendable_after_all_taxes"]
    return result


def _simulate_rule_of_55(inputs: EarlyRetirementInputs) -> dict:
    minimum_age = inputs.ruleset.rule_of_55_min_separation_age
    if inputs.separated_from_employer_age < minimum_age:
        return _strategy_result(
            "rule_of_55",
            [],
            eligible=False,
            failure_reason=(
                f"Employer separation age {inputs.separated_from_employer_age} "
                f"is below the ruleset minimum age {minimum_age}."
            ),
        )
    if inputs.separated_from_employer_age > inputs.retirement_age:
        return _strategy_result(
            "rule_of_55",
            [],
            eligible=False,
            failure_reason=(
                f"Employer separation at age {inputs.separated_from_employer_age} "
                f"occurs after retirement at age {inputs.retirement_age}."
            ),
        )
    balance = inputs.workplace_plan_balance
    tax_rate = inputs.ordinary_tax_rate_percent / 100
    growth_rate = inputs.annual_return_percent / 100
    years = []
    for age in range(inputs.retirement_age, inputs.end_age):
        opening = balance
        gross_needed = (
            inputs.annual_spending / (1 - tax_rate) if tax_rate < 1 else 0
        )
        withdrawal = min(balance, gross_needed)
        balance = max(balance - withdrawal, 0) * (1 + growth_rate)
        years.append(
            _year_row(
                age=age,
                opening=opening,
                withdrawal=withdrawal,
                ordinary_tax=withdrawal * tax_rate,
                spending=inputs.annual_spending,
                closing=balance,
            )
        )
    result = _strategy_result("rule_of_55", years)
    result["constraints"] = {
        "minimum_separation_age": minimum_age,
        "limited_to_separating_employer_plan": True,
    }
    return result


def _simulate_sepp(inputs: EarlyRetirementInputs) -> dict:
    if inputs.sepp_annual_distribution <= 0:
        return _strategy_result(
            "sepp_72t",
            [],
            eligible=False,
            failure_reason="A positive fixed annual SEPP distribution is required.",
        )
    tax_rate = inputs.ordinary_tax_rate_percent / 100
    growth_rate = inputs.annual_return_percent / 100
    must_continue_until_age = max(
        inputs.retirement_age + inputs.ruleset.sepp_minimum_years,
        inputs.ruleset.unrestricted_access_age,
    )
    required_commitment_payments = (
        math.ceil(must_continue_until_age) - inputs.retirement_age
    )
    commitment_balance = inputs.traditional_balance
    first_unsustainable_age = None
    for payment_number in range(required_commitment_payments):
        if commitment_balance + 0.005 < inputs.sepp_annual_distribution:
            first_unsustainable_age = inputs.retirement_age + payment_number
            break
        commitment_balance = (
            commitment_balance - inputs.sepp_annual_distribution
        ) * (1 + growth_rate)
    constraints = {
        "fixed_annual_distribution": _money(inputs.sepp_annual_distribution),
        "must_continue_until_age": must_continue_until_age,
        "minimum_years": inputs.ruleset.sepp_minimum_years,
        "required_commitment_payments": required_commitment_payments,
        "recapture_risk_if_schedule_modified": True,
        "first_unsustainable_age": first_unsustainable_age,
    }
    if first_unsustainable_age is not None:
        result = _strategy_result(
            "sepp_72t",
            [],
            eligible=False,
            failure_reason=(
                "The account cannot sustain the fixed annual distribution "
                f"through age {must_continue_until_age}; the payment at age "
                f"{first_unsustainable_age} would fail. Modifying the SEPP "
                "schedule before the commitment ends creates recapture-tax "
                "and interest risk."
            ),
        )
        result["constraints"] = constraints
        return result

    balance = inputs.traditional_balance
    years = []
    for age in range(inputs.retirement_age, inputs.end_age):
        opening = balance
        withdrawal = min(balance, inputs.sepp_annual_distribution)
        balance = max(balance - withdrawal, 0) * (1 + growth_rate)
        years.append(
            _year_row(
                age=age,
                opening=opening,
                withdrawal=withdrawal,
                ordinary_tax=withdrawal * tax_rate,
                spending=inputs.annual_spending,
                closing=balance,
            )
        )
    result = _strategy_result("sepp_72t", years)
    result["constraints"] = constraints
    return result


def compare_early_access_strategies(inputs: EarlyRetirementInputs) -> dict:
    completed = {
        "penalized_traditional": _simulate_penalized_traditional(inputs),
        "taxable_bridge": _simulate_taxable_bridge(inputs),
        "roth_contribution_basis": _simulate_roth_basis(inputs),
        "roth_conversion_ladder": _simulate_conversion_ladder(inputs),
        "rule_of_55": _simulate_rule_of_55(inputs),
        "sepp_72t": _simulate_sepp(inputs),
    }
    strategies = [
        completed.get(strategy) or _strategy_result(strategy, [])
        for strategy in STRATEGIES
    ]
    return {
        "model_version": "early-retirement-access-v1",
        "retirement_age": inputs.retirement_age,
        "ruleset": {
            "version": inputs.ruleset.version,
            "effective_date": inputs.ruleset.effective_date.isoformat(),
        },
        "comparison_metric": "Spendable withdrawals after modeled taxes and penalties, less conversion taxes.",
        "strategies": strategies,
        "ranking": [
            {
                "strategy": result["strategy"],
                "spendable_value": result["spendable_value"],
                "eligible": result["eligible"],
            }
            for result in sorted(
                strategies,
                key=lambda item: item["spendable_value"],
                reverse=True,
            )
        ],
    }


@router.post("/api/public/retirement/access/compare")
def early_retirement_comparison(inputs: EarlyRetirementInputs) -> dict:
    return compare_early_access_strategies(inputs)
