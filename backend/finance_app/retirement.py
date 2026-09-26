from pydantic import BaseModel, Field, model_validator


class RetirementInputs(BaseModel):
    current_age: int = Field(ge=18, le=100)
    retirement_age: int = Field(ge=18, le=100)
    end_age: int = Field(ge=19, le=120)
    annual_income: float = Field(ge=0, le=100_000_000)
    annual_expenses: float = Field(ge=0, le=100_000_000)
    taxable_balance: float = Field(ge=0, le=1_000_000_000)
    taxable_basis: float = Field(ge=0, le=1_000_000_000)
    traditional_balance: float = Field(ge=0, le=1_000_000_000)
    roth_balance: float = Field(ge=0, le=1_000_000_000)
    hsa_balance: float = Field(ge=0, le=1_000_000_000)
    taxable_contribution: float = Field(ge=0, le=10_000_000)
    traditional_contribution: float = Field(ge=0, le=10_000_000)
    roth_contribution: float = Field(ge=0, le=10_000_000)
    hsa_contribution: float = Field(ge=0, le=10_000_000)
    annual_return_percent: float = Field(ge=-100, le=100)
    taxable_tax_drag_percent: float = Field(ge=0, le=20)
    inflation_percent: float = Field(ge=-10, le=30)
    ordinary_tax_rate_percent: float = Field(ge=0, le=60)
    capital_gains_tax_rate_percent: float = Field(ge=0, le=40)

    @model_validator(mode="after")
    def validate_timeline(self):
        if self.retirement_age < self.current_age:
            raise ValueError("retirement age cannot precede current age")
        if self.end_age <= self.current_age:
            raise ValueError("end age must be after current age")
        if self.end_age < self.retirement_age:
            raise ValueError("end age cannot precede retirement age")
        if self.annual_return_percent - self.taxable_tax_drag_percent < -100:
            raise ValueError("taxable return after tax drag cannot be below -100%")
        return self


def _money(value: float) -> float:
    return round(value + 0.0, 2)


def _withdraw_taxable(
    balance: float,
    basis: float,
    needed: float,
    capital_gains_rate: float,
) -> tuple[float, float, float, float]:
    if balance <= 0 or needed <= 0:
        return balance, basis, 0.0, needed
    gain_ratio = max(balance - basis, 0) / balance
    effective_tax_rate = gain_ratio * capital_gains_rate
    gross_needed = needed / (1 - effective_tax_rate)
    gross = min(balance, gross_needed)
    tax = gross * effective_tax_rate
    net = gross - tax
    basis_withdrawn = min(basis, gross * min(basis / balance, 1))
    return balance - gross, basis - basis_withdrawn, tax, max(needed - net, 0)


def _withdraw_taxed(
    balance: float,
    needed: float,
    tax_rate: float,
) -> tuple[float, float, float]:
    if balance <= 0 or needed <= 0:
        return balance, 0.0, needed
    gross = min(balance, needed / (1 - tax_rate))
    tax = gross * tax_rate
    return balance - gross, tax, max(needed - (gross - tax), 0)


def _withdraw_tax_free(balance: float, needed: float) -> tuple[float, float]:
    withdrawal = min(balance, needed)
    return balance - withdrawal, needed - withdrawal


def calculate_retirement(inputs: RetirementInputs) -> dict:
    taxable = inputs.taxable_balance
    taxable_basis = inputs.taxable_basis
    traditional = inputs.traditional_balance
    roth = inputs.roth_balance
    hsa = inputs.hsa_balance
    taxable_growth = 0.0
    tax_deferred_growth = 0.0
    tax_free_growth = 0.0
    years: list[dict[str, float | int | str]] = []

    ordinary_rate = inputs.ordinary_tax_rate_percent / 100
    gains_rate = inputs.capital_gains_tax_rate_percent / 100
    taxable_return = (
        inputs.annual_return_percent - inputs.taxable_tax_drag_percent
    ) / 100
    sheltered_return = inputs.annual_return_percent / 100

    for elapsed, age in enumerate(
        range(inputs.current_age, inputs.end_age), start=1
    ):
        working = age < inputs.retirement_age
        inflated_expenses = inputs.annual_expenses * (
            1 + inputs.inflation_percent / 100
        ) ** (elapsed - 1)
        working_cash_surplus = 0.0
        if working:
            taxable += inputs.taxable_contribution
            taxable_basis += inputs.taxable_contribution
            traditional += inputs.traditional_contribution
            roth += inputs.roth_contribution
            hsa += inputs.hsa_contribution
            working_cash_surplus = (
                inputs.annual_income
                - inflated_expenses
                - inputs.taxable_contribution
                - inputs.traditional_contribution
                - inputs.roth_contribution
                - inputs.hsa_contribution
            )

        taxable_year_growth = taxable * taxable_return
        traditional_year_growth = traditional * sheltered_return
        roth_year_growth = roth * sheltered_return
        hsa_year_growth = hsa * sheltered_return
        taxable += taxable_year_growth
        traditional += traditional_year_growth
        roth += roth_year_growth
        hsa += hsa_year_growth
        taxable_growth += taxable_year_growth
        tax_deferred_growth += traditional_year_growth
        tax_free_growth += roth_year_growth + hsa_year_growth

        spending = 0.0
        taxes = 0.0
        unmet_spending = 0.0
        if not working:
            spending = inflated_expenses
            unmet_spending = spending
            taxable, taxable_basis, tax, unmet_spending = _withdraw_taxable(
                taxable, taxable_basis, unmet_spending, gains_rate
            )
            taxes += tax
            traditional, tax, unmet_spending = _withdraw_taxed(
                traditional, unmet_spending, ordinary_rate
            )
            taxes += tax
            roth, unmet_spending = _withdraw_tax_free(roth, unmet_spending)
            hsa, unmet_spending = _withdraw_tax_free(hsa, unmet_spending)

        taxable_gain = max(taxable - taxable_basis, 0)
        total_balance = taxable + traditional + roth + hsa
        spendable_after_tax = (
            taxable
            - taxable_gain * gains_rate
            + traditional * (1 - ordinary_rate)
            + roth
            + hsa
        )
        years.append(
            {
                "age": age + 1,
                "phase": "working" if working else "retired",
                "taxable_balance": _money(taxable),
                "traditional_balance": _money(traditional),
                "roth_balance": _money(roth),
                "hsa_balance": _money(hsa),
                "taxable_growth": _money(taxable_growth),
                "tax_deferred_growth": _money(tax_deferred_growth),
                "tax_free_growth": _money(tax_free_growth),
                "working_cash_surplus": _money(working_cash_surplus),
                "spending": _money(spending),
                "taxes": _money(taxes),
                "unmet_spending": _money(unmet_spending),
                "total_balance": _money(total_balance),
                "spendable_after_tax": _money(spendable_after_tax),
            }
        )

    return {
        "currency": "USD",
        "definitions": {
            "taxable": "Return is reduced by tax drag; unrealized gains are valued after the capital-gains tax assumption.",
            "traditional": "Growth is tax-deferred; the balance is valued after the ordinary-income tax assumption.",
            "roth": "Contributions and growth are valued tax-free.",
            "hsa": "Contributions and growth are valued tax-free assuming qualified medical use.",
        },
        "years": years,
    }
