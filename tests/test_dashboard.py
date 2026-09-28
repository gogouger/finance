import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from base64 import urlsafe_b64encode
from datetime import date
from pathlib import Path

import pytest

from backend.finance_app.dashboard import (
    _net_worth_attribution,
    _taxable_brokerage_cash_equivalents,
)


OWNER = {"X-Forwarded-User": "owner", "X-Auth-Method": "webauthn"}


def test_net_worth_change_reconciles_opening_drivers_and_ending_value():
    result = _net_worth_attribution(
        account_by_key={
            "connection:cash": {
                "type": "depository",
                "name": "Checking",
            },
            "connection:brokerage": {
                "type": "investment",
                "name": "Brokerage",
                "subtype": "brokerage",
            },
            "connection:kids": {
                "type": "investment",
                "name": "Child UTMA",
                "subtype": "utma",
            },
            "connection:card": {"type": "credit", "name": "Card"},
        },
        balance_history={
            "connection:cash": [
                {"current": 10_000, "observed_at": "2025-09-20T00:00:00Z"}
            ],
            "connection:brokerage": [
                {"current": 100_000, "observed_at": "2025-09-20T00:00:00Z"}
            ],
            "connection:kids": [
                {"current": 50_000, "observed_at": "2025-09-20T00:00:00Z"}
            ],
            "connection:card": [
                {"current": 2_000, "observed_at": "2025-09-20T00:00:00Z"}
            ],
        },
        assets=[
            {
                "kind": "home",
                "name": "Home",
                "valuation": {
                    "amount": 650_000,
                    "valued_at": "2026-09-20T00:00:00Z",
                },
                "valuation_history": [
                    {
                        "amount": 600_000,
                        "valued_at": "2025-09-20T00:00:00Z",
                    }
                ],
                "ownership": {"debt_balance": 0},
            }
        ],
        comparison_date=date(2025, 9, 27),
        ending_net_worth=800_000,
        operating_surplus=50_000,
    )

    assert result["available"] is True
    assert result["opening_net_worth"] == 710_000
    assert result["ending_net_worth"] == 800_000
    assert result["change"] == 90_000
    assert result["direction"] == "stronger"
    assert result["drivers"][0]["value"] == 50_000
    assert result["drivers"][1]["value"] == 40_000
    assert result["reconciliation_difference"] == 0
    assert result["coverage"]["covered"] == 3
    assert "Child UTMA" not in result["coverage"]["sources"]


def test_brokerage_cash_equivalents_excludes_retirement_and_custodial_cash():
    accounts = {
        "brokerage": {"account_id": "brokerage", "type": "investment", "subtype": "brokerage"},
        "ira": {"account_id": "ira", "type": "investment", "subtype": "ira"},
        "utma": {"account_id": "utma", "type": "investment", "subtype": "utma"},
    }
    securities = {
        "spaxx": {"security_id": "spaxx", "type": "cash"},
        "fund": {"security_id": "fund", "type": "mutual fund"},
    }
    holdings = [
        {"account_id": "brokerage", "security_id": "spaxx", "institution_value": 14_177.19},
        {"account_id": "brokerage", "security_id": "fund", "institution_value": 2_000},
        {"account_id": "ira", "security_id": "spaxx", "institution_value": 3_000},
        {"account_id": "utma", "security_id": "spaxx", "institution_value": 1_000},
    ]

    assert _taxable_brokerage_cash_equivalents(holdings, securities, accounts) == 14_177.19


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


def _json(request: urllib.request.Request) -> dict:
    with urllib.request.urlopen(request) as response:
        assert response.status in {200, 201}
        return json.load(response)


@pytest.fixture
def dashboard_service(tmp_path: Path):
    port = _unused_port()
    environment = os.environ.copy()
    environment.update(
        {
            "FINANCE_DATA_DIR": str(tmp_path),
            "FINANCE_ENCRYPTION_KEY": urlsafe_b64encode(b"d" * 32).decode(),
            "PLAID_MODE": "fake",
            "FINANCE_INTERNAL_KEY": "dashboard-internal-key",
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

        fresh_owner = {**OWNER, "X-Auth-Time": str(time.time())}
        _json(
            _request(
                f"{base_url}/api/private/connections/plaid/exchange",
                {
                    "public_token": "public-sandbox-accounting",
                    "connection_type": "credit",
                    "display_name": "Dashboard fixture",
                    "institution_id": "ins_dashboard",
                    "institution_name": "Dashboard Bank",
                },
                method="POST",
                headers=fresh_owner,
            )
        )
        _json(
            _request(
                f"{base_url}/api/internal/nightly-reconcile",
                method="POST",
                headers={"X-Internal-Key": "dashboard-internal-key"},
            )
        )
        _json(
            _request(
                f"{base_url}/api/private/assets",
                {
                    "kind": "home",
                    "name": "Primary residence",
                    "identifiers": {"parcel_id": "private"},
                    "purchase_price": 350000,
                    "ownership": {"owned_outright": False, "debt_balance": 100000},
                    "valuation": {
                        "amount": 600000,
                        "valued_at": "2026-09-20T16:00:00Z",
                        "source_label": "owner estimate",
                    },
                    "selling_cost_percent": 6,
                    "annual_costs": {},
                },
                method="POST",
                headers=OWNER,
            )
        )
        yield base_url
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_dashboard_denies_unauthenticated_access(dashboard_service: str):
    with pytest.raises(urllib.error.HTTPError) as denied:
        urllib.request.urlopen(f"{dashboard_service}/api/private/dashboard")

    assert denied.value.code == 401


def test_owner_sees_explainable_metrics_from_normalized_records(
    dashboard_service: str,
):
    dashboard = _json(
        _request(f"{dashboard_service}/api/private/dashboard", headers=OWNER)
    )

    values = {metric["key"]: metric["value"] for metric in dashboard["metrics"]}
    assert values == {
        "net_worth": 510000,
        "cash": 10000,
        "taxable_brokerage_cash": 0,
        "debt": 100000,
        "income": 2000,
        "raw_cash_flow": 1748,
        "raw_spending": 402,
        "adjusted_personal_spending": 302,
        "true_monthly_cost": 0,
        "current_card_balance": 200,
        "investment_value": 0,
        "custodial_investment_value": 0,
        "retirement_value": 0,
        "household_asset_value": 600000,
    }
    assert dashboard["currency"] == "USD"
    assert dashboard["spending_by_category"]["GENERAL_MERCHANDISE"] == 140
    for metric in dashboard["metrics"]:
        assert metric["definition"]
        assert isinstance(metric["inclusions"], list)
        assert isinstance(metric["exclusions"], list)
        assert set(metric["freshness"]) == {"status", "as_of"}
        assert isinstance(metric["gaps"], list)
        assert set(metric["confidence"]) == {"level", "rationale"}
        assert set(metric["source_coverage"]) == {
            "covered",
            "total",
            "percent",
            "sources",
        }

    assert dashboard["sections"]["cash_flow"]["net"] == 1748
    assert dashboard["sections"]["cash_flow"]["depository_credits"] == 2500
    assert dashboard["sections"]["cash_flow"]["refund_credits"] == 0
    assert dashboard["sections"]["cash_flow"]["income_credits"] == 2000
    assert dashboard["sections"]["cash_flow"]["card_payment_debits"] == 200
    assert dashboard["sections"]["cash_flow"]["operating_surplus"] == 1698
    assert dashboard["sections"]["cash_flow"]["available"] is True
    assert dashboard["sections"]["adjusted_spending"]["value"] == 302
    assert dashboard["sections"]["adjusted_spending"]["excluded_from_personal"] == 100
    assert "not a budget target" in dashboard["sections"]["adjusted_spending"][
        "context"
    ]
    assert dashboard["net_worth_change"]["available"] is False
    assert dashboard["net_worth_change"]["ending_net_worth"] == 510000
    assert dashboard["net_worth_change"]["direction"] == "not_yet_measurable"
    assert dashboard["net_worth_change"]["drivers"][0]["value"] == 1698
    assert dashboard["net_worth_change"]["coverage"]["covered"] == 0
    assert "not available" in dashboard["net_worth_change"]["limitations"][-1]


def test_expected_recurring_costs_are_not_mislabelled_as_unusual(
    dashboard_service: str,
):
    fresh_owner = {**OWNER, "X-Auth-Time": str(time.time())}
    _json(
        _request(
            f"{dashboard_service}/api/private/connections/plaid/exchange",
            {
                "public_token": "public-sandbox-recurring",
                "connection_type": "banking",
                "display_name": "Irregular-cost fixture",
                "institution_id": "ins_irregular",
                "institution_name": "Irregular Bank",
            },
            method="POST",
            headers=fresh_owner,
        )
    )
    _json(
        _request(
            f"{dashboard_service}/api/internal/nightly-reconcile",
            method="POST",
            headers={"X-Internal-Key": "dashboard-internal-key"},
        )
    )

    dashboard = _json(
        _request(f"{dashboard_service}/api/private/dashboard", headers=OWNER)
    )
    recurring_charge_signals = [
        item
        for item in dashboard["unusual_activity"]
        if item.get("merchant_name")
        in {"Douglas County Treasurer", "Home Shield Insurance"}
    ]

    assert recurring_charge_signals == []
    assert "six earlier merchant-specific observations" in dashboard[
        "unusual_activity_method"
    ]["limitations"]
    assert "not fraud determinations" in dashboard["unusual_activity_method"][
        "limitations"
    ]


def test_dashboard_recommendations_have_evidence_and_remember_owner_feedback(
    dashboard_service: str,
):
    fresh_owner = {**OWNER, "X-Auth-Time": str(time.time())}
    _json(
        _request(
            f"{dashboard_service}/api/private/connections/plaid/exchange",
            {
                "public_token": "public-sandbox-opportunities",
                "connection_type": "banking",
                "display_name": "Opportunity fixture",
                "institution_id": "ins_opportunities",
                "institution_name": "Opportunity Bank",
            },
            method="POST",
            headers=fresh_owner,
        )
    )
    _json(
        _request(
            f"{dashboard_service}/api/internal/nightly-reconcile",
            method="POST",
            headers={"X-Internal-Key": "dashboard-internal-key"},
        )
    )

    dashboard = _json(
        _request(f"{dashboard_service}/api/private/dashboard", headers=OWNER)
    )
    recommendations = dashboard["recommendations"]
    fee = next(
        item
        for item in recommendations["opportunities"]
        if item["kind"] == "fees"
    )
    growth = next(
        item
        for item in recommendations["opportunities"]
        if item["kind"] == "category_growth"
    )
    assert fee["estimated_impact"]["annual"] == 30
    assert fee["confidence"]["level"] == "high"
    assert fee["evidence"]
    assert growth["estimated_impact"]["monthly"] > 300
    assert "not a forecast" in fee["estimated_impact"]["investment_assumption"]
    assert "No service is cancelled" in recommendations["method"]["boundaries"]

    feedback = _json(
        _request(
            f"{dashboard_service}/api/private/recommendations/{fee['id']}/feedback",
            {"action": "essential", "note": "This account is required."},
            method="POST",
            headers=OWNER,
        )
    )
    assert feedback["action"] == "essential"

    after = _json(
        _request(f"{dashboard_service}/api/private/dashboard", headers=OWNER)
    )["recommendations"]
    assert fee["id"] not in {item["id"] for item in after["opportunities"]}
    remembered = next(item for item in after["reviewed"] if item["id"] == fee["id"])
    assert remembered["owner_feedback"]["action"] == "essential"


def test_private_dashboard_page_ships_the_command_center_ui(
    dashboard_service: str,
):
    with urllib.request.urlopen(
        _request(f"{dashboard_service}/dashboard", headers=OWNER)
    ) as response:
        html = response.read().decode()
    asset_path = html.split('src="', 1)[1].split('"', 1)[0]

    with urllib.request.urlopen(f"{dashboard_service}{asset_path}") as response:
        application = response.read().decode()

    assert "Amount spent" in application
    assert "Amount saved" in application
    assert "Unusual activity" in application
    assert "Review, not a verdict" in application
    assert "Where the money actually went" in application
    assert "Credit-card obligations" in application
    assert "Transaction explorer" in application


def test_spending_analytics_separates_payments_and_builds_time_views(
    dashboard_service: str,
):
    analytics = _json(
        _request(
            f"{dashboard_service}/api/private/spending/analytics?months=12",
            headers=OWNER,
        )
    )

    assert analytics["summary"]["credit_card_payments"] == 400
    assert analytics["summary"]["raw_spending"] == 402
    assert analytics["summary"]["refunds"] == 100
    assert analytics["summary"]["net_spending"] == 302
    assert analytics["data_quality"]["cross_source_records_excluded"] == 0
    assert analytics["monthly"][-1]["month"] == "2026-09"
    assert analytics["quarterly"][-1] == {
        "period": "2026-Q3",
        "net_spending": 302,
    }
    assert analytics["annual"][-1] == {
        "period": "2026",
        "net_spending": 302,
    }
    assert analytics["yearly"][0]["year"] == 2026
    assert analytics["yearly"][0]["net_spending"] == 302
    assert analytics["yearly"][0]["months_covered"] == 1
    assert analytics["yearly"][0]["complete_year"] is False
    assert analytics["yearly"][0]["current_year"] is True
    assert analytics["liabilities"][0]["last_statement_balance"] == 180
    assert analytics["liabilities"][0]["next_payment_due_date"] == "2026-10-02"
    assert isinstance(analytics["anomalies"], list)
