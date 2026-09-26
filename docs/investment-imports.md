# Investment fallback imports

Finance supports explicit, preview-first CSV imports for data that Plaid does not
provide. Both preview and commit routes require the authenticated household owner.
Commit the exact content that was reviewed in preview; the returned batch ID is a
SHA-256 fingerprint of its normalized records.

## Fidelity tax lots

Routes:

- `POST /api/private/investments/imports/fidelity/preview`
- `POST /api/private/investments/imports/fidelity/commit`

The JSON body is `{"content": "<CSV text>"}`. Required CSV columns are `Account
Number`, `Symbol`, `Description`, `Quantity`, `Cost Basis Total`, `Date Acquired`,
and `As Of Date`. Dates use `YYYY-MM-DD`; values are USD. A blank cost basis is
valid and remains explicitly unknown.

Fidelity lots enrich a Plaid holding only when its provider basis is missing and
every matching imported lot has a known basis. Finance never sums a partial lot
set into an apparently complete basis.

## Vestwell fallback

Routes:

- `POST /api/private/investments/imports/vestwell/preview`
- `POST /api/private/investments/imports/vestwell/commit`

Required columns are `Record Type`, `Account ID`, `Symbol`, `Description`,
`Quantity`, `Market Value`, `Cost Basis`, `Effective Date`, `Activity Type`, and
`Amount`. `Record Type` is `HOLDING` or `ACTIVITY`. Activity types are `buy`,
`sell`, `deposit`, `withdrawal`, `dividend`, `interest`, or `fee`.

Imported observations retain source, effective date, batch provenance, and a
stable logical record ID. Recommitting the same export is idempotent. When Plaid
and an import are equally current and complete, the Plaid observation remains the
visible holding.
