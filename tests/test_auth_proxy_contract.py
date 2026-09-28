from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_caddy_never_fabricates_authentication_method_or_freshness():
    caddyfile = (ROOT / "deploy" / "Caddyfile.finance").read_text()

    assert "header_up X-Auth-Method webauthn" not in caddyfile
    assert "request_header -X-Auth-Method" in caddyfile
    assert "request_header -X-Auth-Time" in caddyfile
    assert "copy_headers X-Forwarded-User X-Auth-Method X-Auth-Time" in caddyfile


def test_caddy_removes_client_spoofable_identity_and_auth_claims():
    caddyfile = (ROOT / "deploy" / "Caddyfile.finance").read_text()

    assert "request_header -Remote-User" in caddyfile
    assert "request_header -X-Forwarded-User" in caddyfile
    assert "request_header -X-Auth-Method" in caddyfile
    assert "request_header -X-Auth-Time" in caddyfile
    assert "forward_auth finance-oidc-bridge:8081" in caddyfile
    assert "copy_headers X-Forwarded-User X-Auth-Method X-Auth-Time" in caddyfile
    assert "header_up -Remote-User" in caddyfile
    assert caddyfile.index("request_header -Remote-User") < caddyfile.index(
        "forward_auth finance-oidc-bridge:8081"
    )


def test_caddy_template_keeps_mcp_protocol_outside_browser_oidc():
    caddyfile = (ROOT / "deploy" / "Caddyfile.finance").read_text()

    for path in (
        "/mcp",
        "/mcp/oauth/token",
        "/mcp/oauth/register",
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource/mcp",
    ):
        assert path in caddyfile
    assert "handle @mcp_protocol" in caddyfile
    assert "/mcp/oauth/authorize" in caddyfile


def test_oidc_migration_contract_requires_verified_passkey_and_step_up_claims():
    contract = (ROOT / "deploy" / "AUTH-CLAIMS-MIGRATION.md").read_text()

    for requirement in (
        "Authorization Code flow with PKCE",
        "`amr`",
        "`auth_time`",
        "`max_age=300`",
        "`hwk` or `swk`",
        "defaults closed",
    ):
        assert requirement in contract
