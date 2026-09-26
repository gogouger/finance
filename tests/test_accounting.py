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

from backend.finance_app.accounting import build_accounting_view


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
def accounting_service(tmp_path: Path):
    port = _unused_port()
    environment = os.environ.copy()
    environment.update(
        {
            "FINANCE_DATA_DIR": str(tmp_path),
            "FINANCE_ENCRYPTION_KEY": urlsafe_b64encode(b"8" * 32).decode(),
            "PLAID_MODE": "fake",
            "FINANCE_INTERNAL_KEY": "accounting-internal-key",
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

        fresh_owner = {
            "X-Forwarded-User": "owner",
            "X-Auth-Method": "webauthn",
            "X-Auth-Time": str(time.time()),
        }
        with urllib.request.urlopen(
            _request(
                f"{base_url}/api/private/connections/plaid/exchange",
                {
                    "public_token": "public-sandbox-accounting",
                    "connection_type": "banking",
                    "display_name": "Accounting fixture",
                    "institution_id": "ins_accounting",
                    "institution_name": "Accounting Bank",
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
                headers={"X-Internal-Key": "accounting-internal-key"},
            )
        ):
            pass
        yield base_url
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


OWNER = {"X-Forwarded-User": "owner", "X-Auth-Method": "webauthn"}


def test_unmatched_credit_card_payment_and_credit_are_not_income():
    rows = [
        {
            "transaction_id": "payment",
            "account_id": "credit",
            "date": "2026-09-01",
            "name": "Payment received",
            "amount": -500,
            "pending": False,
            "personal_finance_category": {
                "primary": "LOAN_PAYMENTS",
                "detailed": "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
            },
        },
        {
            "transaction_id": "refund",
            "account_id": "credit",
            "date": "2026-09-02",
            "name": "Store credit",
            "amount": -25,
            "pending": False,
            "personal_finance_category": {
                "primary": "GENERAL_MERCHANDISE",
                "detailed": "GENERAL_MERCHANDISE_OTHER",
            },
        },
    ]

    result = build_accounting_view(rows, credit_account_ids={"credit"})
    by_id = {item["id"]: item for item in result["transactions"]}

    assert by_id["payment"]["accounting_type"] == "credit_card_payment"
    assert by_id["refund"]["accounting_type"] == "refund"
    assert result["metrics"]["income"] == 0
    assert result["metrics"]["cash_flow"] == {
        "depository_credits": 0,
        "depository_debits": 0,
        "net": 0,
        "refund_credits": 0,
        "excludes_credit_accounts": True,
    }
    assert result["metrics"]["credit_card_activity"] == {
        "payments": 500,
        "refund_credits": 25,
    }


def test_cross_source_overlap_prefers_plaid_without_collapsing_real_repeats():
    rows = [
        {
            "transaction_id": "csv-1",
            "account_id": "credit",
            "date": "2026-08-21",
            "name": "UNITED AIRLINES 016123",
            "amount": 601.5,
            "pending": False,
            "source": "capital_one_csv",
        },
        {
            "transaction_id": "csv-2",
            "account_id": "credit",
            "date": "2026-08-21",
            "name": "UNITED AIRLINES 016456",
            "amount": 601.5,
            "pending": False,
            "source": "capital_one_csv",
        },
        {
            "transaction_id": "plaid-1",
            "account_id": "credit",
            "date": "2026-08-21",
            "merchant_name": "United Airlines",
            "amount": 601.5,
            "pending": False,
        },
        {
            "transaction_id": "plaid-2",
            "account_id": "credit",
            "date": "2026-08-21",
            "merchant_name": "United Airlines",
            "amount": 601.5,
            "pending": False,
        },
        {
            "transaction_id": "unique-repeat",
            "account_id": "credit",
            "date": "2026-08-21",
            "merchant_name": "United Airlines",
            "amount": 601.5,
            "pending": False,
        },
    ]

    result = build_accounting_view(rows, credit_account_ids={"credit"})

    assert {item["id"] for item in result["transactions"]} == {
        "plaid-1",
        "plaid-2",
        "unique-repeat",
    }
    assert result["metrics"]["finalized_spending"]["raw"] == 1804.5
    assert result["metrics"]["deduplication"] == {
        "cross_source_records_excluded": 2,
        "cross_source_absolute_value_excluded": 1203,
        "preferred_source": "plaid",
        "raw_records_preserved": True,
    }


def _accounting(base_url: str) -> dict:
    with urllib.request.urlopen(
        _request(f"{base_url}/api/private/transactions/accounting", headers=OWNER)
    ) as response:
        assert response.status == 200
        return json.load(response)


def test_posted_transaction_replaces_pending_without_duplicate_spending(
    accounting_service: str,
):
    accounting = _accounting(accounting_service)

    grocery = [
        item
        for item in accounting["transactions"]
        if item["merchant_name"] == "Mountain Market"
    ]
    assert [(item["id"], item["status"]) for item in grocery] == [
        ("grocery-posted", "posted")
    ]
    assert accounting["metrics"]["finalized_spending"]["raw"] == 402
    assert accounting["metrics"]["provisional_spending"] == 0


def test_transfers_and_card_payments_stay_visible_without_becoming_spending(
    accounting_service: str,
):
    accounting = _accounting(accounting_service)
    activity_types = {
        item["id"]: item["accounting_type"] for item in accounting["transactions"]
    }

    assert activity_types["transfer-out"] == "internal_transfer"
    assert activity_types["transfer-in"] == "internal_transfer"
    assert activity_types["card-payment-out"] == "credit_card_payment"
    assert activity_types["card-payment-in"] == "credit_card_payment"
    assert accounting["metrics"]["income"] == 2000
    assert accounting["metrics"]["finalized_spending"]["raw"] == 402
    assert accounting["metrics"]["cash_flow"] == {
        "depository_credits": 2500,
        "depository_debits": 752,
        "net": 1748,
        "refund_credits": 0,
        "excludes_credit_accounts": True,
    }


def test_refund_corrects_original_category_but_keeps_receipt_cash_date(
    accounting_service: str,
):
    accounting = _accounting(accounting_service)
    refund = next(
        item for item in accounting["transactions"] if item["id"] == "store-refund"
    )

    assert refund["accounting_type"] == "refund"
    assert refund["cash_flow_date"] == "2026-09-23"
    assert refund["analytic_purchase_date"] == "2026-09-10"
    assert refund["category"]["primary"] == "GENERAL_MERCHANDISE"
    assert accounting["metrics"]["finalized_spending"]["net_of_refunds"] == 302
    assert accounting["metrics"]["spending_by_category"]["GENERAL_MERCHANDISE"] == 140


def test_owner_flags_change_adjusted_spending_without_changing_raw_cash_flow(
    accounting_service: str,
):
    original = _accounting(accounting_service)
    adjustments = {
        "reimbursable": {"reimbursable": True},
        "shared": {"shared": True, "personal_share_percent": 50},
        "business": {"business": True},
        "excluded": {"excluded": True},
    }
    for transaction_id, flags in adjustments.items():
        with urllib.request.urlopen(
            _request(
                f"{accounting_service}/api/private/transactions/{transaction_id}/adjustment",
                flags,
                method="PATCH",
                headers=OWNER,
            )
        ) as response:
            assert response.status == 200

    adjusted = _accounting(accounting_service)

    assert adjusted["metrics"]["finalized_spending"] == {
        "raw": 402,
        "net_of_refunds": 302,
        "adjusted": 82,
    }
    assert adjusted["metrics"]["cash_flow"] == original["metrics"]["cash_flow"]
    assert {
        item["id"]: item["adjustment"]
        for item in adjusted["transactions"]
        if item["id"] in adjustments
    } == {
        "reimbursable": {
            "reimbursable": True,
            "shared": False,
            "business": False,
            "excluded": False,
            "personal_share_percent": 100,
        },
        "shared": {
            "reimbursable": False,
            "shared": True,
            "business": False,
            "excluded": False,
            "personal_share_percent": 50,
        },
        "business": {
            "reimbursable": False,
            "shared": False,
            "business": True,
            "excluded": False,
            "personal_share_percent": 100,
        },
        "excluded": {
            "reimbursable": False,
            "shared": False,
            "business": False,
            "excluded": True,
            "personal_share_percent": 100,
        },
    }


def test_provider_corrections_preserve_immutable_source_versions(
    accounting_service: str,
):
    with urllib.request.urlopen(
        _request(
            f"{accounting_service}/api/internal/nightly-reconcile",
            method="POST",
            headers={"X-Internal-Key": "accounting-internal-key"},
        )
    ):
        pass

    accounting = _accounting(accounting_service)
    grocery = next(
        item
        for item in accounting["transactions"]
        if item["id"] == "grocery-posted"
    )

    assert grocery["amount"] == 53
    assert [
        version["provider_record"]["amount"]
        for version in grocery["provider_versions"]
    ] == [52, 53]
    assert grocery["provider_versions"][0]["provider_record"]["transaction_id"] == (
        "grocery-posted"
    )
