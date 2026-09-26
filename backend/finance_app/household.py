import calendar
from datetime import date
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator


router = APIRouter()

FilingStatus = Literal[
    "single",
    "head_of_household",
    "married_filing_jointly",
    "married_filing_separately",
]
EventType = Literal[
    "spouse_work",
    "homeschooling",
    "childcare",
    "child",
    "education",
    "moving",
    "income_change",
    "retirement",
]
MONEY_FIELDS = (
    "income",
    "personal_expenses",
    "tax_liability",
    "housing_cost",
    "retirement_contribution",
)


class Dependent(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    birth_date: date
    relationship: Literal["child", "other"] = "child"


class HouseholdProfile(BaseModel):
    marital_status: Literal["single", "married"]
    filing_status: FilingStatus
    dependents: list[Dependent] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_filing_status(self):
        married_filing = self.filing_status.startswith("married_")
        if self.marital_status == "single" and married_filing:
            raise ValueError("married filing status requires a married profile")
        if self.marital_status == "married" and not married_filing:
            raise ValueError("married profile requires a married filing status")
        dependent_ids = [dependent.id for dependent in self.dependents]
        if len(dependent_ids) != len(set(dependent_ids)):
            raise ValueError("dependent ids must be unique")
        return self


class AnnualHouseholdBaseline(BaseModel):
    primary_income: float = Field(ge=0, le=100_000_000)
    spouse_income: float = Field(default=0, ge=0, le=100_000_000)
    personal_expenses: float = Field(ge=0, le=100_000_000)
    tax_liability: float = Field(ge=0, le=100_000_000)
    housing_cost: float = Field(ge=0, le=100_000_000)
    retirement_contribution: float = Field(ge=0, le=10_000_000)


class FinancialEffects(BaseModel):
    income: float = Field(default=0, ge=-100_000_000, le=100_000_000)
    personal_expenses: float = Field(
        default=0, ge=-100_000_000, le=100_000_000
    )
    tax_liability: float = Field(
        default=0, ge=-100_000_000, le=100_000_000
    )
    housing_cost: float = Field(
        default=0, ge=-100_000_000, le=100_000_000
    )
    retirement_contribution: float = Field(
        default=0, ge=-10_000_000, le=10_000_000
    )
    dependent_count: int = Field(default=0, ge=-20, le=20)


class HouseholdEvent(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    event_type: EventType
    start_date: date
    end_date: date
    effects: FinancialEffects

    @model_validator(mode="after")
    def validate_dates(self):
        if self.end_date < self.start_date:
            raise ValueError("end date cannot precede start date")
        return self


class HouseholdPlan(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    start_date: date
    end_date: date
    profile: HouseholdProfile
    baseline: AnnualHouseholdBaseline
    events: list[HouseholdEvent] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_timeline(self):
        if self.end_date < self.start_date:
            raise ValueError("plan end date cannot precede start date")
        event_ids = [event.id for event in self.events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("event ids must be unique")
        for event in self.events:
            if event.start_date < self.start_date or event.end_date > self.end_date:
                raise ValueError("event dates must fall within the plan horizon")
        return self


class HouseholdComparison(BaseModel):
    baseline: HouseholdPlan
    variants: list[HouseholdPlan] = Field(min_length=1, max_length=20)


def _money(value: float) -> float:
    return round(value + 0.0, 2)


def _year_fraction(start: date, end: date, year: int) -> float:
    period_start = max(start, date(year, 1, 1))
    period_end = min(end, date(year, 12, 31))
    if period_end < period_start:
        return 0.0
    days = (period_end - period_start).days + 1
    return days / (366 if calendar.isleap(year) else 365)


def _effect_values(event: HouseholdEvent, year: int) -> dict[str, float]:
    fraction = _year_fraction(event.start_date, event.end_date, year)
    return {
        field: getattr(event.effects, field) * fraction for field in MONEY_FIELDS
    }


def project_household(plan: HouseholdPlan) -> dict:
    timeline: list[dict] = []
    baseline_annual = {
        "income": plan.baseline.primary_income + plan.baseline.spouse_income,
        "personal_expenses": plan.baseline.personal_expenses,
        "tax_liability": plan.baseline.tax_liability,
        "housing_cost": plan.baseline.housing_cost,
        "retirement_contribution": plan.baseline.retirement_contribution,
    }

    for year in range(plan.start_date.year, plan.end_date.year + 1):
        plan_fraction = _year_fraction(plan.start_date, plan.end_date, year)
        values = {
            field: baseline_annual[field] * plan_fraction for field in MONEY_FIELDS
        }
        dependent_count = len(plan.profile.dependents)
        active_events: list[str] = []
        for event in plan.events:
            effects = _effect_values(event, year)
            if _year_fraction(event.start_date, event.end_date, year) > 0:
                active_events.append(event.id)
                dependent_count += event.effects.dependent_count
            for field in MONEY_FIELDS:
                values[field] += effects[field]
        values = {field: _money(value) for field, value in values.items()}
        surplus = _money(
            values["income"]
            - values["personal_expenses"]
            - values["tax_liability"]
            - values["housing_cost"]
            - values["retirement_contribution"]
        )
        timeline.append(
            {
                "year": year,
                "period": {
                    "start_date": max(plan.start_date, date(year, 1, 1)).isoformat(),
                    "end_date": min(plan.end_date, date(year, 12, 31)).isoformat(),
                },
                "active_events": active_events,
                "cash_flow": {**values, "surplus_after_saving": surplus},
                "tax_inputs": {
                    "filing_status": plan.profile.filing_status,
                    "dependent_count": max(dependent_count, 0),
                    "gross_income": values["income"],
                    "estimated_tax_liability": values["tax_liability"],
                },
                "housing_inputs": {
                    "annual_housing_cost": values["housing_cost"],
                },
                "retirement_inputs": {
                    "annual_income": values["income"],
                    "annual_expenses": _money(
                        values["personal_expenses"] + values["housing_cost"]
                    ),
                    "annual_contribution": values["retirement_contribution"],
                },
            }
        )

    cumulative = {
        field: _money(sum(row["cash_flow"][field] for row in timeline))
        for field in MONEY_FIELDS
    }
    cumulative["surplus_after_saving"] = _money(
        sum(row["cash_flow"]["surplus_after_saving"] for row in timeline)
    )
    return {
        "currency": "USD",
        "name": plan.name,
        "profile": {
            "marital_status": plan.profile.marital_status,
            "filing_status": plan.profile.filing_status,
            "dependent_count": len(plan.profile.dependents),
        },
        "timeline": timeline,
        "cumulative": cumulative,
    }


def _event_cumulative(event: HouseholdEvent) -> dict[str, float]:
    values = {field: 0.0 for field in MONEY_FIELDS}
    for year in range(event.start_date.year, event.end_date.year + 1):
        year_values = _effect_values(event, year)
        for field in MONEY_FIELDS:
            values[field] += year_values[field]
    values["surplus_after_saving"] = (
        values["income"]
        - values["personal_expenses"]
        - values["tax_liability"]
        - values["housing_cost"]
        - values["retirement_contribution"]
    )
    return {field: _money(value) for field, value in values.items()}


def _baseline_cumulative(plan: HouseholdPlan) -> dict[str, float]:
    years = range(plan.start_date.year, plan.end_date.year + 1)
    factor = sum(
        _year_fraction(plan.start_date, plan.end_date, year) for year in years
    )
    values = {
        "income": (
            plan.baseline.primary_income + plan.baseline.spouse_income
        )
        * factor,
        "personal_expenses": plan.baseline.personal_expenses * factor,
        "tax_liability": plan.baseline.tax_liability * factor,
        "housing_cost": plan.baseline.housing_cost * factor,
        "retirement_contribution": plan.baseline.retirement_contribution
        * factor,
    }
    values["surplus_after_saving"] = (
        values["income"]
        - values["personal_expenses"]
        - values["tax_liability"]
        - values["housing_cost"]
        - values["retirement_contribution"]
    )
    return {field: _money(value) for field, value in values.items()}


def _event_drivers(
    baseline: HouseholdPlan, variant: HouseholdPlan
) -> list[dict]:
    baseline_events = {event.id: event for event in baseline.events}
    variant_events = {event.id: event for event in variant.events}
    baseline_before = _baseline_cumulative(baseline)
    baseline_after = _baseline_cumulative(variant)
    baseline_effects = {
        field: _money(baseline_after[field] - baseline_before[field])
        for field in (*MONEY_FIELDS, "surplus_after_saving")
    }
    drivers = []
    if any(value != 0 for value in baseline_effects.values()):
        drivers.append(
            {
                "event_id": "baseline-assumptions",
                "label": "Baseline household assumptions",
                "event_type": "baseline",
                "effects": baseline_effects,
            }
        )
    for event_id in sorted(set(baseline_events) | set(variant_events)):
        before = baseline_events.get(event_id)
        after = variant_events.get(event_id)
        before_values = (
            _event_cumulative(before)
            if before is not None
            else {field: 0.0 for field in (*MONEY_FIELDS, "surplus_after_saving")}
        )
        after_values = (
            _event_cumulative(after)
            if after is not None
            else {field: 0.0 for field in (*MONEY_FIELDS, "surplus_after_saving")}
        )
        effects = {
            field: _money(after_values[field] - before_values[field])
            for field in (*MONEY_FIELDS, "surplus_after_saving")
        }
        if any(value != 0 for value in effects.values()) or (
            before is not None and after is None
        ):
            source = after or before
            assert source is not None
            drivers.append(
                {
                    "event_id": event_id,
                    "label": source.label,
                    "event_type": source.event_type,
                    "effects": effects,
                }
            )
    return drivers


def compare_household_plans(
    baseline: HouseholdPlan, variants: list[HouseholdPlan]
) -> dict:
    baseline_result = project_household(baseline)
    comparisons = []
    for variant in variants:
        result = project_household(variant)
        outcome_changes = {
            field: _money(
                result["cumulative"][field] - baseline_result["cumulative"][field]
            )
            for field in (*MONEY_FIELDS, "surplus_after_saving")
        }
        drivers = _event_drivers(baseline, variant)
        attribution_reconciles = all(
            _money(sum(driver["effects"][field] for driver in drivers))
            == outcome_changes[field]
            for field in (*MONEY_FIELDS, "surplus_after_saving")
        )
        comparisons.append(
            {
                "name": variant.name,
                "profile": result["profile"],
                "timeline": result["timeline"],
                "outcome_changes": outcome_changes,
                "drivers": drivers,
                "attribution_reconciles": attribution_reconciles,
            }
        )
    return {
        "currency": "USD",
        "baseline": baseline_result,
        "variants": comparisons,
    }


@router.post("/api/public/household/project")
def household_projection(plan: HouseholdPlan) -> dict:
    return project_household(plan)


@router.post("/api/public/household/compare")
def household_comparison(payload: HouseholdComparison) -> dict:
    return compare_household_plans(payload.baseline, payload.variants)
