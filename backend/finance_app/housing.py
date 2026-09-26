from typing import Literal

from pydantic import BaseModel, Field, model_validator


class HousingAssumptionCase(BaseModel):
    name: Literal["conservative", "expected", "optimistic"]
    home_appreciation_percent: float = Field(ge=-20, le=30)
    rent_growth_percent: float = Field(ge=-20, le=30)
    investment_return_percent: float = Field(ge=-50, le=50)


class HousingInputs(BaseModel):
    home_price: float = Field(gt=0, le=100_000_000)
    down_payment: float = Field(ge=0)
    mortgage_rate_percent: float = Field(ge=0, le=30)
    mortgage_term_years: int = Field(ge=1, le=50)
    monthly_rent: float = Field(ge=0, le=1_000_000)
    years: int = Field(ge=1, le=30)
    home_appreciation_percent: float = Field(ge=-20, le=30)
    rent_growth_percent: float = Field(ge=-20, le=30)
    investment_return_percent: float = Field(ge=-50, le=50)
    investment_tax_drag_percent: float = Field(ge=0, le=20)
    property_tax_percent: float = Field(ge=0, le=10)
    home_insurance_annual: float = Field(ge=0, le=1_000_000)
    maintenance_percent: float = Field(ge=0, le=20)
    hoa_monthly: float = Field(ge=0, le=100_000)
    owner_utilities_monthly: float = Field(ge=0, le=100_000)
    renter_utilities_monthly: float = Field(ge=0, le=100_000)
    buy_closing_cost_percent: float = Field(ge=0, le=20)
    sell_cost_percent: float = Field(ge=0, le=20)
    loan_type: Literal["conventional", "fha", "va"] = "conventional"
    va_first_use: bool = True
    upfront_funding_fee_percent: float | None = Field(default=None, ge=0, le=10)
    finance_upfront_funding_fee: bool = True
    pmi_annual_percent: float | None = Field(default=None, ge=0, le=10)
    pmi_cancel_ltv_percent: float | None = Field(default=None, ge=0, le=100)
    mortgage_insurance_duration_months: int | None = Field(
        default=None, ge=0, le=600
    )
    points_percent: float = Field(default=0, ge=0, le=10)
    lender_fees: float = Field(default=0, ge=0, le=1_000_000)
    extra_principal_monthly: float = Field(default=0, ge=0, le=1_000_000)
    annual_standard_deduction: float = Field(default=0, ge=0, le=10_000_000)
    annual_other_itemized_deductions: float = Field(
        default=0, ge=0, le=10_000_000
    )
    annual_other_salt_deductions: float = Field(default=0, ge=0, le=10_000_000)
    salt_deduction_cap: float = Field(default=0, ge=0, le=10_000_000)
    marginal_tax_rate_percent: float = Field(default=0, ge=0, le=60)
    mortgage_interest_deduction_limit: float = Field(
        default=750_000, ge=0, le=100_000_000
    )
    include_case_comparison: bool = False
    assumption_cases: list[HousingAssumptionCase] | None = None

    @model_validator(mode="after")
    def validate_financing(self):
        if self.down_payment > self.home_price:
            raise ValueError("down payment cannot exceed home price")
        if self.years > self.mortgage_term_years:
            raise ValueError("core calculator horizon cannot exceed mortgage term")
        if self.assumption_cases is not None:
            names = [case.name for case in self.assumption_cases]
            if names != ["conservative", "expected", "optimistic"]:
                raise ValueError(
                    "assumption cases must be conservative, expected, and optimistic"
                )
        return self


def _money(value: float) -> float:
    return round(value + 0.0, 2)


def _mortgage_payment(principal: float, annual_rate_percent: float, months: int) -> float:
    if principal == 0:
        return 0
    monthly_rate = annual_rate_percent / 100 / 12
    if monthly_rate == 0:
        return principal / months
    growth = (1 + monthly_rate) ** months
    return principal * monthly_rate * growth / (growth - 1)


def _assumption(
    value: float,
    source: str,
    source_url: str,
    effective_date: str,
    confidence: Literal["low", "medium", "high"],
) -> dict[str, float | str | bool]:
    return {
        "value": _money(value),
        "source": source,
        "source_url": source_url,
        "effective_date": effective_date,
        "confidence": confidence,
        "editable": True,
    }


def _assumption_defaults(
    inputs: HousingInputs,
    funding_fee_percent: float,
    pmi_rate: float,
) -> dict[str, dict[str, float | str | bool]]:
    if inputs.loan_type == "fha":
        fee_source = (
            "HUD FHA Annual Management Report FY2025",
            "https://www.hud.gov/sites/dfiles/Housing/documents/FHAFY2025ANNUALMGMNTRPT.PDF",
            "2024-10-01",
            "high",
        )
    elif inputs.loan_type == "va":
        fee_source = (
            "VA funding fee and loan closing costs",
            "https://www.va.gov/housing-assistance/home-loans/funding-fee-and-closing-costs/",
            "2023-04-07",
            "high",
        )
    else:
        fee_source = (
            "Conventional loan model (no government funding fee)",
            "https://www.consumerfinance.gov/owning-a-home/loan-options/",
            "2025-01-01",
            "high",
        )
    return {
        "upfront_funding_fee_percent": _assumption(
            funding_fee_percent, *fee_source
        ),
        "pmi_annual_percent": _assumption(
            pmi_rate,
            "HUD FHA Annual Management Report FY2025"
            if inputs.loan_type == "fha"
            else "User-editable lender estimate",
            "https://www.hud.gov/sites/dfiles/Housing/documents/FHAFY2025ANNUALMGMNTRPT.PDF"
            if inputs.loan_type == "fha"
            else "https://www.consumerfinance.gov/ask-cfpb/what-is-private-mortgage-insurance-en-122/",
            "2024-10-01" if inputs.loan_type == "fha" else "2025-01-01",
            "high" if inputs.loan_type == "fha" else "low",
        ),
        "mortgage_interest_deduction_limit": _assumption(
            inputs.mortgage_interest_deduction_limit,
            "IRS Publication 936 (2025)",
            "https://www.irs.gov/publications/p936",
            "2025-01-01",
            "high",
        ),
        "property_tax_percent": _assumption(
            inputs.property_tax_percent,
            "Douglas County residential property-tax calculation",
            "https://www.douglas.co.us/assessor/residential-property-tax-calculations/",
            "2025-01-01",
            "low",
        ),
        "home_insurance_annual": _assumption(
            inputs.home_insurance_annual,
            "Colorado Division of Insurance consumer guidance; replace with a local quote",
            "https://doi.colorado.gov/insurance-products/homeowners/renters-insurance",
            "2025-01-01",
            "low",
        ),
        "buy_closing_cost_percent": _assumption(
            inputs.buy_closing_cost_percent,
            "CFPB home-loan toolkit; replace with lender Loan Estimates",
            "https://www.consumerfinance.gov/owning-a-home/",
            "2025-01-01",
            "low",
        ),
        "maintenance_percent": _assumption(
            inputs.maintenance_percent,
            "User-editable planning allowance; property-specific history preferred",
            "https://www.consumerfinance.gov/owning-a-home/",
            "2025-01-01",
            "low",
        ),
        "rent_growth_percent": _assumption(
            inputs.rent_growth_percent,
            "BLS rent of primary residence index; local lease evidence preferred",
            "https://www.bls.gov/cpi/factsheets/owners-equivalent-rent-and-rent.htm",
            "2025-01-01",
            "medium",
        ),
        "home_appreciation_percent": _assumption(
            inputs.home_appreciation_percent,
            "FHFA House Price Index; use the applicable local series",
            "https://www.fhfa.gov/data/hpi",
            "2025-01-01",
            "medium",
        ),
    }


def calculate_housing(inputs: HousingInputs) -> dict:
    enhanced_year_rows = any(
        (
            inputs.loan_type != "conventional",
            inputs.upfront_funding_fee_percent is not None,
            not inputs.finance_upfront_funding_fee,
            inputs.pmi_annual_percent is not None,
            inputs.pmi_cancel_ltv_percent is not None,
            inputs.mortgage_insurance_duration_months is not None,
            inputs.points_percent != 0,
            inputs.lender_fees != 0,
            inputs.extra_principal_monthly != 0,
            inputs.annual_standard_deduction != 0,
            inputs.annual_other_itemized_deductions != 0,
            inputs.annual_other_salt_deductions != 0,
            inputs.salt_deduction_cap != 0,
            inputs.marginal_tax_rate_percent != 0,
        )
    )
    base_loan_amount = inputs.home_price - inputs.down_payment
    down_payment_percent = inputs.down_payment / inputs.home_price * 100
    if inputs.loan_type == "fha":
        default_funding_fee = 1.75
    elif inputs.loan_type == "va":
        if down_payment_percent >= 10:
            default_funding_fee = 1.25
        elif down_payment_percent >= 5:
            default_funding_fee = 1.5
        else:
            default_funding_fee = 2.15 if inputs.va_first_use else 3.3
    else:
        default_funding_fee = 0.0
    funding_fee_percent = (
        default_funding_fee
        if inputs.upfront_funding_fee_percent is None
        else inputs.upfront_funding_fee_percent
    )
    default_pmi_rate = 0.55 if inputs.loan_type == "fha" else 0.0
    pmi_rate = (
        default_pmi_rate
        if inputs.pmi_annual_percent is None
        else inputs.pmi_annual_percent
    )
    default_cancel_ltv = 78.0 if inputs.loan_type == "conventional" else None
    cancel_ltv = (
        default_cancel_ltv
        if inputs.pmi_cancel_ltv_percent is None
        else inputs.pmi_cancel_ltv_percent
    )
    mortgage_months = inputs.mortgage_term_years * 12
    if inputs.mortgage_insurance_duration_months is not None:
        insurance_duration_months = inputs.mortgage_insurance_duration_months
    elif inputs.loan_type == "fha":
        insurance_duration_months = (
            132 if down_payment_percent >= 10 else mortgage_months
        )
    else:
        insurance_duration_months = None
    upfront_funding_fee = base_loan_amount * funding_fee_percent / 100
    financed_funding_fee = (
        upfront_funding_fee if inputs.finance_upfront_funding_fee else 0.0
    )
    loan_balance = base_loan_amount + financed_funding_fee
    initial_loan_balance = loan_balance
    points = base_loan_amount * inputs.points_percent / 100
    cash_funding_fee = upfront_funding_fee - financed_funding_fee
    origination_cash = points + inputs.lender_fees + cash_funding_fee
    mortgage_payment = _mortgage_payment(
        loan_balance,
        inputs.mortgage_rate_percent,
        mortgage_months,
    )
    mortgage_rate_monthly = inputs.mortgage_rate_percent / 100 / 12
    appreciation_monthly = (1 + inputs.home_appreciation_percent / 100) ** (1 / 12) - 1
    rent_growth_monthly = (1 + inputs.rent_growth_percent / 100) ** (1 / 12) - 1
    investment_return_net = (
        inputs.investment_return_percent - inputs.investment_tax_drag_percent
    )
    investment_return_monthly = (1 + investment_return_net / 100) ** (1 / 12) - 1

    home_value = inputs.home_price
    rent = inputs.monthly_rent
    buy_closing_cost = inputs.home_price * inputs.buy_closing_cost_percent / 100
    renter_investments = inputs.down_payment + buy_closing_cost + origination_cash
    renter_net_contributions = renter_investments
    renter_investment_growth = 0.0
    renter_housing_cash_paid = 0.0
    buyer_housing_cash_paid = inputs.down_payment + buy_closing_cost + origination_cash
    buyer_principal_contributed = inputs.down_payment
    buyer_unrecoverable = buy_closing_cost + origination_cash
    renter_unrecoverable = 0.0
    years: list[dict[str, float | int]] = []
    crossover_years: list[int] = []
    previous_advantage = 0.0
    buyer_mortgage_insurance = 0.0
    buyer_extra_principal = 0.0
    buyer_tax_benefit = 0.0
    buyer_deductible_housing_expense = 0.0
    buyer_incremental_itemized_deduction = 0.0
    annual_mortgage_interest = 0.0
    annual_property_tax = 0.0

    for month in range(1, inputs.years * 12 + 1):
        interest = loan_balance * mortgage_rate_monthly
        scheduled_payment = min(mortgage_payment, loan_balance + interest)
        principal = min(max(scheduled_payment - interest, 0), loan_balance)
        extra_principal = min(inputs.extra_principal_monthly, loan_balance - principal)
        current_ltv_percent = loan_balance / inputs.home_price * 100
        mortgage_insurance = (
            loan_balance * pmi_rate / 100 / 12
            if pmi_rate > 0
            and (cancel_ltv is None or current_ltv_percent > cancel_ltv)
            and (
                insurance_duration_months is None
                or month <= insurance_duration_months
            )
            else 0.0
        )
        loan_balance -= principal + extra_principal
        buyer_principal_contributed += principal + extra_principal
        buyer_extra_principal += extra_principal
        buyer_mortgage_insurance += mortgage_insurance

        property_tax = home_value * inputs.property_tax_percent / 100 / 12
        annual_mortgage_interest += interest
        annual_property_tax += property_tax
        insurance = inputs.home_insurance_annual / 12
        maintenance = home_value * inputs.maintenance_percent / 100 / 12
        owner_unrecoverable_month = (
            interest
            + property_tax
            + insurance
            + maintenance
            + inputs.hoa_monthly
            + inputs.owner_utilities_monthly
            + mortgage_insurance
        )
        owner_cash = scheduled_payment + extra_principal + mortgage_insurance + property_tax + insurance + maintenance + inputs.hoa_monthly + inputs.owner_utilities_monthly
        renter_cash = rent + inputs.renter_utilities_monthly

        buyer_unrecoverable += owner_unrecoverable_month
        renter_unrecoverable += renter_cash
        buyer_housing_cash_paid += owner_cash
        renter_housing_cash_paid += renter_cash
        investment_growth = renter_investments * investment_return_monthly
        investment_contribution = owner_cash - renter_cash
        renter_investment_growth += investment_growth
        renter_net_contributions += investment_contribution
        renter_investments += investment_growth + investment_contribution
        home_value *= 1 + appreciation_monthly
        rent *= 1 + rent_growth_monthly

        if month % 12 == 0:
            deductible_interest_ratio = min(
                1.0,
                inputs.mortgage_interest_deduction_limit / base_loan_amount,
            ) if base_loan_amount else 0.0
            deductible_interest = annual_mortgage_interest * deductible_interest_ratio
            salt_without_home = min(
                inputs.annual_other_salt_deductions,
                inputs.salt_deduction_cap,
            )
            salt_with_home = min(
                inputs.annual_other_salt_deductions + annual_property_tax,
                inputs.salt_deduction_cap,
            )
            baseline_itemized = (
                inputs.annual_other_itemized_deductions + salt_without_home
            )
            itemized_with_home = (
                inputs.annual_other_itemized_deductions
                + salt_with_home
                + deductible_interest
            )
            incremental_itemized = max(
                inputs.annual_standard_deduction,
                itemized_with_home,
            ) - max(inputs.annual_standard_deduction, baseline_itemized)
            annual_tax_benefit = (
                incremental_itemized * inputs.marginal_tax_rate_percent / 100
            )
            deductible_housing_expense = (
                deductible_interest + salt_with_home - salt_without_home
            )
            buyer_tax_benefit += annual_tax_benefit
            buyer_deductible_housing_expense += deductible_housing_expense
            buyer_incremental_itemized_deduction += incremental_itemized
            buyer_unrecoverable -= annual_tax_benefit
            renter_investments -= annual_tax_benefit
            renter_net_contributions -= annual_tax_benefit
            year = month // 12
            buyer_equity = home_value - loan_balance
            buyer_sale_cost = home_value * inputs.sell_cost_percent / 100
            buyer_net_wealth = home_value - buyer_sale_cost - loan_balance
            buyer_appreciation = home_value - inputs.home_price
            buyer_total_unrecoverable = buyer_unrecoverable + buyer_sale_cost
            advantage = buyer_net_wealth - renter_investments
            crossed = (advantage >= 0 > previous_advantage) or (
                advantage < 0 <= previous_advantage
            )
            if crossed or (year == 1 and advantage > 0 == previous_advantage):
                crossover_years.append(year)
            previous_advantage = advantage
            year_result = {
                    "year": year,
                    "buyer_equity": _money(buyer_equity),
                    "buyer_net_wealth": _money(buyer_net_wealth),
                    "buyer_housing_cash_paid": _money(buyer_housing_cash_paid),
                    "buyer_principal_contributed": _money(
                        buyer_principal_contributed
                    ),
                    "buyer_appreciation": _money(buyer_appreciation),
                    "buyer_sale_cost": _money(buyer_sale_cost),
                    "renter_investments": _money(renter_investments),
                    "renter_housing_cash_paid": _money(renter_housing_cash_paid),
                    "renter_net_contributions": _money(renter_net_contributions),
                    "renter_investment_growth": _money(renter_investment_growth),
                    "buyer_unrecoverable_cost": _money(
                        buyer_total_unrecoverable
                    ),
                    "renter_unrecoverable_cost": _money(renter_unrecoverable),
                    "buyer_advantage": _money(advantage),
                }
            if enhanced_year_rows:
                year_result.update(
                    {
                        "buyer_mortgage_insurance": _money(
                            buyer_mortgage_insurance
                        ),
                        "buyer_extra_principal": _money(buyer_extra_principal),
                        "loan_balance": _money(loan_balance),
                        "buyer_deductible_housing_expense": _money(
                            buyer_deductible_housing_expense
                        ),
                        "buyer_incremental_itemized_deduction": _money(
                            buyer_incremental_itemized_deduction
                        ),
                        "buyer_tax_benefit": _money(buyer_tax_benefit),
                    }
                )
            years.append(year_result)
            annual_mortgage_interest = 0.0
            annual_property_tax = 0.0

    result = {
        "currency": "USD",
        "monthly_mortgage_payment": _money(mortgage_payment),
        "initial_cash_allocation": {
            "shared_starting_cash": _money(
                inputs.down_payment + buy_closing_cost + origination_cash
            ),
            "buyer_down_payment_to_home": _money(inputs.down_payment),
            "buyer_purchase_costs": _money(buy_closing_cost + origination_cash),
            "renter_starting_investment": _money(
                inputs.down_payment + buy_closing_cost + origination_cash
            ),
        },
        "financing": {
            "loan_type": inputs.loan_type,
            "base_loan_amount": _money(base_loan_amount),
            "upfront_funding_fee": _money(upfront_funding_fee),
            "financed_funding_fee": _money(financed_funding_fee),
            "initial_loan_balance": _money(initial_loan_balance),
            "points": _money(points),
            "lender_fees": _money(inputs.lender_fees),
            "cash_to_close": _money(
                inputs.down_payment + buy_closing_cost + origination_cash
            ),
        },
        "assumption_defaults": _assumption_defaults(
            inputs,
            funding_fee_percent,
            default_pmi_rate
            if inputs.pmi_annual_percent is None
            else inputs.pmi_annual_percent,
        ),
        "years": years,
        "crossover_years": crossover_years,
    }
    if inputs.include_case_comparison:
        cases = inputs.assumption_cases or [
            HousingAssumptionCase(
                name="conservative",
                home_appreciation_percent=inputs.home_appreciation_percent - 1,
                rent_growth_percent=inputs.rent_growth_percent - 1,
                investment_return_percent=inputs.investment_return_percent - 2,
            ),
            HousingAssumptionCase(
                name="expected",
                home_appreciation_percent=inputs.home_appreciation_percent,
                rent_growth_percent=inputs.rent_growth_percent,
                investment_return_percent=inputs.investment_return_percent,
            ),
            HousingAssumptionCase(
                name="optimistic",
                home_appreciation_percent=inputs.home_appreciation_percent + 1,
                rent_growth_percent=inputs.rent_growth_percent + 1,
                investment_return_percent=inputs.investment_return_percent + 2,
            ),
        ]
        comparison = []
        for case in cases:
            case_values = case.model_dump(exclude={"name"})
            case_result = calculate_housing(
                inputs.model_copy(
                    update={
                        **case_values,
                        "include_case_comparison": False,
                        "assumption_cases": None,
                    }
                )
            )
            comparison.append(
                {
                    "name": case.name,
                    "assumptions": case_values,
                    "monthly_mortgage_payment": case_result[
                        "monthly_mortgage_payment"
                    ],
                    "years": case_result["years"],
                    "crossover_years": case_result["crossover_years"],
                }
            )
        result["case_comparison"] = comparison
    return result
