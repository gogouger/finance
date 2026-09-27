import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse


app = FastAPI()
issuer = os.environ["FAKE_OIDC_ISSUER"].rstrip("/")
private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
public_numbers = private_key.public_key().public_numbers()
codes: dict[str, dict] = {}
mode = "passkey"


@app.get("/health")
def health():
    return {"status": "ok"}


def _b64(value: int) -> str:
    size = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(size, "big")).decode().rstrip("=")


@app.get("/.well-known/openid-configuration")
def discovery():
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/jwks",
        "end_session_endpoint": f"{issuer}/logout",
    }


@app.get("/jwks")
def jwks():
    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": "fake-key",
                "use": "sig",
                "alg": "RS256",
                "n": _b64(public_numbers.n),
                "e": _b64(public_numbers.e),
            }
        ]
    }


@app.post("/test/mode/{value}")
def set_mode(value: str):
    global mode
    mode = value
    return {"mode": mode}


@app.get("/authorize")
def authorize(request: Request):
    query = request.query_params
    verifier_challenge = query["code_challenge"]
    code = secrets.token_urlsafe(16)
    codes[code] = {
        "client_id": query["client_id"],
        "redirect_uri": query["redirect_uri"],
        "nonce": query["nonce"],
        "code_challenge": verifier_challenge,
        "mode": mode,
        "max_age": query.get("max_age"),
    }
    location = query["redirect_uri"] + "?" + urllib.parse.urlencode(
        {"code": code, "state": query["state"]}
    )
    return RedirectResponse(location, status_code=302)


@app.post("/token")
async def token(request: Request):
    form = urllib.parse.parse_qs((await request.body()).decode())
    code = form["code"][0]
    record = codes.pop(code)
    verifier = form["code_verifier"][0]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    if challenge != record["code_challenge"]:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    now = int(time.time())
    selected = record["mode"]
    claims = {
        "iss": issuer,
        "aud": "finance",
        "azp": "finance",
        "sub": "alice",
        "preferred_username": "alice",
        "iat": now,
        "exp": now + 600,
        "nonce": record["nonce"],
        "auth_time": now,
        "amr": ["hwk", "user"],
    }
    if selected == "password":
        claims["amr"] = ["pwd", "mfa"]
    elif selected == "recovery":
        claims["amr"] = ["recovery"]
    elif selected == "stale":
        claims["auth_time"] = now - 900
    elif selected == "bad_nonce":
        claims["nonce"] = "wrong"
    elif selected == "bad_audience":
        claims["aud"] = "someone-else"
    elif selected == "bad_azp":
        claims["aud"] = ["finance", "someone-else"]
        claims["azp"] = "someone-else"
    elif selected == "wrong_owner":
        claims["sub"] = "mallory"
        claims["preferred_username"] = "mallory"

    return {
        "access_token": "unused",
        "token_type": "Bearer",
        "expires_in": 600,
        "id_token": jwt.encode(
            claims,
            private_key,
            algorithm="RS256",
            headers={"kid": "fake-key"},
        ),
    }


@app.get("/logout")
def logout(post_logout_redirect_uri: str):
    return RedirectResponse(post_logout_redirect_uri, status_code=302)
