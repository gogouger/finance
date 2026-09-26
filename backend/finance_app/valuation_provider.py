import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any


DOUGLAS_COUNTY_ASSESSOR_URL = (
    "https://apps.douglas.co.us/apps/assessor/search/parcelDetails.do"
)


class ValuationProviderUnavailable(Exception):
    pass


class ValuationProviderRateLimited(Exception):
    pass


class DisabledValuationProvider:
    cache_seconds = 86400

    def fetch(self, kind: str, identifiers: dict[str, str]) -> list[dict[str, Any]]:
        del kind, identifiers
        raise ValuationProviderUnavailable(
            "no permitted automated valuation provider is configured"
        )

    def terms(self) -> dict[str, Any]:
        return {
            "respected": True,
            "mode": "disabled",
            "reason": "automation remains off until a permitted source is configured",
        }


class FakeValuationProvider:
    """Deterministic synthetic observations for running-service tests only."""

    cache_seconds = 86400

    def fetch(self, kind: str, identifiers: dict[str, str]) -> list[dict[str, Any]]:
        del identifiers
        observed_at = datetime.now(UTC).isoformat()
        if kind == "home":
            return [
                {
                    "amount": 565000.0,
                    "currency": "USD",
                    "estimate_type": "assessor_actual_value",
                    "effective_at": "2026-06-30T00:00:00+00:00",
                    "observed_at": observed_at,
                    "source": {
                        "id": "test-douglas-county-assessor",
                        "label": "Synthetic Douglas County assessment",
                        "url": "https://example.invalid/test-assessment",
                        "terms_url": "https://example.invalid/test-terms",
                    },
                    "confidence": {
                        "level": "high",
                        "score": 0.95,
                        "basis": "official-assessment-shaped synthetic fixture",
                    },
                },
                {
                    "amount": 610000.0,
                    "currency": "USD",
                    "estimate_type": "market_value",
                    "effective_at": observed_at,
                    "observed_at": observed_at,
                    "source": {
                        "id": "test-public-market",
                        "label": "Synthetic public market comparison",
                        "url": "https://example.invalid/test-market",
                        "terms_url": "https://example.invalid/test-terms",
                    },
                    "confidence": {
                        "level": "medium",
                        "score": 0.7,
                        "basis": "synthetic comparable-sales fixture",
                    },
                },
            ]
        return [
            {
                "amount": 24500.0,
                "currency": "USD",
                "estimate_type": "market_value",
                "effective_at": observed_at,
                "observed_at": observed_at,
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
            }
        ]

    def terms(self) -> dict[str, Any]:
        return {
            "respected": True,
            "mode": "fake",
            "synthetic": True,
            "cache_seconds": self.cache_seconds,
            "minimum_request_interval_seconds": 0,
        }


class DouglasCountyPublicDataProvider:
    """Opt-in, read-only adapter for the official assessor parcel detail page."""

    cache_seconds = 86400
    minimum_request_interval_seconds = 2

    def __init__(self, *, terms_url: str, accepted_at: str, user_agent: str):
        if not terms_url.startswith("https://") or not accepted_at or not user_agent:
            raise ValueError("source terms acknowledgement and user agent are required")
        self._terms_url = terms_url
        self._accepted_at = accepted_at
        self._user_agent = user_agent
        self._last_request_at: datetime | None = None
        self._lock = threading.Lock()

    def fetch(self, kind: str, identifiers: dict[str, str]) -> list[dict[str, Any]]:
        if kind != "home":
            raise ValuationProviderUnavailable(
                "the configured public source does not publish vehicle valuations"
            )
        parcel_id = identifiers.get("parcel_id", "").strip()
        if not re.fullmatch(r"R\d{7}", parcel_id):
            raise ValuationProviderUnavailable(
                "a Douglas County account identifier like R0123456 is required"
            )
        now = datetime.now(UTC)
        with self._lock:
            if self._last_request_at is not None:
                elapsed = (now - self._last_request_at).total_seconds()
                if elapsed < self.minimum_request_interval_seconds:
                    raise ValuationProviderRateLimited(
                        "public source request interval has not elapsed"
                    )
            self._last_request_at = now

        url = f"{DOUGLAS_COUNTY_ASSESSOR_URL}?{urllib.parse.urlencode({'propertyId': parcel_id})}"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": self._user_agent, "Accept": "text/html"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status != 200:
                    raise ValuationProviderUnavailable(
                        f"public source returned HTTP {response.status}"
                    )
                document = response.read(2_000_000).decode("utf-8", errors="replace")
        except (OSError, urllib.error.URLError) as error:
            raise ValuationProviderUnavailable("public source is unavailable") from error

        matches = re.findall(
            r"(?:\d{4}\s+)?Actual Value[^$]{0,120}\$\s*([0-9,]+)",
            document,
            flags=re.IGNORECASE,
        )
        if not matches:
            raise ValuationProviderUnavailable(
                "public source response did not contain an assessed actual value"
            )
        observed_at = now.isoformat()
        return [
            {
                "amount": float(matches[0].replace(",", "")),
                "currency": "USD",
                "estimate_type": "assessor_actual_value",
                "effective_at": observed_at,
                "observed_at": observed_at,
                "source": {
                    "id": "douglas-county-assessor",
                    "label": "Douglas County Assessor actual value",
                    "url": url,
                    "terms_url": self._terms_url,
                },
                "confidence": {
                    "level": "high",
                    "score": 0.95,
                    "basis": "official county assessment; not a current market appraisal",
                },
            }
        ]

    def terms(self) -> dict[str, Any]:
        return {
            "respected": True,
            "mode": "douglas_county_public_data",
            "terms_url": self._terms_url,
            "terms_accepted_at": self._accepted_at,
            "cache_seconds": self.cache_seconds,
            "minimum_request_interval_seconds": self.minimum_request_interval_seconds,
        }


def create_valuation_provider():
    mode = os.environ.get("VALUATION_MODE", "disabled").strip().lower()
    if mode == "fake":
        return FakeValuationProvider()
    if mode == "douglas_county_public_data":
        try:
            return DouglasCountyPublicDataProvider(
                terms_url=os.environ["VALUATION_SOURCE_TERMS_URL"],
                accepted_at=os.environ["VALUATION_SOURCE_TERMS_ACCEPTED_AT"],
                user_agent=os.environ["VALUATION_SOURCE_USER_AGENT"],
            )
        except (KeyError, ValueError):
            return DisabledValuationProvider()
    return DisabledValuationProvider()
