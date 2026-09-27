import pytest

from backend.finance_app.housing import HousingInputs, calculate_housing


def _inputs(**overrides) -> HousingInputs:
    values = {
        "home_price": 100_000,
        "down_payment": 20_000,
        "mortgage_rate_percent": 0,
        "mortgage_term_years": 30,
        "monthly_rent": 0,
        "years": 1,
        "home_appreciation_percent": 0,
        "rent_growth_percent": 0,
        "investment_return_percent": 0,
        "investment_tax_drag_percent": 0,
        "property_tax_percent": 0,
        "home_insurance_annual": 0,
        "maintenance_percent": 0,
        "hoa_monthly": 0,
        "owner_utilities_monthly": 0,
        "renter_utilities_monthly": 0,
        "buy_closing_cost_percent": 0,
        "sell_cost_percent": 0,
    }
    values.update(overrides)
    return HousingInputs(**values)


def test_conventional_financing_includes_points_fees_pmi_and_extra_principal():
    result = calculate_housing(
        _inputs(
            points_percent=1,
            lender_fees=750,
            pmi_annual_percent=1.2,
            pmi_cancel_ltv_percent=0,
            extra_principal_monthly=100,
        )
    )

    assert result["financing"] == {
        "loan_type": "conventional",
        "base_loan_amount": 80_000,
        "upfront_funding_fee": 0,
        "financed_funding_fee": 0,
        "initial_loan_balance": 80_000,
        "points": 800,
        "lender_fees": 750,
        "cash_to_close": 21_550,
    }
    assert result["monthly_mortgage_payment"] == 222.22
    assert result["years"][0]["buyer_extra_principal"] == 1_200
    assert result["years"][0]["buyer_mortgage_insurance"] == pytest.approx(
        938.73, abs=0.01
    )
    assert result["years"][0]["loan_balance"] == pytest.approx(76_133.33, abs=0.01)
    assert result["years"][0]["renter_investments"] == result["years"][0][
        "buyer_housing_cash_paid"
    ]

    paid_off = calculate_housing(_inputs(extra_principal_monthly=100_000))
    assert paid_off["years"][0]["loan_balance"] == 0
    assert paid_off["years"][0]["buyer_housing_cash_paid"] == 100_000


def test_down_payment_is_home_equity_for_buyer_and_invested_for_renter():
    result = calculate_housing(
        _inputs(
            down_payment=25_000,
            buy_closing_cost_percent=2,
            lender_fees=500,
        )
    )

    assert result["initial_cash_allocation"] == {
        "shared_starting_cash": 27_500,
        "buyer_down_payment_to_home": 25_000,
        "buyer_purchase_costs": 2_500,
        "renter_starting_investment": 27_500,
    }
    assert result["years"][0]["buyer_principal_contributed"] == 27_500
    assert result["years"][0]["renter_net_contributions"] == 30_000


def test_fha_and_va_apply_distinct_configurable_funding_and_insurance_costs():
    fha = calculate_housing(
        _inputs(loan_type="fha", down_payment=3_500, pmi_annual_percent=0.55)
    )
    va = calculate_housing(_inputs(loan_type="va", down_payment=0))
    va_five_percent_down = calculate_housing(
        _inputs(loan_type="va", down_payment=5_000)
    )
    va_repeat_use = calculate_housing(
        _inputs(loan_type="va", down_payment=0, va_first_use=False)
    )
    va_exempt = calculate_housing(
        _inputs(
            loan_type="va",
            down_payment=0,
            upfront_funding_fee_percent=0,
        )
    )

    assert fha["financing"]["base_loan_amount"] == 96_500
    assert fha["financing"]["upfront_funding_fee"] == 1_688.75
    assert fha["financing"]["initial_loan_balance"] == 98_188.75
    assert fha["years"][0]["buyer_mortgage_insurance"] == pytest.approx(
        531.79, abs=0.01
    )
    assert va["financing"]["upfront_funding_fee"] == 2_150
    assert va["financing"]["initial_loan_balance"] == 102_150
    assert va["years"][0]["buyer_mortgage_insurance"] == 0
    assert va_five_percent_down["financing"]["upfront_funding_fee"] == 1_425
    assert va_repeat_use["financing"]["upfront_funding_fee"] == 3_300
    assert va_exempt["financing"]["initial_loan_balance"] == 100_000


def test_tax_benefit_is_only_the_increment_above_the_standard_deduction():
    result = calculate_housing(
        _inputs(
            property_tax_percent=10,
            annual_standard_deduction=12_000,
            annual_other_itemized_deductions=8_000,
            annual_other_salt_deductions=0,
            salt_deduction_cap=20_000,
            marginal_tax_rate_percent=25,
        )
    )

    year = result["years"][0]
    assert year["buyer_deductible_housing_expense"] == 10_000
    assert year["buyer_incremental_itemized_deduction"] == 6_000
    assert year["buyer_tax_benefit"] == 1_500
    assert year["buyer_unrecoverable_cost"] == 8_500


def test_model_defaults_publish_editable_source_date_and_confidence():
    fha = calculate_housing(_inputs(loan_type="fha"))

    funding_fee = fha["assumption_defaults"]["upfront_funding_fee_percent"]
    assert funding_fee == {
        "value": 1.75,
        "source": "HUD FHA Annual Management Report FY2025",
        "source_url": "https://www.hud.gov/sites/dfiles/Housing/documents/FHAFY2025ANNUALMGMNTRPT.PDF",
        "effective_date": "2024-10-01",
        "confidence": "high",
        "editable": True,
    }
    assert fha["assumption_defaults"]["mortgage_interest_deduction_limit"] == {
        "value": 750_000,
        "source": "IRS Publication 936 (2025)",
        "source_url": "https://www.irs.gov/publications/p936",
        "effective_date": "2025-01-01",
        "confidence": "high",
        "editable": True,
    }
    local_tax = fha["assumption_defaults"]["property_tax_percent"]
    assert local_tax["source"] == "Douglas County residential property-tax calculation"
    assert local_tax["effective_date"] == "2025-01-01"
    assert local_tax["confidence"] == "low"
    assert local_tax["editable"] is True
    locally_variable = {
        "property_tax_percent",
        "home_insurance_annual",
        "buy_closing_cost_percent",
        "maintenance_percent",
        "rent_growth_percent",
        "general_inflation_percent",
        "home_appreciation_percent",
    }
    assert locally_variable <= fha["assumption_defaults"].keys()
    for key in locally_variable:
        assert set(fha["assumption_defaults"][key]) == {
            "value",
            "source",
            "source_url",
            "effective_date",
            "confidence",
            "editable",
        }


def test_three_editable_assumption_cases_cover_the_full_horizon():
    result = calculate_housing(
        _inputs(
            years=3,
            include_case_comparison=True,
            assumption_cases=[
                {
                    "name": "conservative",
                    "home_appreciation_percent": -2,
                    "rent_growth_percent": 1,
                    "investment_return_percent": 2,
                },
                {
                    "name": "expected",
                    "home_appreciation_percent": 3,
                    "rent_growth_percent": 3,
                    "investment_return_percent": 6,
                },
                {
                    "name": "optimistic",
                    "home_appreciation_percent": 6,
                    "rent_growth_percent": 5,
                    "investment_return_percent": 9,
                },
            ],
        )
    )

    cases = result["case_comparison"]
    assert [case["name"] for case in cases] == [
        "conservative",
        "expected",
        "optimistic",
    ]
    assert all(len(case["years"]) == 3 for case in cases)
    assert all(case["years"][-1]["year"] == 3 for case in cases)
    assert [case["assumptions"]["home_appreciation_percent"] for case in cases] == [
        -2,
        3,
        6,
    ]
    assert len({case["years"][-1]["buyer_net_wealth"] for case in cases}) == 3


def test_sensitivity_changes_one_assumption_at_a_time_and_ranks_the_swing():
    result = calculate_housing(
        _inputs(
            years=10,
            monthly_rent=2_000,
            mortgage_rate_percent=6,
            home_appreciation_percent=3,
            investment_return_percent=7,
        )
    )

    sensitivity = result["sensitivity"]
    assert [item["swing"] for item in sensitivity] == sorted(
        (item["swing"] for item in sensitivity), reverse=True
    )
    appreciation = next(
        item for item in sensitivity if item["field"] == "home_appreciation_percent"
    )
    assert appreciation["base"] == 3
    assert appreciation["lower"]["assumption"] == 2
    assert appreciation["higher"]["assumption"] == 4
    assert appreciation["higher"]["buyer_advantage"] > appreciation["lower"]["buyer_advantage"]
    assert all(item["unit"] == "percentage_points" for item in sensitivity)


def test_general_inflation_escalates_recurring_costs_and_exposes_story_layers():
    result = calculate_housing(
        _inputs(
            years=2,
            monthly_rent=1_000,
            rent_growth_percent=10,
            home_insurance_annual=1_200,
            hoa_monthly=100,
            owner_utilities_monthly=200,
            renter_utilities_monthly=150,
            general_inflation_percent=10,
        )
    )

    first, second = result["years"]
    assert second["renter_components"]["rent"] > first["renter_components"]["rent"] * 2
    assert second["buyer_components"]["insurance"] > first["buyer_components"]["insurance"] * 2
    assert second["buyer_components"]["hoa"] > first["buyer_components"]["hoa"] * 2
    assert second["buyer_components"]["utilities"] > first["buyer_components"]["utilities"] * 2
    assert second["renter_components"]["utilities"] > first["renter_components"]["utilities"] * 2
    assert result["cost_escalation"] == {
        "general_inflation_percent": 10,
        "home_insurance_growth_percent": 10,
        "hoa_growth_percent": 10,
        "owner_utilities_growth_percent": 10,
        "renter_utilities_growth_percent": 10,
    }

    buyer = second["buyer_components"]
    assert buyer["home_value"] - buyer["loan_balance"] - buyer["sale_cost"] == pytest.approx(
        second["buyer_net_wealth"], abs=0.02
    )
    assert buyer["down_payment"] + buyer["principal_paid"] == pytest.approx(
        second["buyer_principal_contributed"], abs=0.02
    )
    assert buyer["interest"] + buyer["property_tax"] + buyer["insurance"] + buyer["maintenance"] + buyer["hoa"] + buyer["utilities"] + buyer["mortgage_insurance"] + buyer["purchase_costs"] - buyer["tax_benefit"] + buyer["sale_cost"] == pytest.approx(
        second["buyer_unrecoverable_cost"], abs=0.08
    )


def test_cost_growth_overrides_are_independent_of_general_inflation():
    result = calculate_housing(
        _inputs(
            years=2,
            home_insurance_annual=1_200,
            hoa_monthly=100,
            owner_utilities_monthly=200,
            renter_utilities_monthly=150,
            general_inflation_percent=10,
            home_insurance_growth_percent=0,
            hoa_growth_percent=1,
            owner_utilities_growth_percent=2,
            renter_utilities_growth_percent=3,
        )
    )

    assert result["cost_escalation"] == {
        "general_inflation_percent": 10,
        "home_insurance_growth_percent": 0,
        "hoa_growth_percent": 1,
        "owner_utilities_growth_percent": 2,
        "renter_utilities_growth_percent": 3,
    }
    first, second = result["years"]
    assert second["buyer_components"]["insurance"] == pytest.approx(
        first["buyer_components"]["insurance"] * 2, abs=0.02
    )
    assert second["buyer_components"]["hoa"] > first["buyer_components"]["hoa"] * 2
    assert second["buyer_components"]["hoa"] < first["buyer_components"]["hoa"] * 2.02
