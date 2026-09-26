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
):
    return urllib.request.Request(
        url,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Forwarded-User": "owner",
            "X-Auth-Method": "webauthn",
            **(headers or {}),
        },
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


def test_fidelity_tax_lots_can_be_validated_and_previewed_without_importing(
    running_service: str,
):
    content = """Account Number,Symbol,Description,Quantity,Cost Basis Total,Date Acquired,As Of Date
account-brokerage,BOND,Example Bond Fund,4,320.00,2024-02-01,2026-09-20
account-brokerage,BOND,Example Bond Fund,3,,2024-05-15,2026-09-20
"""

    with urllib.request.urlopen(
        _request(
            f"{running_service}/api/private/investments/imports/fidelity/preview",
            {"content": content},
            method="POST",
        )
    ) as response:
        preview = json.load(response)

    assert response.status == 200
    assert preview == {
        "provider": "fidelity",
        "valid": True,
        "counts": {"tax_lots": 2, "holdings": 0, "activities": 0},
        "records": [
            {
                "kind": "tax_lot",
                "account_id": "account-brokerage",
                "symbol": "BOND",
                "description": "Example Bond Fund",
                "quantity": 4.0,
                "cost_basis": 320.0,
                "cost_basis_status": "reported",
                "acquired_date": "2024-02-01",
                "effective_date": "2026-09-20",
                "currency": "USD",
                "source": "fidelity_csv",
            },
            {
                "kind": "tax_lot",
                "account_id": "account-brokerage",
                "symbol": "BOND",
                "description": "Example Bond Fund",
                "quantity": 3.0,
                "cost_basis": None,
                "cost_basis_status": "unknown",
                "acquired_date": "2024-05-15",
                "effective_date": "2026-09-20",
                "currency": "USD",
                "source": "fidelity_csv",
            },
        ],
    }

    with urllib.request.urlopen(
        _request(f"{running_service}/api/private/investments/positions")
    ) as response:
        positions = json.load(response)
    assert positions["tax_lots"] == []


def test_fidelity_import_is_idempotent_and_preserves_unknown_basis_and_provenance(
    running_service: str,
):
    content = """Account Number,Symbol,Description,Quantity,Cost Basis Total,Date Acquired,As Of Date
account-brokerage,BOND,Example Bond Fund,4,320.00,2024-02-01,2026-09-20
account-brokerage,BOND,Example Bond Fund,3,,2024-05-15,2026-09-20
"""
    import_request = lambda: _request(
        f"{running_service}/api/private/investments/imports/fidelity/commit",
        {"content": content},
        method="POST",
    )

    with urllib.request.urlopen(import_request()) as response:
        first = json.load(response)
    with urllib.request.urlopen(import_request()) as response:
        repeated = json.load(response)

    assert first["result"] == {"created": 2, "unchanged": 0}
    assert repeated["result"] == {"created": 0, "unchanged": 2}
    assert first["import_batch_id"] == repeated["import_batch_id"]

    with urllib.request.urlopen(
        _request(f"{running_service}/api/private/investments/positions")
    ) as response:
        positions = json.load(response)

    assert len(positions["tax_lots"]) == 2
    lots = {item["acquired_date"]: item for item in positions["tax_lots"]}
    assert lots["2024-02-01"]["cost_basis"] == 320
    assert lots["2024-02-01"]["cost_basis_status"] == "reported"
    assert lots["2024-05-15"]["cost_basis"] is None
    assert lots["2024-05-15"]["cost_basis_status"] == "unknown"
    assert lots["2024-05-15"]["source"] == "fidelity_csv"
    assert lots["2024-05-15"]["effective_date"] == "2026-09-20"
    assert lots["2024-05-15"]["import_batch_id"] == first["import_batch_id"]


def test_vestwell_export_previews_holdings_and_activity_with_validation(
    running_service: str,
):
    content = """Record Type,Account ID,Symbol,Description,Quantity,Market Value,Cost Basis,Effective Date,Activity Type,Amount
HOLDING,vestwell-401k,TDF2065,Target Date 2065,25,2500,,2026-09-20,,
ACTIVITY,vestwell-401k,TDF2065,Employee contribution,,,,2026-09-15,deposit,350
"""

    with urllib.request.urlopen(
        _request(
            f"{running_service}/api/private/investments/imports/vestwell/preview",
            {"content": content},
            method="POST",
        )
    ) as response:
        preview = json.load(response)

    assert preview["provider"] == "vestwell"
    assert preview["valid"] is True
    assert preview["counts"] == {"tax_lots": 0, "holdings": 1, "activities": 1}
    holding, activity = preview["records"]
    assert holding == {
        "kind": "holding",
        "account_id": "vestwell-401k",
        "security_id": "vestwell:TDF2065",
        "symbol": "TDF2065",
        "description": "Target Date 2065",
        "quantity": 25.0,
        "institution_value": 2500.0,
        "cost_basis": None,
        "cost_basis_status": "unknown",
        "effective_date": "2026-09-20",
        "currency": "USD",
        "iso_currency_code": "USD",
        "source": "vestwell_csv",
    }
    assert activity == {
        "kind": "investment_activity",
        "account_id": "vestwell-401k",
        "symbol": "TDF2065",
        "description": "Employee contribution",
        "date": "2026-09-15",
        "effective_date": "2026-09-15",
        "type": "cash",
        "subtype": "deposit",
        "amount": 350.0,
        "currency": "USD",
        "iso_currency_code": "USD",
        "source": "vestwell_csv",
    }


def test_vestwell_fallback_import_is_repeat_safe_and_visible_in_positions(
    running_service: str,
):
    content = """Record Type,Account ID,Symbol,Description,Quantity,Market Value,Cost Basis,Effective Date,Activity Type,Amount
HOLDING,vestwell-401k,TDF2065,Target Date 2065,25,2500,,2026-09-20,,
ACTIVITY,vestwell-401k,TDF2065,Employee contribution,,,,2026-09-15,deposit,350
"""
    import_request = lambda: _request(
        f"{running_service}/api/private/investments/imports/vestwell/commit",
        {"content": content},
        method="POST",
    )

    with urllib.request.urlopen(import_request()) as response:
        first = json.load(response)
    with urllib.request.urlopen(import_request()) as response:
        repeated = json.load(response)

    assert first["result"] == {"created": 2, "unchanged": 0}
    assert repeated["result"] == {"created": 0, "unchanged": 2}

    with urllib.request.urlopen(
        _request(f"{running_service}/api/private/investments/positions")
    ) as response:
        positions = json.load(response)

    assert len(positions["holdings"]) == 1
    assert positions["holdings"][0]["ticker_symbol"] == "TDF2065"
    assert positions["holdings"][0]["cost_basis"] is None
    assert positions["holdings"][0]["cost_basis_status"] == "unknown"
    assert positions["holdings"][0]["source"] == "vestwell_csv"
    assert positions["holdings"][0]["effective_date"] == "2026-09-20"
    assert len(positions["activities"]) == 1
    assert positions["activities"][0]["source"] == "vestwell_csv"
    assert positions["activities"][0]["effective_date"] == "2026-09-15"


def test_preview_rejects_duplicate_logical_rows_before_import(running_service: str):
    content = """Account Number,Symbol,Description,Quantity,Cost Basis Total,Date Acquired,As Of Date
account-brokerage,BOND,Example Bond Fund,4,320.00,2024-02-01,2026-09-20
account-brokerage,BOND,Example Bond Fund,4,320.00,2024-02-01,2026-09-20
"""

    with pytest.raises(urllib.error.HTTPError) as rejected:
        urllib.request.urlopen(
            _request(
                f"{running_service}/api/private/investments/imports/fidelity/preview",
                {"content": content},
                method="POST",
            )
        )

    assert rejected.value.code == 422
    assert json.load(rejected.value)["detail"] == "duplicate logical record at row 3"


def test_plaid_holding_wins_when_import_is_equally_current_and_complete(
    running_service: str,
):
    with urllib.request.urlopen(
        _request(
            f"{running_service}/api/private/connections/plaid/exchange",
            {
                "public_token": "public-sandbox-investments",
                "connection_type": "investment",
                "display_name": "Sandbox brokerage",
                "institution_id": "ins_investments",
                "institution_name": "Sandbox Investments",
            },
            method="POST",
            headers={"X-Auth-Time": str(time.time())},
        )
    ):
        pass
    with urllib.request.urlopen(
        _request(
            f"{running_service}/api/internal/nightly-reconcile",
            method="POST",
            headers={"X-Internal-Key": "test-internal-key"},
        )
    ):
        pass

    today = date.today().isoformat()
    content = f"""Record Type,Account ID,Symbol,Description,Quantity,Market Value,Cost Basis,Effective Date,Activity Type,Amount
HOLDING,account-brokerage,TOTAL,Imported total market fund,10,1200,900,{today},,
"""
    with urllib.request.urlopen(
        _request(
            f"{running_service}/api/private/investments/imports/vestwell/commit",
            {"content": content},
            method="POST",
        )
    ):
        pass

    with urllib.request.urlopen(
        _request(f"{running_service}/api/private/investments/positions")
    ) as response:
        positions = json.load(response)

    total_market = [
        holding
        for holding in positions["holdings"]
        if holding["ticker_symbol"] == "TOTAL"
    ]
    assert len(total_market) == 1
    assert total_market[0]["source"] == "plaid_cached"
    assert total_market[0]["quantity"] == 10
    assert total_market[0]["cost_basis"] == 900


def test_fidelity_lots_enrich_missing_basis_only_when_every_lot_is_known(
    running_service: str,
):
    with urllib.request.urlopen(
        _request(
            f"{running_service}/api/private/connections/plaid/exchange",
            {
                "public_token": "public-sandbox-investments",
                "connection_type": "investment",
                "display_name": "Sandbox brokerage",
                "institution_id": "ins_investments",
                "institution_name": "Sandbox Investments",
            },
            method="POST",
            headers={"X-Auth-Time": str(time.time())},
        )
    ):
        pass
    with urllib.request.urlopen(
        _request(
            f"{running_service}/api/internal/nightly-reconcile",
            method="POST",
            headers={"X-Internal-Key": "test-internal-key"},
        )
    ):
        pass

    incomplete = """Account Number,Symbol,Description,Quantity,Cost Basis Total,Date Acquired,As Of Date
account-brokerage,BOND,Example Bond Fund,4,320,2024-02-01,2026-09-20
account-brokerage,BOND,Example Bond Fund,3,,2024-05-15,2026-09-20
"""
    with urllib.request.urlopen(
        _request(
            f"{running_service}/api/private/investments/imports/fidelity/commit",
            {"content": incomplete},
            method="POST",
        )
    ):
        pass
    with urllib.request.urlopen(
        _request(f"{running_service}/api/private/investments/positions")
    ) as response:
        positions = json.load(response)
    bond = next(item for item in positions["holdings"] if item["ticker_symbol"] == "BOND")
    assert bond["cost_basis"] is None
    assert bond["cost_basis_status"] == "unknown"
    assert bond["cost_basis_lot_coverage"] == {"known": 1, "total": 2}

    complete = incomplete.replace(
        "3,,2024-05-15", "3,180,2024-05-15"
    )
    with urllib.request.urlopen(
        _request(
            f"{running_service}/api/private/investments/imports/fidelity/commit",
            {"content": complete},
            method="POST",
        )
    ):
        pass
    with urllib.request.urlopen(
        _request(f"{running_service}/api/private/investments/positions")
    ) as response:
        positions = json.load(response)
    bond = next(item for item in positions["holdings"] if item["ticker_symbol"] == "BOND")
    assert bond["cost_basis"] == 500
    assert bond["cost_basis_status"] == "imported"
    assert bond["cost_basis_source"] == "fidelity_csv"
    assert bond["cost_basis_as_of"] == "2026-09-20"
    assert bond["cost_basis_lot_coverage"] == {"known": 2, "total": 2}


def test_imported_activity_overlapping_plaid_is_not_double_counted(
    running_service: str,
):
    with urllib.request.urlopen(
        _request(
            f"{running_service}/api/private/connections/plaid/exchange",
            {
                "public_token": "public-sandbox-investments",
                "connection_type": "investment",
                "display_name": "Sandbox brokerage",
                "institution_id": "ins_investments",
                "institution_name": "Sandbox Investments",
            },
            method="POST",
            headers={"X-Auth-Time": str(time.time())},
        )
    ):
        pass
    with urllib.request.urlopen(
        _request(
            f"{running_service}/api/internal/nightly-reconcile",
            method="POST",
            headers={"X-Internal-Key": "test-internal-key"},
        )
    ):
        pass

    duplicate = """Record Type,Account ID,Symbol,Description,Quantity,Market Value,Cost Basis,Effective Date,Activity Type,Amount
ACTIVITY,account-brokerage,TOTAL,Employee contribution,,,,2025-06-30,deposit,500
"""
    with urllib.request.urlopen(
        _request(
            f"{running_service}/api/private/investments/imports/vestwell/commit",
            {"content": duplicate},
            method="POST",
        )
    ):
        pass

    with urllib.request.urlopen(
        _request(
            f"{running_service}/api/private/investments/performance"
            "?start=2025-01-01&end=2025-12-31&benchmark=VTI"
        )
    ) as response:
        performance = json.load(response)

    assert performance["attribution"]["contributions"] == 500
