import os
import hmac
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import require_owner
from .classification import classification_review_queue
from .dashboard import financial_dashboard
from .recurring import recurring_costs


router = APIRouter()


class WeeklyReviewRequest(BaseModel):
    link_ttl_hours: int = Field(default=168, ge=0, le=168)


class ReplyPayload(BaseModel):
    token: str = Field(min_length=20, max_length=500)
    sender: str = Field(min_length=3, max_length=320)
    provider_message_id: str = Field(min_length=1, max_length=500)
    spf: Literal["pass", "fail", "neutral", "none"]
    dkim: Literal["pass", "fail", "neutral", "none"]
    dmarc: Literal["pass", "fail", "none"]
    raw_message: str = Field(min_length=1, max_length=1_000_000)


class RawExpiryRequest(BaseModel):
    as_of: datetime


def _strip_quoted_content(raw_message: str) -> str:
    kept = []
    for line in raw_message.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped.startswith("On ") and stripped.endswith("wrote:"):
            break
        if stripped.casefold() == "-----original message-----":
            break
        if stripped.startswith(">"):
            continue
        kept.append(line.rstrip())
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept).strip()


def _review_items(request: Request) -> list[dict]:
    items = []
    classification_count = len(classification_review_queue(request)["items"])
    if classification_count:
        items.append(
            {
                "kind": "classification",
                "summary": "Transactions need category review",
                "count": classification_count,
                "confirmation_required": True,
                "authenticated_action": "/dashboard?review=classification",
            }
        )
    recurring_count = len(recurring_costs(request, date.today())["proposals"])
    if recurring_count:
        items.append(
            {
                "kind": "recurring_cost",
                "summary": "Recurring-cost patterns need confirmation",
                "count": recurring_count,
                "confirmation_required": True,
                "authenticated_action": "/dashboard?review=recurring",
            }
        )
    unusual_count = len(financial_dashboard(request)["unusual_activity"])
    if unusual_count:
        items.append(
            {
                "kind": "unusual_activity",
                "summary": "Unusual activity signals are ready for review",
                "count": unusual_count,
                "confirmation_required": True,
                "authenticated_action": "/dashboard?review=unusual",
            }
        )
    connection_count = len(
        [
            item
            for item in request.app.state.storage.list_connections(
                require_owner(request)
            )
            if item.get("status") != "healthy"
        ]
    )
    if connection_count:
        items.append(
            {
                "kind": "connection_health",
                "summary": "A data connection needs attention",
                "count": connection_count,
                "confirmation_required": True,
                "authenticated_action": "/settings/connections",
            }
        )
    return items


@router.post("/api/private/reviews/weekly/send")
def send_weekly_review(
    payload: WeeklyReviewRequest, request: Request
) -> dict:
    owner = require_owner(request)
    recipient = os.environ.get("FINANCE_REVIEW_EMAIL")
    sender = os.environ.get("FINANCE_MAIL_FROM")
    base_url = os.environ.get("FINANCE_BASE_URL", "").rstrip("/")
    if not recipient or not sender or not base_url or request.app.state.mail is None:
        raise HTTPException(status_code=503, detail="review mail is not configured")
    items = _review_items(request)
    if not items:
        return {"sent": False, "reason": "no_meaningful_review_items", "item_count": 0}

    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=payload.link_ttl_hours)
    token = request.app.state.storage.create_email_review(
        owner,
        {
            "created_at": now.isoformat(),
            "recipient": recipient,
            "items": items,
            "item_count": sum(item["count"] for item in items),
        },
        expires_at,
    )
    review_url = f"{base_url}/api/private/reviews/{token}"
    lines = [
        "Your weekly Finance review is ready.",
        "",
        *[f"- {item['summary']} ({item['count']})" for item in items],
        "",
        "No amounts, merchants, account identifiers, or transaction details are included in this email.",
        f"Sign in with your passkey to review and confirm: {review_url}",
        "You may reply with context, but email replies never authorize changes.",
    ]
    body = "\n".join(lines)
    message_id = request.app.state.mail.send(
        recipient=recipient,
        subject="Finance weekly review",
        body=body,
    )
    return {
        "sent": True,
        "transport": request.app.state.mail.mode,
        "message_id": message_id,
        "recipient": recipient,
        "item_count": sum(item["count"] for item in items),
        "review_url": review_url,
        "expires_at": expires_at.isoformat(),
        "redacted_body": body,
    }


@router.get("/api/private/reviews/{token}")
def get_email_review(token: str, request: Request) -> dict:
    owner = require_owner(request)
    review = request.app.state.storage.get_email_review(token, owner)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    if review["expired"]:
        raise HTTPException(status_code=410, detail="review link expired")
    return {
        "created_at": review["created_at"],
        "expires_at": review["expires_at"],
        "item_count": review["item_count"],
        "items": review["items"],
        "reply_received": review["reply_received_at"] is not None,
    }


@router.post("/api/public/email/replies")
def ingest_email_reply(
    payload: ReplyPayload,
    request: Request,
    x_mail_webhook_key: str | None = Header(default=None),
) -> dict:
    expected_webhook_key = os.environ.get("FINANCE_MAIL_WEBHOOK_KEY")
    if not expected_webhook_key or x_mail_webhook_key != expected_webhook_key:
        raise HTTPException(status_code=401, detail="invalid mail webhook credential")
    review = request.app.state.storage.get_email_review(payload.token)
    if review is None:
        raise HTTPException(status_code=404, detail="reply token not found")

    now = datetime.now(UTC)
    expected_sender = str(review["recipient"])
    sender_match = payload.sender.strip().casefold() == expected_sender.casefold()
    evidence = {
        "spf": payload.spf,
        "dkim": payload.dkim,
        "dmarc": payload.dmarc,
        "sender_match": sender_match,
        "token_found": True,
        "token_expired": review["expired"],
        "token_unique": review["reply_received_at"] is None,
    }
    passes_transport_authentication = all(
        value == "pass" for value in (payload.spf, payload.dkim, payload.dmarc)
    )
    accepted = (
        passes_transport_authentication
        and sender_match
        and not review["expired"]
        and evidence["token_unique"]
    )
    if accepted:
        accepted = request.app.state.storage.claim_email_reply_token(
            payload.token, now.isoformat()
        )
        evidence["token_unique"] = accepted

    extracted = _strip_quoted_content(payload.raw_message) if accepted else ""
    if not extracted:
        accepted = False
    reply_id = str(uuid4())
    record = {
        "id": reply_id,
        "received_at": now.isoformat(),
        "raw_expires_at": (now + timedelta(days=90)).isoformat(),
        "raw_message": payload.raw_message,
        "status": (
            "accepted_as_untrusted_proposal" if accepted else "quarantined"
        ),
        "validation_evidence": evidence,
        "note": (
            {
                "text": extracted,
                "kind": "proposal",
                "trust": "untrusted",
                "authority": "none",
                "requires_authenticated_confirmation": True,
            }
            if accepted
            else None
        ),
        "provenance": {
            "reply_id": reply_id,
            "review_created_at": review["created_at"],
            "provider_message_id": payload.provider_message_id,
            "sender": payload.sender,
            "received_at": now.isoformat(),
        },
        "state_changes_applied": [],
    }
    request.app.state.storage.save_email_reply(review["owner"], record)
    return {
        "id": reply_id,
        "status": record["status"],
        "state_changes_applied": [],
    }


@router.get("/api/private/email/notes")
def list_email_notes(request: Request) -> dict:
    owner = require_owner(request)
    replies = request.app.state.storage.list_email_replies(owner)
    notes = []
    for reply in replies:
        if reply.get("note") is None:
            continue
        notes.append(
            {
                **reply["note"],
                "id": reply["id"],
                "received_at": reply["received_at"],
                "raw_expires_at": reply["raw_expires_at"],
                "raw_available": "raw_message" in reply,
                "validation_evidence": reply["validation_evidence"],
                "provenance": reply["provenance"],
                "state_changes_applied": reply["state_changes_applied"],
            }
        )
    return {"notes": notes}


@router.get("/api/private/email/replies")
def list_email_reply_audit(request: Request) -> dict:
    owner = require_owner(request)
    replies = request.app.state.storage.list_email_replies(owner)
    return {
        "replies": [
            {
                "id": reply["id"],
                "status": reply["status"],
                "validation_evidence": reply["validation_evidence"],
                "raw_available": "raw_message" in reply,
                "note_created": reply.get("note") is not None,
                "state_changes_applied": reply["state_changes_applied"],
            }
            for reply in replies
        ]
    }


@router.post("/api/internal/email/expire-raw")
def expire_email_reply_raw(
    payload: RawExpiryRequest,
    request: Request,
    x_internal_key: str | None = Header(default=None),
) -> dict:
    expected = os.environ.get("FINANCE_INTERNAL_KEY")
    if (
        not expected
        or x_internal_key is None
        or not hmac.compare_digest(x_internal_key, expected)
    ):
        raise HTTPException(status_code=401, detail="invalid internal credential")
    as_of = payload.as_of
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    purged = request.app.state.storage.purge_expired_email_reply_raw(as_of)
    return {"purged": purged, "retention_days": 90}
