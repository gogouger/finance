# Finance portable data export

Finance provides versioned JSON and CSV exports for moving normalized household data without coupling the export to the encrypted SQLite schema. Both endpoints require a passkey-authenticated session verified within the preceding five minutes.

## JSON

`GET /api/private/lifecycle/export.json` returns a `finance-portable-records` document with schema version `1`, USD currency, an export timestamp, and a `records` array. Every record has:

- `collection`: its portable logical collection, such as `normalized/transactions`, `classification_rules`, `scenarios`, or `audit_events`.
- `source_id`: the stable source identifier needed to deduplicate or reconnect imported data.
- `data`: the normalized record.
- `connection_id`, `removed`, and `updated_at` when those fields apply to provider-derived normalized records.

Consumers should ignore unknown fields, preserve unknown collections, and identify a record by the tuple `(collection, source_id)`. A later schema version may add fields but will not silently change version 1 meanings.

## CSV

`GET /api/private/lifecycle/export.csv` represents the same portable envelopes with this header:

```text
schema_version,collection,source_id,connection_id,removed,updated_at,data_json
```

`data_json` is compact, valid JSON containing the same `data` object as the JSON export. Parse it as JSON rather than treating it as an opaque display string. Reconstruct records by grouping on `(collection, source_id)` and retaining the optional metadata columns. Standard CSV quoting applies.

## Included data

Exports include sanitized connection metadata, normalized financial records and their source identifiers, transaction adjustments and classifications, deterministic classification rules, confirmed recurring obligations, saved scenarios with pinned rulesets, email-note provenance and validation evidence, named MCP client and grant metadata, and audit metadata. Removed normalized records remain marked as removed so an importer can preserve history correctly.

The `email_notes` collection contains only extracted untrusted notes, validation evidence, and provenance; encrypted raw email bodies are deliberately excluded. The `mcp_grants` collection identifies the named client, scopes, status, redirect URI, and creation or revocation timestamps without authorization codes, token hashes, or other credentials.

## Deliberately excluded secrets

Exports never contain provider access tokens, client secrets, passwords, recovery passwords or codes, TOTP secrets, private keys, or credential-bearing fields. Provider revocation is an operational action, not portable data. Treat exports as sensitive financial files even though credentials are excluded.

## Disconnect and permanent erasure

Disconnecting a provider connection revokes its access and marks the connection disconnected while preserving normalized history by default. This lets existing reports and exports remain coherent.

Permanent erasure is intentionally separate. First, create a ten-minute erasure intent with `POST /api/private/lifecycle/erasure-intents`. Then send its `confirmation_id` and the exact displayed confirmation phrase to `DELETE /api/private/lifecycle/data`. Both requests require passkey verification within five minutes. Finance revokes every active provider connection before deleting local connections, normalized records and versions, rules, classifications, adjustments, recurring obligations, scenarios and shares, sync state, and audit history. If provider revocation fails, local erasure does not begin.
