# Finance OIDC claims bridge

The bridge is a separate, private-network BFF implemented by
`backend.finance_auth_bridge.app`. It is disabled by default and never accepts
identity or authentication claims from a browser. Its only trusted outputs are
the three headers returned by the internal `/oauth2/auth` authorization
subrequest after a server-side session has been validated.

## Security contract

- Authorization Code flow uses S256 PKCE, state, nonce, and an exact redirect URI.
- Discovery issuer and every metadata endpoint must match the configured issuer
  origin. Production URLs must use HTTPS.
- ID tokens require a trusted RS256 or ES256 JWKS key, issuer, audience, expiry,
  issued-at time, nonce, `azp`, stable subject, numeric `auth_time`, and `amr`.
- A passkey session requires `hwk` or `swk` plus `user` or `pin`. Password,
  recovery, missing, and unknown method references fail closed.
- The signed subject/username must match the configured owner allowlist. An
  empty allowlist makes an enabled bridge unhealthy and unable to authorize.
- Sensitive authentication sends `max_age=300` and rejects a returned
  `auth_time` older than five minutes. Finance still performs its own freshness
  check.
- Flow and login state are opaque, random, server-side records. Browser cookies
  are Secure, HttpOnly, SameSite=Lax; callback consumes state once and rotates
  any existing login session. Logout revokes the server record before using the
  provider's RP-initiated logout endpoint.

The in-memory store intentionally supports one bridge replica. A future
multi-replica deployment must replace it with an encrypted shared store that
preserves consume-once state and revocation semantics.

The example proxy follows Caddy's documented `forward_auth` behavior: a 2xx
allows the request and copies only configured response headers; any other
response, including the bridge's login redirect, is returned to the browser.
See <https://caddyserver.com/docs/caddyfile/directives/forward_auth>.

## Deployment gate

Do not enable the bridge merely because Authelia login succeeds. First use a
staging client and verify all of these outcomes through Caddy:

1. The bridge is on a dedicated Docker network containing only Caddy, Finance,
   and the bridge. It publishes no host port. `/oauth2/auth` is not a
   browser-routed bridge endpoint.
2. A password, OTP, or recovery login is denied and no identity headers reach
   Finance.
3. A user-verifying passkey produces an ID token whose signed `amr` contains a
   key method (`hwk` or `swk`) and verification (`user` or `pin`), and whose
   signed `auth_time` is numeric.
4. A sensitive request starts a new authorization with `max_age=300`; a stale
   token is denied.
5. Browser-supplied identity, method, time, and freshness headers are stripped.

If Authelia does not emit those exact signed claims, leave
`FINANCE_OIDC_BRIDGE_ENABLED=false`. Do not translate an Authelia session,
WebAuthn registration, or `two_factor` result into claims the issuer did not
provide.

The target Caddy integration is documented in
`Caddyfile.finance-oidc-bridge.example`. Validate it with `caddy validate`
against the complete production Caddyfile before reload.

`compose.oidc-bridge.yaml` is an additive Compose file for the dedicated bridge
process. It reuses the Finance image, publishes no host port, has no data-volume
mount, and joins only `finance_edge`. When the bridge is deployed, that network
must contain exactly Caddy, Finance, and the bridge; update the base network
migration evidence accordingly. Store its environment at
`/opt/data/finance/oidc-bridge.env`, owned by root with mode `0600`. Sensitive
route classification is repeated inside the bridge so proxy-matcher drift
cannot bypass the five-minute step-up policy.
