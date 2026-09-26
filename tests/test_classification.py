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


OWNER = {"X-Forwarded-User": "owner", "X-Auth-Method": "webauthn"}


@pytest.fixture
def classification_service(tmp_path: Path):
    port = _unused_port()
    environment = os.environ.copy()
    environment.update(
        {
            "FINANCE_DATA_DIR": str(tmp_path),
            "FINANCE_ENCRYPTION_KEY": urlsafe_b64encode(b"9" * 32).decode(),
            "PLAID_MODE": "fake",
            "FINANCE_INTERNAL_KEY": "classification-internal-key",
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
                    "public_token": "public-sandbox-accounting",
                    "connection_type": "banking",
                    "display_name": "Classification fixture",
                    "institution_id": "ins_classification",
                    "institution_name": "Classification Bank",
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
                headers={"X-Internal-Key": "classification-internal-key"},
            )
        ):
            pass
        yield base_url
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def _json(request: urllib.request.Request) -> dict:
    with urllib.request.urlopen(request) as response:
        assert response.status == 200
        return json.load(response)


def test_owner_can_assign_canonical_category_tags_and_exact_splits(
    classification_service: str,
):
    saved = _json(
        _request(
            f"{classification_service}/api/private/transactions/shared/classification",
            {
                "merchant_name": "Office & Home Supply",
                "category": {"primary": "household", "detailed": "supplies"},
                "tags": ["family", "reimbursable", "family"],
                "splits": [
                    {
                        "amount": 45,
                        "category": {"primary": "household", "detailed": "supplies"},
                        "tags": ["family"],
                    },
                    {
                        "amount": 15,
                        "category": {"primary": "work", "detailed": "supplies"},
                        "tags": ["reimbursable"],
                    },
                ],
            },
            method="PUT",
            headers=OWNER,
        )
    )

    assert saved == {
        "transaction_id": "shared",
        "merchant_name": "Office & Home Supply",
        "category": {"primary": "household", "detailed": "supplies"},
        "tags": ["family", "reimbursable"],
        "splits": [
            {
                "amount": 45.0,
                "category": {"primary": "household", "detailed": "supplies"},
                "tags": ["family"],
            },
            {
                "amount": 15.0,
                "category": {"primary": "work", "detailed": "supplies"},
                "tags": ["reimbursable"],
            },
        ],
        "confidence": 1.0,
        "provenance": "owner",
        "explanation": "Classified directly by the owner.",
        "needs_review": False,
    }

    listing = _json(
        _request(
            f"{classification_service}/api/private/transactions/classifications",
            headers=OWNER,
        )
    )
    assert next(
        item for item in listing["transactions"] if item["transaction_id"] == "shared"
    )["classification"] == saved


def test_splits_must_reconcile_to_source_amount(classification_service: str):
    request = _request(
        f"{classification_service}/api/private/transactions/shared/classification",
        {
            "category": {"primary": "household", "detailed": "supplies"},
            "splits": [
                {
                    "amount": 59.99,
                    "category": {"primary": "household", "detailed": "supplies"},
                }
            ],
        },
        method="PUT",
        headers=OWNER,
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)

    assert error.value.code == 422
    assert "must equal the source amount 60.00" in error.value.read().decode()


def test_rule_preview_and_historical_application_explain_deterministic_matches(
    classification_service: str,
):
    rule = {
        "name": "Mountain Market is groceries",
        "priority": 100,
        "match": {"merchant_contains": "mountain market"},
        "assignment": {
            "merchant_name": "Mountain Market",
            "category": {"primary": "food", "detailed": "groceries"},
            "tags": ["household"],
        },
    }
    preview = _json(
        _request(
            f"{classification_service}/api/private/classification/rules/preview",
            rule,
            method="POST",
            headers=OWNER,
        )
    )

    assert preview["effect_count"] == 2
    assert preview["conflicts"] == []
    assert {effect["transaction_id"] for effect in preview["effects"]} == {
        "grocery-pending",
        "grocery-posted",
    }
    assert all(
        effect["explanation"]
        == "Matched merchant containing 'mountain market' at priority 100."
        for effect in preview["effects"]
    )

    created = _json(
        _request(
            f"{classification_service}/api/private/classification/rules",
            {**rule, "apply_historical": True},
            method="POST",
            headers=OWNER,
        )
    )
    assert created["historical_effect_count"] == 2
    assert created["rule"]["order"] == 1

    listing = _json(
        _request(
            f"{classification_service}/api/private/transactions/classifications",
            headers=OWNER,
        )
    )
    grocery = next(
        item
        for item in listing["transactions"]
        if item["transaction_id"] == "grocery-posted"
    )["classification"]
    assert grocery["category"] == {"primary": "food", "detailed": "groceries"}
    assert grocery["confidence"] == 0.98
    assert grocery["provenance"] == f"rule:{created['rule']['id']}"
    assert grocery["needs_review"] is False


def test_equal_priority_rule_conflicts_are_not_silently_applied(
    classification_service: str,
):
    first_rule = {
        "name": "Trail Store outdoors",
        "priority": 50,
        "match": {"merchant_contains": "trail store"},
        "assignment": {
            "category": {"primary": "recreation", "detailed": "outdoors"},
            "tags": ["gear"],
        },
        "apply_historical": True,
    }
    _json(
        _request(
            f"{classification_service}/api/private/classification/rules",
            first_rule,
            method="POST",
            headers=OWNER,
        )
    )

    conflicting = _json(
        _request(
            f"{classification_service}/api/private/classification/rules/preview",
            {
                "name": "Trail Store clothing",
                "priority": 50,
                "match": {"merchant_contains": "trail store"},
                "assignment": {
                    "category": {"primary": "shopping", "detailed": "clothing"},
                    "tags": [],
                },
            },
            method="POST",
            headers=OWNER,
        )
    )

    assert conflicting["effect_count"] == 0
    assert {item["transaction_id"] for item in conflicting["conflicts"]} == {
        "store-purchase",
        "store-refund",
    }
    assert all(len(item["competing_rules"]) == 2 for item in conflicting["conflicts"])


def test_review_queue_contains_only_low_confidence_conflicts_and_unapplied_ai_suggestions(
    classification_service: str,
):
    common = {
        "priority": 50,
        "match": {"merchant_contains": "trail store"},
        "apply_historical": True,
    }
    for name, primary in (("Outdoors", "recreation"), ("Clothes", "shopping")):
        _json(
            _request(
                f"{classification_service}/api/private/classification/rules",
                {
                    **common,
                    "name": name,
                    "assignment": {
                        "category": {"primary": primary, "detailed": "gear"},
                        "tags": [],
                    },
                },
                method="POST",
                headers=OWNER,
            )
        )

    suggestion = _json(
        _request(
            f"{classification_service}/api/private/classification/suggestions",
            {
                "transaction_id": "business",
                "category": {"primary": "work", "detailed": "software"},
                "tags": ["deductible"],
                "confidence": 0.91,
                "reason": "The merchant resembles a software vendor.",
            },
            method="POST",
            headers=OWNER,
        )
    )
    assert suggestion["applied"] is False
    assert suggestion["provenance"] == "ai_suggestion"

    queue = _json(
        _request(
            f"{classification_service}/api/private/classification/review-queue",
            headers=OWNER,
        )
    )
    by_id = {item["transaction_id"]: item for item in queue["items"]}
    assert by_id["store-purchase"]["reason"] == "rule_conflict"
    assert by_id["store-refund"]["reason"] == "rule_conflict"
    assert by_id["business"]["reason"] == "ai_suggestion"
    assert by_id["business"]["suggestion"]["applied"] is False
    assert by_id["paycheck"]["reason"] == "low_confidence"
    assert all(item["needs_review"] for item in queue["items"])

    listing = _json(
        _request(
            f"{classification_service}/api/private/transactions/classifications",
            headers=OWNER,
        )
    )
    business = next(
        item
        for item in listing["transactions"]
        if item["transaction_id"] == "business"
    )["classification"]
    assert business["provenance"] == "provider"
    assert business["category"] != suggestion["category"]


def test_owner_must_explicitly_accept_ai_suggestion_before_it_changes_a_transaction(
    classification_service: str,
):
    suggestion = _json(
        _request(
            f"{classification_service}/api/private/classification/suggestions",
            {
                "transaction_id": "business",
                "category": {"primary": "work", "detailed": "software"},
                "tags": ["deductible"],
                "confidence": 0.91,
                "reason": "The merchant resembles a software vendor.",
            },
            method="POST",
            headers=OWNER,
        )
    )

    accepted = _json(
        _request(
            f"{classification_service}/api/private/classification/suggestions/{suggestion['id']}/decision",
            {"decision": "accept"},
            method="POST",
            headers=OWNER,
        )
    )
    assert accepted["status"] == "accepted"
    assert accepted["applied"] is True

    listing = _json(
        _request(
            f"{classification_service}/api/private/transactions/classifications",
            headers=OWNER,
        )
    )
    business = next(
        item
        for item in listing["transactions"]
        if item["transaction_id"] == "business"
    )["classification"]
    assert business["category"] == {"primary": "work", "detailed": "software"}
    assert business["tags"] == ["deductible"]
    assert business["provenance"] == "owner"
    assert business["explanation"] == "Owner accepted AI suggestion."
