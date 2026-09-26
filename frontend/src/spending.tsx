import { useEffect, useMemo, useState } from "react";

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});
const label = (value: string) => value.replaceAll("_", " ").toLowerCase();
const dateLabel = (value?: string) =>
  value
    ? new Date(`${value}T12:00:00`).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      })
    : "Unavailable";

type PeriodRow = { period: string; net_spending: number };
type YearlyRow = {
  year: number;
  net_spending: number;
  raw_spending: number;
  refunds: number;
  card_payments: number;
  transaction_count: number;
  months_covered: number;
  average_per_observed_month: number;
  annualized_pace: number;
  complete_year: boolean;
  current_year: boolean;
};
type MonthRow = {
  month: string;
  net_spending: number;
  categories: Record<string, number>;
};
type CategoryRow = { category: string; net_spending: number };
type MerchantRow = {
  merchant_name: string;
  net_spending: number;
  transaction_count: number;
};
type TransactionRow = {
  id: string;
  date: string;
  merchant_name: string;
  amount: number;
  accounting_type: string;
  category: { primary: string; detailed: string };
};
type RecurringRow = {
  id: string;
  merchant_name: string;
  cadence: string;
  estimated_amount: number;
  explanation: string;
};
type ReviewRow = {
  transaction_id: string;
  date: string;
  merchant_name: string;
  amount: number;
  category: string;
};
type Liability = {
  account_id: string;
  is_overdue: boolean;
  last_payment_amount?: number;
  last_payment_date?: string;
  last_statement_balance?: number;
  minimum_payment_amount?: number;
  next_payment_due_date?: string;
};
type Anomaly = {
  type: string;
  title: string;
  merchant_name: string;
  date: string;
  amount: number;
  explanation: string;
};
type SpendingResult = {
  data_quality: {
    cross_source_records_excluded: number;
    cross_source_absolute_value_excluded: number;
    preferred_source: string;
    raw_records_preserved: boolean;
  };
  period: { months: number; start: string | null; end: string };
  summary: {
    raw_spending: number;
    refunds: number;
    net_spending: number;
    average_monthly_spending: number;
    credit_card_payments: number;
    transaction_count: number;
    review_count: number;
  };
  monthly: MonthRow[];
  quarterly: PeriodRow[];
  annual: PeriodRow[];
  yearly: YearlyRow[];
  categories: CategoryRow[];
  merchants: MerchantRow[];
  transactions: TransactionRow[];
  recurring: RecurringRow[];
  review: { count: number; items: ReviewRow[] };
  anomalies: Anomaly[];
  liabilities: Liability[];
};

const categoryOptions = [
  "FOOD_AND_DRINK",
  "GENERAL_MERCHANDISE",
  "GENERAL_SERVICES",
  "HOME_IMPROVEMENT",
  "MEDICAL",
  "RENT_AND_UTILITIES",
  "TRANSPORTATION",
  "TRAVEL",
  "ENTERTAINMENT",
  "GOVERNMENT_AND_NON_PROFIT",
  "BANK_FEES",
  "OTHER",
];

function SpendingChart({ rows }: { rows: MonthRow[] }) {
  const width = 820,
    height = 270,
    left = 62,
    bottom = 38,
    top = 18;
  const max = Math.max(1, ...rows.map((row) => row.net_spending));
  const inner = height - top - bottom;
  const slot = (width - left - 12) / Math.max(1, rows.length);
  return (
    <figure className="spending-chart">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Monthly net spending bar chart"
      >
        {[0, 0.5, 1].map((fraction) => (
          <g key={fraction}>
            <line
              x1={left}
              x2={width - 8}
              y1={top + inner * fraction}
              y2={top + inner * fraction}
            />
            <text x={left - 8} y={top + inner * fraction + 4} textAnchor="end">
              {money.format(max * (1 - fraction))}
            </text>
          </g>
        ))}
        {rows.map((row, index) => {
          const barHeight = (Math.max(0, row.net_spending) / max) * inner;
          return (
            <g key={row.month}>
              <rect
                x={left + index * slot + slot * 0.16}
                y={top + inner - barHeight}
                width={Math.max(4, slot * 0.68)}
                height={barHeight}
              />
              {(rows.length <= 12 || index % 3 === 0) && (
                <text
                  x={left + index * slot + slot * 0.5}
                  y={height - 12}
                  textAnchor="middle"
                >
                  {row.month.slice(2).replace("-", "/")}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <figcaption>
        Net purchases after refunds. Payments and transfers are excluded.
      </figcaption>
    </figure>
  );
}

function ReviewItem({ item, saved }: { item: ReviewRow; saved: () => void }) {
  const [merchant, setMerchant] = useState(item.merchant_name);
  const [category, setCategory] = useState(
    item.category === "UNCATEGORIZED" ? "OTHER" : item.category,
  );
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    setWorking(true);
    setError("");
    try {
      const response = await fetch(
        `/api/private/transactions/${encodeURIComponent(item.transaction_id)}/classification`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            merchant_name: merchant,
            category: { primary: category, detailed: `${category}_OTHER` },
            tags: [],
            splits: [],
          }),
        },
      );
      if (!response.ok) throw new Error("save failed");
      saved();
    } catch {
      setError("Could not save this classification.");
    } finally {
      setWorking(false);
    }
  }
  return (
    <article className="review-row">
      <div>
        <span>{dateLabel(item.date)}</span>
        <strong>{money.format(item.amount)}</strong>
      </div>
      <label>
        <span>Merchant</span>
        <input
          value={merchant}
          onChange={(event) => setMerchant(event.target.value)}
        />
      </label>
      <label>
        <span>Category</span>
        <select
          value={category}
          onChange={(event) => setCategory(event.target.value)}
        >
          {categoryOptions.map((value) => (
            <option value={value} key={value}>
              {label(value)}
            </option>
          ))}
        </select>
      </label>
      <button type="button" onClick={() => void save()} disabled={working}>
        {working ? "Saving…" : "Save correction"}
      </button>
      {error && <small className="inline-error">{error}</small>}
    </article>
  );
}

export function SpendingAnalytics() {
  const [months, setMonths] = useState(12);
  const [data, setData] = useState<SpendingResult | null>(null);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const [activity, setActivity] = useState("all");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    setError("");
    fetch(`/api/private/spending/analytics?months=${months}`)
      .then((response) => {
        if (!response.ok) throw new Error("unavailable");
        return response.json();
      })
      .then(setData)
      .catch(() => setError("Spending analytics could not be loaded."));
  }, [months, revision]);
  const filtered = useMemo(
    () =>
      (data?.transactions || []).filter(
        (row) =>
          (!search ||
            row.merchant_name.toLowerCase().includes(search.toLowerCase())) &&
          (category === "all" || row.category.primary === category) &&
          (activity === "all" || row.accounting_type === activity),
      ),
    [data, search, category, activity],
  );
  if (error)
    return (
      <section className="analytics-shell">
        <p className="dashboard-error" role="alert">
          {error}
        </p>
      </section>
    );
  if (!data)
    return (
      <section className="analytics-shell">
        <p className="dashboard-loading">Building spending history…</p>
      </section>
    );
  const categoryMax = Math.max(
    1,
    ...data.categories.map((row) => row.net_spending),
  );
  const periodLabel = months === 0 ? "All available history" : `Trailing ${months} months`;
  return (
    <section className="analytics-shell" aria-labelledby="spending-title">
      <div className="analytics-heading">
        <div>
          <p className="eyebrow">Observed spending</p>
          <h2 id="spending-title">Where the money actually went</h2>
          <p>
            Payments, transfers, and refunds are separated before purchases are
            summarized.
          </p>
        </div>
        <label>
          Period
          <select
            value={months}
            onChange={(event) => setMonths(Number(event.target.value))}
          >
            <option value={3}>3 months</option>
            <option value={6}>6 months</option>
            <option value={12}>12 months</option>
            <option value={24}>24 months</option>
            <option value={0}>All history</option>
          </select>
        </label>
      </div>
      <div className="spending-summary">
        <article>
          <span>Net spending · {periodLabel}</span>
          <strong>{money.format(data.summary.net_spending)}</strong>
          <small>
            {dateLabel(data.period.start || undefined)}–
            {dateLabel(data.period.end)}
          </small>
        </article>
        <article>
          <span>Average per month</span>
          <strong>{money.format(data.summary.average_monthly_spending)}</strong>
          <small>Observed, not a target</small>
        </article>
        <article>
          <span>Refunds and credits</span>
          <strong>{money.format(data.summary.refunds)}</strong>
          <small>Removed from purchases</small>
        </article>
        <article>
          <span>Card payments</span>
          <strong>{money.format(data.summary.credit_card_payments)}</strong>
          <small>Cash movement, not spending</small>
        </article>
      </div>
      {data.data_quality.cross_source_records_excluded > 0 && (
        <p className="dedupe-note">
          {data.data_quality.cross_source_records_excluded} overlapping CSV/Plaid
          copies ({money.format(data.data_quality.cross_source_absolute_value_excluded)})
          are excluded from every total. Plaid is used for the calculated record;
          the encrypted raw imports remain preserved for audit history.
        </p>
      )}
      <div className="analytics-grid wide-left">
        <article className="analytics-card">
          <div className="section-title">
            <div>
              <p className="eyebrow">Monthly history</p>
              <h2>Spending over time</h2>
            </div>
            <span>{data.summary.transaction_count} records</span>
          </div>
          <SpendingChart rows={data.monthly} />
        </article>
        <article className="analytics-card">
          <div className="section-title">
            <div>
              <p className="eyebrow">Merchant totals</p>
              <h2>Largest destinations</h2>
            </div>
          </div>
          <div className="rank-list">
            {data.merchants.slice(0, 12).map((row) => (
              <div key={row.merchant_name}>
                <span>
                  {row.merchant_name}
                  <small>{row.transaction_count} transactions</small>
                </span>
                <strong>{money.format(row.net_spending)}</strong>
              </div>
            ))}
          </div>
        </article>
      </div>
      <div className="analytics-grid">
        <article className="analytics-card">
          <div className="section-title">
            <div>
              <p className="eyebrow">Categories</p>
              <h2>Composition</h2>
            </div>
          </div>
          <div className="bar-list">
            {data.categories
              .filter((row) => row.net_spending > 0)
              .map((row) => (
                <div key={row.category}>
                  <span>{label(row.category)}</span>
                  <div>
                    <i
                      style={{
                        width: `${(row.net_spending / categoryMax) * 100}%`,
                      }}
                    />
                  </div>
                  <strong>{money.format(row.net_spending)}</strong>
                </div>
              ))}
          </div>
        </article>
        <article className="analytics-card">
          <div className="section-title">
            <div>
              <p className="eyebrow">Longer cycles</p>
              <h2>Quarterly detail</h2>
            </div>
          </div>
          <div className="period-columns">
            <div>
              <h3>Quarterly</h3>
              {data.quarterly.map((row) => (
                <p key={row.period}>
                  <span>{row.period}</span>
                  <strong>{money.format(row.net_spending)}</strong>
                </p>
              ))}
            </div>
            <div className="period-explainer">
              <h3>How to read it</h3>
              <p>Each quarter uses only purchases posted in the selected period, after refunds.</p>
              <p>Full calendar-year comparisons are shown below with coverage, so partial years do not masquerade as complete ones.</p>
            </div>
          </div>
        </article>
      </div>
      <article className="analytics-card yearly-card">
        <div className="section-title">
          <div>
            <p className="eyebrow">Calendar-year context</p>
            <h2>What each year actually represents</h2>
          </div>
          <span>All imported history</span>
        </div>
        <div className="yearly-grid">
          {data.yearly.map((row) => (
            <article key={row.year}>
              <div><strong>{row.year}</strong><span>{row.complete_year ? "Complete year" : `${row.months_covered} months imported`}</span></div>
              <b>{money.format(row.net_spending)} <small>net spending</small></b>
              <dl>
                <div><dt>Average / observed month</dt><dd>{money.format(row.average_per_observed_month)}</dd></div>
                <div><dt>Refunds</dt><dd>{money.format(row.refunds)}</dd></div>
                <div><dt>Card payments excluded</dt><dd>{money.format(row.card_payments)}</dd></div>
                <div><dt>Transactions</dt><dd>{row.transaction_count}</dd></div>
              </dl>
              {row.current_year && <p>Current pace: {money.format(row.annualized_pace)} over 12 months. This is context, not a forecast.</p>}
              {!row.complete_year && !row.current_year && <p>Partial historical import. No full-year extrapolation is shown.</p>}
            </article>
          ))}
        </div>
      </article>
      <div className="analytics-grid">
        <article className="analytics-card">
          <div className="section-title">
            <div>
              <p className="eyebrow">Recurring detection</p>
              <h2>Likely subscriptions and obligations</h2>
            </div>
            <span>{data.recurring.length} found</span>
          </div>
          {data.recurring.length === 0 ? (
            <p className="quiet">No stable cadence has enough evidence yet.</p>
          ) : (
            <div className="decision-list">
              {data.recurring.slice(0, 12).map((row) => (
                <article key={row.id}>
                  <span>{row.cadence}</span>
                  <strong>
                    {row.merchant_name} · {money.format(row.estimated_amount)}
                  </strong>
                  <p>{row.explanation}</p>
                </article>
              ))}
            </div>
          )}
        </article>
        <article className="analytics-card">
          <div className="section-title">
            <div>
              <p className="eyebrow">Statement status</p>
              <h2>Credit-card obligations</h2>
            </div>
          </div>
          {data.liabilities.length === 0 ? (
            <p className="quiet">
              Statement details have not been returned by the provider yet.
            </p>
          ) : (
            data.liabilities.map((row) => (
              <div className="liability-card" key={row.account_id}>
                <p>
                  <span>Statement balance</span>
                  <strong>
                    {money.format(row.last_statement_balance || 0)}
                  </strong>
                </p>
                <p>
                  <span>Next due</span>
                  <strong>{dateLabel(row.next_payment_due_date)}</strong>
                </p>
                <p>
                  <span>Minimum due</span>
                  <strong>
                    {money.format(row.minimum_payment_amount || 0)}
                  </strong>
                </p>
                <p>
                  <span>Last payment</span>
                  <strong>
                    {money.format(row.last_payment_amount || 0)} ·{" "}
                    {dateLabel(row.last_payment_date)}
                  </strong>
                </p>
                <small
                  className={row.is_overdue ? "overdue" : "current-status"}
                >
                  {row.is_overdue ? "Reported overdue" : "Not reported overdue"}
                </small>
              </div>
            ))
          )}
        </article>
      </div>
      <article className="analytics-card">
        <div className="section-title">
          <div>
            <p className="eyebrow">Review signals</p>
            <h2>Strong deviations worth a second look</h2>
          </div>
          <span>{data.anomalies.length} signals</span>
        </div>
        <div className="anomaly-grid">
          {data.anomalies.length === 0 ? (
            <p className="quiet">
              No high-confidence review signals in this period.
            </p>
          ) : (
            data.anomalies.slice(0, 12).map((row, index) => (
              <article key={`${row.type}-${row.date}-${index}`}>
                <span>
                  {label(row.type)} · {dateLabel(row.date)}
                </span>
                <strong>{row.merchant_name}</strong>
                <b>{money.format(row.amount)}</b>
                <p>{row.explanation}</p>
              </article>
            ))
          )}
        </div>
      </article>
      {data.review.items.length > 0 && (
        <article className="analytics-card">
          <div className="section-title">
            <div>
              <p className="eyebrow">Data quality</p>
              <h2>Transactions needing a category</h2>
            </div>
            <span>{data.review.count} total</span>
          </div>
          <p className="quiet">
            Corrections become encrypted owner classifications and are reused in
            future analytics.
          </p>
          <div className="review-grid">
            {data.review.items.slice(0, 12).map((row) => (
              <ReviewItem
                item={row}
                key={row.transaction_id}
                saved={() => setRevision((value) => value + 1)}
              />
            ))}
          </div>
        </article>
      )}
      <article className="analytics-card transaction-browser">
        <div className="section-title">
          <div>
            <p className="eyebrow">Drill down</p>
            <h2>Transaction explorer</h2>
          </div>
          <span>{filtered.length} matches</span>
        </div>
        <div className="transaction-filters">
          <label>
            Search
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Merchant name"
            />
          </label>
          <label>
            Category
            <select
              value={category}
              onChange={(event) => setCategory(event.target.value)}
            >
              <option value="all">All categories</option>
              {Array.from(
                new Set(data.transactions.map((row) => row.category.primary)),
              )
                .sort()
                .map((value) => (
                  <option value={value} key={value}>
                    {label(value)}
                  </option>
                ))}
            </select>
          </label>
          <label>
            Activity
            <select
              value={activity}
              onChange={(event) => setActivity(event.target.value)}
            >
              <option value="all">All activity</option>
              {Array.from(
                new Set(data.transactions.map((row) => row.accounting_type)),
              )
                .sort()
                .map((value) => (
                  <option value={value} key={value}>
                    {label(value)}
                  </option>
                ))}
            </select>
          </label>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Merchant</th>
                <th>Type</th>
                <th>Category</th>
                <th>Amount</th>
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, 100).map((row) => (
                <tr key={row.id}>
                  <td>{dateLabel(row.date)}</td>
                  <td>{row.merchant_name}</td>
                  <td>{label(row.accounting_type)}</td>
                  <td>{label(row.category.primary)}</td>
                  <td>{money.format(row.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {filtered.length > 100 && (
          <p className="quiet">
            Showing the first 100 matching transactions. Narrow the filters to
            inspect a smaller set.
          </p>
        )}
      </article>
    </section>
  );
}
