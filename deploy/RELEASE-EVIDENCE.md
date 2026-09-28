# Finance release evidence

Production linking remains disabled until the live deployment owner changes `status` in `release-gates.json` from `blocked` to `approved` after repeating every operational check below. A checked-in default can therefore never enable production Plaid access by itself.

## Backup and recovery

- `finance-backup.cron` creates authenticated encrypted snapshots nightly. Retention is enforced at 30 daily and 12 monthly copies.
- `finance-restore-test.cron` decrypts the newest monthly snapshot into an isolated temporary directory, verifies the archive SHA-256 manifest, runs SQLite `PRAGMA integrity_check`, and never writes into live data.
- Backup encryption uses a dedicated `FINANCE_BACKUP_KEY`; provider tokens and the database encryption key are not reused.

## VM resource and operational evidence

- Compose caps Finance at 256 MiB memory, 0.35 CPU, and 128 PIDs; its temporary filesystem is 16 MiB and JSON logs rotate at 10 MiB with three files. The image runs as UID/GID 10001, read-only, with all capabilities dropped and no host port. Finance and Caddy use the dedicated `finance_edge` network described in `NETWORK-MIGRATION.md`; unrelated containers must not join it.
- The private operations endpoint returns only connection status counts, stale-feed count, review-queue depth, backup age, disk capacity, and release state. It requires a distinct internal key and excludes amounts, balances, merchants, account owners, institution names, provider tokens, and direct identifiers.
- Local staging evidence on 2026-09-25: the idle service used 12,688 KiB RSS and 0.1% CPU, well below the assigned container ceilings; its test data used 192 KiB. Live verification still requires `docker inspect finance-finance-1` to match those budgets and `docker stats --no-stream finance-finance-1` to remain below them during reconciliation, backup, and restore testing. Disk free space and backup age must be reviewed before approval.

## Accessibility

- Every route provides a keyboard-visible skip link and global high-contrast `:focus-visible` treatment.
- The interface honors `prefers-reduced-motion`; calculators use native labeled controls and announce result/error changes.
- The wealth chart has an accessible name and the same values are available in a year-by-year HTML table. Dashboard metrics also have a table alternative and never depend on color alone.
- axe-core 4.13.0 found 0 automated violations on all three public routes on 2026-09-25. Release verification additionally includes authenticated keyboard-only journeys at 200% zoom. Any serious or critical accessibility result blocks approval.

## Cloudflare and origin controls

- Cloudflare proxies the public DNS record. The zone-wide custom WAF rule blocks requests whose source country is not the United States; leaked-credential protection remains enabled.
- The origin firewall admits HTTP/S only from Cloudflare's published networks. Finance exposes port 8080 only on the private Docker network, and Caddy strips client-supplied authentication headers before proxying.
- Before approval, capture the active DNS proxy, WAF rule, current Cloudflare IP allowlist, TLS mode, origin firewall, and denied direct-origin request. Store no credentials or raw request data in this evidence.

## OWASP and scan gates

- `OWASP-REVIEW.md` covers all OWASP Top 10:2025 categories at each trust boundary and records no unresolved high-risk finding.
- Python dependency audit: 0 known vulnerabilities after upgrading `cryptography` to 50.0.1. Frontend production dependency audit: 0 known vulnerabilities.
- Gitleaks 8.30.1 scanned tracked and unignored source with redaction enabled on 2026-09-25: 0 findings. Scan reports must never contain recovered credential values.
- `zap-staging.sh` refuses non-local targets. ZAP 2.17.0 active-scanned only the isolated loopback staging service on 2026-09-25: 0 high, 2 medium, 1 low, and 2 informational alerts. The medium header alerts occur only when bypassing the Caddy security-header boundary and must be rechecked through the staging proxy before approval. Never point ZAP at production or an unrelated service.

## Approval record

Do not approve the gate based only on this document. Record timestamped outputs from the live backup/restore, resource inspection, accessibility checks, Cloudflare/origin inspection, dependency/secret scans, and scoped ZAP staging scans. Until all evidence is current and reviewed, production linking remains disabled.

Encrypted off-host replication is active through the configured Cloudflare R2
crypt remote. Object-locked history is retained when it fits beneath the
application safety ceiling; when the immutable history cannot be pruned, a
separately encrypted rolling recovery point is uploaded and verified instead.
The live backup-status record is the authority for freshness, verification
mode, and remote byte usage. Personal iCloud Drive remains intentionally
excluded because it has no supported unattended Linux server integration; no
Apple password or consumer-web automation is stored on the VM. The checked-in
release status remains `blocked` until the independent release checks above are
captured and reviewed.
