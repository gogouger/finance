import { FormEvent, StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { Connections } from "./connections";
import { FinanceNav } from "./navigation";
import { SpendingAnalytics } from "./spending";
import { DashboardPreview, HousingVisuals, RetirementVisuals } from "./visuals";

const tools = [
  [
    "Private + public demo",
    "Financial dashboard",
    "Understand net worth, actual spending, recurring costs, and investment growth without forcing your life into a generic budget.",
    "/preview",
    "View a fictional demo",
  ],
  [
    "Public calculator",
    "The real cost of a house",
    "Compare buying with renting and investing the difference. See what becomes equity, what is spent, and when either path breaks even.",
    "/housing",
    "Compare housing",
  ],
  [
    "Public calculator",
    "Retire on your terms",
    "Compare taxable, Roth, and traditional accounts using spendable after-tax outcomes—including the realities of retiring early.",
    "/retirement",
    "Plan retirement",
  ],
] as const;

type Inputs = {
  home_price: number;
  down_payment: number;
  mortgage_rate_percent: number;
  mortgage_term_years: number;
  monthly_rent: number;
  years: number;
  home_appreciation_percent: number;
  rent_growth_percent: number;
  investment_return_percent: number;
  investment_tax_drag_percent: number;
  property_tax_percent: number;
  home_insurance_annual: number;
  maintenance_percent: number;
  hoa_monthly: number;
  owner_utilities_monthly: number;
  renter_utilities_monthly: number;
  buy_closing_cost_percent: number;
  sell_cost_percent: number;
};
type Year = {
  year: number;
  buyer_equity: number;
  buyer_net_wealth: number;
  buyer_housing_cash_paid: number;
  buyer_principal_contributed: number;
  buyer_appreciation: number;
  buyer_sale_cost: number;
  renter_investments: number;
  renter_housing_cash_paid: number;
  renter_net_contributions: number;
  renter_investment_growth: number;
  buyer_unrecoverable_cost: number;
  renter_unrecoverable_cost: number;
  buyer_advantage: number;
};
type InitialCashAllocation = {
  shared_starting_cash: number;
  buyer_down_payment_to_home: number;
  buyer_purchase_costs: number;
  renter_starting_investment: number;
};
type Result = {
  currency: "USD";
  monthly_mortgage_payment: number;
  initial_cash_allocation: InitialCashAllocation;
  years: Year[];
  crossover_years: number[];
};
type RetirementInputs = {
  current_age: number;
  retirement_age: number;
  end_age: number;
  annual_income: number;
  annual_expenses: number;
  taxable_balance: number;
  taxable_basis: number;
  traditional_balance: number;
  roth_balance: number;
  hsa_balance: number;
  taxable_contribution: number;
  traditional_contribution: number;
  roth_contribution: number;
  hsa_contribution: number;
  annual_return_percent: number;
  taxable_tax_drag_percent: number;
  inflation_percent: number;
  ordinary_tax_rate_percent: number;
  capital_gains_tax_rate_percent: number;
};
type RetirementYear = {
  age: number;
  phase: "working" | "retired";
  taxable_balance: number;
  traditional_balance: number;
  roth_balance: number;
  hsa_balance: number;
  taxable_growth: number;
  tax_deferred_growth: number;
  tax_free_growth: number;
  working_cash_surplus: number;
  spending: number;
  taxes: number;
  unmet_spending: number;
  total_balance: number;
  spendable_after_tax: number;
};
type RetirementResult = {
  currency: "USD";
  definitions: Record<string, string>;
  years: RetirementYear[];
};
type DashboardMetric = {
  key: string;
  label: string;
  value: number;
  currency: "USD";
  definition: string;
  inclusions: string[];
  exclusions: string[];
  freshness: { status: string; as_of: string | null };
  gaps: string[];
  confidence: { level: string; rationale: string };
  source_coverage: {
    covered: number;
    total: number;
    percent: number;
    sources: string[];
  };
};
type ActivitySignal = {
  type: string;
  title: string;
  amount: number;
  date: string | null;
  merchant_name?: string;
  account_reference?: string;
  explanation: string;
  confidence: { level: string; rationale: string };
  review_required: boolean;
};
type BillingAlert = {
  type: "unexpected_provider_charge";
  provider: string;
  date: string | null;
  amount: number;
  currency: "USD";
  message: string;
  review_required: boolean;
};
type DashboardResult = {
  currency: "USD";
  generated_at: string;
  reporting_period: { label: string; start: string; end: string };
  metrics: DashboardMetric[];
  sections: {
    cash_flow: {
      depository_credits: number;
      depository_debits: number;
      net: number;
      refund_credits: number;
      available: boolean;
      context: string;
    };
    adjusted_spending: {
      value: number;
      raw_purchase_outflows: number;
      excluded_from_personal: number;
      context: string;
    };
  };
  spending_by_category: Record<string, number>;
  unusual_activity: ActivitySignal[];
  billing_alerts: BillingAlert[];
  unusual_activity_method: { definition: string; limitations: string };
};
type InvestmentAccountSummary = {
  account_id: string;
  name: string;
  subtype: string | null;
  ownership_scope: "household" | "custodial";
  tax_treatment: "taxable" | "tax_deferred" | "roth" | "custodial";
  market_value: number;
  position_count: number;
  known_cost_basis: number;
  known_basis_market_value: number;
  basis_coverage_percent: number;
  unrealized_gain_on_known_basis: number;
  unrealized_gain_percent: number | null;
  estimated_federal_tax_if_sold: {
    gain_subject_to_scenario: number;
    at_0_percent: number;
    at_15_percent: number;
    at_23_8_percent: number;
    definition: string;
    exclusions: string[];
  };
};
type InvestmentHolding = {
  account_id: string;
  security_id: string;
  security_name: string | null;
  ticker_symbol: string | null;
  ownership_scope: "household" | "custodial";
  institution_value: number | null;
  cost_basis: number | null;
};
type InvestmentResult = {
  summary: {
    household_market_value: number;
    household_position_count: number;
    custodial_market_value: number;
    custodial_position_count: number;
    custodial_definition: string;
  };
  account_summaries: InvestmentAccountSummary[];
  holdings: InvestmentHolding[];
};
type HomeAsset = {
  id: string;
  kind: string;
  name: string;
  purchase_price: number;
  valuation: { amount: number; source_label: string; valued_at: string };
  ownership: { debt_balance: number; net_equity_after_sale: number };
  cost_summary: {
    annual_ownership_costs?: Record<string, number>;
    annual_ownership_total?: number;
  };
  valuation_automation?: {
    latest_estimates?: Array<{
      amount: number;
      observed_at: string;
      estimate_range?: { low: number; high: number };
      source?: { label?: string };
    }>;
  };
  valuation_history?: Array<{ amount: number; valued_at: string; source_label: string }>;
};
type AssetsResult = { currency: "USD"; assets: HomeAsset[] };

const defaults: Inputs = {
  home_price: 500000,
  down_payment: 100000,
  mortgage_rate_percent: 6.5,
  mortgage_term_years: 30,
  monthly_rent: 2600,
  years: 10,
  home_appreciation_percent: 3,
  rent_growth_percent: 3,
  investment_return_percent: 7,
  investment_tax_drag_percent: 0.75,
  property_tax_percent: 0.65,
  home_insurance_annual: 2800,
  maintenance_percent: 1,
  hoa_monthly: 0,
  owner_utilities_monthly: 350,
  renter_utilities_monthly: 250,
  buy_closing_cost_percent: 3,
  sell_cost_percent: 7,
};
const retirementDefaults: RetirementInputs = {
  current_age: 26,
  retirement_age: 45,
  end_age: 95,
  annual_income: 120000,
  annual_expenses: 70000,
  taxable_balance: 25000,
  taxable_basis: 22000,
  traditional_balance: 60000,
  roth_balance: 20000,
  hsa_balance: 5000,
  taxable_contribution: 10000,
  traditional_contribution: 23000,
  roth_contribution: 7000,
  hsa_contribution: 4000,
  annual_return_percent: 7,
  taxable_tax_drag_percent: 0.75,
  inflation_percent: 2.5,
  ordinary_tax_rate_percent: 22,
  capital_gains_tax_rate_percent: 15,
};
const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});
const Nav = FinanceNav;

function Landing() {
  return (
    <main>
      <FinanceNav />
      <section className="hero" aria-labelledby="page-title">
        <p className="kicker">A clearer household balance sheet</p>
        <h1 id="page-title">Your money, explained.</h1>
        <p className="lede">
          One private picture of what you own, spend, and grow—plus transparent
          tools for the two decisions that reshape a lifetime.
        </p>
      </section>
      <section className="areas" aria-label="Finance tools">
        {tools.map((tool, index) => (
          <article className="card" key={tool[3]}>
            <div className="number" aria-hidden="true">
              0{index + 1}
            </div>
            <p className="eyebrow">{tool[0]}</p>
            <h2>{tool[1]}</h2>
            <p>{tool[2]}</p>
            <a href={tool[3]}>
              {tool[4]} <span aria-hidden="true">→</span>
            </a>
          </article>
        ))}
      </section>
      <footer>
        <p>
          Read-only by design. Sources, freshness, and assumptions stay visible.
        </p>
        <p>USD · United States</p>
      </footer>
    </main>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  format,
  change,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format: (value: number) => string;
  change: (value: number) => void;
}) {
  return (
    <label className="slider-row">
      <span>
        {label}
        <output>{format(value)}</output>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => change(Number(event.target.value))}
      />
    </label>
  );
}

function NumberField({
  label,
  value,
  suffix,
  change,
}: {
  label: string;
  value: number;
  suffix?: string;
  change: (value: number) => void;
}) {
  return (
    <label className="number-field">
      <span>{label}</span>
      <div>
        <input
          type="number"
          min="0"
          step="any"
          value={value}
          onChange={(event) => change(Number(event.target.value))}
        />
        {suffix && <small>{suffix}</small>}
      </div>
    </label>
  );
}

function Chart({ years }: { years: Year[] }) {
  const width = 720,
    height = 260;
  const max = Math.max(
    1,
    ...years.flatMap((year) => [
      year.buyer_net_wealth,
      year.renter_investments,
    ]),
  );
  const min = Math.min(
    0,
    ...years.flatMap((year) => [
      year.buyer_net_wealth,
      year.renter_investments,
    ]),
  );
  const points = (field: "buyer_net_wealth" | "renter_investments") =>
    years
      .map(
        (year, index) =>
          `${years.length === 1 ? width : (index / (years.length - 1)) * width},${height - ((year[field] - min) / (max - min || 1)) * height}`,
      )
      .join(" ");
  return (
    <figure className="wealth-chart">
      <figcaption>Projected net wealth by year</figcaption>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Buyer and renter projected net wealth lines"
      >
        <polyline className="buyer-line" points={points("buyer_net_wealth")} />
        <polyline
          className="renter-line"
          points={points("renter_investments")}
        />
      </svg>
      <div className="legend">
        <span className="buyer-dot" /> Buy <span className="renter-dot" /> Rent
        + invest
      </div>
    </figure>
  );
}

function Housing() {
  const [inputs, setInputs] = useState(defaults);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [completedAt, setCompletedAt] = useState("");
  const update = (key: keyof Inputs) => (value: number) =>
    setInputs((current) => ({ ...current, [key]: value }));
  async function calculate(event?: FormEvent) {
    event?.preventDefault();
    setError("");
    setRunning(true);
    try {
      const response = await fetch("/api/public/housing/calculate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(inputs),
      });
      if (!response.ok) throw new Error("invalid assumptions");
      setResult(await response.json());
      setCompletedAt(
        new Date().toLocaleTimeString([], {
          hour: "numeric",
          minute: "2-digit",
          second: "2-digit",
        }),
      );
    } catch {
      setError(
        "The projection could not run. Check the assumptions and try again.",
      );
    } finally {
      setRunning(false);
    }
  }
  useEffect(() => {
    void calculate();
  }, []);
  const final = result?.years.at(-1);
  const outcome = !final
    ? "Calculating…"
    : final.buyer_advantage >= 0
      ? `Buying finishes ${money.format(final.buyer_advantage)} ahead.`
      : `Renting and investing finishes ${money.format(Math.abs(final.buyer_advantage))} ahead.`;
  return (
    <main>
      <Nav />
      <header className="calculator-head">
        <a className="back-link" href="/">
          ← Finance
        </a>
        <p className="kicker">Housing, without the sales pitch</p>
        <h1>The real cost of a house</h1>
        <p className="lede">
          Principal builds equity. Everything else is a cost. Renting can invest
          the cash it does not spend. This model keeps all three ideas separate.
        </p>
      </header>
      <div className="calculator-grid">
        <form className="assumptions" onSubmit={calculate}>
          <div className="section-title">
            <h2>Assumptions</h2>
            <span>Editable</span>
          </div>
          <Slider
            label="Home price"
            value={inputs.home_price}
            min={100000}
            max={2000000}
            step={10000}
            format={money.format}
            change={update("home_price")}
          />
          <Slider
            label="Down payment"
            value={inputs.down_payment}
            min={0}
            max={inputs.home_price}
            step={5000}
            format={money.format}
            change={update("down_payment")}
          />
          <Slider
            label="Mortgage rate"
            value={inputs.mortgage_rate_percent}
            min={0}
            max={15}
            step={0.125}
            format={(v) => `${v}%`}
            change={update("mortgage_rate_percent")}
          />
          <Slider
            label="Comparable rent"
            value={inputs.monthly_rent}
            min={500}
            max={10000}
            step={100}
            format={(v) => `${money.format(v)}/mo`}
            change={update("monthly_rent")}
          />
          <Slider
            label="Time in home"
            value={inputs.years}
            min={1}
            max={30}
            step={1}
            format={(v) => `${v} years`}
            change={update("years")}
          />
          <Slider
            label="Home appreciation"
            value={inputs.home_appreciation_percent}
            min={-5}
            max={10}
            step={0.25}
            format={(v) => `${v}%/yr`}
            change={update("home_appreciation_percent")}
          />
          <Slider
            label="Investment return"
            value={inputs.investment_return_percent}
            min={-5}
            max={15}
            step={0.25}
            format={(v) => `${v}%/yr`}
            change={update("investment_return_percent")}
          />
          <button type="submit" disabled={running}>
            {running ? "Recalculating…" : "Recalculate projection"}
          </button>
          {completedAt && !running && (
            <p className="completion" role="status">
              Updated at {completedAt}
            </p>
          )}
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
        </form>
        <section className="results" aria-live="polite">
          <div className="result-lead">
            <p className="eyebrow">At year {inputs.years}</p>
            <h2>{outcome}</h2>
            <p>
              Mortgage payment:{" "}
              {result ? money.format(result.monthly_mortgage_payment) : "—"} per
              month, before ownership costs.
            </p>
          </div>
          {final && result && (
            <>
              <div className="metric-grid">
                <div>
                  <span>Buyer net wealth</span>
                  <strong>{money.format(final.buyer_net_wealth)}</strong>
                </div>
                <div>
                  <span>Renter investments</span>
                  <strong>{money.format(final.renter_investments)}</strong>
                </div>
                <div>
                  <span>Buyer unrecoverable</span>
                  <strong>
                    {money.format(final.buyer_unrecoverable_cost)}
                  </strong>
                </div>
                <div>
                  <span>Renter unrecoverable</span>
                  <strong>
                    {money.format(final.renter_unrecoverable_cost)}
                  </strong>
                </div>
              </div>
              <HousingVisuals
                years={result.years}
                initialCash={result.initial_cash_allocation}
              />
              <details className="definitions">
                <summary>What these numbers include</summary>
                <p>
                  Buyer wealth is sale proceeds after remaining debt and
                  estimated sale costs. Buyer unrecoverable cost includes
                  interest, tax, insurance, maintenance, HOA, utilities,
                  purchase costs, and sale costs. Renter wealth starts with
                  avoided down payment and closing costs, then adds or withdraws
                  the monthly cash-flow difference. Investment growth is shown
                  separately from those contributions.
                </p>
              </details>
              <details>
                <summary>Year-by-year accessible results</summary>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Year</th>
                        <th>Buy</th>
                        <th>Rent + invest</th>
                        <th>Difference</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.years.map((year) => (
                        <tr key={year.year}>
                          <td>{year.year}</td>
                          <td>{money.format(year.buyer_net_wealth)}</td>
                          <td>{money.format(year.renter_investments)}</td>
                          <td>{money.format(year.buyer_advantage)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            </>
          )}
        </section>
      </div>
    </main>
  );
}

function Retirement() {
  const [inputs, setInputs] = useState(retirementDefaults);
  const [result, setResult] = useState<RetirementResult | null>(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [completedAt, setCompletedAt] = useState("");
  const update = (key: keyof RetirementInputs) => (value: number) =>
    setInputs((current) => ({ ...current, [key]: value }));
  async function calculate(event?: FormEvent) {
    event?.preventDefault();
    setError("");
    setRunning(true);
    try {
      const response = await fetch("/api/public/retirement/calculate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(inputs),
      });
      if (!response.ok) throw new Error("invalid assumptions");
      setResult(await response.json());
      setCompletedAt(
        new Date().toLocaleTimeString([], {
          hour: "numeric",
          minute: "2-digit",
          second: "2-digit",
        }),
      );
    } catch {
      setError(
        "The projection could not run. Current age must come before retirement age, and retirement must come before the ending age.",
      );
    } finally {
      setRunning(false);
    }
  }
  useEffect(() => {
    void calculate();
  }, []);
  const final = result?.years.at(-1);
  const fields: [string, keyof RetirementInputs, string?][] = [
    ["Current age", "current_age", "years"],
    ["Retire at", "retirement_age", "years"],
    ["Plan through", "end_age", "age"],
    ["Annual income", "annual_income", "USD"],
    ["Annual expenses", "annual_expenses", "USD"],
    ["Taxable balance", "taxable_balance", "USD"],
    ["Taxable cost basis", "taxable_basis", "USD"],
    ["Traditional balance", "traditional_balance", "USD"],
    ["Roth balance", "roth_balance", "USD"],
    ["HSA balance", "hsa_balance", "USD"],
    ["Taxable contribution", "taxable_contribution", "USD/yr"],
    ["Traditional contribution", "traditional_contribution", "USD/yr"],
    ["Roth contribution", "roth_contribution", "USD/yr"],
    ["HSA contribution", "hsa_contribution", "USD/yr"],
    ["Annual return", "annual_return_percent", "%"],
    ["Taxable tax drag", "taxable_tax_drag_percent", "%"],
    ["Inflation", "inflation_percent", "%"],
    ["Ordinary tax rate", "ordinary_tax_rate_percent", "%"],
    ["Capital-gains rate", "capital_gains_tax_rate_percent", "%"],
  ];
  return (
    <main>
      <Nav />
      <header className="calculator-head">
        <a className="back-link" href="/">
          ← Finance
        </a>
        <p className="kicker">After-tax planning</p>
        <h1>Retire on your terms</h1>
        <p className="lede">
          A transparent year-by-year baseline across taxable, traditional, Roth,
          and HSA accounts. Balances are not equal until taxes are accounted
          for.
        </p>
      </header>
      <div className="retirement-layout">
        <form className="retirement-form" onSubmit={calculate}>
          <div className="section-title">
            <h2>Baseline assumptions</h2>
            <span>Editable</span>
          </div>
          <div className="field-grid">
            {fields.map(([label, key, suffix]) => (
              <NumberField
                key={key}
                label={label}
                value={inputs[key]}
                suffix={suffix}
                change={update(key)}
              />
            ))}
          </div>
          <button type="submit" disabled={running}>
            {running ? "Running projection…" : "Run projection"}
          </button>
          {completedAt && !running && (
            <p className="completion" role="status">
              Projection updated at {completedAt}
            </p>
          )}
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
        </form>
        <section className="results" aria-live="polite">
          <div className="result-lead">
            <p className="eyebrow">At age {inputs.end_age}</p>
            <h2>
              {final
                ? `${money.format(final.spendable_after_tax)} spendable after tax`
                : "Calculating…"}
            </h2>
            <p>
              {final
                ? `${money.format(final.total_balance)} headline balance before embedded taxes.`
                : "Building the year-by-year projection."}
            </p>
          </div>
          {final && result && (
            <>
              <div className="metric-grid">
                <div>
                  <span>Taxable growth</span>
                  <strong>{money.format(final.taxable_growth)}</strong>
                </div>
                <div>
                  <span>Tax-deferred growth</span>
                  <strong>{money.format(final.tax_deferred_growth)}</strong>
                </div>
                <div>
                  <span>Tax-free growth</span>
                  <strong>{money.format(final.tax_free_growth)}</strong>
                </div>
                <div>
                  <span>Unmet spending</span>
                  <strong>{money.format(final.unmet_spending)}</strong>
                </div>
              </div>
              <RetirementVisuals
                years={result.years}
                retirementAge={inputs.retirement_age}
              />
              <details className="definitions" open>
                <summary>Calculation definitions</summary>
                {Object.entries(result.definitions).map(
                  ([account, definition]) => (
                    <p key={account}>
                      <strong>
                        {account[0].toUpperCase() + account.slice(1)}:
                      </strong>{" "}
                      {definition}
                    </p>
                  ),
                )}
              </details>
              <details>
                <summary>Year-by-year accessible results</summary>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Age</th>
                        <th>Phase</th>
                        <th>Total</th>
                        <th>After tax</th>
                        <th>Spending</th>
                        <th>Unmet</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.years.map((year) => (
                        <tr key={year.age}>
                          <td>{year.age}</td>
                          <td>{year.phase}</td>
                          <td>{money.format(year.total_balance)}</td>
                          <td>{money.format(year.spendable_after_tax)}</td>
                          <td>{money.format(year.spending)}</td>
                          <td>{money.format(year.unmet_spending)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            </>
          )}
        </section>
      </div>
    </main>
  );
}

function NetWorthVisual({ dashboard }: { dashboard: DashboardResult }) {
  const values = new Map(dashboard.metrics.map((metric) => [metric.key, metric.value]));
  const parts = [
    { label: "Home + vehicles", value: values.get("household_asset_value") || 0, color: "#83d7ad" },
    { label: "Investments", value: values.get("investment_value") || 0, color: "#f4c86a" },
    { label: "Cash", value: values.get("cash") || 0, color: "#7eb6d8" },
  ];
  const liabilities = (values.get("debt") || 0) + (values.get("credit_card_liabilities") || 0);
  const gross = parts.reduce((total, item) => total + item.value, 0);
  const net = values.get("net_worth") || gross - liabilities;
  const max = Math.max(gross, 1);
  const gradient = parts
    .reduce<{ stops: string[]; cursor: number }>((result, item) => {
      const start = result.cursor;
      const end = start + (item.value / max) * 100;
      result.stops.push(`${item.color} ${start}% ${end}%`);
      result.cursor = end;
      return result;
    }, { stops: [], cursor: 0 }).stops.join(", ");
  return (
    <section className="wealth-story dashboard-panel" aria-labelledby="wealth-heading">
      <div className="section-title">
        <div><p className="eyebrow">Net worth, visually</p><h2 id="wealth-heading">What you own, minus what you owe</h2></div>
        <strong>{money.format(net)}</strong>
      </div>
      <div className="wealth-layout">
        <div className="wealth-ring" style={{ background: `conic-gradient(${gradient})` }}>
          <div><span>Net worth</span><strong>{money.format(net)}</strong><small>{money.format(liabilities)} liabilities</small></div>
        </div>
        <div className="wealth-breakdown">
          {parts.map((part) => (
            <div key={part.label}>
              <i style={{ background: part.color }} />
              <span>{part.label}<small>{((part.value / max) * 100).toFixed(1)}% of gross assets</small></span>
              <strong>{money.format(part.value)}</strong>
            </div>
          ))}
          <div className="liability-row"><i /><span>Liabilities<small>Card snapshot + registered asset debt</small></span><strong>−{money.format(liabilities)}</strong></div>
        </div>
      </div>
      <p className="visual-note">Children’s custodial investments are intentionally outside this household total.</p>
    </section>
  );
}

function InvestmentOverview({ data }: { data: InvestmentResult }) {
  const householdAccounts = data.account_summaries.filter((item) => item.ownership_scope === "household");
  const householdHoldings = data.holdings.filter((item) => item.ownership_scope === "household");
  const maxAccount = Math.max(...householdAccounts.map((item) => item.market_value), 1);
  const treatment = {
    taxable: "Taxable brokerage",
    tax_deferred: "Tax deferred",
    roth: "Roth",
    custodial: "Child-owned custodial",
  };
  return (
    <section className="dashboard-panel investment-overview" aria-labelledby="investment-heading">
      <div className="section-title">
        <div><p className="eyebrow">Investments</p><h2 id="investment-heading">Performance and tax exposure</h2></div>
        <strong>{money.format(data.summary.household_market_value)}</strong>
      </div>
      <p className="visual-note">“Gain” below is current value minus known cost basis—not total return. Dividends, fees, and positions without basis are not silently guessed.</p>
      <div className="account-performance-list">
        {householdAccounts.map((account) => (
          <article key={account.account_id}>
            <div className="account-row-head">
              <div><h3>{account.name}</h3><span>{treatment[account.tax_treatment]} · {account.position_count} positions</span></div>
              <strong>{money.format(account.market_value)}</strong>
            </div>
            <div className="account-value-track"><i style={{ width: `${(account.market_value / maxAccount) * 100}%` }} /></div>
            <div className="account-stats">
              <span>Known-basis gain <strong className={account.unrealized_gain_on_known_basis < 0 ? "negative" : "positive"}>{money.format(account.unrealized_gain_on_known_basis)}{account.unrealized_gain_percent !== null ? ` · ${account.unrealized_gain_percent.toFixed(1)}%` : ""}</strong></span>
              <span>Basis coverage <strong>{account.basis_coverage_percent.toFixed(0)}%</strong></span>
              <span>Illustrative federal tax at 15% <strong>{account.tax_treatment === "taxable" ? money.format(account.estimated_federal_tax_if_sold.at_15_percent) : "Not currently realized"}</strong></span>
            </div>
          </article>
        ))}
      </div>
      <details className="position-details">
        <summary>See every household position</summary>
        <div className="position-list">
          {householdHoldings.sort((a, b) => (b.institution_value || 0) - (a.institution_value || 0)).map((holding) => {
            const gain = holding.cost_basis === null ? null : (holding.institution_value || 0) - holding.cost_basis;
            return <div key={`${holding.account_id}-${holding.security_id}`}><span><strong>{holding.ticker_symbol || holding.security_name || "Investment"}</strong><small>{holding.security_name}</small></span><span>{money.format(holding.institution_value || 0)}<small>{gain === null ? "Basis unavailable" : `${gain >= 0 ? "+" : ""}${money.format(gain)} vs. basis`}</small></span></div>;
          })}
        </div>
      </details>
      <div className="tax-explainer">
        <strong>Tax estimate boundaries</strong>
        <p>Taxable accounts use 0%, 15%, and 23.8% federal long-term-gain scenarios only. Retirement trades generally do not create a current capital-gains bill; future traditional-account distributions are generally taxable, while qualified Roth distributions are generally tax-free. State tax, holding period, income brackets, loss netting, and missing basis still need tax-return data.</p>
      </div>
    </section>
  );
}

function HomeOverview({ home }: { home: HomeAsset }) {
  const [growthRate, setGrowthRate] = useState(4);
  const estimate = home.valuation_automation?.latest_estimates?.[0];
  const currentValue = estimate?.amount || home.valuation.amount;
  const costs = home.cost_summary.annual_ownership_costs || {};
  const years = [0, 5, 10, 20];
  const projections = years.map((year) => ({ year, value: currentValue * Math.pow(1 + growthRate / 100, year) }));
  const maxProjection = projections.at(-1)?.value || currentValue;
  return (
    <section className="dashboard-panel home-overview" aria-labelledby="home-heading">
      <div className="section-title">
        <div><p className="eyebrow">Home</p><h2 id="home-heading">Market value, carrying cost, and growth</h2></div>
        <strong>{money.format(currentValue)}</strong>
      </div>
      <div className="home-value-grid">
        <article><span>Current market estimate</span><strong>{money.format(currentValue)}</strong><small>{estimate?.source?.label || home.valuation.source_label} · {estimate ? new Date(estimate.observed_at).toLocaleDateString() : new Date(home.valuation.valued_at).toLocaleDateString()}</small>{estimate?.estimate_range && <div className="estimate-range"><i /><p>{money.format(estimate.estimate_range.low)} <span>estimated range</span> {money.format(estimate.estimate_range.high)}</p></div>}</article>
        <article><span>County / registered value</span><strong>{money.format(home.valuation.amount)}</strong><small>{home.valuation.source_label}</small></article>
        <article><span>Known annual carrying cost</span><strong>{money.format(home.cost_summary.annual_ownership_total || 0)}</strong><small>{Object.keys(costs).map((key) => key.replaceAll("_", " ")).join(" + ") || "No costs recorded"}. Maintenance, insurance, utilities, and improvements remain excluded until linked.</small></article>
      </div>
      <div className="growth-control">
        <label htmlFor="home-growth">Annual appreciation scenario <strong>{growthRate.toFixed(1)}%</strong></label>
        <input id="home-growth" type="range" min="0" max="8" step="0.25" value={growthRate} onChange={(event) => setGrowthRate(Number(event.target.value))} />
      </div>
      <div className="projection-bars" aria-label={`Home value projection at ${growthRate}% annual appreciation`}>
        {projections.map((point) => <div key={point.year}><span>{point.year === 0 ? "Today" : `${point.year} years`}</span><div><i style={{ width: `${(point.value / maxProjection) * 100}%` }} /></div><strong>{money.format(point.value)}</strong></div>)}
      </div>
      <p className="visual-note">This is a compound-growth scenario, not a forecast. There is not enough time-series history for this property yet to calculate a defensible house-specific historical rate; future automated valuations will build that record.</p>
    </section>
  );
}

function Dashboard() {
  const [dashboard, setDashboard] = useState<DashboardResult | null>(null);
  const [investments, setInvestments] = useState<InvestmentResult | null>(null);
  const [assets, setAssets] = useState<AssetsResult | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    Promise.allSettled([
      fetch("/api/private/dashboard").then((response) => {
        if (!response.ok) throw new Error("dashboard unavailable");
        return response.json() as Promise<DashboardResult>;
      }),
      fetch("/api/private/investments/positions").then((response) => {
        if (!response.ok) throw new Error("investments unavailable");
        return response.json() as Promise<InvestmentResult>;
      }),
      fetch("/api/private/assets").then((response) => {
        if (!response.ok) throw new Error("assets unavailable");
        return response.json() as Promise<AssetsResult>;
      }),
    ]).then(([dashboardResult, investmentResult, assetResult]) => {
      if (dashboardResult.status === "fulfilled") setDashboard(dashboardResult.value);
      else setError("The financial overview could not be loaded. Check connection health and try again.");
      if (investmentResult.status === "fulfilled") setInvestments(investmentResult.value);
      if (assetResult.status === "fulfilled") setAssets(assetResult.value);
    });
  }, []);
  const byKey = new Map(
    dashboard?.metrics.map((metric) => [metric.key, metric]),
  );
  const headlineKeys = [
    "net_worth",
    "cash",
    "credit_card_liabilities",
    "income",
    "adjusted_personal_spending",
    "true_monthly_cost",
    "investment_value",
    "custodial_investment_value",
  ];
  return (
    <main>
      <Nav />
      <header className="dashboard-head">
        <a className="back-link" href="/">
          ← Finance
        </a>
        <p className="kicker">Private household command center</p>
        <h1>Your financial picture, with receipts.</h1>
        <p className="lede">
          Observed balances and activity—not a generic budget. Every number says
          what it includes, what it leaves out, and how current it is.
        </p>
        {dashboard && <p className="reporting-period">Activity metrics: {dashboard.reporting_period.label.toLowerCase()} · {dashboard.reporting_period.start} through {dashboard.reporting_period.end}. Balance metrics are current snapshots.</p>}
      </header>
      {error && (
        <p className="dashboard-error" role="alert">
          {error}
        </p>
      )}
      {!dashboard && !error && (
        <p className="dashboard-loading" role="status">
          Loading normalized financial records…
        </p>
      )}
      {dashboard && (
        <>
          {dashboard.billing_alerts.map((alert, index) => (
            <p
              className="dashboard-error"
              role="alert"
              key={`${alert.provider}-${alert.date}-${index}`}
            >
              {alert.message} Posted {alert.date || "on an unknown date"} for{" "}
              {money.format(alert.amount)}. Review the charge and the RentCast
              subscription.
            </p>
          ))}
          <section
            className="dashboard-metrics"
            aria-label="Financial overview"
          >
            {headlineKeys.map((key) => {
              const metric = byKey.get(key);
              if (!metric) return null;
              return (
                <article className="dashboard-metric" key={key}>
                  <div>
                    <p>{metric.label}</p>
                    <span
                      className={`freshness freshness-${metric.freshness.status}`}
                    >
                      {metric.freshness.status}
                    </span>
                  </div>
                  <strong>{money.format(metric.value)}</strong>
                  <p>{metric.definition}</p>
                  <details>
                    <summary>Definition and data quality</summary>
                    <dl>
                      <dt>Includes</dt>
                      <dd>{metric.inclusions.join("; ") || "Nothing yet"}</dd>
                      <dt>Excludes</dt>
                      <dd>{metric.exclusions.join("; ") || "Nothing"}</dd>
                      <dt>Confidence</dt>
                      <dd>
                        {metric.confidence.level} —{" "}
                        {metric.confidence.rationale}
                      </dd>
                      <dt>Coverage</dt>
                      <dd>
                        {metric.source_coverage.covered} of{" "}
                        {metric.source_coverage.total} sources (
                        {metric.source_coverage.percent}%)
                      </dd>
                      <dt>As of</dt>
                      <dd>
                        {metric.freshness.as_of
                          ? new Date(metric.freshness.as_of).toLocaleString()
                          : "Unavailable"}
                      </dd>
                      {metric.gaps.length > 0 && (
                        <>
                          <dt>Known gaps</dt>
                          <dd>{metric.gaps.join(" ")}</dd>
                        </>
                      )}
                    </dl>
                  </details>
                </article>
              );
            })}
          </section>
          <NetWorthVisual dashboard={dashboard} />
          {investments && <InvestmentOverview data={investments} />}
          {assets?.assets.find((asset) => asset.kind === "home") && (
            <HomeOverview home={assets.assets.find((asset) => asset.kind === "home")!} />
          )}
          <div className="dashboard-columns">
            <section className="dashboard-panel">
              <div className="section-title">
                <div>
                  <p className="eyebrow">Cash movement vs. spending</p>
                  <h2>Two different truths</h2>
                </div>
              </div>
              <div className="flow-compare">
                <article>
                  <span>Depository movement · trailing 12 months</span>
                  <strong>
                    {dashboard.sections.cash_flow.available
                      ? money.format(dashboard.sections.cash_flow.net)
                      : "Not available yet"}
                  </strong>
                  {dashboard.sections.cash_flow.available && <p>
                    {money.format(dashboard.sections.cash_flow.depository_credits)} bank credits ·{" "}
                    {money.format(dashboard.sections.cash_flow.depository_debits)} bank debits
                  </p>}
                  <small>{dashboard.sections.cash_flow.context}</small>
                </article>
                <article>
                  <span>Adjusted personal spending · trailing 12 months</span>
                  <strong>
                    {money.format(dashboard.sections.adjusted_spending.value)}
                  </strong>
                  <p>
                    {money.format(
                      dashboard.sections.adjusted_spending
                        .raw_purchase_outflows,
                    )}{" "}
                    raw purchases ·{" "}
                    {money.format(
                      dashboard.sections.adjusted_spending
                        .excluded_from_personal,
                    )}{" "}
                    removed by refunds or adjustments
                  </p>
                  <small>{dashboard.sections.adjusted_spending.context}</small>
                </article>
              </div>
              <h3>Spending by category · trailing 12 months</h3>
              {Object.keys(dashboard.spending_by_category).length === 0 ? (
                <p className="quiet">No finalized spending is available.</p>
              ) : (
                <div className="category-list">
                  {Object.entries(dashboard.spending_by_category).map(
                    ([category, amount]) => (
                      <div key={category}>
                        <span>
                          {category.replaceAll("_", " ").toLowerCase()}
                        </span>
                        <strong>{money.format(amount)}</strong>
                      </div>
                    ),
                  )}
                </div>
              )}
            </section>
            <section className="dashboard-panel">
              <div className="section-title">
                <div>
                  <p className="eyebrow">Review, not a verdict</p>
                  <h2>Unusual activity</h2>
                </div>
                <span>{dashboard.unusual_activity.length} signals</span>
              </div>
              <p className="quiet">
                {dashboard.unusual_activity_method.definition}{" "}
                {dashboard.unusual_activity_method.limitations}
              </p>
              {dashboard.unusual_activity.length === 0 ? (
                <div className="empty-signal">
                  <strong>No review signals right now</strong>
                  <p>
                    An empty list is not proof that every transaction is
                    expected.
                  </p>
                </div>
              ) : (
                <div className="signal-list">
                  {dashboard.unusual_activity.map((signal, index) => (
                    <article key={`${signal.type}-${signal.date}-${index}`}>
                      <div>
                        <span>{signal.type.replaceAll("_", " ")}</span>
                        <time>{signal.date || "Date unavailable"}</time>
                      </div>
                      <h3>{signal.title}</h3>
                      <strong>{money.format(signal.amount)}</strong>
                      <p>{signal.explanation}</p>
                      <small>
                        {signal.confidence.level} confidence ·{" "}
                        {signal.confidence.rationale}
                      </small>
                    </article>
                  ))}
                </div>
              )}
            </section>
          </div>
          <SpendingAnalytics />
          <section className="metric-catalog">
            <div className="section-title">
              <div>
                <p className="eyebrow">Metric registry</p>
                <h2>All definitions and gaps</h2>
              </div>
              <span>{dashboard.metrics.length} metrics</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Metric</th>
                    <th>Value</th>
                    <th>Freshness</th>
                    <th>Confidence</th>
                    <th>Known gaps</th>
                  </tr>
                </thead>
                <tbody>
                  {dashboard.metrics.map((metric) => (
                    <tr key={metric.key}>
                      <td>
                        <strong>{metric.label}</strong>
                        <small>{metric.definition}</small>
                      </td>
                      <td>{money.format(metric.value)}</td>
                      <td>{metric.freshness.status}</td>
                      <td>{metric.confidence.level}</td>
                      <td>{metric.gaps.join(" ") || "None reported"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </main>
  );
}

function App() {
  let page;
  if (location.pathname === "/housing") page = <Housing />;
  else if (location.pathname === "/retirement") page = <Retirement />;
  else if (location.pathname === "/preview")
    page = (
      <DashboardPreview
        embedded={new URLSearchParams(location.search).has("embed")}
      />
    );
  else if (location.pathname === "/settings/connections") page = <Connections />;
  else if (location.pathname === "/dashboard") page = <Dashboard />;
  else page = <Landing />;
  return (
    <>
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <div id="main-content">{page}</div>
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
