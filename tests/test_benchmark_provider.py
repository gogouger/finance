import json

import pytest

from backend.finance_app.benchmark_provider import (
    BenchmarkProviderUnavailable,
    fetch_alpha_vantage_weekly_price,
    fetch_alpha_vantage_weekly_adjusted,
)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_weekly_adjusted_benchmark_retains_provider_dates_and_adjusted_values():
    observations = fetch_alpha_vantage_weekly_adjusted(
        "not-a-real-key",
        fetch=lambda *_args, **_kwargs: _Response(
            {
                "Weekly Adjusted Time Series": {
                    "2026-09-25": {"5. adjusted close": "650.00"},
                    "2026-09-18": {"5. adjusted close": "640.00"},
                }
            }
        ),
    )

    assert observations == [
        {
            "date": "2026-09-18",
            "value": 640.0,
            "symbol": "SPY",
            "currency": "USD",
            "source": "alpha_vantage_weekly_adjusted",
            "return_basis": "dividend_and_split_adjusted",
            "cadence": "weekly",
            "alignment": "latest_weekly_observation_on_or_before_lot_date",
        },
        {
            "date": "2026-09-25",
            "value": 650.0,
            "symbol": "SPY",
            "currency": "USD",
            "source": "alpha_vantage_weekly_adjusted",
            "return_basis": "dividend_and_split_adjusted",
            "cadence": "weekly",
            "alignment": "latest_weekly_observation_on_or_before_lot_date",
        },
    ]


def test_weekly_adjusted_benchmark_rejects_provider_error():
    with pytest.raises(BenchmarkProviderUnavailable):
        fetch_alpha_vantage_weekly_adjusted(
            "not-a-real-key",
            fetch=lambda *_args, **_kwargs: _Response({"Note": "slow down"}),
        )


def test_weekly_price_benchmark_keeps_raw_closes_separate_from_total_return():
    observations = fetch_alpha_vantage_weekly_price(
        "not-a-real-key",
        fetch=lambda *_args, **_kwargs: _Response(
            {
                "Weekly Time Series": {
                    "2026-09-25": {"4. close": "650.00"},
                    "2026-09-18": {"4. close": "640.00"},
                }
            }
        ),
    )
    assert observations[0]["date"] == "2026-09-18"
    assert observations[0]["value"] == 640.0
    assert observations[0]["return_basis"] == "price_return"
    assert observations[0]["source"] == "alpha_vantage_weekly_price"
