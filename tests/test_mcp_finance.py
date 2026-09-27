import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from base64 import urlsafe_b64encode
from pathlib import Path

import pytest


OWNER = {"X-Forwarded-User": "owner", "X-Auth-Method": "webauthn"}
VERIFIER = "deterministic-pkce-verifier-for-finance-mcp-client"
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")


def _fresh_owner():
    return {**OWNER, "X-Auth-Time": str(time.time())}


def _unused_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _request(url, payload=None, *, method="GET", headers=None):
    return urllib.request.Request(
        url,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )


def _json(request):
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def _token_request(base_url: str, fields: dict[str, str]):
    return urllib.request.Request(
        f"{base_url}/mcp/oauth/token",
        data=urllib.parse.urlencode(fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )


@pytest.fixture
def mcp_service(tmp_path: Path):
    port = _unused_port()
    environment = os.environ.copy()
    environment.update(
        {
            "FINANCE_DATA_DIR": str(tmp_path),
            "FINANCE_ENCRYPTION_KEY": urlsafe_b64encode(b"m" * 32).decode(),
            "PLAID_MODE": "fake",
            "FINANCE_INTERNAL_KEY": "mcp-internal-key",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.finance_app.main:app", "--host", "127.0.0.1", "--port", str(port)],
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
        _json(
            _request(
                f"{base_url}/api/private/connections/plaid/exchange",
                {
                    "public_token": "public-sandbox-accounting",
                    "connection_type": "banking",
                    "display_name": "MCP fixture",
                    "institution_id": "ins_mcp",
                    "institution_name": "MCP Bank",
                },
                method="POST",
                headers=_fresh_owner(),
            )
        )
        _json(
            _request(
                f"{base_url}/api/internal/nightly-reconcile",
                method="POST",
                headers={"X-Internal-Key": "mcp-internal-key"},
            )
        )
        yield base_url
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def _grant(base_url: str, scopes: list[str]):
    return _json(
        _request(
            f"{base_url}/api/private/mcp/grants",
            {
                "client_id": "codex-device-1",
                "client_name": "Codex on personal laptop",
                "redirect_uri": "http://127.0.0.1:8765/oauth/callback",
                "scopes": scopes,
                "code_challenge": CHALLENGE,
                "code_challenge_method": "S256",
            },
            method="POST",
            headers=_fresh_owner(),
        )
    )


def _exchange(base_url: str, grant: dict):
    return _json(
        _token_request(
            base_url,
            {
                "grant_type": "authorization_code",
                "code": grant["authorization_code"],
                "client_id": "codex-device-1",
                "redirect_uri": "http://127.0.0.1:8765/oauth/callback",
                "code_verifier": VERIFIER,
            },
        )
    )


def _call(base_url: str, access_token: str, tool: str, arguments=None):
    return _json(
        _request(
            f"{base_url}/mcp/tools/call",
            {"tool": tool, "arguments": arguments or {}},
            method="POST",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    )


def test_oauth_discovery_short_lived_tokens_rotation_and_revocation(mcp_service):
    discovery = _json(
        urllib.request.Request(
            f"{mcp_service}/.well-known/oauth-authorization-server"
        )
    )
    assert discovery["token_endpoint"].endswith("/mcp/oauth/token")
    assert discovery["authorization_endpoint"].endswith("/mcp/oauth/authorize")
    assert discovery["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert discovery["code_challenge_methods_supported"] == ["S256"]

    grant = _grant(mcp_service, ["finance:summary"])
    assert grant["client_name"] == "Codex on personal laptop"
    tokens = _exchange(mcp_service, grant)
    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] == 600
    assert tokens["scope"] == "finance:summary"

    rotated = _json(
        _token_request(
            mcp_service,
            {
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": "codex-device-1",
            },
        )
    )
    assert rotated["refresh_token"] != tokens["refresh_token"]
    with pytest.raises(urllib.error.HTTPError) as replay:
        urllib.request.urlopen(
            _token_request(
                mcp_service,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                    "client_id": "codex-device-1",
                },
            )
        )
    assert replay.value.code == 401

    _json(
        _request(
            f"{mcp_service}/api/private/mcp/grants/{grant['grant_id']}",
            method="DELETE",
            headers=_fresh_owner(),
        )
    )
    with pytest.raises(urllib.error.HTTPError) as revoked:
        _call(mcp_service, rotated["access_token"], "finance.summary")
    assert revoked.value.code == 401


def test_passkey_fresh_authorization_screen_displays_scoped_consent(mcp_service):
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": "claude-desktop-test",
            "client_name": "Claude Desktop test",
            "redirect_uri": "http://127.0.0.1:8765/oauth/callback",
            "scope": "athletics:training:summary library:reading:metrics",
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
            "state": "csrf-state",
            "resource": f"{mcp_service}/mcp",
        }
    )
    request = urllib.request.Request(
        f"{mcp_service}/mcp/oauth/authorize?{query}",
        headers=_fresh_owner(),
    )
    with urllib.request.urlopen(request) as response:
        page = response.read().decode()
    assert "Approve read-only access?" in page
    assert "Claude Desktop test" in page
    assert "athletics:training:summary" in page
    assert "library:reading:metrics" in page


def test_default_tools_are_aggregate_redacted_and_unknown_scopes_are_rejected(mcp_service):
    with pytest.raises(urllib.error.HTTPError) as foreign_scope:
        _grant(mcp_service, ["finance:summary", "books:library"])
    assert foreign_scope.value.code == 422

    grant = _grant(
        mcp_service,
        ["finance:summary", "finance:metrics", "finance:spending"],
    )
    tokens = _exchange(mcp_service, grant)
    summary = _call(mcp_service, tokens["access_token"], "finance.summary")
    breakdown = _call(
        mcp_service, tokens["access_token"], "finance.spending_breakdown"
    )

    assert summary["sensitivity"] == "aggregate"
    assert summary["data"]["currency"] == "USD"
    assert "transactions" not in summary["data"]
    assert breakdown["data"]["categories"]["GENERAL_MERCHANDISE"] == 140
    serialized = json.dumps([summary, breakdown]).lower()
    assert "mountain market" not in serialized
    assert "access_token" not in serialized
    assert "address" not in serialized
    assert "parcel" not in serialized

    with pytest.raises(urllib.error.HTTPError) as denied:
        _call(
            mcp_service,
            tokens["access_token"],
            "finance.transactions.list",
            {"start": "2026-09-01", "end": "2026-09-30"},
        )
    assert denied.value.code == 403


def test_detailed_transactions_require_scope_bounded_range_and_are_audited(mcp_service):
    grant = _grant(mcp_service, ["finance:transactions:detail"])
    tokens = _exchange(mcp_service, grant)
    with pytest.raises(urllib.error.HTTPError) as unbounded:
        _call(
            mcp_service,
            tokens["access_token"],
            "finance.transactions.list",
            {"start": "2025-01-01", "end": "2026-09-30"},
        )
    assert unbounded.value.code == 422

    result = _call(
        mcp_service,
        tokens["access_token"],
        "finance.transactions.list",
        {"start": "2026-09-01", "end": "2026-09-30"},
    )
    assert result["sensitivity"] == "transaction_detail"
    assert result["date_range"] == {"start": "2026-09-01", "end": "2026-09-30"}
    assert any(item["id"] == "grocery-posted" for item in result["data"]["transactions"])
    assert all(set(item) <= {"id", "date", "amount", "merchant_name", "accounting_type", "category"} for item in result["data"]["transactions"])

    audits = _json(
        _request(f"{mcp_service}/api/private/audit-events", headers=OWNER)
    )
    detail_audit = next(
        item
        for item in reversed(audits)
        if item.get("action") == "mcp.tool.called"
        and item.get("tool") == "finance.transactions.list"
        and item.get("outcome") == "success"
    )
    assert detail_audit["client_name"] == "Codex on personal laptop"
    assert detail_audit["scope"] == "finance:transactions:detail"
    assert detail_audit["date_range"] == {"start": "2026-09-01", "end": "2026-09-30"}
    assert detail_audit["sensitivity"] == "transaction_detail"
    assert "conversation" not in detail_audit


def test_streamable_http_mcp_publishes_metadata_and_read_only_tools(mcp_service):
    metadata = _json(
        urllib.request.Request(
            f"{mcp_service}/.well-known/oauth-protected-resource/mcp"
        )
    )
    assert metadata["resource"] == f"{mcp_service}/mcp"
    assert metadata["authorization_servers"] == [mcp_service]

    with pytest.raises(urllib.error.HTTPError) as unauthenticated:
        urllib.request.urlopen(
            _request(
                f"{mcp_service}/mcp",
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                method="POST",
                headers={"Accept": "application/json, text/event-stream"},
            )
        )
    assert unauthenticated.value.code == 401
    assert "oauth-protected-resource/mcp" in unauthenticated.value.headers["WWW-Authenticate"]

    grant = _grant(mcp_service, ["finance:summary", "finance:metrics"])
    tokens = _exchange(mcp_service, grant)
    headers = {
        "Authorization": f"Bearer {tokens['access_token']}",
        "Accept": "application/json, text/event-stream",
    }
    initialized = _json(
        _request(
            f"{mcp_service}/mcp",
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "Codex", "version": "test"}, "capabilities": {}}},
            method="POST",
            headers=headers,
        )
    )
    assert initialized["result"]["protocolVersion"] == "2025-06-18"
    assert initialized["result"]["serverInfo"]["name"] == "Gordon Gouger Personal Data"

    listed = _json(
        _request(
            f"{mcp_service}/mcp",
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            method="POST",
            headers={**headers, "MCP-Protocol-Version": "2025-06-18"},
        )
    )
    tools = listed["result"]["tools"]
    assert {item["name"] for item in tools} == {
        "finance.summary",
        "finance.cash_flow_trend",
        "finance.metric_definitions",
    }
    assert all(item["annotations"]["readOnlyHint"] is True for item in tools)

    called = _json(
        _request(
            f"{mcp_service}/mcp",
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "finance.summary", "arguments": {}}},
            method="POST",
            headers={**headers, "MCP-Protocol-Version": "2025-06-18"},
        )
    )
    assert called["result"]["isError"] is False
    assert called["result"]["structuredContent"]["data"]["currency"] == "USD"


def test_central_gateway_only_lists_authorized_cross_project_tools(mcp_service):
    grant = _grant(
        mcp_service,
        ["athletics:training:summary", "library:reading:metrics"],
    )
    tokens = _exchange(mcp_service, grant)
    headers = {
        "Authorization": f"Bearer {tokens['access_token']}",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    }
    listed = _json(
        _request(
            f"{mcp_service}/mcp",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            method="POST",
            headers=headers,
        )
    )
    assert {item["name"] for item in listed["result"]["tools"]} == {
        "athletics.training.summary",
        "library.reading.metrics",
    }

    # Fixture deployments deliberately have no module URLs. The central
    # gateway fails closed instead of accepting an agent-supplied destination.
    with pytest.raises(urllib.error.HTTPError) as unavailable:
        _call(mcp_service, tokens["access_token"], "athletics.training.summary")
    assert unavailable.value.code == 503
