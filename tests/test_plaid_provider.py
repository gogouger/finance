import json

import pytest
from cryptography.fernet import Fernet

from backend.finance_app.plaid_provider import HttpPlaidProvider, create_plaid_provider


def test_http_provider_uses_explicit_environment_and_activity_page_options():
    provider = HttpPlaidProvider("client", "secret", "production")
    captured = {}

    def fake_post(path, payload):
        captured.update({"path": path, "payload": payload})
        return {"investment_transactions": [], "total_investment_transactions": 0}

    provider._post = fake_post
    provider.investments_transactions_get(
        "access-token",
        "2025-01-01",
        "2025-12-31",
        offset=500,
        count=250,
    )

    assert provider.environment == "production"
    assert provider._base_url == "https://production.plaid.com"
    assert captured == {
        "path": "/investments/transactions/get",
        "payload": {
            "access_token": "access-token",
            "start_date": "2025-01-01",
            "end_date": "2025-12-31",
            "options": {"count": 250, "offset": 500},
        },
    }


def test_provider_loads_authenticated_encrypted_credentials(tmp_path, monkeypatch):
    key = Fernet.generate_key()
    key_path = tmp_path / "plaid.key"
    credentials_path = tmp_path / "plaid.fernet"
    key_path.write_bytes(key)
    credentials_path.write_bytes(
        Fernet(key).encrypt(
            json.dumps({"client_id": "encrypted-client", "secret": "encrypted-secret"}).encode()
        )
    )
    monkeypatch.setenv("PLAID_CREDENTIALS_KEY_FILE", str(key_path))
    monkeypatch.setenv("PLAID_CREDENTIALS_FILE", str(credentials_path))
    monkeypatch.setenv("PLAID_ENVIRONMENT", "sandbox")
    monkeypatch.delenv("PLAID_CLIENT_ID", raising=False)
    monkeypatch.delenv("PLAID_SECRET", raising=False)

    provider = create_plaid_provider()

    assert isinstance(provider, HttpPlaidProvider)
    assert provider._client_id == "encrypted-client"
    assert provider._secret == "encrypted-secret"


def test_provider_fails_closed_for_tampered_encrypted_credentials(tmp_path, monkeypatch):
    key_path = tmp_path / "plaid.key"
    credentials_path = tmp_path / "plaid.fernet"
    key_path.write_bytes(Fernet.generate_key())
    credentials_path.write_bytes(b"tampered")
    monkeypatch.setenv("PLAID_CREDENTIALS_KEY_FILE", str(key_path))
    monkeypatch.setenv("PLAID_CREDENTIALS_FILE", str(credentials_path))

    with pytest.raises(RuntimeError, match="could not be loaded"):
        create_plaid_provider()
