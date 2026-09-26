import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from base64 import urlsafe_b64encode
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.finance_app.household import (
    HouseholdPlan,
    compare_household_plans,
    project_household,
)


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture
def household_service(tmp_path: Path):
    port = _unused_port()
    environment = os.environ.copy()
    environment.update(
        {
            "FINANCE_DATA_DIR": str(tmp_path),
            "FINANCE_ENCRYPTION_KEY": urlsafe_b64encode(b"0" * 32).decode(),
            "PLAID_MODE": "fake",
            "FINANCE_INTERNAL_KEY": "test-internal-key",
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
        yield base_url
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def _baseline(**overrides) -> HouseholdPlan:
    values = {
        "name": "Married single-income baseline",
        "start_date": "2026-01-01",
        "end_date": "2027-12-31",
        "profile": {
            "marital_status": "married",
            "filing_status": "married_filing_jointly",
            "dependents": [
                {"id": "child-1", "birth_date": "2021-04-10"},
                {"id": "child-2", "birth_date": "2024-07-02"},
            ],
        },
        "baseline": {
            "primary_income": 100_000,
            "spouse_income": 0,
            "personal_expenses": 60_000,
            "tax_liability": 15_000,
            "housing_cost": 12_000,
            "retirement_contribution": 10_000,
        },
        "events": [],
    }
    values.update(overrides)
    return HouseholdPlan.model_validate(values)


def test_profiles_validate_single_married_and_dependent_assumptions():
    married = _baseline()
    assert married.profile.filing_status == "married_filing_jointly"
    assert len(married.profile.dependents) == 2

    single = _baseline(
        profile={
            "marital_status": "single",
            "filing_status": "single",
            "dependents": [],
        }
    )
    assert single.profile.filing_status == "single"

    with pytest.raises(ValidationError, match="married filing status"):
        _baseline(
            profile={
                "marital_status": "single",
                "filing_status": "married_filing_jointly",
                "dependents": [],
            }
        )


def test_dated_typed_event_updates_every_domain_from_one_timeline():
    plan = _baseline(
        events=[
            {
                "id": "spouse-work",
                "label": "Spouse returns to work",
                "event_type": "spouse_work",
                "start_date": "2027-01-01",
                "end_date": "2027-12-31",
                "effects": {
                    "income": 50_000,
                    "personal_expenses": 12_000,
                    "tax_liability": 10_000,
                    "housing_cost": 0,
                    "retirement_contribution": 5_000,
                },
            }
        ]
    )

    result = project_household(plan)
    assert len(result["timeline"]) == 2
    assert result["timeline"][0]["active_events"] == []
    year = result["timeline"][1]
    assert year["active_events"] == ["spouse-work"]
    assert year["cash_flow"] == {
        "income": 150_000,
        "personal_expenses": 72_000,
        "tax_liability": 25_000,
        "housing_cost": 12_000,
        "retirement_contribution": 15_000,
        "surplus_after_saving": 26_000,
    }
    assert year["tax_inputs"] == {
        "filing_status": "married_filing_jointly",
        "dependent_count": 2,
        "gross_income": 150_000,
        "estimated_tax_liability": 25_000,
    }
    assert year["housing_inputs"] == {"annual_housing_cost": 12_000}
    assert year["retirement_inputs"] == {
        "annual_income": 150_000,
        "annual_expenses": 84_000,
        "annual_contribution": 15_000,
    }


def test_baseline_compares_homeschool_and_spouse_work_with_driver_attribution():
    homeschooling = _baseline(
        name="Homeschooling",
        events=[
            {
                "id": "homeschool",
                "label": "Homeschool both children",
                "event_type": "homeschooling",
                "start_date": "2027-01-01",
                "end_date": "2027-12-31",
                "effects": {"personal_expenses": 6_000},
            }
        ],
    )
    spouse_work = _baseline(
        name="Spouse returns to work",
        events=[
            {
                "id": "spouse-work",
                "label": "Spouse returns to work",
                "event_type": "spouse_work",
                "start_date": "2027-01-01",
                "end_date": "2027-12-31",
                "effects": {
                    "income": 50_000,
                    "personal_expenses": 12_000,
                    "tax_liability": 10_000,
                    "retirement_contribution": 5_000,
                },
            }
        ],
    )

    comparison = compare_household_plans(
        _baseline(), [homeschooling, spouse_work]
    )
    variants = {item["name"]: item for item in comparison["variants"]}

    assert variants["Homeschooling"]["outcome_changes"] == {
        "income": 0,
        "personal_expenses": 6_000,
        "tax_liability": 0,
        "housing_cost": 0,
        "retirement_contribution": 0,
        "surplus_after_saving": -6_000,
    }
    assert variants["Homeschooling"]["drivers"] == [
        {
            "event_id": "homeschool",
            "label": "Homeschool both children",
            "event_type": "homeschooling",
            "effects": {
                "income": 0,
                "personal_expenses": 6_000,
                "tax_liability": 0,
                "housing_cost": 0,
                "retirement_contribution": 0,
                "surplus_after_saving": -6_000,
            },
        }
    ]
    assert variants["Spouse returns to work"]["outcome_changes"] == {
        "income": 50_000,
        "personal_expenses": 12_000,
        "tax_liability": 10_000,
        "housing_cost": 0,
        "retirement_contribution": 5_000,
        "surplus_after_saving": 23_000,
    }
    assert variants["Spouse returns to work"]["drivers"][0]["event_id"] == (
        "spouse-work"
    )
    assert all(item["attribution_reconciles"] for item in variants.values())


def test_events_require_valid_explicit_start_and_end_dates():
    with pytest.raises(ValidationError, match="end date cannot precede start date"):
        _baseline(
            events=[
                {
                    "id": "move",
                    "label": "Move",
                    "event_type": "moving",
                    "start_date": date(2027, 6, 1),
                    "end_date": date(2027, 5, 31),
                    "effects": {"housing_cost": 3_000},
                }
            ]
        )


def test_household_timeline_is_available_through_the_public_service_boundary(
    household_service: str,
):
    payload = _baseline().model_dump(mode="json")
    request = urllib.request.Request(
        f"{household_service}/api/public/household/project",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request) as response:
        body = json.load(response)

    assert response.status == 200
    assert body["profile"] == {
        "marital_status": "married",
        "filing_status": "married_filing_jointly",
        "dependent_count": 2,
    }
    assert [row["year"] for row in body["timeline"]] == [2026, 2027]
