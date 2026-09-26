import time

from fastapi import HTTPException, Request


def require_owner(request: Request) -> str:
    owner = request.headers.get("Remote-User") or request.headers.get(
        "X-Forwarded-User"
    )
    auth_method = request.headers.get("X-Auth-Method", "").lower()
    if not owner:
        raise HTTPException(status_code=401, detail="authentication required")
    if auth_method != "webauthn":
        raise HTTPException(status_code=403, detail="passkey authentication required")
    return owner


def require_fresh_owner(request: Request) -> str:
    owner = require_owner(request)
    try:
        authenticated_at = float(request.headers.get("X-Auth-Time", ""))
    except ValueError:
        authenticated_at = 0
    age = time.time() - authenticated_at
    if age < 0 or age > 300:
        raise HTTPException(
            status_code=403,
            detail="passkey verification within five minutes required",
        )
    return owner
