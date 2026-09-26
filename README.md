# Finance

Private household finance analytics and transparent public planning calculators.

- [Public product demo](https://finance.gordongouger.com/preview)
- [Buy versus rent calculator](https://finance.gordongouger.com/housing)
- [Retirement projection](https://finance.gordongouger.com/retirement)

The public demo contains fictional data. Real financial records, provider tokens,
account identifiers, and household assumptions are runtime data and are not part
of this repository.

## Local development

1. Copy `.env.example` to `.env` and generate a unique encryption key.
2. Install Python dependencies with `uv sync`.
3. Install and build the browser application with `npm install` and `npm run build` from `frontend/`.
4. Start the service with `uv run uvicorn backend.finance_app.main:app --reload`.

Run the service-boundary suite with `uv run pytest`. No real account credentials or financial data are required.

## Security boundary

The service is read-only with respect to financial institutions. Secrets belong in the runtime environment or mounted secret files, never source control. The production image runs as an unprivileged user and expects a writable `/data` volume.

Production deployment and authentication requirements are documented in
[`deploy/SECURITY.md`](deploy/SECURITY.md). The production Compose file joins a
dedicated external `finance_edge` proxy network and deliberately publishes no
host port.

## Payroll Income safety

Payroll Income uses its own Plaid Link flow and stays in Sandbox by default. A
production flow is rejected unless exact one-time and per-refresh prices are
configured, the current versioned terms are confirmed with a fresh passkey
session, and a monthly USD spending cap can cover the request. Paystub data is
read-only and keeps gross pay, net pay, withholding, and deductions separate.
When payroll coverage is unavailable, Finance can show a clearly labeled
net-only cashflow estimate or a user-confirmed manual payroll profile.

## Asset valuation safety

Automated valuations are off by default. The opt-in
`douglas_county_public_data` mode reads the official Douglas County Assessor
parcel-detail source only after its terms URL, acknowledgement time, and a
descriptive user agent are configured. Successful observations are cached for
24 hours, retained as append-only history, and labeled with source, estimate
type, confidence, and freshness. County assessed actual value is never
represented as a market appraisal. No permitted vehicle-value source is assumed:
when one is unavailable, the existing manual value remains in place with a stale
or unavailable warning. Address, parcel, VIN, plate, and source-detail URLs are
omitted from the MCP-safe asset projection.
