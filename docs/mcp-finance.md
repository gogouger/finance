# Personal MCP gateway contract

The Finance service is the central, read-only personal MCP gateway. It owns the
passkey-approved OAuth grants and can compose narrow aggregate projections from
Finance, Athletic Analytics, and Library. It does not expose SQL, arbitrary
record queries, provider credentials, account numbers, property identifiers,
GPS routes, per-activity timestamps, purchase prices, or mutation tools.

## OAuth client grants

OAuth metadata is available at `/.well-known/oauth-authorization-server`. An owner creates a named client-installation grant through the passkey-protected consent boundary at `POST /api/private/mcp/grants`. The grant accepts only the explicit scopes documented below and requires an S256 PKCE challenge. The one-use authorization code is exchanged at the form-encoded OAuth token endpoint, `/mcp/oauth/token`, without a permanent client secret.

Access tokens live for ten minutes. Refresh tokens live for thirty days and rotate on every use; replaying an old refresh token fails. Only hashes of authorization codes and tokens are stored inside the encrypted database. A freshly passkey-authenticated owner can revoke one named grant immediately at `DELETE /api/private/mcp/grants/{grant_id}`.

The local `/mcp/tools/call` route is the deterministic gateway adapter used in tests. `/api/internal/mcp/finance/tools/call` is its narrow internal equivalent. Both require the same short-lived OAuth bearer token; neither accepts a permanent shared API key. Module base URLs are deployment settings, not tool arguments, so an MCP client cannot make the gateway fetch an arbitrary address.

## Finance scopes and tools

| Scope | Read-only tools |
| --- | --- |
| `finance:summary` | `finance.summary`, `finance.cash_flow_trend` |
| `finance:metrics` | `finance.metric_definitions` |
| `finance:spending` | `finance.spending_breakdown` |
| `finance:investments` | `finance.investments.summary` |
| `finance:scenarios` | `finance.scenarios.list`, `finance.scenario.calculate` |
| `finance:transactions:detail` | `finance.transactions.list` |
| `athletics:training:summary` | `athletics.training.summary` |
| `library:reading:metrics` | `library.reading.metrics` |

Aggregate tools do not return merchant or transaction rows. Scenario responses remove direct-identifier fields. `finance.transactions.list` requires its distinct detail scope and explicit ISO `start` and `end` dates spanning no more than 31 days. Returned transaction fields are allowlisted.

`athletics.training.summary` is limited to the aggregate projection used on the
public project preview. `library.reading.metrics` uses Library's owner-less
metrics projection, which omits purchase values and prices. Finance stays off
the shared application network: Caddy accepts its module calls only from the
isolated Finance edge network and proxies two fixed paths. Every authenticated
tool call appends an audit event with the named client, grant, required scope,
tool, timestamp, requested date range, result sensitivity class, and outcome.
Audit events exclude access tokens, financial amounts, provider payloads, and
complete model conversations.
