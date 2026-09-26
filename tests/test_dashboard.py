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


OWNER = {"X-Forwarded-User": "owner", "X-Auth-Method": "webauthn"}


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
        "net_worth": 509800,
        "cash": 10000,
        "debt": 100000,
        "income": 2000,
        "raw_cash_flow": 1748,
        "raw_spending": 402,
        "adjusted_personal_spending": 302,
        "true_monthly_cost": 0,
        "credit_card_liabilities": 200,
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
    assert dashboard["sections"]["cash_flow"]["available"] is True
    assert dashboard["sections"]["adjusted_spending"]["value"] == 302
    assert dashboard["sections"]["adjusted_spending"]["excluded_from_personal"] == 100
    assert "not a budget target" in dashboard["sections"]["adjusted_spending"][
        "context"
    ]


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

    assert "Adjusted personal spending" in application
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
