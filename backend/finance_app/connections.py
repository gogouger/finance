import hashlib
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import require_fresh_owner, require_owner
from .plaid_provider import PlaidProviderError


router = APIRouter()
PRODUCTS = {
    "banking": ["transactions"],
    "credit": ["transactions", "liabilities"],
    "investment": ["investments"],
}


class LinkTokenRequest(BaseModel):
    connection_type: Literal["banking", "credit", "investment"]
    display_name: str = Field(min_length=1, max_length=120)


class ExchangeRequest(LinkTokenRequest):
    public_token: str = Field(min_length=1, max_length=500)
    institution_id: str = Field(min_length=1, max_length=120)
    institution_name: str = Field(min_length=1, max_length=200)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _public(connection: dict) -> dict:
    return {
        key: value
        for key, value in connection.items()
        if key not in {"access_token", "owner"}
    }


def _audit(request: Request, owner: str, action: str, resource_id: str) -> None:
    request.app.state.storage.append_audit(
        owner,
        {
            "action": action,
            "resource_type": "plaid_connection",
            "resource_id": resource_id,
            "outcome": "success",
            "occurred_at": _now(),
        },
    )


@router.post("/api/private/connections/plaid/link-token")
def create_link_token(payload: LinkTokenRequest, request: Request) -> dict:
    owner = require_fresh_owner(request)
    products = PRODUCTS[payload.connection_type]
    client_user_id = hashlib.sha256(owner.encode()).hexdigest()
    try:
        provider_response = request.app.state.plaid.create_link_token(
            client_user_id, products
        )
    except PlaidProviderError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    _audit(request, owner, "plaid.link_token.created", "pending")
    return {
        "link_token": provider_response["link_token"],
        "expiration": provider_response["expiration"],
        "environment": request.app.state.plaid.environment,
        "products": products,
    }


@router.post("/api/private/connections/plaid/exchange")
def exchange_public_token(payload: ExchangeRequest, request: Request) -> dict:
    owner = require_fresh_owner(request)
    try:
        token_response = request.app.state.plaid.exchange_public_token(
            payload.public_token
        )
        source = request.app.state.plaid.get_item(token_response["access_token"])
    except PlaidProviderError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    connection = request.app.state.storage.save_connection(
        owner,
        {
            "provider": "plaid",
            "environment": request.app.state.plaid.environment,
            "display_name": payload.display_name,
            "connection_type": payload.connection_type,
            "products": PRODUCTS[payload.connection_type],
            "item_id": token_response["item_id"],
            "access_token": token_response["access_token"],
            "institution_id": payload.institution_id,
            "institution_name": payload.institution_name,
            "status": "healthy" if source.get("item", {}).get("error") is None else "user-action-required",
            "last_checked_at": _now(),
            "local_history_preserved": False,
        },
    )
    _audit(request, owner, "plaid.connection.created", connection["id"])
    return _public(connection)


@router.get("/api/private/connections")
def list_connections(request: Request) -> list[dict]:
    return [
        _public(connection)
        for connection in request.app.state.storage.list_connections(
            require_owner(request)
        )
    ]


@router.delete("/api/private/connections/{connection_id}")
def disconnect(connection_id: str, request: Request) -> dict:
    owner = require_fresh_owner(request)
    connection = request.app.state.storage.get_connection(owner, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="connection not found")
    if connection["status"] != "disconnected":
        try:
            request.app.state.plaid.remove_item(connection["access_token"])
        except PlaidProviderError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
    connection.update(
        {
            "status": "disconnected",
            "disconnected_at": _now(),
            "local_history_preserved": True,
        }
    )
    connection.pop("access_token", None)
    request.app.state.storage.replace_connection(owner, connection)
    _audit(request, owner, "plaid.connection.disconnected", connection_id)
    return _public(connection)


@router.get("/api/private/audit-events")
def list_audit_events(request: Request) -> list[dict]:
    return request.app.state.storage.list_audits(require_owner(request))
