# Finance production boundary

- `finance.gordongouger.com`, `/housing`, `/retirement`, and public calculation APIs are anonymous only after Cloudflare has admitted the request from the United States.
- All other routes require a verified passkey claim in addition to Authelia
  `two_factor`. The OIDC bridge validates signed `amr`, `auth_time`, issuer,
  audience, nonce, and the owner allowlist. Authelia independently restricts
  the Finance client to the `gordon` subject.
- Sessions expire after 12 hours and lock after 30 minutes of inactivity. Sensitive APIs additionally reject requests unless the trusted authentication layer provides a verified OIDC `auth_time` assertion no older than five minutes. Missing or stale freshness always fails closed.
- The service has no host port. Caddy is the only HTTP peer. The container is non-root, read-only, capability-free, `no-new-privileges`, PID-limited, CPU-limited, and memory-limited.
- Cloudflare proxies every web hostname and uses Full (strict), HTTPS-only,
  TLS 1.2+, HSTS, DNSSEC, and the US-only WAF rule. Only the signed Plaid and
  mail callback paths are exempt from the country rule. When traveling,
  connect through a trusted US VPN rather than relaxing the rule.
- The origin firewall permits ports 80 and 443 only from Cloudflare's published networks. SSH remains key-authenticated and is monitored by Fail2Ban.

## Verification

After deployment, verify public routes from an allowed US network, verify that `/dashboard` redirects to Authelia and remains denied after a plain ForwardAuth session, verify that password/recovery and spoofed-header sessions cannot reach it, and inspect the running container for its user, capabilities, mounts, port bindings, memory, CPU, and PID limits. Once the OIDC bridge is installed, also verify passkey access and forced step-up after five minutes.
