import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from base64 import urlsafe_b64encode
from contextlib import contextmanager
from pathlib import Path


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
) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **OWNER},
        method=method,
    )


@contextmanager
def _service(tmp_path: Path, *, valuation_mode: str = "fake"):
    port = _unused_port()
    environment = os.environ.copy()
    environment.update(
        {
            "FINANCE_DATA_DIR": str(tmp_path),
            "FINANCE_ENCRYPTION_KEY": urlsafe_b64encode(b"v" * 32).decode(),
            "PLAID_MODE": "fake",
            "FINANCE_INTERNAL_KEY": "valuation-internal-key",
            "VALUATION_MODE": valuation_mode,
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
                raise AssertionError(f"service exited during startup\n{stdout}\n{stderr}")
            try:
                with urllib.request.urlopen(f"{base_url}/health", timeout=0.2):
                    break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.05)
        else:
            raise AssertionError("service did not become healthy")
        yield base_url
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def _create_home(base_url: str) -> dict:
    with urllib.request.urlopen(
        _request(
            f"{base_url}/api/private/assets",
            {
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
                    "valued_at": "2026-01-01T00:00:00Z",
                    "source_label": "owner estimate",
                },
            },
            method="POST",
        )
    ) as response:
        return json.load(response)


def test_refresh_records_conflicting_sourced_observations_without_overwriting_history(
    tmp_path: Path,
):
    with _service(tmp_path) as base_url:
        home = _create_home(base_url)
        refresh_url = f"{base_url}/api/private/assets/{home['id']}/valuations/refresh"
        with urllib.request.urlopen(_request(refresh_url, {}, method="POST")) as response:
            refreshed = json.load(response)

        assert response.status == 200
        assert refreshed["status"] == "refreshed"
        assert refreshed["cache"]["status"] == "miss"
        assert refreshed["terms"]["respected"] is True
        assert len(refreshed["estimates"]) == 2
        assert {item["source"]["id"] for item in refreshed["estimates"]} == {
            "test-douglas-county-assessor",
            "test-public-market",
        }
        assert {item["confidence"]["level"] for item in refreshed["estimates"]} == {
            "high",
            "medium",
        }
        assert all(item["observed_at"] for item in refreshed["estimates"])
        assert all(item["source"]["terms_url"] for item in refreshed["estimates"])
        assert all(
            item["freshness"]["status"] == "fresh"
            and item["freshness"]["fresh_until"]
            for item in refreshed["estimates"]
        )

        with urllib.request.urlopen(_request(refresh_url, {}, method="POST")) as response:
            cached = json.load(response)
        assert cached["status"] == "cached"
        assert cached["cache"]["status"] == "hit"
        assert cached["estimates"] == refreshed["estimates"]

        with urllib.request.urlopen(
            _request(f"{base_url}/api/private/assets", method="GET")
        ) as response:
            stored = json.load(response)["assets"][0]

        assert stored["valuation_history"][0] == home["valuation_history"][0]
        assert stored["valuation_observations"] == refreshed["estimates"]
        assert len(stored["valuation_observations"]) == 2


def test_unavailable_provider_keeps_manual_value_and_mcp_projection_is_private(
    tmp_path: Path,
):
    private_address = "123 Private Lane, Castle Rock, CO"
    private_parcel = "R0123456"
    with _service(tmp_path, valuation_mode="disabled") as base_url:
        home = _create_home(base_url)
        refresh_url = f"{base_url}/api/private/assets/{home['id']}/valuations/refresh"
        with urllib.request.urlopen(_request(refresh_url, {}, method="POST")) as response:
            unavailable = json.load(response)

        assert unavailable["status"] == "unavailable"
        assert unavailable["warning"] == (
            "no permitted automated valuation provider is configured"
        )
        assert unavailable["fallback"] == {
            "status": "manual_value_retained",
            "valuation": {
                "amount": 600000,
                "currency": "USD",
                "valued_at": "2026-01-01T00:00:00Z",
                "source_label": "owner estimate",
            },
            "freshness": "stale",
        }

        with urllib.request.urlopen(
            _request(f"{base_url}/api/private/assets/mcp-safe", method="GET")
        ) as response:
            projection = json.load(response)

        assert projection["privacy"] == {
            "identifiers_included": False,
            "source_urls_included": False,
        }
        assert projection["assets"][0]["name"] == "Primary residence"
        assert "identifiers" not in projection["assets"][0]
        serialized = json.dumps(projection)
        assert private_address not in serialized
        assert private_parcel not in serialized


def test_vehicle_refresh_is_source_labeled_and_identifiers_are_encrypted_at_rest(
    tmp_path: Path,
):
    private_vin = "1PRIVATE2345678901"
    private_plate = "PRIVATE"
    with _service(tmp_path) as base_url:
        with urllib.request.urlopen(
            _request(
                f"{base_url}/api/private/assets",
                {
                    "kind": "vehicle",
                    "name": "Family SUV",
                    "identifiers": {
                        "vin": private_vin,
                        "license_plate": private_plate,
                    },
                    "purchase_price": 40000,
                    "ownership": {"owned_outright": True, "debt_balance": 0},
                    "valuation": {
                        "amount": 28000,
                        "valued_at": "2026-01-01T00:00:00Z",
                        "source_label": "owner estimate",
                    },
                },
                method="POST",
            )
        ) as response:
            vehicle = json.load(response)

        with urllib.request.urlopen(
            _request(
                f"{base_url}/api/private/assets/{vehicle['id']}/valuations/refresh",
                {},
                method="POST",
            )
        ) as response:
            refreshed = json.load(response)

        assert refreshed["status"] == "refreshed"
        assert refreshed["estimates"] == [
            {
                "amount": 24500,
                "currency": "USD",
                "estimate_type": "market_value",
                "effective_at": refreshed["estimates"][0]["effective_at"],
                "observed_at": refreshed["estimates"][0]["observed_at"],
                "source": {
                    "id": "test-public-vehicle-market",
                    "label": "Synthetic public vehicle comparison",
                    "url": "https://example.invalid/test-vehicle",
                    "terms_url": "https://example.invalid/test-terms",
                },
                "confidence": {
                    "level": "medium",
                    "score": 0.72,
                    "basis": "synthetic make/model/year comparison fixture",
                },
                "freshness": {
                    "status": "fresh",
                    "fresh_until": refreshed["estimates"][0]["freshness"][
                        "fresh_until"
                    ],
                },
            }
        ]

    database_bytes = b"".join(
        path.read_bytes() for path in tmp_path.iterdir() if path.is_file()
    )
    assert private_vin.encode() not in database_bytes
    assert private_plate.encode() not in database_bytes
