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


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _request(
    url: str,
    payload: dict | None = None,
    *,
    method: str = "GET",
    fresh: bool = False,
) -> urllib.request.Request:
    headers = {
        "Content-Type": "application/json",
        "X-Forwarded-User": "owner",
        "X-Auth-Method": "webauthn",
    }
    if fresh:
        headers["X-Auth-Time"] = str(time.time())
    return urllib.request.Request(
        url,
        data=None if payload is None else json.dumps(payload).encode(),
        headers=headers,
        method=method,
    )


@pytest.fixture
def payroll_service(tmp_path: Path, request: pytest.FixtureRequest):
    port = _unused_port()
    environment = os.environ.copy()
    environment.update(
        {
            "FINANCE_DATA_DIR": str(tmp_path),
            "FINANCE_ENCRYPTION_KEY": urlsafe_b64encode(b"p" * 32).decode(),
            "PLAID_MODE": "fake",
            "PAYROLL_ENVIRONMENT": "sandbox",
        }
    )
    environment.update(getattr(request, "param", {}))
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


def test_payroll_uses_separate_sandbox_link_and_reports_provider_coverage(
    payroll_service: str,
):
    provider = urllib.parse.quote("QuickBooks Workforce")
    with urllib.request.urlopen(
        _request(f"{payroll_service}/api/private/payroll/coverage?provider_name={provider}")
    ) as response:
        coverage = json.load(response)

    assert coverage == {
        "provider_name": "QuickBooks Workforce",
        "support": "supported",
        "environment": "sandbox",
        "checked_via": "plaid_adapter",
        "fallback": "cashflow_estimate",
    }

    with urllib.request.urlopen(
        _request(
            f"{payroll_service}/api/private/payroll/link-token",
            {"provider_name": "QuickBooks Workforce"},
            method="POST",
            fresh=True,
        )
    ) as response:
        link = json.load(response)

    assert response.status == 200
    assert link["environment"] == "sandbox"
    assert link["products"] == ["income_verification"]
    assert link["income_source_types"] == ["payroll"]
    assert link["flow_types"] == ["payroll_digital_income"]
    assert link["link_token"].startswith("payroll-link-sandbox-")


@pytest.mark.parametrize(
    "payroll_service",
    [
        {
            "PAYROLL_ENVIRONMENT": "production",
            "PAYROLL_INITIAL_PRICE_USD": "2.00",
            "PAYROLL_REFRESH_PRICE_USD": "0.50",
        }
    ],
    indirect=True,
)
def test_live_payroll_requires_price_confirmation_and_enforces_spending_cap(
    payroll_service: str,
):
    with urllib.request.urlopen(
        _request(f"{payroll_service}/api/private/payroll/terms")
    ) as response:
        terms = json.load(response)

    assert terms["pricing_status"] == "configured"
    assert terms["initial_verification"] == {
        "billing_model": "one_time",
        "unit_price_usd": 2.0,
    }
    assert terms["refresh"] == {
        "billing_model": "per_request",
        "unit_price_usd": 0.5,
    }

    link_request = _request(
        f"{payroll_service}/api/private/payroll/link-token",
        {"provider_name": "QuickBooks Workforce"},
        method="POST",
        fresh=True,
    )
    with pytest.raises(urllib.error.HTTPError) as blocked:
        urllib.request.urlopen(link_request)
    assert blocked.value.code == 409

    with urllib.request.urlopen(
        _request(
            f"{payroll_service}/api/private/payroll/approval",
            {
                "terms_id": terms["terms_id"],
                "confirmed": True,
                "monthly_spending_cap_usd": 2.5,
            },
            method="POST",
            fresh=True,
        )
    ) as response:
        approval = json.load(response)

    assert approval["approved"] is True
    assert approval["monthly_spending_cap_usd"] == 2.5
    assert approval["spent_this_month_usd"] == 0.0

    with urllib.request.urlopen(link_request) as response:
        assert json.load(response)["environment"] == "production"

    with urllib.request.urlopen(
        _request(
            f"{payroll_service}/api/private/payroll/ingest",
            method="POST",
            fresh=True,
        )
    ) as response:
        assert json.load(response)["records_ingested"] == 1

    with pytest.raises(urllib.error.HTTPError) as capped:
        urllib.request.urlopen(
            _request(
                f"{payroll_service}/api/private/payroll/ingest",
                method="POST",
                fresh=True,
            )
        )
    assert capped.value.code == 429
    assert "spending cap" in json.load(capped.value)["detail"]


def test_payroll_ingestion_separates_paystub_amounts_and_exposes_freshness(
    payroll_service: str,
):
    with urllib.request.urlopen(
        _request(
            f"{payroll_service}/api/private/payroll/link-token",
            {"provider_name": "QuickBooks Workforce"},
            method="POST",
            fresh=True,
        )
    ):
        pass

    with urllib.request.urlopen(
        _request(
            f"{payroll_service}/api/private/payroll/ingest",
            method="POST",
        )
    ) as response:
        ingested = json.load(response)

    assert ingested["records_ingested"] == 1
    assert ingested["status"] == "fresh"

    with urllib.request.urlopen(
        _request(f"{payroll_service}/api/private/payroll/income")
    ) as response:
        income = json.load(response)

    assert income["status"] == "fresh"
    assert income["source"] == "plaid_payroll"
    assert income["provenance"] == {
        "provider": "Plaid Payroll Income",
        "provider_name": "QuickBooks Workforce",
        "record_type": "paystub",
    }
    assert income["confidence"] == {"level": "high", "score": 1.0}
    assert income["currency"] == "USD"
    assert income["latest_paystub"]["pay_date"] == "2026-09-15"
    assert income["latest_paystub"]["gross_pay"] == 5000.0
    assert income["latest_paystub"]["net_pay"] == 3500.0
    assert income["latest_paystub"]["withholding_total"] == 1200.0
    assert income["latest_paystub"]["deduction_total"] == 300.0
    assert income["latest_paystub"]["withholdings"] == [
        {"description": "Federal income tax", "amount": 900.0},
        {"description": "Social Security", "amount": 300.0},
    ]
    assert income["latest_paystub"]["deductions"] == [
        {"description": "401(k)", "amount": 300.0}
    ]
    assert income["retrieved_at"]
    assert income["age_seconds"] >= 0


def test_income_falls_back_to_cashflow_and_accepts_a_confirmed_manual_profile(
    payroll_service: str,
):
    with urllib.request.urlopen(
        _request(
            f"{payroll_service}/api/private/connections/plaid/exchange",
            {
                "connection_type": "banking",
                "display_name": "Checking",
                "public_token": "public-sandbox-income-fallback",
                "institution_id": "ins_test",
                "institution_name": "Sandbox Bank",
            },
            method="POST",
            fresh=True,
        )
    ) as response:
        connection = json.load(response)

    with urllib.request.urlopen(
        _request(
            f"{payroll_service}/api/private/connections/{connection['id']}/refresh",
            method="POST",
        )
    ):
        pass

    with urllib.request.urlopen(
        _request(f"{payroll_service}/api/private/payroll/income")
    ) as response:
        estimated = json.load(response)

    assert estimated["status"] == "estimated"
    assert estimated["source"] == "transaction_cashflow"
    assert estimated["confidence"] == {"level": "medium", "score": 0.55}
    assert estimated["currency"] == "USD"
    assert estimated["estimate"] == {
        "observed_net_pay": 2000.0,
        "gross_pay": None,
        "withholding_total": None,
        "deduction_total": None,
        "paycheck_count": 1,
    }
    assert estimated["provenance"]["method"] == "observed_paycheck_deposits"
    assert estimated["limitations"] == [
        "gross pay, taxes, deductions, and benefits are unavailable from cashflow"
    ]
    assert estimated["freshness"]["status"] == "fresh"

    with urllib.request.urlopen(
        _request(
            f"{payroll_service}/api/private/payroll/manual",
            {
                "effective_date": "2026-09-15",
                "pay_frequency": "biweekly",
                "gross_pay": 5000,
                "net_pay": 3500,
                "withholding_total": 1200,
                "deduction_total": 300,
            },
            method="POST",
            fresh=True,
        )
    ) as response:
        assert json.load(response)["saved"] is True

    with urllib.request.urlopen(
        _request(f"{payroll_service}/api/private/payroll/income")
    ) as response:
        manual = json.load(response)

    assert manual["status"] == "configured"
    assert manual["source"] == "manual"
    assert manual["confidence"] == {"level": "high", "score": 1.0}
    assert manual["latest_paystub"] == {
        "pay_date": "2026-09-15",
        "pay_frequency": "biweekly",
        "gross_pay": 5000.0,
        "net_pay": 3500.0,
        "withholding_total": 1200.0,
        "deduction_total": 300.0,
        "withholdings": [],
        "deductions": [],
    }


@pytest.mark.parametrize(
    "payroll_service",
    [{"PAYROLL_FAKE_ERROR": "1"}],
    indirect=True,
)
def test_payroll_provider_failure_is_reported_as_an_error_state(
    payroll_service: str,
):
    with urllib.request.urlopen(
        _request(
            f"{payroll_service}/api/private/payroll/link-token",
            {"provider_name": "QuickBooks Workforce"},
            method="POST",
            fresh=True,
        )
    ):
        pass

    with pytest.raises(urllib.error.HTTPError) as failure:
        urllib.request.urlopen(
            _request(
                f"{payroll_service}/api/private/payroll/ingest",
                method="POST",
            )
        )
    assert failure.value.code == 502

    with urllib.request.urlopen(
        _request(f"{payroll_service}/api/private/payroll/income")
    ) as response:
        state = json.load(response)

    assert state["status"] == "error"
    assert state["source"] == "plaid_payroll"
    assert state["confidence"] == {"level": "unavailable", "score": 0.0}
    assert state["error"] == {
        "code": "INCOME_PROVIDER_UNAVAILABLE",
        "message": "payroll income temporarily unavailable",
    }
    assert state["retrieved_at"]
