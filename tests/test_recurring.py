import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
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
        assert response.status == 200
        return json.load(response)


@pytest.fixture
def recurring_service(tmp_path: Path):
    port = _unused_port()
    environment = os.environ.copy()
    environment.update(
        {
            "FINANCE_DATA_DIR": str(tmp_path),
            "FINANCE_ENCRYPTION_KEY": urlsafe_b64encode(b"r" * 32).decode(),
            "PLAID_MODE": "fake",
            "FINANCE_INTERNAL_KEY": "recurring-internal-key",
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
                    "public_token": "public-sandbox-recurring",
                    "connection_type": "banking",
                    "display_name": "Recurring fixture",
                    "institution_id": "ins_recurring",
                    "institution_name": "Recurring Bank",
                },
                method="POST",
                headers=fresh_owner,
            )
        )
        _json(
            _request(
                f"{base_url}/api/internal/nightly-reconcile",
                method="POST",
                headers={"X-Internal-Key": "recurring-internal-key"},
            )
        )
        yield base_url
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def _costs(base_url: str, as_of: str = "2026-10-20") -> dict:
    query = urllib.parse.urlencode({"as_of": as_of})
    return _json(
        _request(
            f"{base_url}/api/private/recurring/costs?{query}", headers=OWNER
        )
    )


def test_likely_recurring_costs_explain_cadence_merchant_and_amount_evidence(
    recurring_service: str,
):
    costs = _costs(recurring_service)

    by_merchant = {item["merchant_name"]: item for item in costs["proposals"]}
    stream = by_merchant["StreamFlix"]
    assert stream["cadence"] == "monthly"
    assert stream["estimated_amount"] == 15
    assert stream["status"] == "proposed"
    assert stream["affects_forecast"] is False
    assert stream["evidence"] == {
        "occurrence_count": 4,
        "transaction_ids": ["stream-jun", "stream-jul", "stream-aug", "stream-sep"],
        "observed_dates": ["2026-06-05", "2026-07-05", "2026-08-05", "2026-09-05"],
        "median_interval_days": 31,
        "amount_range": {"minimum": 15, "maximum": 15},
    }
    assert stream["confidence"] == 0.99
    assert stream["explanation"] == (
        "4 charges from StreamFlix recur about every 31 days with stable amounts."
    )
    assert "Neighborhood Hardware" not in by_merchant
    assert costs["metrics"] == {
        "currency": "USD",
        "confirmed_cost_count": 0,
        "true_monthly_cost": 0,
        "true_annual_cost": 0,
    }


def test_proposal_changes_forecast_only_after_owner_confirms_it(
    recurring_service: str,
):
    before = _costs(recurring_service)
    proposal = next(
        item for item in before["proposals"] if item["merchant_name"] == "StreamFlix"
    )
    assert before["metrics"]["true_monthly_cost"] == 0

    confirmed = _json(
        _request(
            f"{recurring_service}/api/private/recurring/proposals/{proposal['id']}/confirm",
            {},
            method="POST",
            headers=OWNER,
        )
    )

    assert confirmed["status"] == "confirmed"
    assert confirmed["merchant_name"] == "StreamFlix"
    assert confirmed["cadence"] == "monthly"
    assert confirmed["amount"] == 15
    assert confirmed["affects_forecast"] is True
    assert confirmed["provenance"] == {
        "source": "detected_proposal",
        "proposal_id": proposal["id"],
        "source_transaction_ids": [
            "stream-jun",
            "stream-jul",
            "stream-aug",
            "stream-sep",
        ],
    }

    after = _costs(recurring_service)
    assert proposal["id"] not in {item["id"] for item in after["proposals"]}
    assert after["metrics"] == {
        "currency": "USD",
        "confirmed_cost_count": 1,
        "true_monthly_cost": 15,
        "true_annual_cost": 180,
    }


def test_quarterly_and_annual_costs_are_normalized_and_added_to_calendar(
    recurring_service: str,
):
    proposals = {
        item["merchant_name"]: item for item in _costs(recurring_service)["proposals"]
    }
    for merchant_name in ("Douglas County Treasurer", "Home Shield Insurance"):
        _json(
            _request(
                f"{recurring_service}/api/private/recurring/proposals/{proposals[merchant_name]['id']}/confirm",
                {},
                method="POST",
                headers=OWNER,
            )
        )

    costs = _costs(recurring_service)

    assert costs["metrics"] == {
        "currency": "USD",
        "confirmed_cost_count": 2,
        "true_monthly_cost": 500,
        "true_annual_cost": 6000,
    }
    normalized = {
        item["merchant_name"]: item["normalized_cost"] for item in costs["confirmed"]
    }
    assert normalized == {
        "Douglas County Treasurer": {"monthly": 400, "annual": 4800},
        "Home Shield Insurance": {"monthly": 100, "annual": 1200},
    }
    assert costs["upcoming_costs"] == [
        {
            "date": "2026-11-01",
            "obligation_id": proposals["Home Shield Insurance"]["id"],
            "merchant_name": "Home Shield Insurance",
            "amount": 1200,
            "cadence": "annual",
        },
        {
            "date": "2027-01-15",
            "obligation_id": proposals["Douglas County Treasurer"]["id"],
            "merchant_name": "Douglas County Treasurer",
            "amount": 1200,
            "cadence": "quarterly",
        },
    ]


def test_owner_edits_keep_detection_provenance_and_append_source_history(
    recurring_service: str,
):
    proposal = next(
        item
        for item in _costs(recurring_service)["proposals"]
        if item["merchant_name"] == "Douglas County Treasurer"
    )
    original = _json(
        _request(
            f"{recurring_service}/api/private/recurring/proposals/{proposal['id']}/confirm",
            {},
            method="POST",
            headers=OWNER,
        )
    )

    edited = _json(
        _request(
            f"{recurring_service}/api/private/recurring/obligations/{proposal['id']}",
            {
                "merchant_name": "County Property Tax",
                "amount": 1300,
                "cadence": "annual",
                "reason": "The county changed the billing schedule.",
            },
            method="PATCH",
            headers=OWNER,
        )
    )

    assert edited["revision"] == 2
    assert edited["merchant_name"] == "County Property Tax"
    assert edited["amount"] == 1300
    assert edited["cadence"] == "annual"
    assert edited["provenance"] == original["provenance"]
    assert edited["detection_evidence"] == original["detection_evidence"]
    assert len(edited["edit_history"]) == 1
    assert edited["edit_history"][0]["actor"] == "owner"
    assert edited["edit_history"][0]["reason"] == (
        "The county changed the billing schedule."
    )
    assert edited["edit_history"][0]["changes"] == {
        "amount": {"before": 1200, "after": 1300},
        "cadence": {"before": "quarterly", "after": "annual"},
        "merchant_name": {
            "before": "Douglas County Treasurer",
            "after": "County Property Tax",
        },
    }

    costs = _costs(recurring_service)
    assert costs["metrics"]["true_annual_cost"] == 1300
    assert costs["metrics"]["true_monthly_cost"] == 108.33
