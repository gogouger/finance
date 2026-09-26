from backend.finance_app.plaid_provider import HttpPlaidProvider


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
