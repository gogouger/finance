import csv
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from base64 import urlsafe_b64encode
from io import StringIO
from pathlib import Path

import pytest


OWNER = {"X-Forwarded-User": "owner", "X-Auth-Method": "webauthn"}


def _fresh_owner() -> dict[str, str]:
    return {**OWNER, "X-Auth-Time": str(time.time())}


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
        return json.load(response)


@pytest.fixture
def lifecycle_service(tmp_path: Path):
    port = _unused_port()
    environment = os.environ.copy()
    environment.update(
        {
            "FINANCE_DATA_DIR": str(tmp_path),
            "FINANCE_ENCRYPTION_KEY": urlsafe_b64encode(b"l" * 32).decode(),
            "PLAID_MODE": "fake",
            "FINANCE_INTERNAL_KEY": "lifecycle-internal-key",
            "MAIL_MODE": "fake",
            "FINANCE_MAIL_FROM": "finance@example.test",
            "FINANCE_REVIEW_EMAIL": "owner@example.test",
            "FINANCE_MAIL_WEBHOOK_KEY": "lifecycle-mail-webhook-key",
            "FINANCE_BASE_URL": f"http://127.0.0.1:{port}",
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
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.05)
        else:
            pytest.fail("service did not become healthy")

        connection = _json(
            _request(
                f"{base_url}/api/private/connections/plaid/exchange",
                {
                    "public_token": "public-sandbox-accounting",
                    "connection_type": "banking",
                    "display_name": "Lifecycle fixture",
                    "institution_id": "ins_lifecycle",
                    "institution_name": "Lifecycle Bank",
                },
                method="POST",
                headers=_fresh_owner(),
            )
        )
        _json(
            _request(
                f"{base_url}/api/internal/nightly-reconcile",
                method="POST",
                headers={"X-Internal-Key": "lifecycle-internal-key"},
            )
        )
        _json(
            _request(
                f"{base_url}/api/private/classification/rules",
                {
                    "name": "Grocery rule",
                    "priority": 10,
                    "match": {"merchant_contains": "mountain market"},
                    "assignment": {
                        "category": {"primary": "food", "detailed": "groceries"},
                        "tags": ["household"],
                    },
                    "apply_historical": True,
                },
                method="POST",
                headers=OWNER,
            )
        )
        scenario = _json(
            _request(
                f"{base_url}/api/private/scenarios",
                {
                    "name": "Portable housing scenario",
                    "calculator": "housing",
                    "inputs": {
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
                    },
                },
                method="POST",
                headers=OWNER,
            )
        )
        yield base_url, connection, scenario
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_json_export_is_portable_complete_and_secret_free(lifecycle_service):
    base_url, connection, scenario = lifecycle_service
    with pytest.raises(urllib.error.HTTPError) as stale:
        urllib.request.urlopen(
            _request(f"{base_url}/api/private/lifecycle/export.json", headers=OWNER)
        )
    assert stale.value.code == 403

    exported = _json(
        _request(
            f"{base_url}/api/private/lifecycle/export.json",
            headers=_fresh_owner(),
        )
    )

    assert exported["format"] == "finance-portable-records"
    assert exported["schema_version"] == 1
    assert exported["currency"] == "USD"
    assert exported["documentation"] == "docs/data-export.md"
    assert {record["collection"] for record in exported["records"]} >= {
        "connections",
        "normalized/accounts",
        "normalized/balances",
        "normalized/transactions",
        "classification_rules",
        "transaction_classifications",
        "scenarios",
        "audit_events",
    }
    records = {(item["collection"], item["source_id"]): item for item in exported["records"]}
    assert records[("connections", connection["id"])]["data"]["institution_id"] == "ins_lifecycle"
    assert records[("scenarios", scenario["id"])]["data"]["ruleset_version"]
    transaction = records[("normalized/transactions", "grocery-posted")]
    assert transaction["connection_id"] == connection["id"]
    assert transaction["data"]["transaction_id"] == "grocery-posted"

    serialized = json.dumps(exported).lower()
    for forbidden in (
        "access_token",
        "client_secret",
        "recovery_code",
        "recovery_password",
        "totp_secret",
        "private_key",
    ):
        assert forbidden not in serialized


def test_csv_round_trips_the_documented_portable_record_envelopes(
    lifecycle_service,
):
    base_url, _, _ = lifecycle_service
    json_export = _json(
        _request(
            f"{base_url}/api/private/lifecycle/export.json",
            headers=_fresh_owner(),
        )
    )
    with urllib.request.urlopen(
        _request(
            f"{base_url}/api/private/lifecycle/export.csv",
            headers=_fresh_owner(),
        )
    ) as response:
        assert response.headers["Content-Type"].startswith("text/csv")
        rows = list(csv.DictReader(StringIO(response.read().decode())))

    csv_records = {
        (row["collection"], row["source_id"]): json.loads(row["data_json"])
        for row in rows
    }
    json_records = {
        (record["collection"], record["source_id"]): record["data"]
        for record in json_export["records"]
    }
    assert csv_records == json_records
    assert {row["schema_version"] for row in rows} == {"1"}

    documentation = Path("docs/data-export.md").read_text()
    assert "finance-portable-records" in documentation
    assert "collection,source_id" in documentation
    assert "data_json" in documentation
    assert "access tokens" in documentation.lower()
    assert "email-note provenance" in documentation.lower()
    assert "named mcp client" in documentation.lower()


def test_export_includes_email_note_provenance_and_named_mcp_client_metadata(
    lifecycle_service,
):
    base_url, _, _ = lifecycle_service
    weekly = _json(
        _request(
            f"{base_url}/api/private/reviews/weekly/send",
            {},
            method="POST",
            headers=OWNER,
        )
    )
    assert weekly["sent"] is True
    token = weekly["review_url"].rsplit("/", 1)[-1]
    reply = _json(
        _request(
            f"{base_url}/api/public/email/replies",
            {
                "token": token,
                "sender": "owner@example.test",
                "provider_message_id": "provider-message-123",
                "spf": "pass",
                "dkim": "pass",
                "dmarc": "pass",
                "raw_message": "Costco was groceries.\n\n> quoted digest",
            },
            method="POST",
            headers={"X-Mail-Webhook-Key": "lifecycle-mail-webhook-key"},
        )
    )
    grant = _json(
        _request(
            f"{base_url}/api/private/mcp/grants",
            {
                "client_id": "codex-lifecycle-device",
                "client_name": "Codex lifecycle test device",
                "redirect_uri": "http://127.0.0.1:8765/oauth/callback",
                "scopes": ["finance:summary"],
                "code_challenge": "a" * 43,
                "code_challenge_method": "S256",
            },
            method="POST",
            headers=_fresh_owner(),
        )
    )

    exported = _json(
        _request(
            f"{base_url}/api/private/lifecycle/export.json",
            headers=_fresh_owner(),
        )
    )
    records = {
        (item["collection"], item["source_id"]): item["data"]
        for item in exported["records"]
    }

    email_note = records[("email_notes", reply["id"])]
    assert email_note["note"]["text"] == "Costco was groceries."
    assert email_note["provenance"]["provider_message_id"] == "provider-message-123"
    assert email_note["validation_evidence"]["dmarc"] == "pass"
    assert "raw_message" not in email_note

    mcp_grant = records[("mcp_grants", grant["grant_id"])]
    assert mcp_grant == {
        "id": grant["grant_id"],
        "client_id": "codex-lifecycle-device",
        "client_name": "Codex lifecycle test device",
        "redirect_uri": "http://127.0.0.1:8765/oauth/callback",
        "scopes": ["finance:summary"],
        "status": "active",
        "created_at": mcp_grant["created_at"],
    }
    serialized = json.dumps([email_note, mcp_grant]).lower()
    assert "authorization_code" not in serialized
    assert "access_token" not in serialized
    assert "refresh_token" not in serialized


def test_disconnect_revokes_provider_access_but_preserves_normalized_history(
    lifecycle_service,
):
    base_url, connection, _ = lifecycle_service
    disconnected = _json(
        _request(
            f"{base_url}/api/private/connections/{connection['id']}",
            method="DELETE",
            headers=_fresh_owner(),
        )
    )
    assert disconnected["status"] == "disconnected"
    assert disconnected["local_history_preserved"] is True

    accounting = _json(
        _request(
            f"{base_url}/api/private/transactions/accounting", headers=OWNER
        )
    )
    assert any(
        item["id"] == "grocery-posted" for item in accounting["transactions"]
    )

    exported = _json(
        _request(
            f"{base_url}/api/private/lifecycle/export.json",
            headers=_fresh_owner(),
        )
    )
    assert any(
        item["collection"] == "normalized/transactions"
        and item["source_id"] == "grocery-posted"
        for item in exported["records"]
    )


def test_permanent_erasure_requires_fresh_passkey_and_separate_confirmation(
    lifecycle_service,
):
    base_url, _, _ = lifecycle_service
    with pytest.raises(urllib.error.HTTPError) as stale_intent:
        urllib.request.urlopen(
            _request(
                f"{base_url}/api/private/lifecycle/erasure-intents",
                {},
                method="POST",
                headers=OWNER,
            )
        )
    assert stale_intent.value.code == 403

    intent = _json(
        _request(
            f"{base_url}/api/private/lifecycle/erasure-intents",
            {},
            method="POST",
            headers=_fresh_owner(),
        )
    )
    assert intent["required_confirmation"] == "PERMANENTLY ERASE MY FINANCE DATA"
    assert intent["expires_at"]

    with pytest.raises(urllib.error.HTTPError) as wrong_phrase:
        urllib.request.urlopen(
            _request(
                f"{base_url}/api/private/lifecycle/data",
                {
                    "confirmation_id": intent["confirmation_id"],
                    "confirmation": "erase it",
                },
                method="DELETE",
                headers=_fresh_owner(),
            )
        )
    assert wrong_phrase.value.code == 422

    with pytest.raises(urllib.error.HTTPError) as stale_erase:
        urllib.request.urlopen(
            _request(
                f"{base_url}/api/private/lifecycle/data",
                {
                    "confirmation_id": intent["confirmation_id"],
                    "confirmation": intent["required_confirmation"],
                },
                method="DELETE",
                headers=OWNER,
            )
        )
    assert stale_erase.value.code == 403

    erased = _json(
        _request(
            f"{base_url}/api/private/lifecycle/data",
            {
                "confirmation_id": intent["confirmation_id"],
                "confirmation": intent["required_confirmation"],
            },
            method="DELETE",
            headers=_fresh_owner(),
        )
    )
    assert erased == {
        "erased": True,
        "provider_access_revoked": True,
        "retained": ["installation metadata", "non-identifying service configuration"],
    }

    assert _json(
        _request(f"{base_url}/api/private/connections", headers=OWNER)
    ) == []
    assert _json(
        _request(f"{base_url}/api/private/scenarios", headers=OWNER)
    ) == []
    accounting = _json(
        _request(
            f"{base_url}/api/private/transactions/accounting", headers=OWNER
        )
    )
    assert accounting["transactions"] == []
    assert accounting["metrics"]["finalized_spending"] == {
        "raw": 0,
        "adjusted": 0,
        "net_of_refunds": 0,
    }
