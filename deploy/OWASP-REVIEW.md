# OWASP Top 10:2025 review

Reviewed 2026-09-25 against the OWASP Top 10:2025. No unresolved high-risk finding remains. This is a boundary review, not a substitute for the fail-closed release gates in `RELEASE-EVIDENCE.md`.

| Risk | Finance control and evidence | Result |
| --- | --- | --- |
| A01 Broken Access Control | Private APIs require proxy-authenticated owner claims; sensitive operations require a fresh verified passkey; MCP grants are named, scoped, expiring, and revocable. Negative tests cover anonymous, stale, recovery, wrong-owner, and over-scoped access. | No high finding |
| A02 Security Misconfiguration | Non-root read-only container, no host port, all capabilities dropped, no-new-privileges, private Docker network, strict Caddy header boundary, Cloudflare proxy/WAF, and US-only edge rule. | No high finding |
| A03 Software Supply Chain Failures | Locked Python and npm dependencies; `pip-audit` and `npm audit --omit=dev` both returned zero known vulnerabilities on 2026-09-25. | No high finding |
| A04 Cryptographic Failures | Fernet protects provider tokens and snapshots with separate keys. Snapshot records are authenticated, manifests are SHA-256 verified, and restore tests fail on ciphertext corruption. Secrets are never included in exports or operational telemetry. | No high finding |
| A05 Injection | SQL is parameterized and not exposed through APIs or MCP. Models validate bounded input. Fixed provider endpoints and the local-only ZAP allowlist prevent arbitrary destinations. | No high finding |
| A06 Insecure Design | Public calculators are separate from private household data. Mutations fail closed behind fresh passkey proof and explicit confirmation. Production Plaid is additionally release-gated. | No high finding |
| A07 Authentication Failures | Proxy assertions are HMAC verified, passkey method and freshness are enforced independently, and recovery/password sessions cannot reach sensitive routes. | No high finding |
| A08 Software or Data Integrity Failures | Webhooks are signed and replay-resistant, imports preserve provenance, exports carry schema/audit metadata, and backups are authenticated before restore. | No high finding |
| A09 Security Logging and Alerting Failures | Audit records cover sensitive actions. The private operations endpoint exposes only redacted health counts, stale feeds, backup age, disk capacity, and gate state. | No high finding |
| A10 Mishandling of Exceptional Conditions | Provider failures retain last-known-good data with explicit freshness; malformed callbacks, replies, archives, grants, and external values fail closed without exposing secrets. | No high finding |

## Automated review results

- `pip-audit`: 0 known vulnerabilities across 23 auditable third-party Python packages; the local `finance` package is not a public PyPI dependency.
- `npm audit --omit=dev`: 0 info, low, moderate, high, or critical production findings.
- Gitleaks 8.30.1 directory scan of tracked and unignored source: 0 findings, with redaction enabled.
- ZAP 2.17.0 quick active scan of the isolated loopback Finance staging server: 0 high, 2 medium, 1 low, and 2 informational alerts. The two medium alerts are missing CSP/clickjacking headers on direct loopback access; those headers are expected when intentionally bypassing Caddy.
- ZAP passive baseline of the public Finance preview on 2026-09-28: 0 failures and 61 passing checks. It reported cache/session observations, missing COEP, and a CSP fallback warning. Public Preview, House, and Retirement now send an explicit `default-src 'self'` CSP; that header is covered by production browser regression checks. The baseline performed no active attacks, did not authenticate, and did not access private routes.
- axe-core 4.13.0: 0 automated violations on the landing, housing, and retirement routes. The private dashboard remains intentionally inaccessible without its authentication boundary; its keyboard, focus, reduced-motion, and tabular chart alternatives are covered by source and integration checks and require an authenticated manual journey before approval.
