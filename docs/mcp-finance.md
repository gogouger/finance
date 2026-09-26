# Finance MCP module contract

Finance exposes a narrow read-only module for the separately hosted personal MCP gateway. It does not expose SQL, arbitrary record queries, provider credentials, account numbers, property identifiers, or mutation tools. Books and Fitness scopes and tools are deliberately outside this module.

## OAuth client grants

OAuth metadata is available at `/.well-known/oauth-authorization-server`. An owner creates a named client-installation grant through the passkey-protected consent boundary at `POST /api/private/mcp/grants`. The grant accepts only explicit `finance:*` scopes and requires an S256 PKCE challenge. The one-use authorization code is exchanged at the form-encoded OAuth token endpoint, `/mcp/oauth/token`, without a permanent client secret.

Access tokens live for ten minutes. Refresh tokens live for thirty days and rotate on every use; replaying an old refresh token fails. Only hashes of authorization codes and tokens are stored inside the encrypted database. A freshly passkey-authenticated owner can revoke one named grant immediately at `DELETE /api/private/mcp/grants/{grant_id}`.

The local `/mcp/tools/call` route is the deterministic gateway adapter used in tests. `/api/internal/mcp/finance/tools/call` is the equivalent narrow Finance-module boundary for the separately hosted central gateway. Both require the same short-lived OAuth bearer token; neither accepts a permanent shared API key.

## Finance scopes and tools

| Scope | Read-only tools |
| --- | --- |
| `finance:summary` | `finance.summary`, `finance.cash_flow_trend` |
| `finance:metrics` | `finance.metric_definitions` |
| `finance:spending` | `finance.spending_breakdown` |
| `finance:investments` | `finance.investments.summary` |
| `finance:scenarios` | `finance.scenarios.list`, `finance.scenario.calculate` |
| `finance:transactions:detail` | `finance.transactions.list` |

Aggregate tools do not return merchant or transaction rows. Scenario responses remove direct-identifier fields. `finance.transactions.list` requires its distinct detail scope and explicit ISO `start` and `end` dates spanning no more than 31 days. Returned transaction fields are allowlisted.

Every authenticated tool call appends an audit event with the named client, grant, required scope, tool, timestamp, requested date range, result sensitivity class, and outcome. Audit events exclude access tokens, financial amounts, provider payloads, and complete model conversations.
