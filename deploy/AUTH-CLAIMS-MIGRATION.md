# Finance authentication-claims migration

## Current safe state

Authelia's ForwardAuth endpoint returns identity (`Remote-User`, groups, name,
and email), but not the OpenID Connect `amr` or `auth_time` claims. Caddy must
not translate a successful ForwardAuth session into `X-Auth-Method: webauthn`
or invent an authentication timestamp.

`Caddyfile.finance` therefore removes identity and authentication headers from
the incoming request, copies only `Remote-User` from Authelia, and removes the
method and freshness headers before proxying to Finance. The backend requires a
passkey claim for every private route, so this configuration defaults closed.
Public calculators and health endpoints remain available.

## Deployable target contract

Place an OIDC claims bridge/BFF between Caddy and Finance. It must:

1. Register a confidential `finance` client in Authelia and use the
   Authorization Code flow with PKCE. Redirect URIs must be exact HTTPS URIs;
   the client policy remains `two_factor`.
2. Validate every ID token using Authelia discovery/JWKS, including signature,
   issuer, `finance` audience, expiry, nonce, and authorized party. Never accept
   claims copied from browser request headers.
3. Treat a session as passkey-authenticated only when the verified `amr` array
   contains `hwk` or `swk` and also records user verification (`user` or
   `pin`). Password, OTP, recovery, absent, or unknown method references do not
   satisfy the Finance passkey requirement.
4. For a sensitive Finance route, start a new authorization request with
   `max_age=300`. Forward the verified numeric `auth_time` only after the new ID
   token is validated. Finance independently rejects future timestamps and
   timestamps older than 300 seconds.
5. Store browser state in Secure, HttpOnly, SameSite cookies, bind callback
   state/nonce/PKCE verifier to that session, rotate the session after login,
   and implement RP-initiated logout and server-side revocation.
6. Listen only on the private Docker network. Caddy must strip
   `Remote-User`, `X-Forwarded-User`, `X-Auth-Method`, and `X-Auth-Time` before
   invoking the bridge, then copy the bridge's verified outputs. Finance must
   have no host port and accept traffic only from that trusted path.
7. If discovery, JWKS retrieval, token validation, required claims, passkey
   method references, or step-up authentication are unavailable, the bridge
   defaults closed and forwards no authentication claim.

The verified bridge output contract is:

| Header | Value |
| --- | --- |
| `X-Forwarded-User` | Stable owner identifier from the validated token |
| `X-Auth-Method` | Literal `webauthn`, only after the `amr` check above |
| `X-Auth-Time` | Validated OIDC `auth_time` Unix timestamp |

The bridge must return these as response headers to Caddy's authorization
subrequest, never as browser-visible credentials. Caddy then copies them into
the internal upstream request in the same way it currently copies Authelia's
identity response.

## Authelia client fragment

Merge this client under the existing `identity_providers.oidc.clients` list
after generating the client-secret digest and configuring the OIDC signing key:

```yaml
- client_id: finance
  client_name: Finance
  client_secret: '<AUTHELIA_HASHED_CLIENT_SECRET>'
  public: false
  authorization_policy: two_factor
  require_pkce: true
  pkce_challenge_method: S256
  redirect_uris:
    - https://finance.gordongouger.com/oauth2/callback
  scopes:
    - openid
    - profile
  response_types:
    - code
  grant_types:
    - authorization_code
  token_endpoint_auth_method: client_secret_basic
```

Do not enable the bridge until an integration test proves that a password or
recovery session is denied, a user-verifying passkey session is accepted, an
old `auth_time` is denied, `max_age=300` performs step-up, and spoofed request
headers are discarded at Caddy.
