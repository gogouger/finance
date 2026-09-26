from backend.finance_app.dashboard import _billing_alerts


def test_rentcast_credit_card_charge_creates_a_billing_alert():
    alerts = _billing_alerts(
        [
            {
                "transaction_id": "rentcast-charge",
                "account_id": "capital-one-card",
                "merchant_name": "RentCast API",
                "date": "2026-09-26",
                "amount": 0.20,
                "pending": False,
                "removed": False,
            }
        ],
        [{"account_id": "capital-one-card", "type": "credit"}],
    )

    assert alerts == [
        {
            "type": "unexpected_provider_charge",
            "provider": "RentCast",
            "date": "2026-09-26",
            "amount": 0.2,
            "currency": "USD",
            "message": "RentCast charged a connected credit card even though the app is configured to remain within the free allowance.",
            "review_required": True,
        }
    ]


def test_rentcast_alert_ignores_non_credit_and_pending_transactions():
    transactions = [
        {
            "account_id": "checking",
            "merchant_name": "RentCast",
            "amount": 10,
            "pending": False,
            "removed": False,
        },
        {
            "account_id": "card",
            "merchant_name": "RentCast",
            "amount": 10,
            "pending": True,
            "removed": False,
        },
    ]
    assert _billing_alerts(
        transactions,
        [
            {"account_id": "checking", "type": "depository"},
            {"account_id": "card", "type": "credit"},
        ],
    ) == []
