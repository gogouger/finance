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

from backend.finance_app.investments import (
    _holding_benchmark_comparison,
    _tax_treatment,
)


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _request(
    url: str,
    payload: dict | None = None,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )


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


def _connect_investment_account(base_url: str) -> tuple[dict, dict[str, str]]:
    fresh_owner = {
        "X-Forwarded-User": "owner",
        "X-Auth-Method": "webauthn",
        "X-Auth-Time": str(time.time()),
    }
    with urllib.request.urlopen(
        _request(
            f"{base_url}/api/private/connections/plaid/exchange",
            {
                "public_token": "public-sandbox-investments",
                "connection_type": "investment",
                "display_name": "Sandbox brokerage",
                "institution_id": "ins_investments",
                "institution_name": "Sandbox Investments",
            },
            method="POST",
            headers=fresh_owner,
        )
    ) as response:
        connection = json.load(response)
    return connection, {
        "X-Forwarded-User": "owner",
        "X-Auth-Method": "webauthn",
    }


def test_tax_treatment_keeps_hsa_and_custodial_assets_out_of_taxable_brokerage():
    assert _tax_treatment({"name": "Health Savings Account"}) == "hsa"
    assert _tax_treatment({"name": "ROTH IRA"}) == "roth"
    assert _tax_treatment({"subtype": "401k"}) == "tax_deferred"
    assert _tax_treatment({"subtype": "utma"}) == "custodial"
    assert _tax_treatment({"name": "Joint WROS"}) == "taxable"


def test_holding_benchmark_uses_same_lot_dollars_and_reports_coverage():
    comparison = _holding_benchmark_comparison(
        {"quantity": 10, "institution_value": 1_500},
        [
            {
                "quantity": 4,
                "cost_basis": 400,
                "acquired_date": "2025-01-01",
            },
            {
                "quantity": 6,
                "cost_basis": 660,
                "acquired_date": "2025-06-01",
            },
        ],
        [
            {"date": "2025-01-01", "value": 100},
            {"date": "2025-05-31", "value": 110},
            {"date": "2025-12-31", "value": 120},
        ],
    )

    assert comparison["status"] == "available"
    assert comparison["basis_covered"] == 1060
    assert comparison["actual_covered_value"] == 1500
    assert comparison["benchmark_value"] == 1200
    assert comparison["excess_value"] == 300
    assert comparison["actual_return_percent"] == pytest.approx(41.51, abs=0.01)
    assert comparison["benchmark_return_percent"] == pytest.approx(13.21, abs=0.01)


def test_holding_benchmark_refuses_to_guess_without_lot_history():
    comparison = _holding_benchmark_comparison(
        {"quantity": 10, "institution_value": 1_500},
        [],
        [{"date": "2025-12-31", "value": 120}],
    )

    assert comparison["status"] == "unavailable"
    assert "acquisition dates" in comparison["reason"]


def test_investment_sync_is_idempotent_and_never_invents_cost_basis(
    running_service: str,
):
    connection, owner = _connect_investment_account(running_service)
    reconcile = _request(
        f"{running_service}/api/internal/nightly-reconcile",
        method="POST",
        headers={"X-Internal-Key": "test-internal-key"},
    )

    with urllib.request.urlopen(reconcile) as response:
        first = json.load(response)["connections"][0]
    with urllib.request.urlopen(reconcile) as response:
        second = json.load(response)["connections"][0]

    assert first["holdings"] == second["holdings"] == 2
    assert first["securities"] == second["securities"] == 2
    assert first["investment_activities"] == second["investment_activities"] == 4

    with urllib.request.urlopen(
        _request(
            f"{running_service}/api/private/investments/positions",
            headers=owner,
        )
    ) as response:
        positions = json.load(response)

    assert positions["currency"] == "USD"
    assert positions["summary"] == {
        "household_market_value": 1760,
        "household_position_count": 2,
        "known_cost_basis": 900,
        "known_basis_market_value": 1200,
        "basis_coverage_percent": 68.2,
        "unrealized_gain_on_known_basis": 300,
        "unrealized_gain_percent": 33.3,
        "custodial_market_value": 0,
        "custodial_position_count": 0,
        "custodial_definition": "UTMA and UGMA assets belong to their child beneficiaries and are excluded from household totals.",
    }
    assert len(positions["account_summaries"]) == 1
    account_summary = positions["account_summaries"][0]
    assert account_summary["ownership_scope"] == "household"
    assert account_summary["tax_treatment"] == "taxable"
    assert account_summary["market_value"] == 1760
    assert account_summary["known_cost_basis"] == 900
    assert account_summary["known_basis_market_value"] == 1200
    assert account_summary["basis_coverage_percent"] == 68.2
    assert account_summary["unrealized_gain_on_known_basis"] == 300
    assert account_summary["unrealized_gain_percent"] == 33.3
    assert account_summary["observed_activity"] == {
        "start": "2025-06-30",
        "end": "2025-10-31",
        "contributions": 500,
        "withdrawals": 100,
        "dividends_and_interest": 40,
        "definition": "Activity visible in the connected provider history; it may not cover the life of the account.",
    }
    assert account_summary["performance_tracking"]["status"] == "collecting_history"
    assert account_summary["estimated_federal_tax_if_sold"]["at_15_percent"] == 45
    assert positions["freshness"][connection["id"]]["holdings"]
    holdings = {holding["ticker_symbol"]: holding for holding in positions["holdings"]}
    assert holdings["TOTAL"]["cost_basis_status"] == "stale"
    assert holdings["TOTAL"]["cost_basis"] == 900
    assert holdings["TOTAL"]["cost_basis_as_of"] == "2025-12-31"
    assert holdings["BOND"]["cost_basis_status"] == "missing"
    assert holdings["BOND"]["cost_basis"] is None
    assert holdings["TOTAL"]["analytics"] == {
        "account_weight_percent": 68.2,
        "household_weight_percent": 68.2,
        "unrealized_gain": 300,
        "unrealized_gain_percent": 33.3,
        "tax_lot_count": 0,
    }
    assert holdings["TOTAL"]["benchmark_comparison"]["status"] == "unavailable"
    assert "Tax-lot" in holdings["TOTAL"]["benchmark_comparison"]["reason"]
    assert all(item["ownership_scope"] == "household" for item in positions["holdings"])
    assert all(item["ownership_scope"] == "household" for item in positions["activities"])


def test_performance_explains_balance_change_and_compares_matching_benchmark(
    running_service: str,
):
    _, owner = _connect_investment_account(running_service)
    with urllib.request.urlopen(
        _request(
            f"{running_service}/api/internal/nightly-reconcile",
            method="POST",
            headers={"X-Internal-Key": "test-internal-key"},
        )
    ):
        pass

    with urllib.request.urlopen(
        _request(
            f"{running_service}/api/private/investments/performance"
            "?start=2025-01-01&end=2025-12-31&benchmark=VTI",
            headers=owner,
        )
    ) as response:
        report = json.load(response)

    assert report["currency"] == "USD"
    assert report["period"] == {"start": "2025-01-01", "end": "2025-12-31"}
    assert report["attribution"] == {
        "beginning_value": 1000,
        "contributions": 500,
        "withdrawals": 100,
        "dividends": 40,
        "fees": 10,
        "market_performance": 330,
        "valuation_changes": 0,
        "ending_value": 1760,
    }
    assert report["returns"]["time_weighted_percent"] == 29.0667
    assert report["returns"]["money_weighted_xirr_percent"] == 30.3726
    assert "external cash flows" in report["returns"]["time_weighted_definition"]
    assert "timing and size" in report["returns"]["money_weighted_definition"]
    assert report["benchmark"] == {
        "symbol": "VTI",
        "period": {"start": "2025-01-01", "end": "2025-12-31"},
        "return_percent": 10,
        "portfolio_excess_percent": 19.0667,
    }
