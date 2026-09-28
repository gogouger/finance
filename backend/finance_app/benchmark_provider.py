"""Authorized, cached benchmark observations for investment comparisons."""

import json
import os
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


ALPHA_VANTAGE_WEEKLY_ADJUSTED_URL = "https://www.alphavantage.co/query"
BENCHMARK_CONNECTION_ID = "benchmark:alpha-vantage:weekly-adjusted"


class BenchmarkProviderUnavailable(RuntimeError):
    """The configured provider did not return a usable benchmark series."""


def _weekly_adjusted_url(api_key: str) -> str:
    return f"{ALPHA_VANTAGE_WEEKLY_ADJUSTED_URL}?{urlencode({
        'function': 'TIME_SERIES_WEEKLY_ADJUSTED',
        'symbol': 'SPY',
        'apikey': api_key,
    })}"


def fetch_alpha_vantage_weekly_adjusted(
    api_key: str, *, fetch=urlopen
) -> list[dict]:
    """Fetch the provider's dividend- and split-adjusted weekly SPY series.

    Weekly points deliberately make this a low-cost, one-request refresh.  A
    comparison uses the last observation on or before a lot's acquisition
    date, so we never invent a daily closing value that was not supplied.
    """
    try:
        with fetch(_weekly_adjusted_url(api_key), timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        raise BenchmarkProviderUnavailable("benchmark provider could not be reached") from error

    if not isinstance(payload, dict):
        raise BenchmarkProviderUnavailable("benchmark provider returned an invalid response")
    if payload.get("Note") or payload.get("Information") or payload.get("Error Message"):
        raise BenchmarkProviderUnavailable("benchmark provider declined the refresh")
    series = payload.get("Weekly Adjusted Time Series")
    if not isinstance(series, dict):
        raise BenchmarkProviderUnavailable("benchmark provider returned no adjusted weekly history")

    observations = []
    for observation_date, values in series.items():
        try:
            datetime.strptime(observation_date, "%Y-%m-%d")
            adjusted_close = float(values["5. adjusted close"])
        except (KeyError, TypeError, ValueError):
            continue
        if adjusted_close <= 0:
            continue
        observations.append(
            {
                "date": observation_date,
                "value": adjusted_close,
                "symbol": "SPY",
                "currency": "USD",
                "source": "alpha_vantage_weekly_adjusted",
                "return_basis": "dividend_and_split_adjusted",
                "cadence": "weekly",
                "alignment": "latest_weekly_observation_on_or_before_lot_date",
            }
        )
    observations.sort(key=lambda item: item["date"])
    if len(observations) < 2:
        raise BenchmarkProviderUnavailable("benchmark provider returned too little usable history")
    return observations


def refresh_spy_benchmark(storage, owner: str) -> dict:
    """Persist one complete encrypted adjusted-SPY history for an owner."""
    api_key = os.environ.get("BENCHMARK_ALPHA_VANTAGE_API_KEY", "").strip()
    if not api_key:
        return {"status": "not_configured", "observations": 0}
    observations = fetch_alpha_vantage_weekly_adjusted(api_key)
    observed_at = datetime.now(UTC).isoformat()
    for observation in observations:
        storage.upsert_financial_record(
            owner,
            BENCHMARK_CONNECTION_ID,
            "benchmark_observation",
            f"SPY:{observation['date']}",
            {**observation, "observed_at": observed_at},
        )
    return {
        "status": "refreshed",
        "observations": len(observations),
        "start": observations[0]["date"],
        "end": observations[-1]["date"],
        "cadence": "weekly",
    }
