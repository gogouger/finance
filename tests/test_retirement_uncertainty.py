import pytest
from pydantic import ValidationError

from backend.finance_app.retirement_uncertainty import (
    RetirementUncertaintyInputs,
    simulate_retirement_uncertainty,
)


def _inputs(**overrides) -> RetirementUncertaintyInputs:
    values = {
        "seed": 8675309,
        "simulations": 200,
        "retirement_age": 55,
        "end_age": 75,
        "starting_balance": 650_000,
        "annual_spending": 45_000,
        "general_inflation_percent": 2.5,
        "return_mean_percent": 5,
        "return_stddev_percent": 12,
        "benefit_tax_rate_percent": 10,
        "social_security": {
            "claim_age": 67,
            "annual_benefit": 30_000,
            "cola_percent": 2,
        },
        "pensions": [],
        "healthcare": {
            "medicare_age": 65,
            "pre_medicare_annual_cost": 12_000,
            "medicare_annual_cost": 7_000,
            "healthcare_inflation_percent": 5,
        },
        "policy_stress_cases": [],
    }
    values.update(overrides)
    return RetirementUncertaintyInputs.model_validate(values)


def test_seeded_monte_carlo_is_reproducible_and_seed_sensitive():
    inputs = _inputs()

    first = simulate_retirement_uncertainty(inputs)
    second = simulate_retirement_uncertainty(inputs)
    different_seed = simulate_retirement_uncertainty(
        inputs.model_copy(update={"seed": 8675310})
    )

    assert first == second
    assert first["seed"] == 8675309
    assert first["cases"][0]["ending_balance_distribution"] != (
        different_seed["cases"][0]["ending_balance_distribution"]
    )


def test_simulation_count_is_bounded_for_the_vm():
    with pytest.raises(ValidationError):
        _inputs(simulations=2_001)


def test_results_report_distributions_success_probability_and_sequence_risk():
    result = simulate_retirement_uncertainty(_inputs(simulations=500))
    baseline = result["cases"][0]

    assert 0 <= baseline["success_probability_percent"] <= 100
    assert list(baseline["ending_balance_distribution"]) == [
        "p10",
        "p25",
        "p50",
        "p75",
        "p90",
    ]
    assert baseline["ending_balance_distribution"]["p10"] <= (
        baseline["ending_balance_distribution"]["p50"]
    ) <= baseline["ending_balance_distribution"]["p90"]
    assert len(baseline["yearly"]) == 20
    assert baseline["sequence_risk"][
        "bottom_quintile_median_ending_balance"
    ] < baseline["sequence_risk"]["top_quintile_median_ending_balance"]
    assert "not a guarantee" in result["interpretation"]["disclaimer"]


def test_social_security_pension_and_healthcare_phases_are_year_specific():
    result = simulate_retirement_uncertainty(
        _inputs(
            retirement_age=63,
            end_age=69,
            pensions=[
                {
                    "name": "Small pension",
                    "start_age": 66,
                    "annual_benefit": 6_000,
                    "cola_percent": 0,
                }
            ],
        )
    )
    years = {year["age"]: year for year in result["cases"][0]["yearly"]}

    assert years[64]["healthcare_phase"] == "pre_medicare"
    assert years[65]["healthcare_phase"] == "medicare"
    assert years[66]["pension_income"] == 6_000
    assert years[66]["social_security"] == 0
    assert years[67]["social_security"] == 30_000


def test_policy_stress_identifies_changed_drivers_and_uses_same_paths():
    result = simulate_retirement_uncertainty(
        _inputs(
            policy_stress_cases=[
                {
                    "name": "reduced_benefits_and_higher_healthcare",
                    "social_security_benefit_reduction_percent": 25,
                    "healthcare_inflation_addition_percent": 2,
                    "annual_return_reduction_percent": 1,
                }
            ]
        )
    )
    baseline, stress = result["cases"]

    assert baseline["name"] == "current_law"
    assert stress["success_probability_percent"] <= baseline["success_probability_percent"]
    assert {driver["assumption"] for driver in stress["drivers"]} == {
        "social_security_benefit_reduction_percent",
        "healthcare_inflation_addition_percent",
        "annual_return_reduction_percent",
    }
    assert stress["change_from_current_law"]["success_probability_points"] <= 0


def test_public_uncertainty_route_is_registered():
    from backend.finance_app.main import app

    paths = [getattr(route, "path", None) for route in app.routes]
    for included in app.routes:
        original_router = getattr(included, "original_router", None)
        if original_router is not None:
            paths.extend(
                getattr(route, "path", None) for route in original_router.routes
            )
    assert paths.count("/api/public/retirement/uncertainty") == 1
