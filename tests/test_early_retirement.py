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
        "retirement_age": 45,
        "end_age": 61,
        "annual_spending": 20_000,
        "taxable_balance": 300_000,
        "taxable_basis": 240_000,
        "traditional_balance": 500_000,
        "workplace_plan_balance": 300_000,
        "roth_contribution_basis": 120_000,
        "annual_conversion_amount": 30_000,
        "separated_from_employer_age": 45,
        "sepp_annual_distribution": 25_000,
        "ordinary_tax_rate_percent": 20,
        "capital_gains_tax_rate_percent": 15,
        "annual_return_percent": 0,
        "ruleset": {
            "version": "test-early-access-2026-v1",
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
        f"{base_url}/api/public/retirement/access/compare",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        assert response.status == 200
        return json.load(response)


def _by_strategy(result: dict) -> dict[str, dict]:
    return {item["strategy"]: item for item in result["strategies"]}


def test_age_45_comparison_includes_every_strategy_and_pins_the_ruleset(
    running_service: str,
):
    result = _compare(running_service, _inputs())

    assert result["retirement_age"] == 45
    assert result["ruleset"] == {
        "version": "test-early-access-2026-v1",
        "effective_date": "2026-01-01",
    }
    assert set(_by_strategy(result)) == {
        "penalized_traditional",
        "taxable_bridge",
        "roth_contribution_basis",
        "roth_conversion_ladder",
        "rule_of_55",
        "sepp_72t",
    }


def test_years_show_taxes_penalties_accessibility_and_spending_shortfalls(
    running_service: str,
):
    result = _compare(
        running_service,
        _inputs(
            end_age=47,
            annual_spending=7_000,
            taxable_balance=10_000,
            taxable_basis=8_000,
            traditional_balance=20_000,
            roth_contribution_basis=12_000,
        ),
    )
    strategies = _by_strategy(result)

    penalized = strategies["penalized_traditional"]["years"][0]
    assert penalized == {
        "age": 45,
        "accessible_opening_balance": 20_000,
        "gross_withdrawal": 10_000,
        "ordinary_tax": 2_000,
        "capital_gains_tax": 0,
        "penalty": 1_000,
        "spendable": 7_000,
        "unmet_spending": 0,
        "accessible_closing_balance": 10_000,
    }

    taxable = strategies["taxable_bridge"]["years"][0]
    assert taxable["gross_withdrawal"] == 7_216.49
    assert taxable["capital_gains_tax"] == 216.49
    assert taxable["penalty"] == 0
    assert taxable["spendable"] == 7_000

    roth_years = strategies["roth_contribution_basis"]["years"]
    assert roth_years[0]["spendable"] == 7_000
    assert roth_years[0]["ordinary_tax"] == 0
    assert roth_years[0]["penalty"] == 0
    assert roth_years[1]["spendable"] == 5_000
    assert roth_years[1]["unmet_spending"] == 2_000


def test_roth_conversion_ladder_enforces_five_tax_year_wait(
    running_service: str,
):
    result = _compare(
        running_service,
        _inputs(end_age=52, annual_spending=20_000),
    )
    ladder = _by_strategy(result)["roth_conversion_ladder"]
    years = {year["age"]: year for year in ladder["years"]}

    assert years[45]["conversion_amount"] == 30_000
    assert years[45]["conversion_tax"] == 6_000
    assert years[45]["spendable"] == 0
    assert years[49]["spendable"] == 0
    assert years[50]["accessible_opening_balance"] == 30_000
    assert years[50]["gross_withdrawal"] == 20_000
    assert years[50]["penalty"] == 0
    assert years[50]["spendable"] == 20_000
    assert ladder["constraints"]["conversion_wait_years"] == 5


def test_rule_of_55_and_sepp_enforce_eligibility_and_commitment_constraints(
    running_service: str,
):
    age_45 = _by_strategy(_compare(running_service, _inputs()))

    rule_55 = age_45["rule_of_55"]
    assert rule_55["eligible"] is False
    assert rule_55["years"] == []
    assert rule_55["failure_reason"] == (
        "Employer separation age 45 is below the ruleset minimum age 55."
    )

    sepp = age_45["sepp_72t"]
    assert sepp["eligible"] is True
    assert sepp["constraints"] == {
        "fixed_annual_distribution": 25_000,
        "must_continue_until_age": 59.5,
        "minimum_years": 5,
        "required_commitment_payments": 15,
        "recapture_risk_if_schedule_modified": True,
        "first_unsustainable_age": None,
    }
    assert sepp["years"][0]["gross_withdrawal"] == 25_000
    assert sepp["years"][0]["ordinary_tax"] == 5_000
    assert sepp["years"][0]["penalty"] == 0
    assert sepp["years"][0]["spendable"] == 20_000

    age_55 = _by_strategy(
        _compare(
            running_service,
            _inputs(retirement_age=55, end_age=56, separated_from_employer_age=55),
        )
    )
    eligible_rule_55 = age_55["rule_of_55"]
    assert eligible_rule_55["eligible"] is True
    assert eligible_rule_55["years"][0]["gross_withdrawal"] == 25_000
    assert eligible_rule_55["years"][0]["penalty"] == 0


def test_sepp_rejects_a_distribution_the_account_cannot_sustain_through_commitment(
    running_service: str,
):
    result = _by_strategy(
        _compare(
            running_service,
            _inputs(
                traditional_balance=30_000,
                sepp_annual_distribution=25_000,
                annual_return_percent=0,
            ),
        )
    )["sepp_72t"]

    assert result["eligible"] is False
    assert result["years"] == []
    assert result["constraints"]["required_commitment_payments"] == 15
    assert result["constraints"]["first_unsustainable_age"] == 46
    assert result["constraints"]["recapture_risk_if_schedule_modified"] is True
    assert "cannot sustain the fixed annual distribution" in result[
        "failure_reason"
    ]
    assert "recapture" in result["failure_reason"].lower()


def test_comparison_uses_common_spendable_value_and_explains_all_constraints(
    running_service: str,
):
    result = _compare(running_service, _inputs())
    strategies = _by_strategy(result)

    assert result["comparison_metric"] == (
        "Spendable withdrawals after modeled taxes and penalties, less conversion taxes."
    )
    assert {item["strategy"] for item in result["ranking"]} == set(strategies)
    assert all("spendable_value" in item for item in strategies.values())
    assert strategies["penalized_traditional"]["constraints"] == {
        "penalty_applies_before_age": 59.5,
        "penalty_percent": 10,
    }
    penalized_years = {
        year["age"]: year
        for year in strategies["penalized_traditional"]["years"]
    }
    assert penalized_years[59]["penalty"] > 0
    assert penalized_years[60]["penalty"] == 0
    assert strategies["taxable_bridge"]["constraints"][
        "capital_gains_tax_applies_to_gains_only"
    ] is True
    assert strategies["roth_contribution_basis"]["constraints"] == {
        "contribution_basis_only": True,
        "earnings_included": False,
    }
