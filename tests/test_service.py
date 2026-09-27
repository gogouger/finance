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


def _json_request(
    url: str,
    payload: dict | None = None,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
):
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    return urllib.request.Request(
        url,
        data=None if payload is None else json.dumps(payload).encode(),
        headers=request_headers,
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


def test_health_reports_only_service_status(running_service: str):
    with urllib.request.urlopen(f"{running_service}/health") as response:
        body = json.load(response)

    assert response.status == 200
    assert body == {"service": "finance", "status": "ok"}


def test_readiness_reports_usable_encrypted_storage(running_service: str):
    with urllib.request.urlopen(f"{running_service}/ready") as response:
        body = json.load(response)

    assert response.status == 200
    assert body["service"] == "finance"
    assert body["status"] == "ready"
    assert body["storage"] == "ready"
    assert len(body["installation_fingerprint"]) == 12


def test_browser_loads_responsive_usd_application_shell(running_service: str):
    with urllib.request.urlopen(f"{running_service}/") as response:
        html = response.read().decode()

    assert response.status == 200
    assert response.headers["Content-Type"].startswith("text/html")
    assert response.headers["Cache-Control"] == "no-store"
    assert "Your money, explained." in html
    assert 'href="/housing"' in html
    assert 'href="/retirement"' in html
    assert 'href="/dashboard"' in html
    assert "USD" in html


def test_housing_compares_buying_with_renting_and_investing_the_difference(
    running_service: str,
):
    payload = {
        "home_price": 120000,
        "down_payment": 12000,
        "mortgage_rate_percent": 0,
        "mortgage_term_years": 30,
        "monthly_rent": 1000,
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
    request = urllib.request.Request(
        f"{running_service}/api/public/housing/calculate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request) as response:
        body = json.load(response)

    assert response.status == 200
    assert body["currency"] == "USD"
    assert body["monthly_mortgage_payment"] == 300
    assert body["initial_cash_allocation"] == {
        "shared_starting_cash": 12000,
        "buyer_down_payment_to_home": 12000,
        "buyer_purchase_costs": 0,
        "renter_starting_investment": 12000,
    }
    year = body["years"][0]
    assert {
        key: year[key]
        for key in (
            "year",
            "buyer_equity",
            "buyer_net_wealth",
            "buyer_housing_cash_paid",
            "buyer_principal_contributed",
            "buyer_appreciation",
            "buyer_sale_cost",
            "renter_investments",
            "renter_housing_cash_paid",
            "renter_net_contributions",
            "renter_investment_growth",
            "buyer_unrecoverable_cost",
            "renter_unrecoverable_cost",
            "buyer_advantage",
        )
    } == {
        "year": 1,
        "buyer_equity": 15600,
        "buyer_net_wealth": 15600,
        "buyer_housing_cash_paid": 15600,
        "buyer_principal_contributed": 15600,
        "buyer_appreciation": 0,
        "buyer_sale_cost": 0,
        "renter_investments": 3600,
        "renter_housing_cash_paid": 12000,
        "renter_net_contributions": 3600,
        "renter_investment_growth": 0,
        "buyer_unrecoverable_cost": 0,
        "renter_unrecoverable_cost": 12000,
        "buyer_advantage": 12000,
    }
    assert year["buyer_components"] == {
        "home_value": 120000,
        "loan_balance": 104400,
        "down_payment": 12000,
        "principal_paid": 3600,
        "appreciation": 0,
        "interest": 0,
        "property_tax": 0,
        "insurance": 0,
        "maintenance": 0,
        "hoa": 0,
        "utilities": 0,
        "mortgage_insurance": 0,
        "purchase_costs": 0,
        "tax_benefit": 0,
        "sale_cost": 0,
    }
    assert body["crossover_years"] == [1]


def test_housing_route_loads_the_browser_application(running_service: str):
    with urllib.request.urlopen(f"{running_service}/housing") as response:
        html = response.read().decode()

    assert response.status == 200
    assert "The real cost of a house" in html


def test_retirement_baseline_separates_account_tax_treatment(running_service: str):
    payload = {
        "current_age": 30,
        "retirement_age": 31,
        "end_age": 31,
        "annual_income": 100000,
        "annual_expenses": 60000,
        "taxable_balance": 1000,
        "taxable_basis": 1000,
        "traditional_balance": 2000,
        "roth_balance": 3000,
        "hsa_balance": 4000,
        "taxable_contribution": 100,
        "traditional_contribution": 100,
        "roth_contribution": 100,
        "hsa_contribution": 100,
        "annual_return_percent": 10,
        "taxable_tax_drag_percent": 2,
        "inflation_percent": 0,
        "ordinary_tax_rate_percent": 25,
        "capital_gains_tax_rate_percent": 20,
    }
    request = urllib.request.Request(
        f"{running_service}/api/public/retirement/calculate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request) as response:
        body = json.load(response)

    assert response.status == 200
    assert body["currency"] == "USD"
    assert body["years"] == [
        {
            "age": 31,
            "phase": "working",
            "taxable_balance": 1188,
            "traditional_balance": 2310,
            "roth_balance": 3410,
            "hsa_balance": 4510,
            "taxable_growth": 88,
            "tax_deferred_growth": 210,
            "tax_free_growth": 720,
            "working_cash_surplus": 39600,
            "spending": 0,
            "taxes": 0,
            "unmet_spending": 0,
            "total_balance": 11418,
            "spendable_after_tax": 10822.9,
        }
    ]


def test_retirement_route_loads_the_browser_application(running_service: str):
    with urllib.request.urlopen(f"{running_service}/retirement") as response:
        html = response.read().decode()

    assert response.status == 200
    assert "Retire on your terms" in html


def test_public_dashboard_preview_loads_without_owner_headers(running_service: str):
    with urllib.request.urlopen(f"{running_service}/preview") as response:
        html = response.read().decode()

    assert response.status == 200
    assert "Finance" in html


def test_owner_can_save_clone_compare_share_and_revoke_scenarios(
    running_service: str,
):
    owner = {"X-Forwarded-User": "owner", "X-Auth-Method": "webauthn"}
    housing_inputs = {
        "home_price": 120000,
        "down_payment": 12000,
        "mortgage_rate_percent": 0,
        "mortgage_term_years": 30,
        "monthly_rent": 1000,
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

    with urllib.request.urlopen(
        _json_request(
            f"{running_service}/api/public/housing/calculate",
            housing_inputs,
            method="POST",
        )
    ):
        pass
    with urllib.request.urlopen(
        _json_request(
            f"{running_service}/api/private/scenarios",
            headers=owner,
        )
    ) as response:
        assert json.load(response) == []

    with urllib.request.urlopen(
        _json_request(
            f"{running_service}/api/private/scenarios",
            {"name": "Baseline", "calculator": "housing", "inputs": housing_inputs},
            method="POST",
            headers=owner,
        )
    ) as response:
        baseline = json.load(response)

    variant_inputs = {**housing_inputs, "monthly_rent": 500}
    with urllib.request.urlopen(
        _json_request(
            f"{running_service}/api/private/scenarios/{baseline['id']}/clone",
            {"name": "Lower rent", "inputs": variant_inputs},
            method="POST",
            headers=owner,
        )
    ) as response:
        variant = json.load(response)

    assert baseline["ruleset_version"] == variant["ruleset_version"]
    assert baseline["inputs"]["monthly_rent"] == 1000
    assert variant["inputs"]["monthly_rent"] == 500

    with urllib.request.urlopen(
        _json_request(
            f"{running_service}/api/private/scenarios/compare",
            {"baseline_id": baseline["id"], "variant_id": variant["id"]},
            method="POST",
            headers=owner,
        )
    ) as response:
        comparison = json.load(response)

    assert comparison["input_changes"]["monthly_rent"] == {
        "baseline": 1000,
        "variant": 500,
    }
    assert comparison["outcome_changes"]["buyer_advantage"] == -6000

    with urllib.request.urlopen(
        _json_request(
            f"{running_service}/api/private/scenarios/{variant['id']}/shares",
            {"expires_in_hours": 1},
            method="POST",
            headers=owner,
        )
    ) as response:
        share = json.load(response)

    with urllib.request.urlopen(
        f"{running_service}{share['path']}"
    ) as response:
        public_copy = json.load(response)

    assert set(public_copy) == {"calculator", "inputs", "output", "ruleset_version"}
    assert "name" not in public_copy

    with urllib.request.urlopen(
        _json_request(
            f"{running_service}/api/private/shares/{share['token']}",
            method="DELETE",
            headers=owner,
        )
    ) as response:
        assert response.status == 204

    with pytest.raises(urllib.error.HTTPError) as revoked:
        urllib.request.urlopen(f"{running_service}{share['path']}")
    assert revoked.value.code == 404


def test_private_scenarios_reject_password_only_sessions(running_service: str):
    with pytest.raises(urllib.error.HTTPError) as denied:
        urllib.request.urlopen(
            _json_request(
                f"{running_service}/api/private/scenarios",
                headers={
                    "X-Forwarded-User": "owner",
                    "X-Auth-Method": "password",
                },
            )
        )

    assert denied.value.code == 403


def test_private_shell_requires_passkey_and_sensitive_actions_require_fresh_auth(
    running_service: str,
):
    with pytest.raises(urllib.error.HTTPError) as anonymous:
        urllib.request.urlopen(f"{running_service}/dashboard")
    assert anonymous.value.code == 401

    with urllib.request.urlopen(
        _json_request(
            f"{running_service}/dashboard",
            headers={"X-Forwarded-User": "owner", "X-Auth-Method": "webauthn"},
        )
    ) as response:
        assert response.status == 200

    with pytest.raises(urllib.error.HTTPError) as stale:
        urllib.request.urlopen(
            _json_request(
                f"{running_service}/api/private/security/fresh-check",
                method="POST",
                headers={
                    "X-Forwarded-User": "owner",
                    "X-Auth-Method": "webauthn",
                },
            )
        )
    assert stale.value.code == 403

    with urllib.request.urlopen(
        _json_request(
            f"{running_service}/api/private/security/fresh-check",
            method="POST",
            headers={
                "X-Forwarded-User": "owner",
                "X-Auth-Method": "webauthn",
                "X-Auth-Time": str(time.time()),
            },
        )
    ) as response:
        assert json.load(response) == {"fresh": True}


def test_plaid_sandbox_link_exchange_health_and_disconnect(running_service: str):
    owner = {
        "X-Forwarded-User": "owner",
        "X-Auth-Method": "webauthn",
    }
    with urllib.request.urlopen(
        _json_request(
            f"{running_service}/api/private/connections/plaid/link-token",
            {"connection_type": "credit", "display_name": "Sandbox card"},
            method="POST",
            headers=owner,
        )
    ) as response:
        link = json.load(response)

    assert link["environment"] == "sandbox"
    assert link["products"] == ["transactions", "liabilities"]
    assert not {"auth", "identity", "transfer", "payment_initiation"} & set(
        link["products"]
    )
    assert link["link_token"].startswith("link-sandbox-")

    with urllib.request.urlopen(
        _json_request(
            f"{running_service}/api/private/connections/plaid/exchange",
            {
                "public_token": "public-sandbox-test",
                "connection_type": "credit",
                "display_name": "Sandbox card",
                "institution_id": "ins_109508",
                "institution_name": "First Platypus Bank",
            },
            method="POST",
            headers=owner,
        )
    ) as response:
        connection = json.load(response)

    assert connection["status"] == "healthy"
    assert connection["environment"] == "sandbox"
    assert connection["institution_name"] == "First Platypus Bank"
    assert "access_token" not in connection
    assert "public_token" not in connection

    with urllib.request.urlopen(
        _json_request(
            f"{running_service}/api/private/connections", headers=owner
        )
    ) as response:
        connections = json.load(response)
    assert connections == [connection]

    with urllib.request.urlopen(
        _json_request(
            f"{running_service}/api/private/connections/{connection['id']}",
            method="DELETE",
            headers=owner,
        )
    ) as response:
        disconnected = json.load(response)

    assert disconnected["status"] == "disconnected"
    assert disconnected["local_history_preserved"] is True
    assert "access_token" not in disconnected

    with urllib.request.urlopen(
        _json_request(
            f"{running_service}/api/private/audit-events", headers=owner
        )
    ) as response:
        audits = json.load(response)
    assert [event["action"] for event in audits] == [
        "plaid.link_token.created",
        "plaid.connection.created",
        "plaid.connection.disconnected",
    ]
    assert all("access_token" not in json.dumps(event) for event in audits)


def test_sync_is_idempotent_recovers_pagination_and_repairs_missed_webhook(
    running_service: str,
):
    fresh_owner = {"X-Forwarded-User": "owner", "X-Auth-Method": "webauthn", "X-Auth-Time": str(time.time())}
    owner = {"X-Forwarded-User": "owner", "X-Auth-Method": "webauthn"}
    with urllib.request.urlopen(
        _json_request(
            f"{running_service}/api/private/connections/plaid/exchange",
            {"public_token": "public-sandbox-mutation", "connection_type": "banking", "display_name": "Missed webhook bank", "institution_id": "ins_1", "institution_name": "Sandbox Bank"},
            method="POST",
            headers=fresh_owner,
        )
    ) as response:
        connection = json.load(response)

    internal = {"X-Internal-Key": "test-internal-key"}
    with urllib.request.urlopen(_json_request(f"{running_service}/api/internal/nightly-reconcile", method="POST", headers=internal)) as response:
        first = json.load(response)["connections"][0]
    assert (first["accounts"], first["balances"], first["transactions"]) == (1, 1, 2)

    with urllib.request.urlopen(_json_request(f"{running_service}/api/internal/nightly-reconcile", method="POST", headers=internal)) as response:
        second = json.load(response)["connections"][0]
    assert (second["accounts"], second["balances"], second["transactions"]) == (1, 1, 2)

    webhook = {"webhook_type": "TRANSACTIONS", "webhook_code": "SYNC_UPDATES_AVAILABLE", "item_id": connection["item_id"]}
    webhook_request = _json_request(f"{running_service}/api/public/plaid/webhook", webhook, method="POST", headers={"Plaid-Verification": "fake-valid"})
    with urllib.request.urlopen(webhook_request) as response:
        assert json.load(response)["duplicate"] is False
    with urllib.request.urlopen(_json_request(f"{running_service}/api/public/plaid/webhook", webhook, method="POST", headers={"Plaid-Verification": "fake-valid"})) as response:
        assert json.load(response)["duplicate"] is True

    with urllib.request.urlopen(_json_request(f"{running_service}/api/private/data/summary", headers=owner)) as response:
        summary = json.load(response)
    assert len(summary["accounts"]) == 1
    assert len(summary["balances"]) == 1
    assert len([item for item in summary["transactions"] if not item["removed"]]) == 2
    assert set(summary["freshness"][connection["id"]]) == {"accounts", "balances", "transactions"}

    refresh_url = f"{running_service}/api/private/connections/{connection['id']}/refresh"
    with urllib.request.urlopen(_json_request(refresh_url, method="POST", headers=owner)) as response:
        assert json.load(response)["transactions"] == 2
    with pytest.raises(urllib.error.HTTPError) as limited:
        urllib.request.urlopen(_json_request(refresh_url, method="POST", headers=owner))
    assert limited.value.code == 429
