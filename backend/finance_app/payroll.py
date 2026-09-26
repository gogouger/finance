import hashlib
import os
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .auth import require_fresh_owner, require_owner
from .plaid_provider import PlaidProviderError


router = APIRouter(prefix="/api/private/payroll")


class PayrollLinkRequest(BaseModel):
    provider_name: str = Field(min_length=1, max_length=160)


class PayrollApprovalRequest(BaseModel):
    terms_id: str = Field(min_length=1, max_length=128)
    confirmed: bool
    monthly_spending_cap_usd: Decimal = Field(ge=0, decimal_places=2)


class ManualPayrollRequest(BaseModel):
    effective_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    pay_frequency: Literal[
        "weekly", "biweekly", "semimonthly", "monthly", "annual", "other"
    ]
    gross_pay: Decimal = Field(ge=0, decimal_places=2)
    net_pay: Decimal = Field(ge=0, decimal_places=2)
    withholding_total: Decimal = Field(ge=0, decimal_places=2)
    deduction_total: Decimal = Field(ge=0, decimal_places=2)


def _environment() -> str:
    return os.environ.get("PAYROLL_ENVIRONMENT", "sandbox").lower()


def _price(name: str, sandbox_default: str = "0") -> Decimal | None:
    raw = os.environ.get(name)
    if raw is None and _environment() == "sandbox":
        raw = sandbox_default
    try:
        price = Decimal(raw) if raw is not None else None
    except InvalidOperation:
        return None
    return price if price is not None and price >= 0 else None


def _terms() -> dict:
    initial = _price("PAYROLL_INITIAL_PRICE_USD")
    refresh = _price("PAYROLL_REFRESH_PRICE_USD")
    identity = "|".join(
        [
            "payroll-pricing-v1",
            _environment(),
            "unavailable" if initial is None else str(initial),
            "unavailable" if refresh is None else str(refresh),
        ]
    )
    return {
        "terms_id": hashlib.sha256(identity.encode()).hexdigest(),
        "environment": _environment(),
        "pricing_status": (
            "configured" if initial is not None and refresh is not None else "unavailable"
        ),
        "initial_verification": {
            "billing_model": "one_time",
            "unit_price_usd": None if initial is None else float(initial),
        },
        "refresh": {
            "billing_model": "per_request",
            "unit_price_usd": None if refresh is None else float(refresh),
        },
    }


def _configuration(request: Request, owner: str) -> dict:
    records = request.app.state.storage.list_financial_records(
        owner, "payroll_configuration"
    )
    return {} if not records else records[-1]


def _save_configuration(request: Request, owner: str, record: dict) -> None:
    request.app.state.storage.upsert_financial_record(
        owner,
        "plaid-payroll",
        "payroll_configuration",
        "current",
        {key: value for key, value in record.items() if key != "removed"},
    )


def _amount_lines(section: dict | None) -> list[dict]:
    return [
        {
            "description": line.get("description") or "Unspecified",
            "amount": float(line.get("current_amount") or 0),
        }
        for line in (section or {}).get("breakdown", [])
        if line.get("iso_currency_code") in {None, "USD"}
    ]


def _current_amount(value: dict | None) -> float:
    return float((value or {}).get("current_amount") or 0)


def _with_freshness(record: dict) -> dict:
    retrieved_at = datetime.fromisoformat(record["retrieved_at"])
    age_seconds = max(0, int((datetime.now(UTC) - retrieved_at).total_seconds()))
    stale_after = int(os.environ.get("PAYROLL_STALE_AFTER_SECONDS", "1209600"))
    return {
        **{key: value for key, value in record.items() if key != "removed"},
        "status": "stale" if age_seconds > stale_after else "fresh",
        "age_seconds": age_seconds,
        "stale_after_seconds": stale_after,
    }


def _manual_income(request: Request, owner: str) -> dict | None:
    records = request.app.state.storage.list_financial_records(
        owner, "payroll_manual"
    )
    if not records:
        return None
    return {key: value for key, value in records[-1].items() if key != "removed"}


def _cashflow_estimate(request: Request, owner: str) -> dict | None:
    paycheck_terms = ("paycheck", "payroll", "salary", "direct deposit")
    candidates = []
    for transaction in request.app.state.storage.list_financial_records(
        owner, "transaction"
    ):
        category = transaction.get("personal_finance_category", {})
        detailed = str(category.get("detailed", "")).upper()
        name = str(transaction.get("name", "")).casefold()
        is_paycheck = "INCOME_WAGES" in detailed or any(
            term in name for term in paycheck_terms
        )
        if (
            not transaction.get("removed")
            and not transaction.get("pending")
            and float(transaction.get("amount", 0)) < 0
            and is_paycheck
        ):
            candidates.append(transaction)
    if not candidates:
        return None
    observed_net = round(
        sum(abs(float(transaction["amount"])) for transaction in candidates), 2
    )
    latest_date = max(str(transaction.get("date", "")) for transaction in candidates)
    try:
        age_days = max(
            0,
            (datetime.now(UTC).date() - datetime.fromisoformat(latest_date).date()).days,
        )
    except ValueError:
        age_days = None
    return {
        "status": "estimated",
        "source": "transaction_cashflow",
        "confidence": {"level": "medium", "score": 0.55},
        "currency": "USD",
        "estimate": {
            "observed_net_pay": observed_net,
            "gross_pay": None,
            "withholding_total": None,
            "deduction_total": None,
            "paycheck_count": len(candidates),
        },
        "provenance": {
            "method": "observed_paycheck_deposits",
            "latest_observation_date": latest_date,
            "record_count": len(candidates),
        },
        "limitations": [
            "gross pay, taxes, deductions, and benefits are unavailable from cashflow"
        ],
        "freshness": {
            "status": (
                "unknown"
                if age_days is None
                else "stale" if age_days > 45 else "fresh"
            ),
            "age_days": age_days,
        },
    }


@router.get("/terms")
def payroll_terms(request: Request) -> dict:
    require_owner(request)
    return _terms()


@router.post("/approval")
def approve_payroll(payload: PayrollApprovalRequest, request: Request) -> dict:
    owner = require_fresh_owner(request)
    terms = _terms()
    initial_price = terms["initial_verification"]["unit_price_usd"]
    if terms["pricing_status"] != "configured":
        raise HTTPException(status_code=409, detail="payroll pricing is not configured")
    if not payload.confirmed or payload.terms_id != terms["terms_id"]:
        raise HTTPException(status_code=409, detail="current payroll pricing must be confirmed")
    if payload.monthly_spending_cap_usd < Decimal(str(initial_price)):
        raise HTTPException(
            status_code=422,
            detail="spending cap must cover one initial verification",
        )
    current = _configuration(request, owner)
    approved = {
        **current,
        "approved": True,
        "approved_terms_id": terms["terms_id"],
        "approved_at": datetime.now(UTC).isoformat(),
        "monthly_spending_cap_usd": float(payload.monthly_spending_cap_usd),
        "spent_this_month_usd": 0.0,
        "spend_month": datetime.now(UTC).strftime("%Y-%m"),
    }
    _save_configuration(request, owner, approved)
    return {
        "approved": True,
        "terms_id": terms["terms_id"],
        "monthly_spending_cap_usd": approved["monthly_spending_cap_usd"],
        "spent_this_month_usd": 0.0,
    }


def _reserve_initial_verification(request: Request, owner: str) -> dict:
    config = _configuration(request, owner)
    if _environment() == "sandbox":
        return config
    terms = _terms()
    if (
        terms["pricing_status"] != "configured"
        or not config.get("approved")
        or config.get("approved_terms_id") != terms["terms_id"]
    ):
        raise HTTPException(
            status_code=409,
            detail="live payroll requires displayed pricing, approval, and a spending cap",
        )
    month = datetime.now(UTC).strftime("%Y-%m")
    spent = config.get("spent_this_month_usd", 0.0) if config.get("spend_month") == month else 0.0
    price = terms["initial_verification"]["unit_price_usd"]
    if spent + price > config["monthly_spending_cap_usd"]:
        raise HTTPException(status_code=429, detail="payroll spending cap reached")
    config.update(
        {
            "spent_this_month_usd": round(spent + price, 2),
            "spend_month": month,
        }
    )
    _save_configuration(request, owner, config)
    return config


def _reserve_refresh(request: Request, owner: str) -> dict:
    config = _configuration(request, owner)
    if _environment() == "sandbox":
        return config
    terms = _terms()
    if (
        terms["pricing_status"] != "configured"
        or not config.get("approved")
        or config.get("approved_terms_id") != terms["terms_id"]
    ):
        raise HTTPException(
            status_code=409,
            detail="live payroll refresh requires current pricing approval",
        )
    month = datetime.now(UTC).strftime("%Y-%m")
    spent = config.get("spent_this_month_usd", 0.0) if config.get("spend_month") == month else 0.0
    price = terms["refresh"]["unit_price_usd"]
    if spent + price > config["monthly_spending_cap_usd"]:
        raise HTTPException(status_code=429, detail="payroll spending cap reached")
    config.update(
        {
            "spent_this_month_usd": round(spent + price, 2),
            "spend_month": month,
        }
    )
    _save_configuration(request, owner, config)
    return config


@router.get("/coverage")
def payroll_coverage(
    request: Request,
    provider_name: str = Query(min_length=1, max_length=160),
) -> dict:
    require_owner(request)
    return {
        "provider_name": provider_name,
        "support": request.app.state.plaid.payroll_provider_coverage(provider_name),
        "environment": _environment(),
        "checked_via": "plaid_adapter",
        "fallback": "cashflow_estimate",
    }


@router.post("/link-token")
def payroll_link_token(payload: PayrollLinkRequest, request: Request) -> dict:
    owner = require_fresh_owner(request)
    configuration = _reserve_initial_verification(request, owner)
    client_user_id = hashlib.sha256(f"payroll:{owner}".encode()).hexdigest()
    try:
        result = request.app.state.plaid.create_payroll_link_token(client_user_id)
    except PlaidProviderError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    _save_configuration(
        request,
        owner,
        {
            **configuration,
            "provider_name": payload.provider_name,
            "user_id": result["user_id"],
            "environment": _environment(),
        },
    )
    return {
        "link_token": result["link_token"],
        "expiration": result["expiration"],
        "environment": _environment(),
        "products": ["income_verification"],
        "income_source_types": ["payroll"],
        "flow_types": ["payroll_digital_income"],
    }


@router.post("/ingest")
def ingest_payroll(request: Request) -> dict:
    owner = (
        require_owner(request)
        if _environment() == "sandbox"
        else require_fresh_owner(request)
    )
    configuration = _configuration(request, owner)
    if not configuration.get("user_id"):
        raise HTTPException(status_code=409, detail="connect a payroll provider first")
    _reserve_refresh(request, owner)
    retrieved_at = datetime.now(UTC).isoformat()
    try:
        response = request.app.state.plaid.payroll_income_get(
            configuration["user_id"]
        )
    except PlaidProviderError as error:
        request.app.state.storage.upsert_financial_record(
            owner,
            "plaid-payroll",
            "payroll_status",
            "current",
            {
                "status": "error",
                "error": {"code": error.code, "message": str(error)},
                "retrieved_at": retrieved_at,
            },
        )
        raise HTTPException(status_code=502, detail="payroll provider unavailable") from error

    records_ingested = 0
    for item in response.get("items", []):
        provider_name = item.get("institution_name") or configuration.get(
            "provider_name", "Unknown"
        )
        for payroll_account in item.get("payroll_income", []):
            account_id = payroll_account.get("account_id") or "default"
            for paystub in payroll_account.get("pay_stubs", []):
                withholdings = _amount_lines(paystub.get("taxes"))
                deductions = _amount_lines(paystub.get("deductions"))
                pay_date = paystub.get("pay_date") or "unknown"
                record = {
                    "source": "plaid_payroll",
                    "provenance": {
                        "provider": "Plaid Payroll Income",
                        "provider_name": provider_name,
                        "record_type": "paystub",
                    },
                    "confidence": {"level": "high", "score": 1.0},
                    "currency": "USD",
                    "latest_paystub": {
                        "pay_date": pay_date,
                        "gross_pay": _current_amount(paystub.get("gross_earnings")),
                        "net_pay": _current_amount(paystub.get("net_pay")),
                        "withholding_total": round(
                            sum(line["amount"] for line in withholdings), 2
                        ),
                        "deduction_total": round(
                            sum(line["amount"] for line in deductions), 2
                        ),
                        "withholdings": withholdings,
                        "deductions": deductions,
                    },
                    "retrieved_at": retrieved_at,
                }
                request.app.state.storage.upsert_financial_record(
                    owner,
                    "plaid-payroll",
                    "payroll_income",
                    f"{account_id}:{pay_date}",
                    record,
                )
                records_ingested += 1
    request.app.state.storage.upsert_financial_record(
        owner,
        "plaid-payroll",
        "payroll_status",
        "current",
        {"status": "fresh", "retrieved_at": retrieved_at, "error": None},
    )
    return {
        "records_ingested": records_ingested,
        "status": "fresh",
        "retrieved_at": retrieved_at,
    }


@router.post("/manual")
def save_manual_payroll(payload: ManualPayrollRequest, request: Request) -> dict:
    owner = require_fresh_owner(request)
    gross = float(payload.gross_pay)
    net = float(payload.net_pay)
    withholding = float(payload.withholding_total)
    deductions = float(payload.deduction_total)
    if net > gross or abs((gross - net) - (withholding + deductions)) > 0.01:
        raise HTTPException(
            status_code=422,
            detail="gross minus net must equal withholding plus deductions",
        )
    record = {
        "status": "configured",
        "source": "manual",
        "provenance": {
            "provider": "owner supplied",
            "record_type": "manual payroll profile",
        },
        "confidence": {"level": "high", "score": 1.0},
        "currency": "USD",
        "latest_paystub": {
            "pay_date": payload.effective_date,
            "pay_frequency": payload.pay_frequency,
            "gross_pay": gross,
            "net_pay": net,
            "withholding_total": withholding,
            "deduction_total": deductions,
            "withholdings": [],
            "deductions": [],
        },
        "retrieved_at": datetime.now(UTC).isoformat(),
    }
    request.app.state.storage.upsert_financial_record(
        owner,
        "manual-payroll",
        "payroll_manual",
        "current",
        record,
    )
    return {"saved": True, "source": "manual"}


@router.get("/income")
def payroll_income(request: Request) -> dict:
    owner = require_owner(request)
    records = [
        record
        for record in request.app.state.storage.list_financial_records(
            owner, "payroll_income"
        )
        if not record.get("removed")
    ]
    if records:
        records.sort(
            key=lambda record: record.get("latest_paystub", {}).get("pay_date", "")
        )
        return _with_freshness(records[-1])
    manual = _manual_income(request, owner)
    if manual is not None:
        return manual
    estimate = _cashflow_estimate(request, owner)
    if estimate is not None:
        return estimate
    status_records = request.app.state.storage.list_financial_records(
        owner, "payroll_status"
    )
    if status_records and status_records[-1].get("status") == "error":
        return {
            **{key: value for key, value in status_records[-1].items() if key != "removed"},
            "source": "plaid_payroll",
            "confidence": {"level": "unavailable", "score": 0.0},
        }
    return {
        "status": "unavailable",
        "source": "none",
        "confidence": {"level": "unavailable", "score": 0.0},
        "missing_inputs": ["payroll connection or observed paycheck transactions"],
    }
