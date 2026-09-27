import base64
import hashlib
import hmac
import json
import os
import secrets
import urllib.parse
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .accounting import build_accounting_view
from .auth import require_fresh_owner, require_owner
from .classification import effective_classifications
from .investment_scope import is_custodial_account
from .scenarios import calculate_scenario


router = APIRouter()
ACCESS_LIFETIME = timedelta(minutes=10)
REFRESH_LIFETIME = timedelta(days=30)
AUTHORIZATION_CODE_LIFETIME = timedelta(minutes=10)
MCP_PROTOCOL_VERSION = "2025-06-18"
FINANCE_SCOPES = {
    "finance:summary",
    "finance:metrics",
    "finance:spending",
    "finance:investments",
    "finance:scenarios",
    "finance:transactions:detail",
}
ATHLETICS_SCOPES = {"athletics:training:summary"}
LIBRARY_SCOPES = {"library:reading:metrics"}
MCP_SCOPES = FINANCE_SCOPES | ATHLETICS_SCOPES | LIBRARY_SCOPES
TOOL_SCOPES = {
    "finance.summary": "finance:summary",
    "finance.cash_flow_trend": "finance:summary",
    "finance.metric_definitions": "finance:metrics",
    "finance.spending_breakdown": "finance:spending",
    "finance.investments.summary": "finance:investments",
    "finance.scenarios.list": "finance:scenarios",
    "finance.scenario.calculate": "finance:scenarios",
    "finance.transactions.list": "finance:transactions:detail",
    "athletics.training.summary": "athletics:training:summary",
    "library.reading.metrics": "library:reading:metrics",
}
DIRECT_IDENTIFIER_KEYS = {
    "account_number",
    "address",
    "email",
    "full_name",
    "owner",
    "parcel_id",
    "phone",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _issuer(request: Request) -> str:
    scheme = request.headers.get("X-Forwarded-Proto") or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}".rstrip("/")


def _resource_uri(request: Request) -> str:
    return f"{_issuer(request)}/mcp"


def _resource_metadata_uri(request: Request) -> str:
    return f"{_issuer(request)}/.well-known/oauth-protected-resource/mcp"


def _oauth_challenge(request: Request, error: str | None = None) -> dict[str, str]:
    value = f'Bearer resource_metadata="{_resource_metadata_uri(request)}"'
    if error:
        value += f', error="{error}"'
    return {"WWW-Authenticate": value}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def _public_grant(grant: dict) -> dict:
    return {
        key: grant[key]
        for key in (
            "id",
            "client_id",
            "client_name",
            "redirect_uri",
            "scopes",
            "status",
            "created_at",
            "last_used_at",
            "revoked_at",
        )
        if key in grant
    }


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact(item)
            for key, item in value.items()
            if key.lower() not in DIRECT_IDENTIFIER_KEYS
            and "token" not in key.lower()
            and "secret" not in key.lower()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class GrantCreate(BaseModel):
    client_id: str = Field(min_length=3, max_length=120)
    client_name: str = Field(min_length=3, max_length=120)
    redirect_uri: str = Field(pattern=r"^https?://", max_length=500)
    scopes: list[str] = Field(min_length=1, max_length=10)
    code_challenge: str = Field(min_length=43, max_length=128)
    code_challenge_method: Literal["S256"]

    @field_validator("redirect_uri")
    @classmethod
    def secure_redirect_uri(cls, value: str) -> str:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme == "https":
            return value
        if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
            return value
        raise ValueError("redirect_uri must use HTTPS or localhost HTTP")

    @field_validator("scopes")
    @classmethod
    def supported_scopes_only(cls, value: list[str]) -> list[str]:
        unique = sorted(set(value))
        unsupported = set(unique) - MCP_SCOPES
        if unsupported:
            raise ValueError(
                f"unsupported MCP scopes: {', '.join(sorted(unsupported))}"
            )
        return unique


class ToolCall(BaseModel):
    tool: str = Field(min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.get("/.well-known/oauth-authorization-server")
def oauth_discovery(request: Request) -> dict:
    issuer = _issuer(request)
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/api/private/mcp/grants",
        "token_endpoint": f"{issuer}/mcp/oauth/token",
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "registration_endpoint": f"{issuer}/mcp/oauth/register",
        "require_pushed_authorization_requests": False,
        "authorization_response_iss_parameter_supported": True,
        "scopes_supported": sorted(MCP_SCOPES),
    }


@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource_metadata(request: Request) -> dict:
    issuer = _issuer(request)
    return {
        "resource": _resource_uri(request),
        "authorization_servers": [issuer],
        "scopes_supported": sorted(MCP_SCOPES),
        "bearer_methods_supported": ["header"],
    }


class DynamicClientRegistration(BaseModel):
    client_name: str = Field(min_length=3, max_length=120)
    redirect_uris: list[str] = Field(min_length=1, max_length=10)
    token_endpoint_auth_method: Literal["none"] = "none"

    @field_validator("redirect_uris")
    @classmethod
    def validate_redirect_uris(cls, values: list[str]) -> list[str]:
        for value in values:
            GrantCreate.secure_redirect_uri(value)
        return values


@router.post("/mcp/oauth/register", status_code=201)
def dynamic_client_registration(
    payload: DynamicClientRegistration, request: Request
) -> dict:
    """Register public-client metadata; consent still happens through owner auth."""
    return {
        "client_id": f"mcp-{uuid4()}",
        "client_name": payload.client_name,
        "redirect_uris": payload.redirect_uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "scope": " ".join(sorted(MCP_SCOPES)),
    }


@router.post("/api/private/mcp/grants")
def create_grant(payload: GrantCreate, request: Request) -> dict:
    owner = require_fresh_owner(request)
    now = _now()
    authorization_code = secrets.token_urlsafe(32)
    grant = {
        "id": str(uuid4()),
        "client_id": payload.client_id,
        "client_name": payload.client_name,
        "resource": _resource_uri(request),
        "redirect_uri": payload.redirect_uri,
        "scopes": payload.scopes,
        "status": "active",
        "created_at": now.isoformat(),
        "authorization_code_hash": _hash(authorization_code),
        "authorization_code_expires_at": (now + AUTHORIZATION_CODE_LIFETIME).isoformat(),
        "code_challenge": payload.code_challenge,
        "code_challenge_method": payload.code_challenge_method,
        "access_token_hash": None,
        "access_token_expires_at": None,
        "refresh_token_hash": None,
        "refresh_token_expires_at": None,
    }
    request.app.state.storage.save_mcp_grant(owner, grant)
    request.app.state.storage.append_audit(
        owner,
        {
            "action": "mcp.grant.created",
            "resource_type": "mcp_grant",
            "resource_id": grant["id"],
            "client_name": grant["client_name"],
            "scopes": grant["scopes"],
            "outcome": "success",
            "occurred_at": now.isoformat(),
        },
    )
    return {
        "grant_id": grant["id"],
        "client_name": grant["client_name"],
        "scopes": grant["scopes"],
        "authorization_code": authorization_code,
        "expires_at": grant["authorization_code_expires_at"],
    }


@router.get("/api/private/mcp/grants")
def list_grants(request: Request) -> dict:
    owner = require_owner(request)
    return {"grants": [_public_grant(item) for item in request.app.state.storage.list_mcp_grants(owner)]}


@router.delete("/api/private/mcp/grants/{grant_id}")
def revoke_grant(grant_id: str, request: Request) -> dict:
    owner = require_fresh_owner(request)
    storage = request.app.state.storage
    grant = storage.get_mcp_grant(owner, grant_id)
    if grant is None:
        raise HTTPException(status_code=404, detail="MCP grant not found")
    if grant["status"] != "revoked":
        grant.update(
            {
                "status": "revoked",
                "revoked_at": _now().isoformat(),
                "access_token_hash": None,
                "refresh_token_hash": None,
            }
        )
        storage.save_mcp_grant(owner, grant)
        storage.append_audit(
            owner,
            {
                "action": "mcp.grant.revoked",
                "resource_type": "mcp_grant",
                "resource_id": grant_id,
                "client_name": grant["client_name"],
                "outcome": "success",
                "occurred_at": _now().isoformat(),
            },
        )
    return _public_grant(grant)


def _find_grant(storage, field: str, token: str) -> dict | None:
    token_hash = _hash(token)
    for grant in storage.list_all_mcp_grants():
        stored = grant.get(field)
        if stored and hmac.compare_digest(stored, token_hash):
            return grant
    return None


def _issue_tokens(storage, grant: dict) -> dict:
    now = _now()
    access_token = secrets.token_urlsafe(32)
    refresh_token = secrets.token_urlsafe(48)
    grant.update(
        {
            "authorization_code_hash": None,
            "access_token_hash": _hash(access_token),
            "access_token_expires_at": (now + ACCESS_LIFETIME).isoformat(),
            "refresh_token_hash": _hash(refresh_token),
            "refresh_token_expires_at": (now + REFRESH_LIFETIME).isoformat(),
        }
    )
    storage.save_mcp_grant(grant["owner"], grant)
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": int(ACCESS_LIFETIME.total_seconds()),
        "refresh_token": refresh_token,
        "scope": " ".join(grant["scopes"]),
    }


@router.post("/mcp/oauth/token")
async def oauth_token(request: Request) -> JSONResponse:
    if "application/x-www-form-urlencoded" not in request.headers.get("content-type", ""):
        raise HTTPException(status_code=415, detail="form-encoded OAuth request required")
    values = {
        key: items[-1]
        for key, items in urllib.parse.parse_qs((await request.body()).decode()).items()
    }
    storage = request.app.state.storage
    grant_type = values.get("grant_type")
    if grant_type == "authorization_code":
        grant = _find_grant(storage, "authorization_code_hash", values.get("code", ""))
        valid = bool(
            grant
            and grant["status"] == "active"
            and grant["client_id"] == values.get("client_id")
            and grant["redirect_uri"] == values.get("redirect_uri")
            and datetime.fromisoformat(grant["authorization_code_expires_at"]) > _now()
            and hmac.compare_digest(
                grant["code_challenge"], _pkce_challenge(values.get("code_verifier", ""))
            )
            and values.get("resource", grant.get("resource")) == grant.get("resource")
        )
    elif grant_type == "refresh_token":
        grant = _find_grant(storage, "refresh_token_hash", values.get("refresh_token", ""))
        valid = bool(
            grant
            and grant["status"] == "active"
            and grant["client_id"] == values.get("client_id")
            and datetime.fromisoformat(grant["refresh_token_expires_at"]) > _now()
            and values.get("resource", grant.get("resource")) == grant.get("resource")
        )
    else:
        raise HTTPException(status_code=400, detail="unsupported_grant_type")
    if not valid or grant is None:
        raise HTTPException(status_code=401, detail="invalid_grant")
    return JSONResponse(
        _issue_tokens(storage, grant),
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _bearer_grant(request: Request, *, require_mcp_resource: bool = False) -> dict:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="OAuth bearer token required",
            headers=_oauth_challenge(request),
        )
    grant = _find_grant(
        request.app.state.storage, "access_token_hash", authorization[7:]
    )
    if (
        grant is None
        or grant["status"] != "active"
        or datetime.fromisoformat(grant["access_token_expires_at"]) <= _now()
        or (
            require_mcp_resource
            and grant.get("resource") not in {None, _resource_uri(request)}
        )
    ):
        raise HTTPException(
            status_code=401,
            detail="invalid or expired access token",
            headers=_oauth_challenge(request, "invalid_token"),
        )
    grant["last_used_at"] = _now().isoformat()
    request.app.state.storage.save_mcp_grant(grant["owner"], grant)
    return grant


def _accounting(storage, owner: str) -> dict:
    accounts = [
        item
        for item in storage.list_financial_records(owner, "account")
        if not item.get("removed")
    ]
    return build_accounting_view(
        storage.list_financial_records(owner, "transaction"),
        storage.list_transaction_adjustments(owner),
        storage.list_provider_record_versions(owner, "transaction"),
        effective_classifications(storage, owner),
        {item["account_id"] for item in accounts if item.get("type") == "credit"},
    )


def _summary(storage, owner: str) -> dict:
    accounting = _accounting(storage, owner)
    accounts = {
        (item.get("connection_id"), item["account_id"]): item
        for item in storage.list_financial_records(owner, "account")
        if not item.get("removed")
    }
    balances: dict[tuple, dict] = {}
    for item in storage.list_financial_records(owner, "balance"):
        if item.get("removed"):
            continue
        key = (item.get("connection_id"), item["account_id"])
        if key not in balances or item.get("observed_at", "") > balances[key].get("observed_at", ""):
            balances[key] = item
    cash = current_card_balance = investments = custodial_investments = 0.0
    for key, balance in balances.items():
        account_type = accounts.get(key, {}).get("type")
        value = float(balance.get("current") or 0)
        if account_type == "depository":
            cash += value
        elif account_type == "credit":
            current_card_balance += max(0, value)
        elif account_type == "investment":
            if is_custodial_account(accounts.get(key)):
                custodial_investments += value
            else:
                investments += value
    assets = [item for item in storage.list_financial_records(owner, "household_asset") if not item.get("removed")]
    asset_value = sum(float(item["valuation"]["amount"]) for item in assets)
    registered_asset_debt = sum(
        float(item["ownership"]["debt_balance"]) for item in assets
    )
    liabilities = current_card_balance + registered_asset_debt
    return {
        "currency": "USD",
        "metrics": {
            "net_worth": round(cash + investments + asset_value - liabilities, 2),
            "cash": round(cash, 2),
            "current_card_balance": round(current_card_balance, 2),
            "registered_asset_debt": round(registered_asset_debt, 2),
            "investment_value": round(investments, 2),
            "custodial_investment_value": round(custodial_investments, 2),
            "income": accounting["metrics"]["income"],
            "adjusted_personal_spending": accounting["metrics"]["finalized_spending"]["adjusted"],
        },
        "freshness": {
            "balance_as_of": min((item.get("observed_at") for item in balances.values()), default=None),
        },
    }


def _date_range(arguments: dict) -> tuple[date, date]:
    try:
        start = date.fromisoformat(str(arguments["start"]))
        end = date.fromisoformat(str(arguments["end"]))
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=422, detail="start and end ISO dates are required") from error
    if end < start or (end - start).days > 31:
        raise HTTPException(status_code=422, detail="date range must be ordered and no longer than 31 days")
    return start, end


def _module_json(base_url: str, path: str) -> dict:
    """Fetch one fixed, internal module endpoint without accepting agent URLs.

    The module base URL is deployment configuration, never a tool argument.
    Keeping both the host and path server-owned prevents this gateway from
    becoming an SSRF primitive while still letting it compose read-only views
    from the separately deployed personal applications.
    """
    if not base_url:
        raise HTTPException(status_code=503, detail="module is not configured")
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise HTTPException(status_code=503, detail="module configuration is invalid")
    url = f"{base_url.rstrip('/')}{path}"
    headers = {"Accept": "application/json", "User-Agent": "ggouger-personal-mcp/1.0"}
    # Finance is isolated from the app network. Its Caddy bridge is a private,
    # un-published listener and the header is set only by deployment config;
    # an MCP client cannot influence either host or path.
    host_header = os.environ.get("MCP_MODULE_HOST", "")
    if host_header:
        headers["Host"] = host_header
    if os.environ.get("MCP_MODULE_INTERNAL") == "1":
        headers["X-Internal-MCP-Module"] = "1"
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            payload = response.read(256 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise HTTPException(status_code=503, detail="module is temporarily unavailable") from error
    if len(payload) > 256 * 1024:
        raise HTTPException(status_code=503, detail="module response exceeded the safe limit")
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(status_code=503, detail="module returned an invalid response") from error
    if not isinstance(value, dict):
        raise HTTPException(status_code=503, detail="module returned an invalid response")
    return value


def _athletics_summary() -> dict:
    source = _module_json(os.environ.get("ATHLETICS_MCP_BASE_URL", ""), "/athletics/summary")
    if not source.get("ok"):
        raise HTTPException(status_code=503, detail="Athletic Analytics summary is unavailable")
    # This is intentionally the same aggregate-only projection as the public
    # site preview—no routes, coordinates, timestamps, activity titles, or
    # per-workout heart-rate data cross the personal MCP boundary.
    keys = (
        "activities", "runs", "lift_sessions", "total_miles", "run_miles",
        "this_week_miles", "this_month_miles", "this_week_lift_sessions",
        "this_month_lift_sessions", "longest_run_mi", "since",
        "weekly_miles", "weekly_training", "lift_maxes", "big3_total",
    )
    return {key: source[key] for key in keys if key in source}


def _library_metrics() -> dict:
    owner = os.environ.get("BOOKS_MCP_OWNER", "")
    if not owner or not owner.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=503, detail="Library module is not configured")
    source = _module_json(
        os.environ.get("BOOKS_MCP_BASE_URL", ""),
        "/library/metrics",
    )
    # The owner-less projection intentionally excludes purchase value and
    # prices. It gives an agent enough context for reading analysis without
    # turning Library into a secondary financial-data path.
    keys = (
        "counts", "tiers", "formats", "read_vs_listened", "categories",
        "lifetime", "this_year", "by_year", "records", "authors", "rating_hist",
    )
    return {key: source[key] for key in keys if key in source}


def _execute_tool(storage, owner: str, payload: ToolCall) -> tuple[dict, str, dict | None]:
    accounting = _accounting(storage, owner) if payload.tool.startswith("finance.") else None
    if payload.tool == "finance.summary":
        return _summary(storage, owner), "aggregate", None
    if payload.tool == "finance.metric_definitions":
        return {
            "metrics": {
                "net_worth": "Known assets minus registered asset debt and the current card-balance snapshot.",
                "cash": "Latest connected depository balances.",
                "current_card_balance": "A transient provider-reported snapshot, not long-term debt.",
                "registered_asset_debt": "Debt explicitly registered against a home or vehicle.",
                "investment_value": "Latest household investment balances, excluding children's UTMA and UGMA accounts.",
                "custodial_investment_value": "Children's UTMA and UGMA balances, tracked separately and excluded from household net worth.",
                "adjusted_personal_spending": "Posted purchases after refunds and owner adjustments.",
            }
        }, "aggregate", None
    if payload.tool == "finance.spending_breakdown":
        assert accounting is not None
        return {"currency": "USD", "categories": accounting["metrics"]["spending_by_category"], "merchants_redacted": True}, "aggregate", None
    if payload.tool == "finance.cash_flow_trend":
        assert accounting is not None
        monthly: dict[str, float] = {}
        for item in accounting["transactions"]:
            if item["status"] != "posted":
                continue
            month = item["date"][:7]
            monthly[month] = round(
                monthly.get(month, 0) + item.get("cash_effect", 0), 2
            )
        return {
            "currency": "USD",
            "monthly_net_depository_movement": monthly,
            "definition": "Posted bank-account credits minus debits; credit-card refunds, purchases, and payment receipts are excluded.",
        }, "aggregate", None
    if payload.tool == "finance.investments.summary":
        holdings = [item for item in storage.list_financial_records(owner, "holding") if not item.get("removed")]
        accounts = {
            item["account_id"]: item
            for item in storage.list_financial_records(owner, "account")
            if not item.get("removed")
        }
        household = [item for item in holdings if not is_custodial_account(accounts.get(item.get("account_id")))]
        custodial = [item for item in holdings if is_custodial_account(accounts.get(item.get("account_id")))]
        return {
            "currency": "USD",
            "market_value": round(sum(float(item.get("institution_value") or 0) for item in household), 2),
            "position_count": len(household),
            "custodial_market_value": round(sum(float(item.get("institution_value") or 0) for item in custodial), 2),
            "custodial_position_count": len(custodial),
            "definition": "Household investments exclude children's UTMA and UGMA assets; custodial totals are reported separately.",
        }, "aggregate", None
    if payload.tool == "finance.scenarios.list":
        return {"scenarios": [_redact({key: value for key, value in item.items() if key != "output"}) for item in storage.list_scenarios(owner)]}, "private_scenario", None
    if payload.tool == "finance.scenario.calculate":
        calculator = payload.arguments.get("calculator")
        inputs = payload.arguments.get("inputs", {})
        try:
            result = calculate_scenario(str(calculator), inputs)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return _redact(result), "calculated_scenario", None
    if payload.tool == "finance.transactions.list":
        assert accounting is not None
        start, end = _date_range(payload.arguments)
        transactions = [
            {key: item[key] for key in ("id", "date", "amount", "merchant_name", "accounting_type", "category")}
            for item in accounting["transactions"]
            if start <= date.fromisoformat(item["date"]) <= end
        ]
        date_range = {"start": start.isoformat(), "end": end.isoformat()}
        return {"currency": "USD", "transactions": transactions}, "transaction_detail", date_range
    if payload.tool == "athletics.training.summary":
        return _athletics_summary(), "aggregate_training", None
    if payload.tool == "library.reading.metrics":
        return _library_metrics(), "aggregate_library", None
    raise HTTPException(status_code=404, detail="unknown MCP tool")


def _call_finance_tool_for_grant(
    payload: ToolCall, request: Request, grant: dict
) -> dict:
    required_scope = TOOL_SCOPES.get(payload.tool)
    if required_scope is None:
        raise HTTPException(status_code=404, detail="unknown Finance tool")
    storage = request.app.state.storage
    audit_base = {
        "action": "mcp.tool.called",
        "resource_type": "mcp_tool",
        "resource_id": payload.tool,
        "grant_id": grant["id"],
        "client_id": grant["client_id"],
        "client_name": grant["client_name"],
        "scope": required_scope,
        "tool": payload.tool,
        "occurred_at": _now().isoformat(),
    }
    if required_scope not in grant["scopes"]:
        storage.append_audit(grant["owner"], {**audit_base, "outcome": "denied", "sensitivity": "not_returned", "date_range": None})
        raise HTTPException(status_code=403, detail=f"scope {required_scope} required")
    try:
        data, sensitivity, date_range = _execute_tool(storage, grant["owner"], payload)
    except HTTPException:
        storage.append_audit(grant["owner"], {**audit_base, "outcome": "failure", "sensitivity": "not_returned", "date_range": {key: payload.arguments.get(key) for key in ("start", "end")} if "start" in payload.arguments or "end" in payload.arguments else None})
        raise
    storage.append_audit(grant["owner"], {**audit_base, "outcome": "success", "sensitivity": sensitivity, "date_range": date_range})
    return {"tool": payload.tool, "sensitivity": sensitivity, "date_range": date_range, "data": _redact(data)}


@router.post("/mcp/tools/call")
@router.post("/api/internal/mcp/finance/tools/call")
def call_finance_tool(payload: ToolCall, request: Request) -> dict:
    return _call_finance_tool_for_grant(payload, request, _bearer_grant(request))


MCP_TOOLS = {
    "finance.summary": {
        "description": "Read aggregate household balances, spending, and freshness without account numbers or individual transactions.",
        "inputSchema": {"type": "object", "additionalProperties": False},
    },
    "finance.cash_flow_trend": {
        "description": "Read aggregate monthly depository movement, with transfers and card activity kept distinct from spending.",
        "inputSchema": {"type": "object", "additionalProperties": False},
    },
    "finance.metric_definitions": {
        "description": "Read the definitions and boundaries for aggregate Finance metrics.",
        "inputSchema": {"type": "object", "additionalProperties": False},
    },
    "finance.spending_breakdown": {
        "description": "Read aggregate spending by category without merchant or transaction-level data.",
        "inputSchema": {"type": "object", "additionalProperties": False},
    },
    "finance.investments.summary": {
        "description": "Read aggregate household and custodial investment totals without tax lots or account numbers.",
        "inputSchema": {"type": "object", "additionalProperties": False},
    },
    "finance.scenarios.list": {
        "description": "List saved financial scenarios with direct identifiers redacted.",
        "inputSchema": {"type": "object", "additionalProperties": False},
    },
    "finance.scenario.calculate": {
        "description": "Run an allowlisted Finance calculator with supplied assumptions; it never saves or changes data.",
        "inputSchema": {
            "type": "object",
            "properties": {"calculator": {"type": "string"}, "inputs": {"type": "object"}},
            "required": ["calculator", "inputs"],
            "additionalProperties": False,
        },
    },
    "finance.transactions.list": {
        "description": "Read minimally scoped transaction detail for an explicit period of 31 days or less.",
        "inputSchema": {
            "type": "object",
            "properties": {"start": {"type": "string", "format": "date"}, "end": {"type": "string", "format": "date"}},
            "required": ["start", "end"],
            "additionalProperties": False,
        },
    },
    "athletics.training.summary": {
        "description": "Read aggregate running and lifting trends without routes, activity timestamps, titles, or per-workout detail.",
        "inputSchema": {"type": "object", "additionalProperties": False},
    },
    "library.reading.metrics": {
        "description": "Read aggregate reading and listening metrics without book purchase values or prices.",
        "inputSchema": {"type": "object", "additionalProperties": False},
    },
}


def _jsonrpc_error(request_id: Any, code: int, message: str, data: Any = None) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _mcp_response(request_id: Any, result: dict, *, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "result": result},
        headers=headers or {},
    )


def _mcp_tool_list(grant: dict) -> list[dict]:
    return [
        {
            "name": name,
            **definition,
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        }
        for name, definition in MCP_TOOLS.items()
        if TOOL_SCOPES[name] in grant["scopes"]
    ]


def _validate_mcp_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != _issuer(request):
        raise HTTPException(status_code=403, detail="MCP Origin is not allowed")


@router.get("/mcp")
def mcp_stream_get(request: Request) -> Response:
    _validate_mcp_origin(request)
    # This server is intentionally request/response-only; it does not offer a
    # server-initiated SSE stream.
    return Response(status_code=405, headers={"Allow": "POST"})


@router.post("/mcp")
async def mcp_streamable_http(request: Request) -> Response:
    _validate_mcp_origin(request)
    accept = request.headers.get("accept", "")
    if "application/json" not in accept or "text/event-stream" not in accept:
        return JSONResponse(
            _jsonrpc_error(None, -32600, "Accept must include application/json and text/event-stream"),
            status_code=406,
        )
    try:
        message = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(_jsonrpc_error(None, -32700, "Parse error"), status_code=400)
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return JSONResponse(_jsonrpc_error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request"), status_code=400)
    method = message["method"]
    request_id = message.get("id")
    grant = _bearer_grant(request, require_mcp_resource=True)
    if request_id is None:
        if method == "notifications/initialized":
            return Response(status_code=202)
        return Response(status_code=202)
    if method == "initialize":
        params = message.get("params") or {}
        version = params.get("protocolVersion")
        if version not in {MCP_PROTOCOL_VERSION, "2025-03-26"}:
            return JSONResponse(_jsonrpc_error(request_id, -32602, "Unsupported protocol version"), status_code=400)
        return _mcp_response(
            request_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "Gordon Gouger Personal Data", "version": "1.1"},
                "instructions": "Read-only personal Finance, Athletic Analytics, and Library tools. Aggregate tools avoid direct identifiers; financial transaction detail requires an explicit bounded date range.",
            },
        )
    if method == "tools/list":
        return _mcp_response(request_id, {"tools": _mcp_tool_list(grant)})
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            payload = ToolCall(tool=params.get("name", ""), arguments=params.get("arguments") or {})
        except Exception as error:
            return _mcp_response(
                request_id,
                {"content": [{"type": "text", "text": str(error)}], "isError": True},
            )
        try:
            tool_result = _call_finance_tool_for_grant(payload, request, grant)
        except HTTPException as error:
            if error.status_code in {401, 403}:
                raise
            return _mcp_response(
                request_id,
                {"content": [{"type": "text", "text": str(error.detail)}], "isError": True},
            )
        return _mcp_response(
            request_id,
            {
                "content": [
                    {"type": "text", "text": json.dumps(tool_result, sort_keys=True)}
                ],
                "structuredContent": tool_result,
                "isError": False,
            },
        )
    return JSONResponse(_jsonrpc_error(request_id, -32601, "Method not found"))
