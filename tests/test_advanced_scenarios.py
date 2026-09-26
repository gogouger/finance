import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from base64 import urlsafe_b64encode
from pathlib import Path

import pytest


OWNER = {"X-Forwarded-User": "owner", "X-Auth-Method": "webauthn"}
VERIFIER = "advanced-scenario-pkce-verifier-for-finance"
CHALLENGE = base64.urlsafe_b64encode(
    hashlib.sha256(VERIFIER.encode()).digest()
).decode().rstrip("=")


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _request(url, payload=None, *, method="GET", headers=None):
    return urllib.request.Request(
        url,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )


def _json(request):
    with urllib.request.urlopen(request) as response:
        return json.load(response)


@pytest.fixture
def scenario_service(tmp_path: Path):
    port = _unused_port()
    environment = os.environ.copy()
    environment.update(
        {
            "FINANCE_DATA_DIR": str(tmp_path),
            "FINANCE_ENCRYPTION_KEY": urlsafe_b64encode(b"s" * 32).decode(),
            "PLAID_MODE": "fake",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.finance_app.main:app", "--host", "127.0.0.1", "--port", str(port)],
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
            except OSError:
                time.sleep(0.05)
        else:
            pytest.fail("service did not become healthy")
        yield base_url
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def _cases() -> dict[str, dict]:
    return {
        "housing_advanced": {
            "home_price": 300_000,
            "down_payment": 15_000,
            "mortgage_rate_percent": 6,
            "mortgage_term_years": 30,
            "monthly_rent": 2_000,
            "years": 2,
            "home_appreciation_percent": 3,
            "rent_growth_percent": 3,
            "investment_return_percent": 7,
            "investment_tax_drag_percent": 1,
            "property_tax_percent": 1,
            "home_insurance_annual": 1_800,
            "maintenance_percent": 1,
            "hoa_monthly": 0,
            "owner_utilities_monthly": 250,
            "renter_utilities_monthly": 150,
            "buy_closing_cost_percent": 2,
            "sell_cost_percent": 6,
            "loan_type": "fha",
            "include_case_comparison": True,
        },
        "retirement_optimizer": {
            "current_age": 40,
            "retirement_age": 45,
            "unrestricted_access_age": 59,
            "annual_income": 100_000,
            "annual_savings_budget": 20_000,
            "accessible_balance": 10_000,
            "annual_bridge_spending": 20_000,
            "annual_return_percent": 6,
            "taxable_tax_drag_percent": 1,
            "current_ordinary_tax_rate_percent": 24,
            "retirement_ordinary_tax_rate_percent": 12,
            "capital_gains_tax_rate_percent": 15,
            "employer_match_rate_percent": 50,
            "employer_matchable_salary_percent": 6,
            "filing_status": "married_filing_jointly",
            "hsa_coverage": "family",
            "eligibility": {"workplace_plan": True, "roth_ira": True, "traditional_ira": True, "hsa": True},
            "ruleset": {"version": "optimizer-rules-v1", "effective_date": "2026-01-01", "workplace_employee_limit": 23_000, "ira_combined_limit": 7_000, "hsa_self_only_limit": 4_000, "hsa_family_limit": 8_000},
        },
        "early_retirement": {
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
            "ruleset": {"version": "access-rules-v1", "effective_date": "2026-01-01", "unrestricted_access_age": 59.5, "early_withdrawal_penalty_percent": 10, "conversion_wait_years": 5, "rule_of_55_min_separation_age": 55, "sepp_minimum_years": 5},
        },
        "household": {
            "name": "Family baseline",
            "start_date": "2026-01-01",
            "end_date": "2027-12-31",
            "profile": {"marital_status": "married", "filing_status": "married_filing_jointly", "dependents": []},
            "baseline": {"primary_income": 100_000, "spouse_income": 0, "personal_expenses": 30_000, "tax_liability": 15_000, "housing_cost": 24_000, "retirement_contribution": 10_000},
            "events": [],
        },
        "retirement_uncertainty": {
            "seed": 42,
            "simulations": 50,
            "retirement_age": 55,
            "end_age": 65,
            "starting_balance": 650_000,
            "annual_spending": 45_000,
            "general_inflation_percent": 2.5,
            "return_mean_percent": 5,
            "return_stddev_percent": 12,
            "benefit_tax_rate_percent": 10,
            "social_security": {"claim_age": 67, "annual_benefit": 30_000, "cola_percent": 2},
            "pensions": [],
            "healthcare": {"medicare_age": 65, "pre_medicare_annual_cost": 12_000, "medicare_annual_cost": 7_000, "healthcare_inflation_percent": 5},
            "policy_stress_cases": [],
        },
    }


def _scenario_token(base_url: str) -> str:
    fresh_owner = {**OWNER, "X-Auth-Time": str(time.time())}
    grant = _json(
        _request(
            f"{base_url}/api/private/mcp/grants",
            {"client_id": "scenario-test-client", "client_name": "Scenario test client", "redirect_uri": "http://127.0.0.1:8765/callback", "scopes": ["finance:scenarios"], "code_challenge": CHALLENGE, "code_challenge_method": "S256"},
            method="POST",
            headers=fresh_owner,
        )
    )
    token_request = urllib.request.Request(
        f"{base_url}/mcp/oauth/token",
        data=urllib.parse.urlencode({"grant_type": "authorization_code", "code": grant["authorization_code"], "client_id": "scenario-test-client", "redirect_uri": "http://127.0.0.1:8765/callback", "code_verifier": VERIFIER}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    return _json(token_request)["access_token"]


def test_advanced_models_can_be_saved_cloned_compared_and_calculated_through_mcp(
    scenario_service: str,
):
    access_token = _scenario_token(scenario_service)

    for calculator, inputs in _cases().items():
        saved = _json(
            _request(
                f"{scenario_service}/api/private/scenarios",
                {"name": f"Saved {calculator}", "calculator": calculator, "inputs": inputs},
                method="POST",
                headers=OWNER,
            )
        )
        assert saved["inputs"] == inputs
        assert saved["output"]
        assert saved["ruleset_version"]

        clone = _json(
            _request(
                f"{scenario_service}/api/private/scenarios/{saved['id']}/clone",
                {"name": f"Clone {calculator}"},
                method="POST",
                headers=OWNER,
            )
        )
        compared = _json(
            _request(
                f"{scenario_service}/api/private/scenarios/compare",
                {"baseline_id": saved["id"], "variant_id": clone["id"]},
                method="POST",
                headers=OWNER,
            )
        )
        assert set(compared["outcome_changes"].values()) == {0}

        mcp = _json(
            _request(
                f"{scenario_service}/mcp/tools/call",
                {"tool": "finance.scenario.calculate", "arguments": {"calculator": calculator, "inputs": inputs}},
                method="POST",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        )
        assert mcp["data"] == saved["output"]
