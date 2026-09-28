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

## Fidelity positions

Routes:

- `POST /api/private/investments/imports/fidelity-positions/preview`
- `POST /api/private/investments/imports/fidelity-positions/commit`

Fidelity's **Portfolio Positions** export provides a point-in-time holding,
current value, and position-level cost basis. It requires the export's `Account
number`, `Account name`, `Symbol`, `Description`, `Quantity`, `Current value`,
`Cost basis total`, `Total gain/loss dollar`, `Total gain/loss percent`, and
`Type` columns, plus its `Date downloaded` footer. The report date becomes the
effective date. Fidelity disclosure rows are ignored.

Position exports improve current allocation, gain, and basis coverage. They do
not include acquisition dates, so they never enable a tax-lot or SPY comparison
on their own. A unique connected-account mask match is used when Fidelity and
Plaid identify the same account differently; an ambiguous account stays
unmatched rather than being assigned to the wrong ownership or tax treatment.

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
