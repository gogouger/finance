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


@pytest.fixture
def asset_service(tmp_path: Path):
    port = _unused_port()
    environment = os.environ.copy()
    environment.update(
        {
            "FINANCE_DATA_DIR": str(tmp_path),
            "FINANCE_ENCRYPTION_KEY": urlsafe_b64encode(b"a" * 32).decode(),
            "PLAID_MODE": "fake",
            "FINANCE_INTERNAL_KEY": "asset-internal-key",
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
        with urllib.request.urlopen(
            _request(
                f"{base_url}/api/private/connections/plaid/exchange",
                {
                    "public_token": "public-sandbox-accounting-assets",
                    "connection_type": "banking",
                    "display_name": "Asset fixture",
                    "institution_id": "ins_assets",
                    "institution_name": "Asset Bank",
                },
                method="POST",
                headers=fresh_owner,
            )
        ):
            pass
        with urllib.request.urlopen(
            _request(
                f"{base_url}/api/internal/nightly-reconcile",
                method="POST",
                headers={"X-Internal-Key": "asset-internal-key"},
            )
        ):
            pass
        yield base_url
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_owner_registers_outright_home_with_private_identity_and_sourced_value(
    asset_service: str,
):
    home = {
        "kind": "home",
        "name": "Primary residence",
        "identifiers": {
            "address": "123 Private Lane, Castle Rock, CO",
            "parcel_id": "R0123456",
        },
        "purchase_price": 350000,
        "ownership": {"owned_outright": True, "debt_balance": 0},
        "valuation": {
            "amount": 600000,
            "valued_at": "2026-09-20T16:00:00Z",
            "source_label": "owner estimate",
        },
        "selling_cost_percent": 6,
        "annual_costs": {
            "property_tax": 4500,
            "hoa": 1200,
            "insurance": 1600,
            "utilities": 3600,
            "maintenance": 2500,
            "improvements": 0,
        },
    }
    with urllib.request.urlopen(
        _request(
            f"{asset_service}/api/private/assets",
            home,
            method="POST",
            headers=OWNER,
        )
    ) as response:
        created = json.load(response)

    assert response.status == 201
    assert created["ownership"] == {
        "owned_outright": True,
        "debt_balance": 0,
        "gross_equity": 600000,
        "estimated_selling_cost": 36000,
        "net_equity_after_sale": 564000,
    }
    assert created["valuation"] == {
        "amount": 600000,
        "currency": "USD",
        "valued_at": "2026-09-20T16:00:00Z",
        "source_label": "owner estimate",
    }
    assert created["identifiers"] == home["identifiers"]
    assert created["cost_summary"] == {
        "annual_ownership_costs": home["annual_costs"],
        "annual_ownership_total": 13400,
        "linked_transaction_total": 0,
    }

    with urllib.request.urlopen(
        _request(f"{asset_service}/api/private/assets", headers=OWNER)
    ) as response:
        assert json.load(response)["assets"] == [created]

    with urllib.request.urlopen(
        _request(
            f"{asset_service}/api/private/assets",
            headers={"X-Forwarded-User": "someone-else", "X-Auth-Method": "webauthn"},
        )
    ) as response:
        assert json.load(response)["assets"] == []

    with pytest.raises(urllib.error.HTTPError) as denied:
        urllib.request.urlopen(f"{asset_service}/api/private/assets")
    assert denied.value.code == 401


def test_ambiguous_home_transaction_defaults_to_maintenance_until_confirmed(
    asset_service: str,
):
    with urllib.request.urlopen(
        _request(
            f"{asset_service}/api/private/assets",
            {
                "kind": "home",
                "name": "Primary residence",
                "purchase_price": 350000,
                "ownership": {"owned_outright": True, "debt_balance": 0},
                "valuation": {
                    "amount": 600000,
                    "valued_at": "2026-09-20T16:00:00Z",
                    "source_label": "owner estimate",
                },
            },
            method="POST",
            headers=OWNER,
        )
    ) as response:
        home = json.load(response)

    link_url = f"{asset_service}/api/private/assets/{home['id']}/cost-links"
    with urllib.request.urlopen(
        _request(
            link_url,
            {"transaction_id": "grocery-posted"},
            method="POST",
            headers=OWNER,
        )
    ) as response:
        linked = json.load(response)

    assert linked == {
        "transaction_id": "grocery-posted",
        "transaction_date": "2026-09-21",
        "merchant_name": "Mountain Market",
        "amount": 52,
        "category": "maintenance",
        "classification": "defaulted",
    }

    with pytest.raises(urllib.error.HTTPError) as unconfirmed:
        urllib.request.urlopen(
            _request(
                f"{link_url}/grocery-posted",
                {"category": "capital_improvement"},
                method="PATCH",
                headers=OWNER,
            )
        )
    assert unconfirmed.value.code == 422

    with urllib.request.urlopen(
        _request(
            f"{link_url}/grocery-posted",
            {
                "category": "capital_improvement",
                "confirm_capital_improvement": True,
            },
            method="PATCH",
            headers=OWNER,
        )
    ) as response:
        confirmed = json.load(response)

    assert confirmed == {
        **linked,
        "category": "capital_improvement",
        "classification": "owner_confirmed",
    }


def test_vehicle_reports_debt_equity_depreciation_and_complete_operating_cost(
    asset_service: str,
):
    annual_costs = {
        "insurance": 1500,
        "registration": 300,
        "energy": 1200,
        "maintenance": 600,
        "repairs": 400,
        "transactions": 200,
    }
    with urllib.request.urlopen(
        _request(
            f"{asset_service}/api/private/assets",
            {
                "kind": "vehicle",
                "name": "Family SUV",
                "identifiers": {
                    "vin": "1PRIVATE2345678901",
                    "license_plate": "PRIVATE",
                },
                "purchase_price": 40000,
                "ownership": {"owned_outright": False, "debt_balance": 10000},
                "valuation": {
                    "amount": 28000,
                    "valued_at": "2026-09-24T12:00:00Z",
                    "source_label": "manual market comparison",
                },
                "annual_costs": annual_costs,
            },
            method="POST",
            headers=OWNER,
        )
    ) as response:
        vehicle = json.load(response)

    assert vehicle["ownership"] == {
        "owned_outright": False,
        "debt_balance": 10000,
        "gross_equity": 18000,
        "estimated_selling_cost": 0,
        "net_equity_after_sale": 18000,
    }
    assert vehicle["cost_summary"] == {
        "depreciation_to_date": 12000,
        "annual_operating_costs": annual_costs,
        "annual_operating_total": 4200,
        "linked_transaction_total": 0,
    }
    assert vehicle["valuation"]["source_label"] == "manual market comparison"
    assert vehicle["valuation"]["valued_at"] == "2026-09-24T12:00:00Z"


def test_new_valuation_preserves_timestamped_source_history(asset_service: str):
    with urllib.request.urlopen(
        _request(
            f"{asset_service}/api/private/assets",
            {
                "kind": "vehicle",
                "name": "Commuter",
                "purchase_price": 20000,
                "ownership": {"owned_outright": True, "debt_balance": 0},
                "valuation": {
                    "amount": 15000,
                    "valued_at": "2025-01-01T00:00:00Z",
                    "source_label": "purchase worksheet",
                },
            },
            method="POST",
            headers=OWNER,
        )
    ) as response:
        vehicle = json.load(response)

    with urllib.request.urlopen(
        _request(
            f"{asset_service}/api/private/assets/{vehicle['id']}/valuation",
            {
                "amount": 14000,
                "valued_at": "2026-09-24T12:00:00Z",
                "source_label": "dealer trade-in quote",
            },
            method="PATCH",
            headers=OWNER,
        )
    ) as response:
        updated = json.load(response)

    assert updated["valuation"]["amount"] == 14000
    assert updated["valuation_history"] == [
        {
            "amount": 15000,
            "valued_at": "2025-01-01T00:00:00Z",
            "source_label": "purchase worksheet",
        },
        {
            "amount": 14000,
            "valued_at": "2026-09-24T12:00:00Z",
            "source_label": "dealer trade-in quote",
        },
    ]
    assert updated["provenance"] == {
        "source_label": "dealer trade-in quote",
        "observed_at": "2026-09-24T12:00:00Z",
        "freshness": "current",
    }
