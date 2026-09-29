import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from base64 import urlsafe_b64encode
from pathlib import Path

import pytest


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture
def running_service(tmp_path: Path):
    port = _unused_port()
    environment = os.environ.copy()
    environment.update(
        {
            "FINANCE_DATA_DIR": str(tmp_path),
            "FINANCE_ENCRYPTION_KEY": urlsafe_b64encode(b"0" * 32).decode(),
            "PLAID_MODE": "fake",
            "FINANCE_INTERNAL_KEY": "test-internal-key",
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.finance_app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                pytest.fail(f"service exited during startup\n{stdout}\n{stderr}")
            try:
                with urllib.request.urlopen(f"{base_url}/health", timeout=0.2):
                    break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.05)
        else:
            pytest.fail("service did not become healthy")
        yield base_url
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def _inputs(**overrides) -> dict:
    values = {
        "current_age": 26,
        "retirement_age": 45,
        "end_age": 75,
        "annual_take_home_sacrifice": 20_000,
        "annual_retirement_spending": 50_000,
        "taxable_balance": 25_000,
        "taxable_basis": 22_000,
        "traditional_balance": 60_000,
        "workplace_plan_balance": 60_000,
        "roth_balance": 20_000,
        "roth_contribution_basis": 15_000,
        "hsa_balance": 5_000,
        "annual_return_percent": 7,
        "taxable_tax_drag_percent": 0.75,
        "inflation_percent": 2.5,
        "current_ordinary_tax_rate_percent": 22,
        "retirement_ordinary_tax_rate_percent": 12,
        "capital_gains_tax_rate_percent": 15,
        "employer_match": 3_000,
        "employee_contribution_for_full_match": 6_000,
        "traditional_contribution_limit": 24_500,
        "roth_contribution_limit": 24_500,
        "hsa_contribution_limit": 8_750,
        "qualified_hsa_spending_percent": 15,
        "annual_conversion_amount": 30_000,
        "sepp_annual_distribution": 25_000,
        "return_stddev_percent": 12,
        "uncertainty_simulations": 100,
        "uncertainty_seed": 42,
        "ruleset": {
            "version": "illustrative-us-2026-v1",
            "effective_date": "2026-01-01",
            "unrestricted_access_age": 59.5,
            "early_withdrawal_penalty_percent": 10,
            "conversion_wait_years": 5,
            "rule_of_55_min_separation_age": 55,
            "sepp_minimum_years": 5,
        },
    }
    values.update(overrides)
    return values


def _compare(base_url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{base_url}/api/public/retirement/compare",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        assert response.status == 200
        return json.load(response)


def test_comparison_holds_take_home_sacrifice_constant(running_service: str):
    result = _compare(running_service, _inputs())
    strategies = {item["key"]: item for item in result["strategies"]}

    assert result["model_version"] == "retirement-comparison-v2-lifetime-cash-flow"
    assert result["comparison_basis"]["annual_take_home_sacrifice"] == 20_000
    assert strategies["taxable"]["annual_take_home_cost"] == 20_000
    assert strategies["roth"]["annual_take_home_cost"] == 20_000
    assert strategies["traditional"]["annual_take_home_cost"] == 20_000
    assert strategies["traditional"]["annual_primary_contribution"] > 20_000
    assert strategies["traditional"]["employer_match"] == 3_000
    assert all(item["years"] for item in strategies.values())
    assert result["optimized_mix"]["annual_take_home_cost"] == 20_000
    assert result["optimized_mix"]["employer_match"] == 3_000
    assert result["optimized_mix"]["match_protected"] is True
    assert sum(
        result["optimized_mix"]["annual_allocations"].values()
    ) > 20_000
    assert result["uncertainty"]["simulations"] == 100
    assert 0 <= result["uncertainty"]["success_probability_percent"] <= 100
    assert result["uncertainty"]["ending_balance_distribution"]["p10"] <= (
        result["uncertainty"]["ending_balance_distribution"]["p50"]
    ) <= result["uncertainty"]["ending_balance_distribution"]["p90"]
    assert result["uncertainty"]["assumptions"] == {
        "starting_wealth_basis": "estimated after-tax spendable value at retirement",
        "social_security_included": False,
        "pension_income_included": False,
        "healthcare_costs_included": False,
        "return_distribution": "independent annual normal returns",
    }
    assert len(result["uncertainty"]["exclusions"]) == 3
    for strategy in strategies.values():
        lifetime = strategy["lifetime_plan"]
        assert lifetime["planned_spending"] >= lifetime["spending_met"]
        assert lifetime["total_tax"] >= 0
        assert lifetime["total_penalty"] >= 0
        assert lifetime["withdrawal_order"]


def test_age_45_result_explains_bridge_and_access_constraints(
    running_service: str,
):
    result = _compare(running_service, _inputs(retirement_age=45))

    assert result["retirement_age"] == 45
    assert result["bridge"]["years"] == 15
    assert result["bridge"]["required_spending"] > 0
    assert result["bridge"]["accessible_at_retirement"] > 0
    assert result["early_access"]["ruleset"]["version"] == (
        "illustrative-us-2026-v1"
    )
    access = {
        item["strategy"]: item
        for item in result["early_access"]["strategies"]
    }
    assert access["rule_of_55"]["eligible"] is False
    assert access["roth_conversion_ladder"]["constraints"][
        "first_release_age"
    ] == 50
    assert result["drivers"]
    assert "educational" in result["disclaimer"].lower()


def test_tax_rate_changes_the_traditional_comparison(running_service: str):
    lower_rate = _compare(
        running_service,
        _inputs(current_ordinary_tax_rate_percent=12),
    )
    higher_rate = _compare(
        running_service,
        _inputs(current_ordinary_tax_rate_percent=32),
    )
    lower = {item["key"]: item for item in lower_rate["strategies"]}
    higher = {item["key"]: item for item in higher_rate["strategies"]}

    assert higher["traditional"]["annual_primary_contribution"] > lower[
        "traditional"
    ]["annual_primary_contribution"]
    assert higher["traditional"]["at_retirement"]["headline_balance"] > lower[
        "traditional"
    ]["at_retirement"]["headline_balance"]


def test_traditional_penalty_applies_only_to_actual_early_withdrawals(
    running_service: str,
):
    result = _compare(
        running_service,
        _inputs(
            retirement_age=45,
            end_age=46,
            taxable_balance=1_000_000,
            taxable_basis=1_000_000,
            annual_retirement_spending=10_000,
            traditional_balance=100_000,
            workplace_plan_balance=0,
            annual_take_home_sacrifice=0,
        ),
    )
    traditional = next(
        item for item in result["strategies"] if item["key"] == "traditional"
    )
    # Taxable assets cover the only retirement year.  A traditional balance is
    # not a withdrawal merely because retirement starts before 59½.
    assert traditional["lifetime_plan"]["total_penalty"] == 0
    assert traditional["lifetime_plan"]["funds_plan_through_end_age"] is True
    assert traditional["at_retirement"]["after_tax_value"] == round(
        traditional["at_retirement"]["headline_balance"] * 0.88,
        2,
    )


def test_lifetime_plan_models_a_valid_sepp_without_an_early_penalty(
    running_service: str,
):
    result = _compare(
        running_service,
        _inputs(
            retirement_age=45,
            end_age=47,
            annual_take_home_sacrifice=0,
            taxable_balance=0,
            taxable_basis=0,
            traditional_balance=1_000_000,
            workplace_plan_balance=0,
            roth_balance=0,
            roth_contribution_basis=0,
            hsa_balance=0,
            annual_retirement_spending=20_000,
            annual_conversion_amount=30_000,
            sepp_annual_distribution=25_000,
            annual_return_percent=0,
        ),
    )
    traditional = next(
        item for item in result["strategies"] if item["key"] == "traditional"
    )["lifetime_plan"]

    assert traditional["sepp_72t"]["enabled"] is True
    assert traditional["sepp_72t"]["commitment_valid"] is True
    assert traditional["conversion_ladder"]["disabled_while_sepp_is_active"] is True
    assert traditional["total_penalty"] == 0
    assert traditional["years"][0]["sources"]["sepp_72t"] == 25_000
