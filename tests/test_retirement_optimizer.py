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


def _request(base_url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{base_url}/api/public/retirement/optimize",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        assert response.status == 200
        return json.load(response)


def _inputs(**overrides) -> dict:
    inputs = {
        "current_age": 40,
        "retirement_age": 45,
        "unrestricted_access_age": 59,
        "annual_income": 100_000,
        "annual_savings_budget": 20_000,
        "accessible_balance": 0,
        "annual_bridge_spending": 10_000,
        "annual_return_percent": 7,
        "taxable_tax_drag_percent": 1,
        "current_ordinary_tax_rate_percent": 24,
        "retirement_ordinary_tax_rate_percent": 12,
        "capital_gains_tax_rate_percent": 15,
        "employer_match_rate_percent": 50,
        "employer_matchable_salary_percent": 6,
        "filing_status": "married_filing_jointly",
        "hsa_coverage": "family",
        "eligibility": {
            "workplace_plan": True,
            "roth_ira": True,
            "traditional_ira": True,
            "hsa": True,
        },
        "ruleset": {
            "version": "test-us-2026-v1",
            "effective_date": "2026-01-01",
            "workplace_employee_limit": 10_000,
            "ira_combined_limit": 4_000,
            "hsa_self_only_limit": 2_000,
            "hsa_family_limit": 5_000,
        },
    }
    inputs.update(overrides)
    return inputs


def test_optimizer_protects_full_employer_match_before_discretionary_saving(
    running_service: str,
):
    result = _request(running_service, _inputs())

    assert result["ruleset"] == {
        "version": "test-us-2026-v1",
        "effective_date": "2026-01-01",
    }
    assert result["allocation"]["traditional_401k"] >= 6_000
    assert result["employer_match"] == 3_000
    assert result["match_protected"] is True
    assert "employer match" in result["drivers"][0].lower()


def test_optimizer_uses_versioned_limits_and_excludes_ineligible_accounts(
    running_service: str,
):
    eligibility = {
        "workplace_plan": True,
        "roth_ira": True,
        "traditional_ira": False,
        "hsa": True,
    }
    ruleset = _inputs()["ruleset"] | {"hsa_allowed": False}
    result = _request(
        running_service,
        _inputs(
            retirement_age=60,
            annual_savings_budget=30_000,
            annual_bridge_spending=0,
            eligibility=eligibility,
            ruleset=ruleset,
        ),
    )

    assert result["allocation"]["traditional_401k"] == 10_000
    assert result["allocation"]["roth_401k"] == 0
    assert result["allocation"]["roth_ira"] == 4_000
    assert result["allocation"]["traditional_ira"] == 0
    assert result["allocation"]["hsa"] == 0
    assert result["invested_tax_savings"] == 2_400
    assert result["allocation"]["taxable"] == 18_400
    assert result["contribution_limits"] == {
        "workplace_employee": 10_000,
        "ira_combined": 4_000,
        "hsa": 0,
    }
    assert result["account_eligibility"]["hsa"] == {
        "eligible": False,
        "reason": "not allowed by ruleset test-us-2026-v1",
    }


def test_optimizer_reports_and_funds_the_early_retirement_bridge_explicitly(
    running_service: str,
):
    result = _request(
        running_service,
        _inputs(annual_return_percent=0, taxable_tax_drag_percent=0),
    )

    assert result["bridge"] == {
        "years": 14,
        "target_at_retirement": 140_000,
        "projected_existing_at_retirement": 0,
        "required_annual_accessible_contribution": 28_000,
        "planned_annual_accessible_contribution": 15_440,
        "projected_funding_gap_at_retirement": 62_800,
        "status": "underfunded",
    }
    assert result["allocation"]["taxable"] == 15_440
    assert result["allocation"]["traditional_401k"] == 6_000
    assert any("bridge" in driver.lower() for driver in result["drivers"])


def test_recommendation_is_explainable_versioned_and_sensitivity_aware(
    running_service: str,
):
    result = _request(running_service, _inputs())

    assert result["model_version"] == "retirement-contribution-optimizer-v1"
    assert result["recommendation"]["preferred_workplace_account"] == (
        "traditional_401k"
    )
    assert "educational" in result["recommendation"]["disclaimer"].lower()
    assert "professional" in result["recommendation"]["disclaimer"].lower()
    assert result["sensitivity"]["retirement_tax_rate_plus_5"][
        "preferred_workplace_account"
    ] == "traditional_401k"
    assert result["sensitivity"]["return_minus_2"][
        "required_annual_accessible_contribution"
    ] > result["bridge"]["required_annual_accessible_contribution"]
    assert any("tax rate" in driver.lower() for driver in result["drivers"])


def test_optimizer_rejects_an_effective_taxable_return_below_negative_one_hundred_percent(
    running_service: str,
):
    request = urllib.request.Request(
        f"{running_service}/api/public/retirement/optimize",
        data=json.dumps(
            _inputs(
                annual_return_percent=-90,
                taxable_tax_drag_percent=20,
            )
        ).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with pytest.raises(urllib.error.HTTPError) as invalid:
        urllib.request.urlopen(request)

    assert invalid.value.code == 422
    assert "effective taxable return cannot be below -100%" in invalid.value.read().decode()
