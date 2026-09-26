import random
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator


router = APIRouter()


class SocialSecurityAssumptions(BaseModel):
    claim_age: int = Field(ge=62, le=75)
    annual_benefit: float = Field(ge=0, le=1_000_000)
    cola_percent: float = Field(ge=-10, le=20)


class PensionIncome(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    start_age: int = Field(ge=18, le=120)
    annual_benefit: float = Field(ge=0, le=10_000_000)
    cola_percent: float = Field(default=0, ge=-10, le=20)
    end_age: int | None = Field(default=None, ge=19, le=120)

    @model_validator(mode="after")
    def validate_end_age(self):
        if self.end_age is not None and self.end_age <= self.start_age:
            raise ValueError("pension end age must be after its start age")
        return self


class HealthcareAssumptions(BaseModel):
    medicare_age: int = Field(ge=50, le=80)
    pre_medicare_annual_cost: float = Field(ge=0, le=10_000_000)
    medicare_annual_cost: float = Field(ge=0, le=10_000_000)
    healthcare_inflation_percent: float = Field(ge=-10, le=30)


class PolicyStressCase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    social_security_benefit_reduction_percent: float = Field(
        default=0, ge=0, le=100
    )
    pension_benefit_reduction_percent: float = Field(default=0, ge=0, le=100)
    healthcare_inflation_addition_percent: float = Field(
        default=0, ge=-20, le=30
    )
    annual_return_reduction_percent: float = Field(default=0, ge=0, le=30)
    spending_increase_percent: float = Field(default=0, ge=0, le=100)
    benefit_tax_rate_addition_percent: float = Field(default=0, ge=0, le=60)


class RetirementUncertaintyInputs(BaseModel):
    seed: int = Field(ge=0, le=2**32 - 1)
    simulations: int = Field(ge=50, le=2_000)
    retirement_age: int = Field(ge=18, le=100)
    end_age: int = Field(ge=19, le=120)
    starting_balance: float = Field(ge=0, le=10_000_000_000)
    annual_spending: float = Field(ge=0, le=100_000_000)
    general_inflation_percent: float = Field(ge=-10, le=30)
    return_mean_percent: float = Field(ge=-50, le=50)
    return_stddev_percent: float = Field(ge=0, le=100)
    benefit_tax_rate_percent: float = Field(ge=0, le=60)
    social_security: SocialSecurityAssumptions
    pensions: list[PensionIncome] = Field(default_factory=list, max_length=20)
    healthcare: HealthcareAssumptions
    policy_stress_cases: list[PolicyStressCase] = Field(
        default_factory=list, max_length=5
    )

    @model_validator(mode="after")
    def validate_horizon_and_cases(self):
        if self.end_age <= self.retirement_age:
            raise ValueError("end age must be after retirement age")
        if self.end_age - self.retirement_age > 80:
            raise ValueError("simulation horizon cannot exceed 80 years")
        names = [case.name for case in self.policy_stress_cases]
        if "current_law" in names:
            raise ValueError("current_law is reserved for the baseline case")
        if len(names) != len(set(names)):
            raise ValueError("policy stress case names must be unique")
        return self


def _money(value: float) -> float:
    return round(value + 0.0, 2)


def _percent(value: float) -> float:
    return round(value + 0.0, 1)


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _distribution(values: list[float], *, money: bool = True) -> dict[str, float]:
    formatter = _money if money else _percent
    return {
        f"p{percentile}": formatter(_percentile(values, percentile))
        for percentile in (10, 25, 50, 75, 90)
    }


def _policy_drivers(case: PolicyStressCase) -> list[dict]:
    labels = {
        "social_security_benefit_reduction_percent": "Social Security benefit reduction",
        "pension_benefit_reduction_percent": "Pension benefit reduction",
        "healthcare_inflation_addition_percent": "Healthcare inflation increase",
        "annual_return_reduction_percent": "Expected return reduction",
        "spending_increase_percent": "Spending increase",
        "benefit_tax_rate_addition_percent": "Benefit tax-rate increase",
    }
    drivers = []
    for field, label in labels.items():
        value = getattr(case, field)
        if value != 0:
            drivers.append(
                {
                    "assumption": field,
                    "label": label,
                    "baseline_percent": 0.0,
                    "stress_percent": _percent(value),
                }
            )
    return drivers


def _year_assumptions(
    inputs: RetirementUncertaintyInputs,
    case: PolicyStressCase,
    age: int,
) -> dict[str, float | str]:
    elapsed = age - inputs.retirement_age
    spending = (
        inputs.annual_spending
        * (1 + case.spending_increase_percent / 100)
        * (1 + inputs.general_inflation_percent / 100) ** elapsed
    )
    healthcare_inflation = (
        inputs.healthcare.healthcare_inflation_percent
        + case.healthcare_inflation_addition_percent
    ) / 100
    if age < inputs.healthcare.medicare_age:
        healthcare_phase = "pre_medicare"
        healthcare = inputs.healthcare.pre_medicare_annual_cost
    else:
        healthcare_phase = "medicare"
        healthcare = inputs.healthcare.medicare_annual_cost
    healthcare *= (1 + healthcare_inflation) ** elapsed

    social_security = 0.0
    if age >= inputs.social_security.claim_age:
        claimed_years = age - inputs.social_security.claim_age
        social_security = (
            inputs.social_security.annual_benefit
            * (
                1
                - case.social_security_benefit_reduction_percent / 100
            )
            * (1 + inputs.social_security.cola_percent / 100) ** claimed_years
        )
    pension = 0.0
    for income in inputs.pensions:
        if age >= income.start_age and (
            income.end_age is None or age < income.end_age
        ):
            pension += (
                income.annual_benefit
                * (1 - case.pension_benefit_reduction_percent / 100)
                * (1 + income.cola_percent / 100) ** (age - income.start_age)
            )
    benefit_tax_rate = min(
        inputs.benefit_tax_rate_percent
        + case.benefit_tax_rate_addition_percent,
        100,
    ) / 100
    benefit_tax = (social_security + pension) * benefit_tax_rate
    return {
        "spending": spending,
        "healthcare_phase": healthcare_phase,
        "healthcare_cost": healthcare,
        "social_security": social_security,
        "pension_income": pension,
        "benefit_tax": benefit_tax,
        "net_portfolio_need": max(
            spending + healthcare - social_security - pension + benefit_tax,
            0,
        ),
    }


def _simulate_case(
    inputs: RetirementUncertaintyInputs,
    case: PolicyStressCase,
    return_paths: list[list[float]],
) -> dict:
    ages = list(range(inputs.retirement_age, inputs.end_age))
    yearly_assumptions = [
        _year_assumptions(inputs, case, age) for age in ages
    ]
    path_results = []
    balances_by_year = [[] for _ in ages]
    successful_by_year = [0 for _ in ages]
    for returns in return_paths:
        balance = inputs.starting_balance
        successful = True
        first_failure_age = None
        for index, age in enumerate(ages):
            applied_return = max(
                returns[index] - case.annual_return_reduction_percent,
                -100,
            ) / 100
            balance = max(balance * (1 + applied_return), 0)
            need = float(yearly_assumptions[index]["net_portfolio_need"])
            withdrawal = min(balance, need)
            if withdrawal + 0.005 < need:
                successful = False
                if first_failure_age is None:
                    first_failure_age = age
            balance -= withdrawal
            balances_by_year[index].append(balance)
            if successful:
                successful_by_year[index] += 1
        early_years = min(5, len(returns))
        path_results.append(
            {
                "success": successful,
                "ending_balance": balance,
                "first_failure_age": first_failure_age,
                "early_return_average": sum(returns[:early_years]) / early_years,
            }
        )

    successes = sum(1 for result in path_results if result["success"])
    ending_balances = [result["ending_balance"] for result in path_results]
    failure_ages = [
        float(result["first_failure_age"])
        for result in path_results
        if result["first_failure_age"] is not None
    ]
    ordered_by_early_return = sorted(
        path_results, key=lambda result: result["early_return_average"]
    )
    quintile_size = max(1, len(path_results) // 5)
    bottom = ordered_by_early_return[:quintile_size]
    top = ordered_by_early_return[-quintile_size:]

    yearly = []
    for index, age in enumerate(ages):
        assumptions = yearly_assumptions[index]
        yearly.append(
            {
                "age": age,
                "healthcare_phase": assumptions["healthcare_phase"],
                "spending": _money(float(assumptions["spending"])),
                "healthcare_cost": _money(
                    float(assumptions["healthcare_cost"])
                ),
                "social_security": _money(
                    float(assumptions["social_security"])
                ),
                "pension_income": _money(
                    float(assumptions["pension_income"])
                ),
                "benefit_tax": _money(float(assumptions["benefit_tax"])),
                "net_portfolio_need": _money(
                    float(assumptions["net_portfolio_need"])
                ),
                "path_success_percent": _percent(
                    successful_by_year[index] / inputs.simulations * 100
                ),
                "balance_distribution": _distribution(balances_by_year[index]),
            }
        )

    return {
        "name": case.name,
        "assumptions": case.model_dump(),
        "success_probability_percent": _percent(
            successes / inputs.simulations * 100
        ),
        "ending_balance_distribution": _distribution(ending_balances),
        "first_failure_age_distribution": (
            _distribution(failure_ages, money=False) if failure_ages else None
        ),
        "yearly": yearly,
        "sequence_risk": {
            "early_window_years": min(5, len(ages)),
            "bottom_quintile_success_percent": _percent(
                sum(result["success"] for result in bottom) / len(bottom) * 100
            ),
            "top_quintile_success_percent": _percent(
                sum(result["success"] for result in top) / len(top) * 100
            ),
            "bottom_quintile_median_ending_balance": _money(
                _percentile(
                    [result["ending_balance"] for result in bottom], 50
                )
            ),
            "top_quintile_median_ending_balance": _money(
                _percentile(
                    [result["ending_balance"] for result in top], 50
                )
            ),
        },
        "drivers": _policy_drivers(case),
    }


def simulate_retirement_uncertainty(
    inputs: RetirementUncertaintyInputs,
) -> dict:
    random_source = random.Random(inputs.seed)
    horizon = inputs.end_age - inputs.retirement_age
    return_paths = [
        [
            random_source.gauss(
                inputs.return_mean_percent,
                inputs.return_stddev_percent,
            )
            for _ in range(horizon)
        ]
        for _ in range(inputs.simulations)
    ]
    baseline = PolicyStressCase(name="current_law")
    cases = [
        _simulate_case(inputs, case, return_paths)
        for case in [baseline, *inputs.policy_stress_cases]
    ]
    baseline_result = cases[0]
    for result in cases:
        result["change_from_current_law"] = {
            "success_probability_points": _percent(
                result["success_probability_percent"]
                - baseline_result["success_probability_percent"]
            ),
            "median_ending_balance": _money(
                result["ending_balance_distribution"]["p50"]
                - baseline_result["ending_balance_distribution"]["p50"]
            ),
        }
    return {
        "model_version": "retirement-uncertainty-v1",
        "currency": "USD",
        "seed": inputs.seed,
        "simulations": inputs.simulations,
        "horizon_years": horizon,
        "cases": cases,
        "interpretation": {
            "success": "Every modeled year of spending and healthcare costs was funded.",
            "disclaimer": "Educational scenario analysis under uncertain assumptions; not a guarantee or professional financial, tax, legal, or healthcare advice.",
        },
    }


@router.post("/api/public/retirement/uncertainty")
def retirement_uncertainty_projection(
    inputs: RetirementUncertaintyInputs,
) -> dict:
    return simulate_retirement_uncertainty(inputs)
