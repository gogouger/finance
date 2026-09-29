import math
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

from .early_retirement import (
    EarlyAccessRuleset,
    EarlyRetirementInputs,
    compare_early_access_strategies,
)
from .retirement_uncertainty import (
    HealthcareAssumptions,
    PolicyStressCase,
    RetirementUncertaintyInputs,
    SocialSecurityAssumptions,
    simulate_retirement_uncertainty,
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
    employee_contribution_for_full_match: float = Field(
        default=0, ge=0, le=1_000_000
    )
    traditional_contribution_limit: float = Field(gt=0, le=1_000_000)
    roth_contribution_limit: float = Field(gt=0, le=1_000_000)
    hsa_contribution_limit: float = Field(gt=0, le=1_000_000)
    qualified_hsa_spending_percent: float = Field(default=15, ge=0, le=100)
    annual_conversion_amount: float = Field(default=0, ge=0, le=100_000_000)
    sepp_annual_distribution: float = Field(default=0, ge=0, le=100_000_000)
    return_stddev_percent: float = Field(default=15, ge=0, le=100)
    uncertainty_simulations: int = Field(default=300, ge=50, le=2_000)
    uncertainty_seed: int = Field(default=42, ge=0, le=2**32 - 1)
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
    """Value a balance after its inherent tax, without pretending it is cashed out.

    An account balance at age 45 is not an early withdrawal.  Applying a 10%
    penalty to every traditional dollar at that point made the old comparison
    both pessimistic and internally inconsistent with the access-path model.
    Actual penalty and withdrawal tax are now applied, dollar by dollar, in
    ``_simulate_lifetime_plan`` below.
    """
    if key == "taxable":
        gain = max(balance - basis, 0)
        return balance - gain * inputs.capital_gains_tax_rate_percent / 100
    if key == "traditional":
        return balance * max(
            1 - inputs.retirement_ordinary_tax_rate_percent / 100,
            0,
        )
    if key == "roth":
        # This is a qualified-withdrawal terminal value.  Before 59½ only
        # contribution basis is automatically accessible; the lifetime model
        # keeps that distinction when it actually funds spending.
        return balance
    if key == "hsa":
        qualified = inputs.qualified_hsa_spending_percent / 100
        nonqualified = balance * (1 - qualified)
        return balance * qualified + nonqualified * max(
            1 - inputs.retirement_ordinary_tax_rate_percent / 100,
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


def _withdraw_taxable(
    balance: float, basis: float, needed: float, gains_rate: float
) -> tuple[float, float, float, float, float]:
    """Fund a net cash need from taxable assets and tax gains only."""
    if balance <= 0 or needed <= 0:
        return balance, basis, 0.0, 0.0, needed
    gain_ratio = max(balance - basis, 0) / balance
    effective_tax_rate = min(gain_ratio * gains_rate, 0.999999)
    gross = min(balance, needed / (1 - effective_tax_rate))
    tax = gross * effective_tax_rate
    net = gross - tax
    basis_withdrawn = min(basis, gross * min(basis / balance, 1))
    return (
        max(balance - gross, 0),
        max(basis - basis_withdrawn, 0),
        gross,
        tax,
        max(needed - net, 0),
    )


def _withdraw_traditional(
    balance: float,
    needed: float,
    ordinary_rate: float,
    penalty_rate: float,
) -> tuple[float, float, float, float, float]:
    """Fund a net need from traditional money, applying tax/penalty only used."""
    if balance <= 0 or needed <= 0:
        return balance, 0.0, 0.0, 0.0, needed
    net_rate = max(1 - ordinary_rate - penalty_rate, 0)
    if net_rate <= 0:
        return balance, 0.0, 0.0, 0.0, needed
    gross = min(balance, needed / net_rate)
    ordinary_tax = gross * ordinary_rate
    penalty = gross * penalty_rate
    net = gross - ordinary_tax - penalty
    return (
        max(balance - gross, 0),
        gross,
        ordinary_tax,
        penalty,
        max(needed - net, 0),
    )


def _withdraw_hsa(
    balance: float,
    needed: float,
    *,
    qualified_fraction: float,
    ordinary_rate: float,
    penalty_rate: float,
) -> tuple[float, float, float, float, float]:
    """Model a stated share of HSA spending as qualified medical spending."""
    if balance <= 0 or needed <= 0:
        return balance, 0.0, 0.0, 0.0, needed
    nonqualified = 1 - qualified_fraction
    net_rate = qualified_fraction + nonqualified * max(
        1 - ordinary_rate - penalty_rate, 0
    )
    if net_rate <= 0:
        return balance, 0.0, 0.0, 0.0, needed
    gross = min(balance, needed / net_rate)
    ordinary_tax = gross * nonqualified * ordinary_rate
    penalty = gross * nonqualified * penalty_rate
    net = gross - ordinary_tax - penalty
    return (
        max(balance - gross, 0),
        gross,
        ordinary_tax,
        penalty,
        max(needed - net, 0),
    )


def _project_existing_balance(
    balance: float, annual_return_percent: float, years: int
) -> float:
    return balance * (1 + annual_return_percent / 100) ** years


def _simulate_lifetime_plan(
    key: StrategyKey,
    strategy: dict,
    inputs: RetirementComparisonInputs,
) -> dict:
    """Run a yearly retirement cash-flow plan for one equal-cost strategy.

    This is intentionally a cash-flow model rather than a balance liquidation
    model: tax and the 10% additional tax arise only for actual withdrawals.
    It uses flexible taxable money, then Roth contribution/conversion basis,
    then traditional money before the access age; after that, traditional,
    Roth, and HSA money can cover remaining spending.  A configured conversion
    ladder is funded only when taxable assets can pay its conversion tax.
    """
    accumulation_years = inputs.retirement_age - inputs.current_age
    sheltered_growth = (1 + inputs.annual_return_percent / 100) ** accumulation_years
    taxable_growth = (
        1 + (inputs.annual_return_percent - inputs.taxable_tax_drag_percent) / 100
    ) ** accumulation_years
    taxable = _project_existing_balance(
        inputs.taxable_balance,
        inputs.annual_return_percent - inputs.taxable_tax_drag_percent,
        accumulation_years,
    )
    taxable_basis = inputs.taxable_basis
    # ``traditional_balance`` is the household's total tax-deferred balance;
    # ``workplace_plan_balance`` is the portion still in the current employer
    # plan.  The latter must not be added again (the previous UI defaults
    # intentionally supplied the same figure in both fields).
    traditional_total = max(
        inputs.traditional_balance, inputs.workplace_plan_balance
    ) * sheltered_growth
    workplace = min(
        inputs.workplace_plan_balance * sheltered_growth, traditional_total
    )
    traditional = traditional_total - workplace
    roth = inputs.roth_balance * sheltered_growth
    roth_regular_basis = inputs.roth_contribution_basis
    hsa = inputs.hsa_balance * sheltered_growth

    primary = strategy["at_retirement"]
    overflow = strategy["at_retirement"]["taxable_overflow"]
    overflow_basis = strategy["annual_taxable_overflow"] * accumulation_years
    if key == "taxable":
        taxable += primary["primary_balance"] + overflow
        taxable_basis += primary["accessible_basis"] + overflow_basis
    elif key == "traditional":
        # The compared traditional contribution is a workplace-plan
        # contribution, so Rule-of-55 eligibility can apply when the user
        # retires after the stated separation age.
        workplace += primary["primary_balance"]
        taxable += overflow
        taxable_basis += overflow_basis
    elif key == "roth":
        roth += primary["primary_balance"]
        roth_regular_basis += primary["accessible_basis"]
        taxable += overflow
        taxable_basis += overflow_basis
    else:
        hsa += primary["primary_balance"]
        taxable += overflow
        taxable_basis += overflow_basis

    ordinary_rate = inputs.retirement_ordinary_tax_rate_percent / 100
    gains_rate = inputs.capital_gains_tax_rate_percent / 100
    early_penalty = inputs.ruleset.early_withdrawal_penalty_percent / 100
    return_rate = inputs.annual_return_percent / 100
    taxable_return = (
        inputs.annual_return_percent - inputs.taxable_tax_drag_percent
    ) / 100
    qualified_hsa = inputs.qualified_hsa_spending_percent / 100
    conversion_releases: dict[int, float] = {}
    conversion_basis_available = 0.0
    years: list[dict] = []
    total_tax = 0.0
    total_penalty = 0.0
    total_spending = 0.0
    total_met = 0.0
    first_unmet_age: int | None = None
    rule_55_available = (
        inputs.retirement_age >= inputs.ruleset.rule_of_55_min_separation_age
    )
    sepp_end_age = math.ceil(
        max(
            inputs.retirement_age + inputs.ruleset.sepp_minimum_years,
            inputs.ruleset.unrestricted_access_age,
        )
    )
    sepp_payment_years = max(sepp_end_age - inputs.retirement_age, 0)
    sepp_enabled = inputs.sepp_annual_distribution > 0
    sepp_valid = True
    if sepp_enabled:
        commitment_balance = traditional + workplace
        for _ in range(sepp_payment_years):
            if commitment_balance + 0.005 < inputs.sepp_annual_distribution:
                sepp_valid = False
                break
            commitment_balance = max(
                commitment_balance - inputs.sepp_annual_distribution, 0
            ) * (1 + return_rate)

    for age in range(inputs.retirement_age, inputs.end_age):
        conversion_basis_available += conversion_releases.pop(age, 0.0)
        annual_spending = inputs.annual_retirement_spending * (
            1 + inputs.inflation_percent / 100
        ) ** (age - inputs.retirement_age)
        total_spending += annual_spending
        remaining = annual_spending
        sources: dict[str, float] = {}
        ordinary_tax = 0.0
        capital_gains_tax = 0.0
        penalty = 0.0
        conversion = 0.0
        conversion_tax = 0.0

        # A conversion is optional.  It is only executed if current taxable
        # assets can actually pay the tax, avoiding fictional unfunded taxes.
        conversion_source_available = traditional + (
            0.0 if rule_55_available else workplace
        )
        if (
            age < inputs.ruleset.unrestricted_access_age
            and inputs.annual_conversion_amount > 0
            and conversion_source_available > 0
            and not sepp_enabled
        ):
            requested = min(
                conversion_source_available, inputs.annual_conversion_amount
            )
            requested_tax = requested * ordinary_rate
            (
                taxable_after_tax,
                basis_after_tax,
                conversion_tax_gross,
                conversion_capital_gains_tax,
                conversion_tax_shortfall,
            ) = _withdraw_taxable(taxable, taxable_basis, requested_tax, gains_rate)
            if conversion_tax_shortfall <= 0.01:
                taxable, taxable_basis = taxable_after_tax, basis_after_tax
                from_traditional = min(traditional, requested)
                traditional -= from_traditional
                workplace -= requested - from_traditional
                roth += requested
                conversion = requested
                conversion_tax = requested_tax
                capital_gains_tax += conversion_capital_gains_tax
                conversion_releases[
                    age + inputs.ruleset.conversion_wait_years
                ] = requested
                sources["conversion_taxable_sale"] = conversion_tax_gross

        # A valid 72(t) schedule is a fixed stream, not an optional tap.  Its
        # cash can cover spending; any net excess is returned to taxable cash
        # with fresh basis.  Conversion-ladder modeling is deliberately not
        # combined with a SEPP schedule because changing either stream can
        # create recapture-tax and interest risk.
        if (
            sepp_enabled
            and sepp_valid
            and age < sepp_end_age
            and remaining > 0
        ):
            sepp_gross = inputs.sepp_annual_distribution
            from_traditional = min(traditional, sepp_gross)
            traditional -= from_traditional
            workplace -= sepp_gross - from_traditional
            sepp_tax = sepp_gross * ordinary_rate
            sepp_net = sepp_gross - sepp_tax
            used_for_spending = min(sepp_net, remaining)
            remaining -= used_for_spending
            surplus = sepp_net - used_for_spending
            if surplus > 0:
                taxable += surplus
                taxable_basis += surplus
            sources["sepp_72t"] = sepp_gross
            ordinary_tax += sepp_tax

        # Default withdrawal order keeps tax-advantaged growth invested while
        # using truly accessible funds first.  This is a transparent heuristic,
        # not a tax-return calculation or personalized advice.
        (
            taxable,
            taxable_basis,
            taxable_gross,
            taxable_tax,
            remaining,
        ) = _withdraw_taxable(taxable, taxable_basis, remaining, gains_rate)
        if taxable_gross:
            sources["taxable"] = taxable_gross
            capital_gains_tax += taxable_tax

        if age < inputs.ruleset.unrestricted_access_age:
            roth_basis_available = min(
                roth, roth_regular_basis + conversion_basis_available
            )
            roth_basis_withdrawal = min(roth_basis_available, remaining)
            if roth_basis_withdrawal:
                roth -= roth_basis_withdrawal
                regular_used = min(roth_regular_basis, roth_basis_withdrawal)
                roth_regular_basis -= regular_used
                conversion_basis_available -= max(
                    roth_basis_withdrawal - regular_used, 0
                )
                remaining -= roth_basis_withdrawal
                sources["roth_basis"] = roth_basis_withdrawal

            if rule_55_available and remaining > 0 and not sepp_enabled:
                workplace, gross, tax, _, remaining = _withdraw_traditional(
                    workplace, remaining, ordinary_rate, 0
                )
                if gross:
                    sources["rule_of_55_workplace"] = gross
                    ordinary_tax += tax

            if remaining > 0 and not sepp_enabled:
                traditional, gross, tax, draw_penalty, remaining = _withdraw_traditional(
                    traditional, remaining, ordinary_rate, early_penalty
                )
                if gross:
                    sources["traditional_early"] = gross
                    ordinary_tax += tax
                    penalty += draw_penalty
            # After separation, a workplace plan can generally be rolled to a
            # traditional IRA.  Unless the Rule of 55 is available (where
            # preserving the plan is preferable), model a necessary early
            # draw as a penalized traditional withdrawal rather than making
            # that balance disappear from the plan.
            if remaining > 0 and not rule_55_available and not sepp_enabled:
                workplace, gross, tax, draw_penalty, remaining = _withdraw_traditional(
                    workplace, remaining, ordinary_rate, early_penalty
                )
                if gross:
                    sources["rolled_workplace_early"] = gross
                    ordinary_tax += tax
                    penalty += draw_penalty
        else:
            traditional, gross, tax, _, remaining = _withdraw_traditional(
                traditional, remaining, ordinary_rate, 0
            )
            if gross:
                sources["traditional"] = gross
                ordinary_tax += tax
            if remaining > 0:
                workplace, gross, tax, _, remaining = _withdraw_traditional(
                    workplace, remaining, ordinary_rate, 0
                )
                if gross:
                    sources["workplace"] = gross
                    ordinary_tax += tax
            if remaining > 0:
                roth_withdrawal = min(roth, remaining)
                roth -= roth_withdrawal
                remaining -= roth_withdrawal
                if roth_withdrawal:
                    sources["roth_qualified"] = roth_withdrawal

        if remaining > 0:
            hsa_penalty = (
                inputs.ruleset.hsa_nonqualified_penalty_percent / 100
                if age < 65
                else 0
            )
            hsa, gross, tax, draw_penalty, remaining = _withdraw_hsa(
                hsa,
                remaining,
                qualified_fraction=qualified_hsa,
                ordinary_rate=ordinary_rate,
                penalty_rate=hsa_penalty,
            )
            if gross:
                sources["hsa"] = gross
                ordinary_tax += tax
                penalty += draw_penalty

        spending_met = annual_spending - remaining
        total_met += spending_met
        total_tax += ordinary_tax + capital_gains_tax + conversion_tax
        total_penalty += penalty
        if remaining > 0.01 and first_unmet_age is None:
            first_unmet_age = age

        # Growth occurs after this year's spending/withdrawal.  This matches
        # the simple annual timing used elsewhere in the calculator.
        taxable *= 1 + taxable_return
        traditional *= 1 + return_rate
        workplace *= 1 + return_rate
        roth *= 1 + return_rate
        hsa *= 1 + return_rate
        years.append(
            {
                "age": age,
                "spending": _money(annual_spending),
                "spending_met": _money(spending_met),
                "unmet_spending": _money(max(remaining, 0)),
                "ordinary_tax": _money(ordinary_tax),
                "capital_gains_tax": _money(capital_gains_tax),
                "conversion_tax": _money(conversion_tax),
                "penalty": _money(penalty),
                "conversion": _money(conversion),
                "sources": {name: _money(value) for name, value in sources.items()},
                "ending_total_balance": _money(
                    taxable + traditional + workplace + roth + hsa
                ),
            }
        )

    terminal_after_tax = (
        _after_tax_value(
            "taxable", balance=taxable, basis=taxable_basis, age=inputs.end_age, inputs=inputs
        )
        + _after_tax_value(
            "traditional", balance=traditional + workplace, basis=0, age=inputs.end_age, inputs=inputs
        )
        + _after_tax_value(
            "roth", balance=roth, basis=roth_regular_basis + conversion_basis_available, age=inputs.end_age, inputs=inputs
        )
        + _after_tax_value("hsa", balance=hsa, basis=0, age=inputs.end_age, inputs=inputs)
    )
    return {
        "withdrawal_order": [
            "Taxable brokerage",
            "Roth contribution and matured conversion basis before unrestricted access",
            "Eligible Rule-of-55 workplace money, then penalized traditional money before unrestricted access",
            "Traditional/workplace, Roth, then HSA after unrestricted access",
        ],
        "rule_of_55_available": rule_55_available,
        "conversion_ladder": {
            "annual_requested": _money(inputs.annual_conversion_amount),
            "wait_years": inputs.ruleset.conversion_wait_years,
            "conversion_tax_must_be_funded_from_taxable_assets": True,
            "disabled_while_sepp_is_active": sepp_enabled,
        },
        "sepp_72t": {
            "annual_distribution": _money(inputs.sepp_annual_distribution),
            "enabled": sepp_enabled,
            "commitment_end_age": sepp_end_age if sepp_enabled else None,
            "commitment_valid": sepp_valid,
            "conversion_ladder_disabled": sepp_enabled,
        },
        "years": years,
        "funds_plan_through_end_age": first_unmet_age is None,
        "first_unmet_age": first_unmet_age,
        "planned_spending": _money(total_spending),
        "spending_met": _money(total_met),
        "total_tax": _money(total_tax),
        "total_penalty": _money(total_penalty),
        "ending_after_tax_value": _money(terminal_after_tax),
    }


def _future_value_of_contributions(
    annual_contribution: float, rate: float, years: int
) -> float:
    if years <= 0:
        return 0.0
    if rate == 0:
        return annual_contribution * years
    return annual_contribution * ((1 + rate) ** years - 1) / rate


def _optimized_mix(
    inputs: RetirementComparisonInputs,
    *,
    bridge_required: float,
    existing_accessible: float,
) -> dict:
    current_rate = inputs.current_ordinary_tax_rate_percent / 100
    taxable_rate = (
        inputs.annual_return_percent - inputs.taxable_tax_drag_percent
    ) / 100
    sheltered_rate = inputs.annual_return_percent / 100
    years = inputs.retirement_age - inputs.current_age
    remaining_take_home = inputs.annual_take_home_sacrifice
    allocations = {
        "traditional": 0.0,
        "roth": 0.0,
        "hsa": 0.0,
        "taxable": 0.0,
    }

    match_contribution = min(
        inputs.employee_contribution_for_full_match,
        inputs.traditional_contribution_limit,
    )
    match_take_home_cost = match_contribution * (1 - current_rate)
    if match_take_home_cost <= remaining_take_home:
        allocations["traditional"] += match_contribution
        remaining_take_home -= match_take_home_cost
        protected_match = inputs.employer_match
    else:
        affordable = (
            remaining_take_home / (1 - current_rate)
            if current_rate < 1
            else remaining_take_home
        )
        allocations["traditional"] += affordable
        remaining_take_home = 0.0
        protected_match = (
            inputs.employer_match
            * affordable
            / match_contribution
            if match_contribution
            else inputs.employer_match
        )

    bridge_gap_before_new_saving = max(
        bridge_required - existing_accessible,
        0,
    )
    taxable_factor = (
        ((1 + taxable_rate) ** years - 1) / taxable_rate
        if taxable_rate != 0 and years > 0
        else float(years)
    )
    annual_bridge_need = (
        bridge_gap_before_new_saving / taxable_factor
        if taxable_factor > 0
        else bridge_gap_before_new_saving
    )
    allocations["taxable"] = min(remaining_take_home, annual_bridge_need)
    remaining_take_home -= allocations["taxable"]

    if remaining_take_home > 0:
        affordable_hsa = (
            remaining_take_home / (1 - current_rate)
            if current_rate < 1
            else remaining_take_home
        )
        allocations["hsa"] = min(
            affordable_hsa,
            inputs.hsa_contribution_limit,
        )
        remaining_take_home -= allocations["hsa"] * (1 - current_rate)

    if remaining_take_home > 0:
        prefer_traditional = (
            inputs.current_ordinary_tax_rate_percent
            > inputs.retirement_ordinary_tax_rate_percent
        )
        if prefer_traditional:
            room = max(
                inputs.traditional_contribution_limit
                - allocations["traditional"],
                0,
            )
            affordable = (
                remaining_take_home / (1 - current_rate)
                if current_rate < 1
                else remaining_take_home
            )
            added = min(room, affordable)
            allocations["traditional"] += added
            remaining_take_home -= added * (1 - current_rate)
        else:
            added = min(inputs.roth_contribution_limit, remaining_take_home)
            allocations["roth"] += added
            remaining_take_home -= added
    allocations["taxable"] += max(remaining_take_home, 0)

    taxable_balance = _future_value_of_contributions(
        allocations["taxable"], taxable_rate, years
    )
    taxable_after_tax = _after_tax_value(
        "taxable",
        balance=taxable_balance,
        basis=allocations["taxable"] * years,
        age=inputs.retirement_age,
        inputs=inputs,
    )
    traditional_balance = _future_value_of_contributions(
        allocations["traditional"] + protected_match,
        sheltered_rate,
        years,
    )
    traditional_after_tax = _after_tax_value(
        "traditional",
        balance=traditional_balance,
        basis=0,
        age=inputs.retirement_age,
        inputs=inputs,
    )
    roth_balance = _future_value_of_contributions(
        allocations["roth"], sheltered_rate, years
    )
    roth_after_tax = _after_tax_value(
        "roth",
        balance=roth_balance,
        basis=allocations["roth"] * years,
        age=inputs.retirement_age,
        inputs=inputs,
    )
    hsa_balance = _future_value_of_contributions(
        allocations["hsa"], sheltered_rate, years
    )
    hsa_after_tax = _after_tax_value(
        "hsa",
        balance=hsa_balance,
        basis=0,
        age=inputs.retirement_age,
        inputs=inputs,
    )
    projected_accessible = existing_accessible + taxable_after_tax
    return {
        "annual_take_home_cost": _money(
            inputs.annual_take_home_sacrifice
        ),
        "annual_allocations": {
            key: _money(value) for key, value in allocations.items()
        },
        "employer_match": _money(protected_match),
        "match_protected": protected_match + 0.005 >= inputs.employer_match,
        "bridge": {
            "required_annual_taxable_saving": _money(annual_bridge_need),
            "planned_annual_taxable_saving": _money(
                allocations["taxable"]
            ),
            "projected_accessible_at_retirement": _money(
                projected_accessible
            ),
            "projected_gap": _money(
                max(bridge_required - projected_accessible, 0)
            ),
        },
        "at_retirement": {
            "headline_balance": _money(
                taxable_balance
                + traditional_balance
                + roth_balance
                + hsa_balance
            ),
            "after_tax_value": _money(
                taxable_after_tax
                + traditional_after_tax
                + roth_after_tax
                + hsa_after_tax
            ),
            "by_account_after_tax": {
                "taxable": _money(taxable_after_tax),
                "traditional": _money(traditional_after_tax),
                "roth": _money(roth_after_tax),
                "hsa": _money(hsa_after_tax),
            },
        },
        "allocation_order": [
            "Protect the available employer match.",
            "Build the taxable bridge required before unrestricted access.",
            "Use eligible HSA space for modeled qualified medical spending.",
            "Choose traditional or Roth space from the displayed current-versus-retirement tax-rate assumption.",
            "Send remaining take-home saving to taxable brokerage.",
        ],
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
    existing_accessible = existing_taxable_after_tax + existing_roth_basis
    optimized_mix = _optimized_mix(
        inputs,
        bridge_required=bridge_required,
        existing_accessible=existing_accessible,
    )
    existing_traditional = max(
        inputs.traditional_balance,
        inputs.workplace_plan_balance,
    ) * sheltered_growth
    existing_roth = inputs.roth_balance * sheltered_growth
    existing_hsa = inputs.hsa_balance * sheltered_growth
    existing_after_tax = (
        existing_taxable_after_tax
        + _after_tax_value(
            "traditional",
            balance=existing_traditional,
            basis=0,
            age=inputs.retirement_age,
            inputs=inputs,
        )
        + _after_tax_value(
            "roth",
            balance=existing_roth,
            basis=inputs.roth_contribution_basis,
            age=inputs.retirement_age,
            inputs=inputs,
        )
        + _after_tax_value(
            "hsa",
            balance=existing_hsa,
            basis=0,
            age=inputs.retirement_age,
            inputs=inputs,
        )
    )
    uncertainty_output = simulate_retirement_uncertainty(
        RetirementUncertaintyInputs(
            seed=inputs.uncertainty_seed,
            simulations=inputs.uncertainty_simulations,
            retirement_age=inputs.retirement_age,
            end_age=inputs.end_age,
            starting_balance=(
                existing_after_tax
                + optimized_mix["at_retirement"]["after_tax_value"]
            ),
            annual_spending=retirement_spending,
            general_inflation_percent=inputs.inflation_percent,
            return_mean_percent=inputs.annual_return_percent,
            return_stddev_percent=inputs.return_stddev_percent,
            benefit_tax_rate_percent=0,
            social_security=SocialSecurityAssumptions(
                claim_age=67,
                annual_benefit=0,
                cola_percent=inputs.inflation_percent,
            ),
            pensions=[],
            healthcare=HealthcareAssumptions(
                medicare_age=65,
                pre_medicare_annual_cost=0,
                medicare_annual_cost=0,
                healthcare_inflation_percent=inputs.inflation_percent,
            ),
            policy_stress_cases=[
                PolicyStressCase(
                    name="lower_returns_and_higher_spending",
                    annual_return_reduction_percent=2,
                    spending_increase_percent=10,
                )
            ],
        )
    )
    uncertainty_baseline = uncertainty_output["cases"][0]
    uncertainty_stress = uncertainty_output["cases"][1]
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
    for strategy in strategies:
        strategy["lifetime_plan"] = _simulate_lifetime_plan(
            strategy["key"], strategy, inputs
        )
    ranked = sorted(
        strategies,
        key=lambda item: (
            item["lifetime_plan"]["funds_plan_through_end_age"],
            item["lifetime_plan"]["spending_met"],
            item["lifetime_plan"]["ending_after_tax_value"],
        ),
        reverse=True,
    )
    return {
        "model_version": "retirement-comparison-v2-lifetime-cash-flow",
        "currency": "USD",
        "retirement_age": inputs.retirement_age,
        "comparison_basis": {
            "annual_take_home_sacrifice": _money(
                inputs.annual_take_home_sacrifice
            ),
            "accumulation_years": accumulation_years,
            "ranking_metric": "First, whether the yearly plan funds spending through the selected end age; then lifetime spending funded and ending after-tax value. Taxes and penalties apply only to actual withdrawals.",
        },
        "strategies": strategies,
        "optimized_mix": optimized_mix,
        "uncertainty": {
            "model_version": uncertainty_output["model_version"],
            "seed": uncertainty_output["seed"],
            "simulations": uncertainty_output["simulations"],
            "success_probability_percent": uncertainty_baseline[
                "success_probability_percent"
            ],
            "ending_balance_distribution": uncertainty_baseline[
                "ending_balance_distribution"
            ],
            "first_failure_age_distribution": uncertainty_baseline[
                "first_failure_age_distribution"
            ],
            "sequence_risk": uncertainty_baseline["sequence_risk"],
            "yearly": [
                {
                    "age": row["age"],
                    "p10": row["balance_distribution"]["p10"],
                    "p50": row["balance_distribution"]["p50"],
                    "p90": row["balance_distribution"]["p90"],
                    "path_success_percent": row["path_success_percent"],
                }
                for row in uncertainty_baseline["yearly"]
            ],
            "stress_case": {
                "name": uncertainty_stress["name"],
                "success_probability_percent": uncertainty_stress[
                    "success_probability_percent"
                ],
                "change_from_baseline_points": uncertainty_stress[
                    "change_from_current_law"
                ]["success_probability_points"],
            },
            "assumptions": {
                "starting_wealth_basis": "estimated after-tax spendable value at retirement",
                "social_security_included": False,
                "pension_income_included": False,
                "healthcare_costs_included": False,
                "return_distribution": "independent annual normal returns",
            },
            "exclusions": [
                "Social Security and pension income are excluded until entered as explicit assumptions.",
                "Healthcare costs are excluded from this comparison and must be planned separately.",
                "The probability is a planning range, not a forecast or guarantee.",
            ],
            "disclaimer": uncertainty_output["interpretation"][
                "disclaimer"
            ],
        },
        "ranking": [
            {
                "key": item["key"],
                "label": item["label"],
                "after_tax_value": item["lifetime_plan"]["ending_after_tax_value"],
                "funds_plan_through_end_age": item["lifetime_plan"][
                    "funds_plan_through_end_age"
                ],
                "first_unmet_age": item["lifetime_plan"]["first_unmet_age"],
                "spending_met": item["lifetime_plan"]["spending_met"],
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
            "The comparison follows actual yearly spending through the selected end age instead of liquidating every account at retirement.",
            "A traditional-account penalty is charged only on traditional dollars actually withdrawn before the displayed unrestricted-access age.",
            "The lifetime plan uses taxable assets, Roth contribution/conversion basis, qualified workplace access where eligible, and then other account balances in a visible order.",
        ],
        "disclaimer": "Educational planning output, not professional financial, tax, or legal advice. Account eligibility and future law can change.",
    }


@router.post("/api/public/retirement/compare")
def retirement_comparison(inputs: RetirementComparisonInputs) -> dict:
    return compare_retirement_choices(inputs)
