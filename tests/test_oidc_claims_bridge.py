import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


opener = urllib.request.build_opener(NoRedirect)


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _response(request: urllib.request.Request):
    try:
        return opener.open(request)
    except urllib.error.HTTPError as response:
        return response


def _cookie(response, name: str) -> str:
    for header in response.headers.get_all("Set-Cookie", []):
        if header.startswith(f"{name}="):
            return header.split(";", 1)[0]
    raise AssertionError(f"missing {name} cookie")


def _wait(base_url: str, process: subprocess.Popen):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(f"service exited during startup\n{stdout}\n{stderr}")
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=0.2):
                return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.05)
    pytest.fail("service did not become healthy")


@contextmanager
def _process(module: str, port: int, environment: dict[str, str]):
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, **environment},
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


@pytest.fixture
def oidc_services():
    idp_port = _unused_port()
    bridge_port = _unused_port()
    issuer = f"http://127.0.0.1:{idp_port}"
    bridge = f"http://127.0.0.1:{bridge_port}"
    with _process(
        "tests.fake_oidc_provider:app",
        idp_port,
        {"FAKE_OIDC_ISSUER": issuer},
    ) as idp_process, _process(
        "backend.finance_auth_bridge.app:app",
        bridge_port,
        {
            "FINANCE_OIDC_BRIDGE_ENABLED": "true",
            "FINANCE_OIDC_ALLOW_HTTP_FOR_TESTS": "true",
            "FINANCE_OIDC_ISSUER": issuer,
            "FINANCE_OIDC_CLIENT_ID": "finance",
            "FINANCE_OIDC_CLIENT_SECRET": "test-client-secret",
            "FINANCE_OIDC_REDIRECT_URI": f"{bridge}/oauth2/callback",
            "FINANCE_OIDC_POST_LOGOUT_URI": f"{bridge}/logged-out",
        },
    ) as bridge_process:
        _wait(issuer, idp_process)
        _wait(bridge, bridge_process)
        yield issuer, bridge


def _login(
    issuer: str,
    bridge: str,
    *,
    sensitive: bool = False,
    existing_session: str | None = None,
):
    query = urllib.parse.urlencode(
        {"return_to": "/dashboard", "sensitive": str(sensitive).lower()}
    )
    started = _response(urllib.request.Request(f"{bridge}/oauth2/start?{query}"))
    assert started.status == 302
    flow_cookie = _cookie(started, "__Secure-finance_oidc_flow")

    authorized = _response(urllib.request.Request(started.headers["Location"]))
    assert authorized.status == 302
    cookie = flow_cookie
    if existing_session:
        cookie = f"{cookie}; {existing_session}"
    callback = urllib.request.Request(
        authorized.headers["Location"], headers={"Cookie": cookie}
    )
    completed = _response(callback)
    return started, completed


def _set_mode(issuer: str, mode: str):
    response = _response(
        urllib.request.Request(
            f"{issuer}/test/mode/{mode}", data=b"", method="POST"
        )
    )
    assert response.status == 200


def test_verified_passkey_login_issues_only_server_validated_auth_headers(oidc_services):
    issuer, bridge = oidc_services
    started, completed = _login(issuer, bridge)
    assert "code_challenge_method=S256" in started.headers["Location"]
    assert completed.status == 303, completed.read().decode()
    assert completed.headers["Location"] == "/dashboard"
    session_cookie = _cookie(completed, "__Host-finance_oidc_session")
    assert "Secure" in completed.headers["Set-Cookie"]
    assert "HttpOnly" in completed.headers["Set-Cookie"]
    assert "SameSite=lax" in completed.headers["Set-Cookie"]

    authenticated = _response(
        urllib.request.Request(
            f"{bridge}/oauth2/auth",
            headers={
                "Cookie": session_cookie,
                "X-Forwarded-Uri": "/dashboard",
                "X-Forwarded-User": "mallory",
                "X-Auth-Method": "password",
                "X-Auth-Time": "0",
            },
        )
    )
    assert authenticated.status == 204
    assert authenticated.headers["X-Forwarded-User"] == "alice"
    assert authenticated.headers["X-Auth-Method"] == "webauthn"
    assert int(authenticated.headers["X-Auth-Time"]) > int(time.time()) - 30

    with urllib.request.urlopen(
        urllib.request.Request(
            f"{bridge}/oauth2/session", headers={"Cookie": session_cookie}
        )
    ) as response:
        assert json.load(response) == {"authenticated": True, "fresh": True}
        assert response.headers["Cache-Control"] == "no-store"


def test_browser_session_discloses_no_identity_when_signed_out(oidc_services):
    _, bridge = oidc_services
    with urllib.request.urlopen(f"{bridge}/oauth2/session") as response:
        assert json.load(response) == {"authenticated": False, "fresh": False}


@pytest.mark.parametrize("mode", ["password", "recovery"])
def test_password_and_recovery_tokens_never_create_a_bridge_session(
    oidc_services, mode
):
    issuer, bridge = oidc_services
    _set_mode(issuer, mode)
    _, completed = _login(issuer, bridge)
    assert completed.status == 401
    assert "__Host-finance_oidc_session=" not in "\n".join(
        completed.headers.get_all("Set-Cookie", [])
    )


def test_spoofed_identity_headers_without_a_server_session_are_denied(oidc_services):
    _, bridge = oidc_services
    denied = _response(
        urllib.request.Request(
            f"{bridge}/oauth2/auth",
            headers={
                "X-Forwarded-Uri": "/dashboard",
                "X-Forwarded-User": "mallory",
                "Remote-User": "mallory",
                "X-Auth-Method": "webauthn",
                "X-Auth-Time": str(time.time()),
            },
        )
    )
    assert denied.status == 302
    assert denied.headers.get("X-Forwarded-User") is None
    assert denied.headers.get("X-Auth-Method") is None
    assert denied.headers["Location"].startswith("/oauth2/start?")


def test_sensitive_login_requests_max_age_and_rejects_stale_auth_time(oidc_services):
    issuer, bridge = oidc_services
    _set_mode(issuer, "stale")
    started, completed = _login(issuer, bridge, sensitive=True)
    authorization_query = urllib.parse.parse_qs(
        urllib.parse.urlparse(started.headers["Location"]).query
    )
    assert authorization_query["max_age"] == ["300"]
    assert completed.status == 401


def test_sensitive_login_accepts_fresh_verified_passkey(oidc_services):
    issuer, bridge = oidc_services
    started, completed = _login(issuer, bridge, sensitive=True)
    assert "max_age=300" in started.headers["Location"]
    assert completed.status == 303, completed.read().decode()
    authenticated = _response(
        urllib.request.Request(
            f"{bridge}/oauth2/auth",
            headers={
                "Cookie": _cookie(completed, "__Host-finance_oidc_session"),
                "X-Forwarded-Uri": "/api/private/lifecycle/export.json",
                "X-Auth-Require-Fresh": "true",
            },
        )
    )
    assert authenticated.status == 204


@pytest.mark.parametrize("mode", ["bad_nonce", "bad_audience", "bad_azp"])
def test_invalid_token_binding_claims_are_denied(oidc_services, mode):
    issuer, bridge = oidc_services
    _set_mode(issuer, mode)
    _, completed = _login(issuer, bridge)
    assert completed.status == 401


def test_callback_state_is_single_use_and_exactly_bound_to_browser_flow(oidc_services):
    _, bridge = oidc_services
    started = _response(
        urllib.request.Request(f"{bridge}/oauth2/start?return_to=%2Fdashboard")
    )
    flow_cookie = _cookie(started, "__Secure-finance_oidc_flow")
    authorized = _response(urllib.request.Request(started.headers["Location"]))
    callback_url = urllib.parse.urlparse(authorized.headers["Location"])
    callback_query = urllib.parse.parse_qs(callback_url.query)
    wrong = urllib.parse.urlunparse(
        callback_url._replace(
            query=urllib.parse.urlencode(
                {"code": callback_query["code"][0], "state": "wrong-state"}
            )
        )
    )
    denied = _response(
        urllib.request.Request(wrong, headers={"Cookie": flow_cookie})
    )
    assert denied.status == 401
    replay = _response(
        urllib.request.Request(
            authorized.headers["Location"], headers={"Cookie": flow_cookie}
        )
    )
    assert replay.status == 401


def test_logout_revokes_server_side_session(oidc_services):
    issuer, bridge = oidc_services
    _, completed = _login(issuer, bridge)
    session_cookie = _cookie(completed, "__Host-finance_oidc_session")
    logged_out = _response(
        urllib.request.Request(
            f"{bridge}/oauth2/logout",
            data=b"",
            method="POST",
            headers={"Cookie": session_cookie},
        )
    )
    assert logged_out.status == 303
    assert logged_out.headers["Location"].startswith(f"{issuer}/logout?")
    denied = _response(
        urllib.request.Request(
            f"{bridge}/oauth2/auth",
            headers={"Cookie": session_cookie, "X-Forwarded-Uri": "/dashboard"},
        )
    )
    assert denied.status == 302


def test_successful_login_rotates_and_revokes_an_existing_session(oidc_services):
    issuer, bridge = oidc_services
    _, first = _login(issuer, bridge)
    old_cookie = _cookie(first, "__Host-finance_oidc_session")
    _, second = _login(issuer, bridge, existing_session=old_cookie)
    new_cookie = _cookie(second, "__Host-finance_oidc_session")
    assert new_cookie != old_cookie
    old = _response(
        urllib.request.Request(
            f"{bridge}/oauth2/auth",
            headers={"Cookie": old_cookie, "X-Forwarded-Uri": "/dashboard"},
        )
    )
    new = _response(
        urllib.request.Request(
            f"{bridge}/oauth2/auth",
            headers={"Cookie": new_cookie, "X-Forwarded-Uri": "/dashboard"},
        )
    )
    assert old.status == 302
    assert new.status == 204


def test_bridge_defaults_disabled_and_never_asserts_identity():
    port = _unused_port()
    bridge = f"http://127.0.0.1:{port}"
    environment = {"FINANCE_OIDC_BRIDGE_ENABLED": "false"}
    with _process("backend.finance_auth_bridge.app:app", port, environment) as process:
        _wait(bridge, process)
        with urllib.request.urlopen(f"{bridge}/health") as response:
            assert json.load(response)["status"] == "disabled"
        denied = _response(urllib.request.Request(f"{bridge}/oauth2/auth"))
        assert denied.status == 503
        assert denied.headers.get("X-Forwarded-User") is None


def test_caddy_contract_strips_spoofable_headers_before_forward_auth():
    caddyfile = (
        Path(__file__).resolve().parents[1]
        / "deploy"
        / "Caddyfile.finance-oidc-bridge.example"
    ).read_text()
    route = caddyfile.index("\t\troute {")
    stripped = caddyfile.index("request_header -X-Forwarded-User", route)
    authorized = caddyfile.index("forward_auth finance-oidc-bridge:8081", route)
    proxied = caddyfile.index("reverse_proxy finance:8080", authorized)
    assert route < stripped < authorized < proxied
    assert "copy_headers X-Forwarded-User X-Auth-Method X-Auth-Time" in caddyfile
    assert "path /oauth2/start /oauth2/callback /oauth2/logout /oauth2/session /logged-out" in caddyfile
    assert "path /oauth2/auth" not in caddyfile
