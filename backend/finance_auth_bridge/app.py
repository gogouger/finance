import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response


FLOW_COOKIE = "__Secure-finance_oidc_flow"
SESSION_COOKIE = "__Host-finance_oidc_session"
PASSKEY_METHODS = {"hwk", "swk"}
USER_VERIFICATION_METHODS = {"user", "pin"}


class OIDCError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    enabled: bool
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    post_logout_uri: str
    allow_http_for_tests: bool
    allowed_subjects: frozenset[str]
    allowed_usernames: frozenset[str]
    backchannel_url: str = ""
    session_seconds: int = 12 * 60 * 60
    freshness_seconds: int = 300

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            enabled=os.environ.get("FINANCE_OIDC_BRIDGE_ENABLED", "false").lower()
            == "true",
            issuer=os.environ.get("FINANCE_OIDC_ISSUER", "").rstrip("/"),
            client_id=os.environ.get("FINANCE_OIDC_CLIENT_ID", ""),
            client_secret=os.environ.get("FINANCE_OIDC_CLIENT_SECRET", ""),
            redirect_uri=os.environ.get("FINANCE_OIDC_REDIRECT_URI", ""),
            post_logout_uri=os.environ.get("FINANCE_OIDC_POST_LOGOUT_URI", ""),
            allow_http_for_tests=os.environ.get(
                "FINANCE_OIDC_ALLOW_HTTP_FOR_TESTS", "false"
            ).lower()
            == "true",
            allowed_subjects=frozenset(
                value.strip()
                for value in os.environ.get(
                    "FINANCE_OIDC_ALLOWED_SUBJECTS", ""
                ).split(",")
                if value.strip()
            ),
            allowed_usernames=frozenset(
                value.strip()
                for value in os.environ.get(
                    "FINANCE_OIDC_ALLOWED_USERNAMES", ""
                ).split(",")
                if value.strip()
            ),
            backchannel_url=os.environ.get(
                "FINANCE_OIDC_BACKCHANNEL_URL", ""
            ).rstrip("/"),
        )

    def validate(self) -> None:
        if not self.enabled:
            return
        required = {
            "issuer": self.issuer,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "post_logout_uri": self.post_logout_uri,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise OIDCError(f"missing OIDC bridge configuration: {', '.join(missing)}")
        if not self.allowed_subjects and not self.allowed_usernames:
            raise OIDCError("OIDC bridge owner allowlist is empty")
        for value in (self.issuer, self.redirect_uri, self.post_logout_uri):
            parsed = urllib.parse.urlparse(value)
            scheme = parsed.scheme
            if scheme != "https" and not (
                self.allow_http_for_tests
                and scheme == "http"
                and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            ):
                raise OIDCError("OIDC bridge URLs must use HTTPS")
        if self.backchannel_url:
            parsed = urllib.parse.urlparse(self.backchannel_url)
            if (parsed.scheme, parsed.hostname, parsed.path) != ("http", "caddy", ""):
                raise OIDCError("OIDC backchannel must be the private Caddy service")


class ServerState:
    _maximum_records = 10_000

    def __init__(self):
        self._lock = threading.Lock()
        self._flows: dict[str, dict[str, Any]] = {}
        self._sessions: dict[str, dict[str, Any]] = {}

    def create_flow(self, flow: dict[str, Any]) -> str:
        identifier = secrets.token_urlsafe(32)
        with self._lock:
            now = time.time()
            self._flows = {
                key: value
                for key, value in self._flows.items()
                if float(value["expires_at"]) >= now
            }
            if len(self._flows) >= self._maximum_records:
                self._flows.pop(next(iter(self._flows)))
            self._flows[identifier] = flow
        return identifier

    def consume_flow(self, identifier: str) -> dict[str, Any] | None:
        with self._lock:
            flow = self._flows.pop(identifier, None)
        if not flow or float(flow["expires_at"]) < time.time():
            return None
        return flow

    def create_session(self, session: dict[str, Any]) -> str:
        identifier = secrets.token_urlsafe(48)
        with self._lock:
            now = time.time()
            self._sessions = {
                key: value
                for key, value in self._sessions.items()
                if float(value["expires_at"]) >= now
            }
            if len(self._sessions) >= self._maximum_records:
                self._sessions.pop(next(iter(self._sessions)))
            self._sessions[identifier] = session
        return identifier

    def session(self, identifier: str) -> dict[str, Any] | None:
        with self._lock:
            session = self._sessions.get(identifier)
            if session and float(session["expires_at"]) < time.time():
                self._sessions.pop(identifier, None)
                session = None
        return session

    def revoke(self, identifier: str) -> dict[str, Any] | None:
        with self._lock:
            return self._sessions.pop(identifier, None)


settings = Settings.from_environment()
server_state = ServerState()
app = FastAPI(
    title="Finance OIDC Claims Bridge",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def _disabled() -> None:
    if not settings.enabled:
        raise HTTPException(status_code=503, detail="OIDC claims bridge is disabled")
    try:
        settings.validate()
    except OIDCError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def _same_origin(url: str) -> None:
    expected = urllib.parse.urlparse(settings.issuer)
    actual = urllib.parse.urlparse(url)
    if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
        raise OIDCError("OIDC metadata endpoint origin does not match issuer")


def _backchannel_url(url: str) -> str:
    _same_origin(url)
    if not settings.backchannel_url:
        return url
    public = urllib.parse.urlparse(url)
    private = urllib.parse.urlparse(settings.backchannel_url)
    return urllib.parse.urlunparse(
        (private.scheme, private.netloc, public.path, public.params, public.query, "")
    )


def _response_origin_is_expected(url: str, requested_url: str) -> bool:
    actual = urllib.parse.urlparse(url)
    expected = urllib.parse.urlparse(requested_url)
    return (actual.scheme, actual.netloc) == (expected.scheme, expected.netloc)


def _json_get(url: str) -> dict[str, Any]:
    _same_origin(url)
    request_url = _backchannel_url(url)
    try:
        with urllib.request.urlopen(request_url, timeout=5) as response:
            if not _response_origin_is_expected(response.geturl(), request_url):
                raise OIDCError("OIDC backchannel redirected to an unexpected origin")
            if response.headers.get_content_type() != "application/json":
                raise OIDCError("OIDC endpoint returned an unexpected content type")
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise OIDCError("OIDC endpoint unavailable") from error


def _discovery() -> dict[str, Any]:
    document = _json_get(
        f"{settings.issuer}/.well-known/openid-configuration"
    )
    if document.get("issuer") != settings.issuer:
        raise OIDCError("OIDC discovery issuer mismatch")
    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        endpoint = document.get(field)
        if not isinstance(endpoint, str):
            raise OIDCError(f"OIDC discovery is missing {field}")
        _same_origin(endpoint)
    if document.get("end_session_endpoint"):
        _same_origin(document["end_session_endpoint"])
    return document


def _post_token(endpoint: str, fields: dict[str, str]) -> dict[str, Any]:
    _same_origin(endpoint)
    request_url = _backchannel_url(endpoint)
    encoded_client = urllib.parse.quote(settings.client_id, safe="")
    encoded_secret = urllib.parse.quote(settings.client_secret, safe="")
    basic = base64.b64encode(f"{encoded_client}:{encoded_secret}".encode()).decode()
    request = urllib.request.Request(
        request_url,
        data=urllib.parse.urlencode(fields).encode(),
        headers={
            "Accept": "application/json",
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if not _response_origin_is_expected(response.geturl(), request_url):
                raise OIDCError("OIDC backchannel redirected to an unexpected origin")
            if response.headers.get_content_type() != "application/json":
                raise OIDCError("OIDC token endpoint returned an unexpected content type")
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise OIDCError("OIDC token exchange failed") from error


def _verified_claims(
    id_token: str,
    nonce: str,
    *,
    require_fresh: bool,
) -> dict[str, Any]:
    discovery = _discovery()
    jwks = _json_get(discovery["jwks_uri"])
    try:
        header = jwt.get_unverified_header(id_token)
        if header.get("alg") not in {"RS256", "ES256"} or not header.get("kid"):
            raise OIDCError("ID token uses an unsupported signing key")
        key_data = next(
            key for key in jwks.get("keys", []) if key.get("kid") == header["kid"]
        )
        if key_data.get("use") not in (None, "sig") or key_data.get("alg") not in (
            None,
            header["alg"],
        ):
            raise OIDCError("ID token signing key metadata is invalid")
        claims = jwt.decode(
            id_token,
            jwt.PyJWK.from_dict(key_data).key,
            algorithms=[header["alg"]],
            audience=settings.client_id,
            issuer=settings.issuer,
            options={
                "require": [
                    "iss",
                    "aud",
                    "exp",
                    "iat",
                    "sub",
                    "nonce",
                    "auth_time",
                    "amr",
                ]
            },
        )
    except OIDCError:
        raise
    except Exception as error:
        raise OIDCError("ID token validation failed") from error

    if not isinstance(claims["nonce"], str) or not hmac.compare_digest(
        claims["nonce"], nonce
    ):
        raise OIDCError("ID token nonce mismatch")
    if claims.get("azp") != settings.client_id:
        raise OIDCError("ID token authorized party mismatch")
    subject = claims["sub"]
    username = claims.get("preferred_username")
    auth_time = claims["auth_time"]
    amr = claims["amr"]
    if (
        not isinstance(subject, str)
        or not subject
        or len(subject) > 255
        or any(ord(character) < 33 or ord(character) > 126 for character in subject)
    ):
        raise OIDCError("ID token subject is invalid")
    if settings.allowed_subjects and subject not in settings.allowed_subjects:
        raise OIDCError("ID token subject is not authorized")
    if settings.allowed_usernames and username not in settings.allowed_usernames:
        raise OIDCError("ID token username is not authorized")
    if isinstance(auth_time, bool) or not isinstance(auth_time, (int, float)):
        raise OIDCError("ID token auth_time is invalid")
    if auth_time <= 0 or auth_time > time.time():
        raise OIDCError("ID token auth_time is in the future")
    if not isinstance(amr, list) or not all(isinstance(value, str) for value in amr):
        raise OIDCError("ID token amr is invalid")
    methods = {value.lower() for value in amr}
    if not methods.intersection(PASSKEY_METHODS) or not methods.intersection(
        USER_VERIFICATION_METHODS
    ):
        raise OIDCError("ID token does not prove a user-verifying passkey")
    if require_fresh and time.time() - float(auth_time) > settings.freshness_seconds:
        raise OIDCError("ID token does not satisfy the fresh-authentication request")
    return claims


def _safe_return_to(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or parsed.netloc
        or "\\" in value
        or any(ord(character) < 32 for character in value)
        or len(value) > 2048
    ):
        raise HTTPException(status_code=400, detail="return_to must be a local path")
    return value


def _auth_redirect(return_to: str, sensitive: bool) -> str:
    return "/oauth2/start?" + urllib.parse.urlencode(
        {"return_to": return_to, "sensitive": str(sensitive).lower()}
    )


def _requires_fresh_auth(method: str, uri: str) -> bool:
    """Classify sensitive requests at the trusted bridge boundary.

    Caddy still sends its marker for compatibility, but this classifier keeps a
    stale proxy matcher from weakening the step-up policy as new endpoints are
    added.
    """
    path = urllib.parse.urlparse(uri).path
    method = method.upper()
    always_sensitive = (
        "/api/private/security/fresh-check",
        "/api/private/lifecycle/",
        "/api/private/mcp/grants",
        "/api/private/investments/imports/",
    )
    if path == always_sensitive[0] or any(
        path.startswith(prefix) for prefix in always_sensitive[1:]
    ):
        return True
    if path in {
        "/api/private/payroll/approval",
        "/api/private/payroll/link-token",
        "/api/private/payroll/manual",
        "/api/private/payroll/ingest",
    }:
        return True
    if path.startswith("/api/private/connections/plaid/") and method == "POST":
        return True
    if path.startswith("/api/private/connections/") and method == "DELETE":
        return True
    return False


@app.get("/health")
def health():
    if settings.enabled:
        try:
            settings.validate()
        except OIDCError:
            return JSONResponse(
                {"service": "finance-oidc-bridge", "status": "misconfigured"},
                status_code=503,
            )
    return {
        "service": "finance-oidc-bridge",
        "status": "ready" if settings.enabled else "disabled",
    }


@app.get("/oauth2/start")
def start(return_to: str = "/dashboard", sensitive: bool = False):
    _disabled()
    return_to = _safe_return_to(return_to)
    discovery = _discovery()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    flow = {
        "state": secrets.token_urlsafe(32),
        "nonce": secrets.token_urlsafe(32),
        "verifier": verifier,
        "return_to": return_to,
        "sensitive": sensitive,
        "expires_at": time.time() + 300,
    }
    flow_id = server_state.create_flow(flow)
    fields = {
        "response_type": "code",
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "scope": "openid profile",
        "state": flow["state"],
        "nonce": flow["nonce"],
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if sensitive:
        fields["max_age"] = str(settings.freshness_seconds)
    response = RedirectResponse(
        discovery["authorization_endpoint"] + "?" + urllib.parse.urlencode(fields),
        status_code=302,
    )
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        FLOW_COOKIE,
        flow_id,
        max_age=300,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/oauth2",
    )
    return response


@app.get("/oauth2/callback")
def callback(request: Request, code: str, state: str):
    _disabled()
    flow = server_state.consume_flow(request.cookies.get(FLOW_COOKIE, ""))
    if not flow or not hmac.compare_digest(state, flow["state"]):
        raise HTTPException(status_code=401, detail="invalid or expired OIDC state")
    try:
        discovery = _discovery()
        tokens = _post_token(
            discovery["token_endpoint"],
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.redirect_uri,
                "code_verifier": flow["verifier"],
            },
        )
        id_token = tokens.get("id_token")
        if not isinstance(id_token, str):
            raise OIDCError("OIDC token response is missing id_token")
        claims = _verified_claims(
            id_token,
            flow["nonce"],
            require_fresh=bool(flow["sensitive"]),
        )
    except OIDCError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error

    expires_at = min(float(claims["exp"]), time.time() + settings.session_seconds)
    server_state.revoke(request.cookies.get(SESSION_COOKIE, ""))
    session_id = server_state.create_session(
        {
            "subject": claims["sub"],
            "auth_time": float(claims["auth_time"]),
            "expires_at": expires_at,
            "id_token": id_token,
        }
    )
    response = RedirectResponse(flow["return_to"], status_code=303)
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(FLOW_COOKIE, path="/oauth2", secure=True, httponly=True)
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=max(1, int(expires_at - time.time())),
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/oauth2/auth")
def authorize_request(request: Request):
    _disabled()
    original_uri = request.headers.get("X-Forwarded-Uri", "/dashboard")
    try:
        original_uri = _safe_return_to(original_uri)
    except HTTPException:
        original_uri = "/dashboard"
    sensitive = (
        request.headers.get("X-Auth-Require-Fresh", "").lower() == "true"
        or _requires_fresh_auth(
            request.headers.get("X-Forwarded-Method", "GET"), original_uri
        )
    )
    session = server_state.session(request.cookies.get(SESSION_COOKIE, ""))
    if session is None:
        return Response(
            status_code=302,
            headers={
                "Location": _auth_redirect(original_uri, sensitive),
                "Cache-Control": "no-store",
            },
        )
    if (
        sensitive
        and time.time() - float(session["auth_time"]) > settings.freshness_seconds
    ):
        return Response(
            status_code=302,
            headers={
                "Location": _auth_redirect(original_uri, True),
                "Cache-Control": "no-store",
            },
        )
    return Response(
        status_code=204,
        headers={
            "X-Forwarded-User": session["subject"],
            "X-Auth-Method": "webauthn",
            "X-Auth-Time": str(int(session["auth_time"])),
            "Cache-Control": "no-store",
        },
    )


@app.get("/oauth2/session")
def browser_session(request: Request):
    """Expose only enough state for public navigation and fresh-auth UX."""
    session = server_state.session(request.cookies.get(SESSION_COOKIE, ""))
    authenticated = settings.enabled and session is not None
    fresh = authenticated and (
        0 <= time.time() - float(session["auth_time"]) <= settings.freshness_seconds
    )
    return JSONResponse(
        {"authenticated": authenticated, "fresh": fresh},
        headers={"Cache-Control": "no-store"},
    )


@app.post("/oauth2/logout")
def logout(request: Request):
    _disabled()
    session = server_state.revoke(request.cookies.get(SESSION_COOKIE, ""))
    destination = settings.post_logout_uri
    if session:
        try:
            endpoint = _discovery().get("end_session_endpoint")
            if endpoint:
                destination = endpoint + "?" + urllib.parse.urlencode(
                    {
                        "id_token_hint": session["id_token"],
                        "post_logout_redirect_uri": settings.post_logout_uri,
                    }
                )
        except OIDCError:
            destination = settings.post_logout_uri
    response = RedirectResponse(destination, status_code=303)
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True)
    return response


@app.get("/logged-out")
def logged_out():
    # This is the RP-initiated logout return target. Keep it public and send the
    # browser back to Finance's real signed-out experience rather than leaving
    # the user on an implementation-status JSON document.
    response = RedirectResponse("/", status_code=303)
    response.headers["Cache-Control"] = "no-store"
    return response
