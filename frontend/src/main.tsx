import { FormEvent, StrictMode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { Connections } from "./connections";
import { FinanceNav } from "./navigation";
import { SpendingAnalytics } from "./spending";
import { DashboardPreview, HousingVisuals, RetirementComparisonVisuals } from "./visuals";

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
  general_inflation_percent: number;
  home_insurance_growth_percent?: number;
  hoa_growth_percent?: number;
  owner_utilities_growth_percent?: number;
  renter_utilities_growth_percent?: number;
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
  buyer_components: {
    home_value: number;
    loan_balance: number;
    down_payment: number;
    principal_paid: number;
    appreciation: number;
    interest: number;
    property_tax: number;
    insurance: number;
    maintenance: number;
    hoa: number;
    utilities: number;
    mortgage_insurance: number;
    purchase_costs: number;
    tax_benefit: number;
    sale_cost: number;
  };
  renter_components: {
    ending_monthly_rent: number;
    rent: number;
    utilities: number;
    net_contributions: number;
    investment_growth: number;
    estimated_investment_tax_drag: number;
  };
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
  cost_escalation: Record<string, number>;
  sensitivity: Array<{
    field: string;
    label: string;
    unit: string;
    base: number;
    lower: { assumption: number; buyer_advantage: number };
    higher: { assumption: number; buyer_advantage: number };
    swing: number;
  }>;
};
type RetirementInputs = {
  current_age: number;
  retirement_age: number;
  end_age: number;
  annual_take_home_sacrifice: number;
  annual_retirement_spending: number;
  taxable_balance: number;
  taxable_basis: number;
  traditional_balance: number;
  workplace_plan_balance: number;
  roth_balance: number;
  roth_contribution_basis: number;
  hsa_balance: number;
  annual_return_percent: number;
  taxable_tax_drag_percent: number;
  inflation_percent: number;
  current_ordinary_tax_rate_percent: number;
  retirement_ordinary_tax_rate_percent: number;
  capital_gains_tax_rate_percent: number;
  employer_match: number;
  employee_contribution_for_full_match: number;
  traditional_contribution_limit: number;
  roth_contribution_limit: number;
  hsa_contribution_limit: number;
  qualified_hsa_spending_percent: number;
  annual_conversion_amount: number;
  sepp_annual_distribution: number;
  return_stddev_percent: number;
  uncertainty_simulations: number;
  uncertainty_seed: number;
  ruleset: {
    version: string;
    effective_date: string;
    unrestricted_access_age: number;
    early_withdrawal_penalty_percent: number;
    hsa_nonqualified_penalty_percent: number;
    conversion_wait_years: number;
    rule_of_55_min_separation_age: number;
    sepp_minimum_years: number;
  };
};
type RetirementComparisonYear = {
  age: number;
  headline_balance: number;
  after_tax_value: number;
  primary_balance: number;
  taxable_overflow: number;
  accessible_basis: number;
};
type RetirementResult = {
  model_version: string;
  currency: "USD";
  retirement_age: number;
  comparison_basis: {
    annual_take_home_sacrifice: number;
    accumulation_years: number;
    ranking_metric: string;
  };
  strategies: Array<{
    key: "taxable" | "roth" | "traditional" | "hsa";
    label: string;
    annual_take_home_cost: number;
    annual_primary_contribution: number;
    annual_taxable_overflow: number;
    employer_match: number;
    years: RetirementComparisonYear[];
    at_retirement: RetirementComparisonYear;
    caveat: string;
  }>;
  ranking: Array<{ key: string; label: string; after_tax_value: number }>;
  optimized_mix: {
    annual_take_home_cost: number;
    annual_allocations: Record<"traditional" | "roth" | "hsa" | "taxable", number>;
    employer_match: number;
    match_protected: boolean;
    bridge: { required_annual_taxable_saving: number; planned_annual_taxable_saving: number; projected_accessible_at_retirement: number; projected_gap: number };
    at_retirement: { headline_balance: number; after_tax_value: number; by_account_after_tax: Record<string, number> };
    allocation_order: string[];
  };
  uncertainty: {
    model_version: string;
    seed: number;
    simulations: number;
    success_probability_percent: number;
    ending_balance_distribution: Record<"p10" | "p25" | "p50" | "p75" | "p90", number>;
    first_failure_age_distribution: Record<string, number> | null;
    sequence_risk: Record<string, number>;
    yearly: Array<{ age: number; p10: number; p50: number; p90: number; path_success_percent: number }>;
    stress_case: { name: string; success_probability_percent: number; change_from_baseline_points: number };
    assumptions: { starting_wealth_basis: string; social_security_included: boolean; pension_income_included: boolean; healthcare_costs_included: boolean; return_distribution: string };
    exclusions: string[];
    disclaimer: string;
  };
  bridge: {
    years: number;
    annual_spending_at_retirement: number;
    required_spending: number;
    accessible_at_retirement: number;
    existing_gap: number;
  };
  early_access: {
    ruleset: { version: string; effective_date: string };
    ranking: Array<{ strategy: string; spendable_value: number; eligible: boolean }>;
    strategies: Array<{
      strategy: string;
      eligible: boolean;
      spendable_value: number;
      failure_reason: string | null;
      constraints: Record<string, number | boolean | string | null>;
      years: Array<{ age: number; spendable: number; penalty: number; unmet_spending: number }>;
    }>;
  };
  drivers: string[];
  disclaimer: string;
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
type SpendingRecommendation = {
  id: string;
  kind: "fees" | "recurring_increase" | "category_growth";
  subject: string;
  title: string;
  explanation: string;
  suggested_action: string;
  estimated_impact: {
    monthly: number;
    annual: number;
    ten_year_investment_value: number;
    investment_assumption: string;
  };
  confidence: { level: string; rationale: string };
  evidence: Array<Record<string, unknown>>;
  advisory_only: boolean;
  owner_feedback: { action: string; note?: string | null; snoozed_until?: string | null; feedback_updated_at: string } | null;
};
type DashboardResult = {
  currency: "USD";
  generated_at: string;
  reporting_period: { label: string; start: string; end: string };
  metrics: DashboardMetric[];
  net_worth_change: {
    available: boolean;
    period: { label: string; start: string };
    opening_net_worth: number | null;
    ending_net_worth: number;
    change: number | null;
    direction: "stronger" | "weaker" | "unchanged" | "not_yet_measurable";
    drivers: Array<{
      key: string;
      label: string;
      value: number | null;
      definition: string;
    }>;
    reconciliation_difference: number | null;
    coverage: { covered: number; total: number; percent: number; sources: string[] };
    limitations: string[];
  };
  retirement_readiness: {
    available: boolean;
    status: string;
    scenario_id?: string;
    scenario_name?: string;
    created_at?: string;
    retirement_age?: number;
    success_probability_percent?: number | null;
    stress_success_probability_percent?: number | null;
    bridge_required?: number | null;
    bridge_projected?: number | null;
    bridge_gap?: number | null;
    first_failure_age_median?: number | null;
    explanation: string;
    action_href: string;
  };
  recommendations: {
    as_of: string;
    opportunities: SpendingRecommendation[];
    reviewed: SpendingRecommendation[];
    method: { definition: string; boundaries: string };
  };
  insights_loading: boolean;
  sections: {
    cash_flow: {
      depository_credits: number;
      depository_debits: number;
      net: number;
      refund_credits: number;
      income_credits: number;
      transfer_credits: number;
      other_credits: number;
      purchase_debits: number;
      card_payment_debits: number;
      investment_transfer_debits: number;
      other_transfer_debits: number;
      other_debits: number;
      operating_surplus: number;
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
type DashboardInsights = Pick<DashboardResult, "recommendations" | "unusual_activity">;
type InvestmentAccountSummary = {
  account_id: string;
  name: string;
  subtype: string | null;
  ownership_scope: "household" | "custodial" | "unlinked";
  tax_treatment: "taxable" | "tax_deferred" | "roth" | "hsa" | "custodial";
  market_value: number;
  position_count: number;
  known_cost_basis: number;
  known_basis_market_value: number;
  basis_coverage_percent: number;
  unrealized_gain_on_known_basis: number;
  unrealized_gain_percent: number | null;
  observed_activity: {
    start: string | null;
    end: string | null;
    contributions: number;
    withdrawals: number;
    dividends_and_interest: number;
    definition: string;
  };
  performance_tracking: {
    status: "available" | "collecting_history";
    valuation_points: number;
    start: string | null;
    end: string | null;
    definition: string;
  };
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
  analytics: {
    account_weight_percent: number;
    household_weight_percent: number;
    unrealized_gain: number | null;
    unrealized_gain_percent: number | null;
    tax_lot_count: number;
  };
  benchmark_comparison:
    | {
        status: "available";
        benchmark: string;
        as_of: string;
        covered_lots: number;
        total_lots: number;
        basis_covered: number;
        actual_covered_value: number;
        benchmark_value: number;
        excess_value: number;
        actual_return_percent: number | null;
        benchmark_return_percent: number | null;
        return_basis: string;
        cadence: string;
        alignment: string;
        definition: string;
        limitations: string[];
      }
    | {
        status: "unavailable";
        benchmark: string;
        reason: string;
        definition: string;
  };
};
type AvailableBenchmarkComparison = Extract<InvestmentHolding["benchmark_comparison"], { status: "available" }>;
type ComparableHolding = InvestmentHolding & { benchmark_comparison: AvailableBenchmarkComparison };

function hasBenchmarkComparison(holding: InvestmentHolding): holding is ComparableHolding {
  return holding.benchmark_comparison.status === "available";
}
type InvestmentTaxLot = {
  linked_account_id: string | null;
  symbol: string;
  description: string;
  quantity: number;
  cost_basis: number | null;
  acquired_date: string;
  effective_date: string;
  cost_basis_status: string;
};
type InvestmentResult = {
  summary: {
    household_market_value: number;
    household_position_count: number;
    known_cost_basis: number;
    known_basis_market_value: number;
    basis_coverage_percent: number;
    unrealized_gain_on_known_basis: number;
    unrealized_gain_percent: number | null;
    custodial_market_value: number;
    custodial_position_count: number;
    custodial_definition: string;
  };
  account_summaries: InvestmentAccountSummary[];
  holdings: InvestmentHolding[];
  tax_lots: InvestmentTaxLot[];
};
type HomeAsset = {
  id: string;
  kind: string;
  name: string;
  identifiers?: Record<string, string>;
  purchase_price: number;
  valuation: { amount: number; source_label: string; valued_at: string };
  effective_valuation?: {
    amount: number;
    source_label: string;
    valued_at: string;
    estimate_type?: string;
    automated: boolean;
    confidence?: { level?: string; basis?: string };
    estimate_range?: { low: number; high: number };
    components?: Array<{ amount: number; source_label: string; valued_at: string }>;
    excluded_outliers?: Array<{ amount: number; source_label: string; valued_at: string }>;
  };
  ownership: { debt_balance: number; net_equity_after_sale: number };
  cost_summary: {
    annual_ownership_costs?: Record<string, number>;
    annual_ownership_total?: number;
    annual_operating_costs?: Record<string, number>;
    annual_operating_total?: number;
    depreciation_to_date?: number | null;
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
  general_inflation_percent: 2.5,
  buy_closing_cost_percent: 3,
  sell_cost_percent: 7,
};
const retirementDefaults: RetirementInputs = {
  current_age: 26,
  retirement_age: 45,
  end_age: 95,
  annual_take_home_sacrifice: 20000,
  annual_retirement_spending: 70000,
  taxable_balance: 25000,
  taxable_basis: 22000,
  traditional_balance: 60000,
  workplace_plan_balance: 60000,
  roth_balance: 20000,
  roth_contribution_basis: 15000,
  hsa_balance: 5000,
  annual_return_percent: 7,
  taxable_tax_drag_percent: 0.75,
  inflation_percent: 2.5,
  current_ordinary_tax_rate_percent: 22,
  retirement_ordinary_tax_rate_percent: 12,
  capital_gains_tax_rate_percent: 15,
  employer_match: 3000,
  employee_contribution_for_full_match: 6000,
  traditional_contribution_limit: 24500,
  roth_contribution_limit: 24500,
  hsa_contribution_limit: 8750,
  qualified_hsa_spending_percent: 15,
  annual_conversion_amount: 30000,
  sepp_annual_distribution: 25000,
  return_stddev_percent: 15,
  uncertainty_simulations: 300,
  uncertainty_seed: 42,
  ruleset: {
    version: "illustrative-us-2026-v1",
    effective_date: "2026-01-01",
    unrestricted_access_age: 59.5,
    early_withdrawal_penalty_percent: 10,
    hsa_nonqualified_penalty_percent: 20,
    conversion_wait_years: 5,
    rule_of_55_min_separation_age: 55,
    sepp_minimum_years: 5,
  },
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
  const [activeStage, setActiveStage] = useState(0);
  const walkthroughRef = useRef<HTMLDivElement>(null);
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
    const timer = window.setTimeout(() => void calculate(), 140);
    return () => window.clearTimeout(timer);
  }, [inputs]);
  useEffect(() => {
    const root = walkthroughRef.current;
    if (!root || !("IntersectionObserver" in window)) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((left, right) => right.intersectionRatio - left.intersectionRatio)[0];
        if (visible) setActiveStage(Number((visible.target as HTMLElement).dataset.stage));
      },
      { rootMargin: "-18% 0px -58%", threshold: [0.15, 0.4, 0.7] },
    );
    root.querySelectorAll<HTMLElement>("[data-stage]").forEach((step) => observer.observe(step));
    return () => observer.disconnect();
  }, []);
  const final = result?.years.at(-1);
  const firstCrossover = result?.crossover_years[0];
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
      <section className="housing-topbar" aria-label="Housing comparison summary" aria-live="polite">
        <article><span>Mortgage</span><strong>{result ? `${money.format(result.monthly_mortgage_payment)}/mo` : "—"}</strong><small>Principal + interest</small></article>
        <article><span>Buyer after sale</span><strong>{final ? money.format(final.buyer_net_wealth) : "—"}</strong><small>Debt and sale costs removed</small></article>
        <article><span>Renter portfolio</span><strong>{final ? money.format(final.renter_investments) : "—"}</strong><small>Cash difference invested</small></article>
        <article className={final && final.buyer_advantage < 0 ? "rent-ahead" : "buy-ahead"}><span>At year {inputs.years}</span><strong>{final ? money.format(Math.abs(final.buyer_advantage)) : "—"}</strong><small>{!final ? "Calculating" : final.buyer_advantage >= 0 ? "Buying ahead" : "Renting ahead"}{firstCrossover ? ` · crossover ${firstCrossover}` : " · no crossover"}</small></article>
      </section>
      {error && <p className="form-error" role="alert">{error}</p>}
      <form className="housing-story-shell" onSubmit={calculate}>
        <div className="housing-walkthrough" ref={walkthroughRef}>
          <section className={activeStage === 0 ? "housing-step active" : "housing-step"} data-stage="0" tabIndex={0} onFocus={() => setActiveStage(0)}>
            <p className="eyebrow">01 · Equal footing</p>
            <h2>Start with the same cash.</h2>
            <p>The buyer moves the down payment into home equity. The renter keeps that cash available to invest. Purchase costs are included on both sides of the comparison.</p>
            <Slider label="Home price" value={inputs.home_price} min={100000} max={2000000} step={10000} format={money.format} change={update("home_price")} />
            <Slider label="Down payment" value={inputs.down_payment} min={0} max={inputs.home_price} step={5000} format={money.format} change={update("down_payment")} />
          </section>
          <section className={activeStage === 1 ? "housing-step active" : "housing-step"} data-stage="1" tabIndex={0} onFocus={() => setActiveStage(1)}>
            <p className="eyebrow">02 · Equity returned</p>
            <h2>Part of the mortgage comes back.</h2>
            <p>Principal builds equity; interest does not. The mix changes every month, so the model amortizes the loan rather than treating the payment as one cost.</p>
            <Slider label="Mortgage rate" value={inputs.mortgage_rate_percent} min={0} max={15} step={0.125} format={(v) => `${v}%`} change={update("mortgage_rate_percent")} />
            <Slider label="Mortgage term" value={inputs.mortgage_term_years} min={10} max={40} step={5} format={(v) => `${v} years`} change={update("mortgage_term_years")} />
          </section>
          <section className={activeStage === 2 ? "housing-step active" : "housing-step"} data-stage="2" tabIndex={0} onFocus={() => setActiveStage(2)}>
            <p className="eyebrow">03 · Market value</p>
            <h2>Appreciation can create wealth.</h2>
            <p>Home growth is not guaranteed. It increases both the value you may recover and value-linked costs such as property tax and maintenance.</p>
            <Slider label="Home appreciation" value={inputs.home_appreciation_percent} min={-5} max={10} step={0.25} format={(v) => `${v}%/yr`} change={update("home_appreciation_percent")} />
          </section>
          <section className={activeStage === 3 ? "housing-step active" : "housing-step"} data-stage="3" tabIndex={0} onFocus={() => setActiveStage(3)}>
            <p className="eyebrow">04 · Ownership costs</p>
            <h2>These dollars do not become equity.</h2>
            <p>Interest, tax, insurance, maintenance, HOA, utilities, and mortgage insurance buy housing services or reduce risk—but they are not returned when the house is sold.</p>
            <Slider label="Property tax" value={inputs.property_tax_percent} min={0} max={3} step={0.05} format={(v) => `${v}%/yr`} change={update("property_tax_percent")} />
            <Slider label="Annual insurance" value={inputs.home_insurance_annual} min={0} max={15000} step={100} format={money.format} change={update("home_insurance_annual")} />
            <Slider label="Maintenance allowance" value={inputs.maintenance_percent} min={0} max={5} step={0.1} format={(v) => `${v}%/yr`} change={update("maintenance_percent")} />
            <Slider label="General cost inflation" value={inputs.general_inflation_percent} min={-2} max={12} step={0.25} format={(v) => `${v}%/yr`} change={update("general_inflation_percent")} />
            <details className="housing-advanced">
              <summary>Advanced cost overrides</summary>
              <Slider label="Insurance growth" value={inputs.home_insurance_growth_percent ?? inputs.general_inflation_percent} min={-2} max={15} step={0.25} format={(v) => `${v}%/yr`} change={update("home_insurance_growth_percent")} />
              <Slider label="HOA growth" value={inputs.hoa_growth_percent ?? inputs.general_inflation_percent} min={-2} max={15} step={0.25} format={(v) => `${v}%/yr`} change={update("hoa_growth_percent")} />
              <Slider label="Owner utility growth" value={inputs.owner_utilities_growth_percent ?? inputs.general_inflation_percent} min={-2} max={15} step={0.25} format={(v) => `${v}%/yr`} change={update("owner_utilities_growth_percent")} />
            </details>
          </section>
          <section className={activeStage === 4 ? "housing-step active" : "housing-step"} data-stage="4" tabIndex={0} onFocus={() => setActiveStage(4)}>
            <p className="eyebrow">05 · Rent changes too</p>
            <h2>Rent is not frozen in time.</h2>
            <p>The renter pays for housing each month, and both rent and utilities can rise. The ending rent is visible in the graph explanation.</p>
            <Slider label="Comparable rent" value={inputs.monthly_rent} min={500} max={10000} step={100} format={(v) => `${money.format(v)}/mo`} change={update("monthly_rent")} />
            <Slider label="Rent growth" value={inputs.rent_growth_percent} min={-5} max={15} step={0.25} format={(v) => `${v}%/yr`} change={update("rent_growth_percent")} />
            <Slider label="Renter utility growth" value={inputs.renter_utilities_growth_percent ?? inputs.general_inflation_percent} min={-2} max={15} step={0.25} format={(v) => `${v}%/yr`} change={update("renter_utilities_growth_percent")} />
          </section>
          <section className={activeStage === 5 ? "housing-step active" : "housing-step"} data-stage="5" tabIndex={0} onFocus={() => setActiveStage(5)}>
            <p className="eyebrow">06 · Invest the difference</p>
            <h2>Cheaper housing leaves investable cash.</h2>
            <p>The renter starts with the avoided cash-to-close, then invests or withdraws the monthly difference. Returns are reduced by an explicit tax drag.</p>
            <Slider label="Investment return" value={inputs.investment_return_percent} min={-5} max={15} step={0.25} format={(v) => `${v}%/yr`} change={update("investment_return_percent")} />
            <Slider label="Investment tax drag" value={inputs.investment_tax_drag_percent} min={0} max={5} step={0.05} format={(v) => `${v}%/yr`} change={update("investment_tax_drag_percent")} />
          </section>
          <section className={activeStage === 6 ? "housing-step active" : "housing-step"} data-stage="6" tabIndex={0} onFocus={() => setActiveStage(6)}>
            <p className="eyebrow">07 · Walk away today</p>
            <h2>Compare money available after leaving.</h2>
            <p>The buyer sells, pays sale costs and the remaining mortgage, and keeps the rest. The renter keeps the investment portfolio. That is the apples-to-apples finish.</p>
            <Slider label="Time in home" value={inputs.years} min={1} max={30} step={1} format={(v) => `${v} years`} change={update("years")} />
            <Slider label="Purchase costs" value={inputs.buy_closing_cost_percent} min={0} max={8} step={0.25} format={(v) => `${v}%`} change={update("buy_closing_cost_percent")} />
            <Slider label="Sale costs" value={inputs.sell_cost_percent} min={0} max={12} step={0.25} format={(v) => `${v}%`} change={update("sell_cost_percent")} />
            <button type="submit" disabled={running}>{running ? "Updating…" : "Refresh comparison"}</button>
            {completedAt && !running && <p className="completion" role="status">Updated at {completedAt}</p>}
          </section>
        </div>
        <section className="housing-visual-sticky" aria-live="polite">
          {result && final ? <HousingVisuals years={result.years} initialCash={result.initial_cash_allocation} sensitivity={result.sensitivity} stage={activeStage} /> : <p className="dashboard-loading">Building the comparison…</p>}
        </section>
      </form>
      {result && <details className="housing-accessible-results"><summary>Year-by-year accessible results and definitions</summary><p>Buyer wealth is sale proceeds after remaining debt and estimated sale costs. Renter wealth begins with avoided cash-to-close, then adds or withdraws the monthly cash-flow difference. Costs and growth assumptions compound monthly.</p><div className="table-wrap"><table><thead><tr><th>Year</th><th>Buy after sale</th><th>Rent + invest</th><th>Difference</th></tr></thead><tbody>{result.years.map((year) => <tr key={year.year}><td>{year.year}</td><td>{money.format(year.buyer_net_wealth)}</td><td>{money.format(year.renter_investments)}</td><td>{money.format(year.buyer_advantage)}</td></tr>)}</tbody></table></div></details>}
    </main>
  );
}

function Retirement() {
  const [inputs, setInputs] = useState(retirementDefaults);
  const [result, setResult] = useState<RetirementResult | null>(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [completedAt, setCompletedAt] = useState("");
  const [activeStage, setActiveStage] = useState(0);
  const walkthroughRef = useRef<HTMLDivElement>(null);
  const update = (key: keyof RetirementInputs) => (value: number) =>
    setInputs((current) => ({ ...current, [key]: value }));
  const updateRule = (key: "unrestricted_access_age" | "early_withdrawal_penalty_percent" | "hsa_nonqualified_penalty_percent" | "conversion_wait_years" | "rule_of_55_min_separation_age" | "sepp_minimum_years") => (value: number) =>
    setInputs((current) => ({
      ...current,
      ruleset: { ...current.ruleset, [key]: value },
    }));
  async function calculate(event?: FormEvent) {
    event?.preventDefault();
    setError("");
    setRunning(true);
    try {
      const response = await fetch("/api/public/retirement/compare", {
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
    const timer = window.setTimeout(() => void calculate(), 140);
    return () => window.clearTimeout(timer);
  }, [inputs]);
  useEffect(() => {
    const root = walkthroughRef.current;
    if (!root || !("IntersectionObserver" in window)) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((left, right) => right.intersectionRatio - left.intersectionRatio)[0];
        if (visible) setActiveStage(Number((visible.target as HTMLElement).dataset.stage));
      },
      { rootMargin: "-18% 0px -58%", threshold: [0.15, 0.4, 0.7] },
    );
    root.querySelectorAll<HTMLElement>("[data-stage]").forEach((step) => observer.observe(step));
    return () => observer.disconnect();
  }, []);
  const best = result?.ranking[0];
  const bridgeFunded = result ? result.bridge.existing_gap <= 0.01 : false;
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
          Give every account the same hit to take-home pay, then see what is
          actually spendable—especially if work ends long before 59½.
        </p>
      </header>
      <section className="housing-topbar retirement-topbar" aria-label="Retirement comparison summary" aria-live="polite">
        <article><span>Same take-home cost</span><strong>{money.format(inputs.annual_take_home_sacrifice)}/yr</strong><small>Held equal for every path</small></article>
        <article><span>Best modeled path</span><strong>{best?.label || "—"}</strong><small>{best ? `${money.format(best.after_tax_value)} spendable at ${inputs.retirement_age}` : "Calculating"}</small></article>
        <article className={bridgeFunded ? "buy-ahead" : "rent-ahead"}><span>Taxable-path bridge</span><strong>{result ? money.format(result.bridge.existing_gap) : "—"}</strong><small>{bridgeFunded ? "No modeled gap" : `Gap before age ${Math.ceil(inputs.ruleset.unrestricted_access_age)}`}</small></article>
        <article><span>First-year spending</span><strong>{result ? money.format(result.bridge.annual_spending_at_retirement) : "—"}</strong><small>Inflation-adjusted at age {inputs.retirement_age}</small></article>
      </section>
      {error && <p className="form-error" role="alert">{error}</p>}
      <form className="housing-story-shell retirement-story-shell" onSubmit={calculate}>
        <div className="housing-walkthrough" ref={walkthroughRef}>
          <section className={activeStage === 0 ? "housing-step active" : "housing-step"} data-stage="0" tabIndex={0} onFocus={() => setActiveStage(0)}>
            <p className="eyebrow">01 · Equal sacrifice</p>
            <h2>Start with the same missing paycheck dollars.</h2>
            <p>A $20,000 Roth contribution and a $20,000 pre-tax contribution do not cost the household the same amount. This comparison holds the take-home sacrifice constant and grosses up deductible contributions.</p>
            <Slider label="Current age" value={inputs.current_age} min={18} max={Math.max(18, inputs.retirement_age - 1)} step={1} format={(v) => `${v}`} change={update("current_age")} />
            <Slider label="Retire at" value={inputs.retirement_age} min={inputs.current_age + 1} max={Math.min(75, inputs.end_age - 1)} step={1} format={(v) => `${v}`} change={update("retirement_age")} />
            <Slider label="Annual take-home sacrifice" value={inputs.annual_take_home_sacrifice} min={0} max={100000} step={1000} format={(v) => `${money.format(v)}/yr`} change={update("annual_take_home_sacrifice")} />
            <details className="housing-advanced"><summary>Starting account balances</summary><div className="field-grid compact-fields">
              <NumberField label="Taxable balance" value={inputs.taxable_balance} suffix="USD" change={update("taxable_balance")} />
              <NumberField label="Taxable basis" value={inputs.taxable_basis} suffix="USD" change={update("taxable_basis")} />
              <NumberField label="Traditional" value={inputs.traditional_balance} suffix="USD" change={update("traditional_balance")} />
              <NumberField label="Workplace plan" value={inputs.workplace_plan_balance} suffix="USD" change={update("workplace_plan_balance")} />
              <NumberField label="Roth balance" value={inputs.roth_balance} suffix="USD" change={update("roth_balance")} />
              <NumberField label="Roth basis" value={inputs.roth_contribution_basis} suffix="USD" change={update("roth_contribution_basis")} />
              <NumberField label="HSA balance" value={inputs.hsa_balance} suffix="USD" change={update("hsa_balance")} />
            </div></details>
          </section>
          <section className={activeStage === 1 ? "housing-step active" : "housing-step"} data-stage="1" tabIndex={0} onFocus={() => setActiveStage(1)}>
            <p className="eyebrow">02 · Flexible brokerage</p>
            <h2>Accessibility has a price—and real value.</h2>
            <p>Brokerage money has no retirement-age gate. The model reduces annual returns for tax drag and taxes only the gain when estimating spendable value.</p>
            <Slider label="Annual market return" value={inputs.annual_return_percent} min={-5} max={15} step={0.25} format={(v) => `${v}%`} change={update("annual_return_percent")} />
            <Slider label="Taxable tax drag" value={inputs.taxable_tax_drag_percent} min={0} max={5} step={0.05} format={(v) => `${v}%/yr`} change={update("taxable_tax_drag_percent")} />
            <Slider label="Capital-gains rate" value={inputs.capital_gains_tax_rate_percent} min={0} max={40} step={1} format={(v) => `${v}%`} change={update("capital_gains_tax_rate_percent")} />
          </section>
          <section className={activeStage === 2 ? "housing-step active" : "housing-step"} data-stage="2" tabIndex={0} onFocus={() => setActiveStage(2)}>
            <p className="eyebrow">03 · Roth vs. traditional</p>
            <h2>Pay tax now, or buy more assets first.</h2>
            <p>Roth uses after-tax dollars. Traditional uses the current deduction to contribute more for the same take-home cost, then pays the modeled retirement tax rate when withdrawn.</p>
            <Slider label="Current ordinary tax rate" value={inputs.current_ordinary_tax_rate_percent} min={0} max={50} step={1} format={(v) => `${v}%`} change={update("current_ordinary_tax_rate_percent")} />
            <Slider label="Retirement ordinary tax rate" value={inputs.retirement_ordinary_tax_rate_percent} min={0} max={50} step={1} format={(v) => `${v}%`} change={update("retirement_ordinary_tax_rate_percent")} />
            <Slider label="Annual employer match" value={inputs.employer_match} min={0} max={25000} step={500} format={(v) => `${money.format(v)}/yr`} change={update("employer_match")} />
            <Slider label="Employee contribution for full match" value={inputs.employee_contribution_for_full_match} min={0} max={25000} step={500} format={(v) => `${money.format(v)}/yr`} change={update("employee_contribution_for_full_match")} />
            <details className="housing-advanced"><summary>Contribution limits</summary>
              <Slider label="Traditional workplace limit" value={inputs.traditional_contribution_limit} min={1000} max={100000} step={500} format={money.format} change={update("traditional_contribution_limit")} />
              <Slider label="Roth path limit" value={inputs.roth_contribution_limit} min={1000} max={100000} step={500} format={money.format} change={update("roth_contribution_limit")} />
            </details>
          </section>
          <section className={activeStage === 3 ? "housing-step active" : "housing-step"} data-stage="3" tabIndex={0} onFocus={() => setActiveStage(3)}>
            <p className="eyebrow">04 · HSA edge case</p>
            <h2>Triple tax benefits depend on medical use.</h2>
            <p>The HSA line does not pretend every retirement dollar is tax free. Choose the portion expected to reimburse qualified medical expenses; the rest is valued with modeled tax and age rules.</p>
            <Slider label="Annual HSA limit" value={inputs.hsa_contribution_limit} min={1000} max={25000} step={250} format={money.format} change={update("hsa_contribution_limit")} />
            <Slider label="Qualified medical use" value={inputs.qualified_hsa_spending_percent} min={0} max={100} step={5} format={(v) => `${v}%`} change={update("qualified_hsa_spending_percent")} />
            <Slider label="Nonqualified HSA penalty before 65" value={inputs.ruleset.hsa_nonqualified_penalty_percent} min={0} max={40} step={1} format={(v) => `${v}%`} change={updateRule("hsa_nonqualified_penalty_percent")} />
          </section>
          <section className={activeStage === 4 ? "housing-step active" : "housing-step"} data-stage="4" tabIndex={0} onFocus={() => setActiveStage(4)}>
            <p className="eyebrow">05 · Stop work early</p>
            <h2>Retiring at 45 is a bridge problem.</h2>
            <p>The model tests taxable assets, Roth contribution basis, conversion ladders, Rule of 55, 72(t), and simply paying the early-withdrawal penalty. Eligibility and failures stay visible.</p>
            <Slider label="Annual spending in today’s dollars" value={inputs.annual_retirement_spending} min={10000} max={250000} step={2500} format={(v) => `${money.format(v)}/yr`} change={update("annual_retirement_spending")} />
            <Slider label="Annual Roth conversion" value={inputs.annual_conversion_amount} min={0} max={150000} step={2500} format={(v) => `${money.format(v)}/yr`} change={update("annual_conversion_amount")} />
            <Slider label="Fixed 72(t) distribution" value={inputs.sepp_annual_distribution} min={0} max={150000} step={2500} format={(v) => `${money.format(v)}/yr`} change={update("sepp_annual_distribution")} />
          </section>
          <section className={activeStage === 5 ? "housing-step active" : "housing-step"} data-stage="5" tabIndex={0} onFocus={() => setActiveStage(5)}>
            <p className="eyebrow">06 · Future rules are uncertain</p>
            <h2>Make assumptions visible, not permanent.</h2>
            <p>Inflation and tax rules can change over decades. This run pins a dated illustrative ruleset so a future update changes an assumption—not history.</p>
            <Slider label="Plan through age" value={inputs.end_age} min={inputs.retirement_age + 1} max={110} step={1} format={(v) => `${v}`} change={update("end_age")} />
            <Slider label="Inflation" value={inputs.inflation_percent} min={0} max={10} step={0.25} format={(v) => `${v}%/yr`} change={update("inflation_percent")} />
            <Slider label="Return volatility" value={inputs.return_stddev_percent} min={0} max={35} step={1} format={(v) => `${v}% standard deviation`} change={update("return_stddev_percent")} />
            <Slider label="Unrestricted access age" value={inputs.ruleset.unrestricted_access_age} min={50} max={75} step={0.5} format={(v) => `${v}`} change={updateRule("unrestricted_access_age")} />
            <Slider label="Early-withdrawal penalty" value={inputs.ruleset.early_withdrawal_penalty_percent} min={0} max={30} step={1} format={(v) => `${v}%`} change={updateRule("early_withdrawal_penalty_percent")} />
            <button type="submit" disabled={running}>{running ? "Updating…" : "Refresh comparison"}</button>
            {completedAt && !running && <p className="completion" role="status">Updated at {completedAt}</p>}
          </section>
        </div>
        <section className="housing-visual-sticky" aria-live="polite">
          {result ? <RetirementComparisonVisuals result={result} stage={activeStage} /> : <p className="dashboard-loading">Building the comparison…</p>}
        </section>
      </form>
      {result && <details className="housing-accessible-results"><summary>Full strategy results and model boundaries</summary><p>{result.comparison_basis.ranking_metric} {result.disclaimer}</p><div className="table-wrap"><table><thead><tr><th>Path</th><th>Primary contribution</th><th>Taxable overflow</th><th>Match</th><th>Headline at {inputs.retirement_age}</th><th>Spendable estimate</th></tr></thead><tbody>{result.strategies.map((strategy) => <tr key={strategy.key}><td>{strategy.label}<small>{strategy.caveat}</small></td><td>{money.format(strategy.annual_primary_contribution)}</td><td>{money.format(strategy.annual_taxable_overflow)}</td><td>{money.format(strategy.employer_match)}</td><td>{money.format(strategy.at_retirement.headline_balance)}</td><td>{money.format(strategy.at_retirement.after_tax_value)}</td></tr>)}</tbody></table></div></details>}
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
  const structuralDebt = values.get("debt") || 0;
  const currentCardBalance = values.get("current_card_balance") || 0;
  const gross = parts.reduce((total, item) => total + item.value, 0);
  const net = values.get("net_worth") || gross - structuralDebt;
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
        <div><p className="eyebrow">Long-term net worth, visually</p><h2 id="wealth-heading">What you own, less asset-backed debt</h2></div>
        <strong>{money.format(net)}</strong>
      </div>
      <div className="wealth-layout">
        <div className="wealth-ring" style={{ background: `conic-gradient(${gradient})` }}>
          <div><span>Long-term net worth</span><strong>{money.format(net)}</strong><small>{money.format(structuralDebt)} asset-backed debt</small></div>
        </div>
        <div className="wealth-breakdown">
          {parts.map((part) => (
            <div key={part.label}>
              <i style={{ background: part.color }} />
              <span>{part.label}<small>{((part.value / max) * 100).toFixed(1)}% of gross assets</small></span>
              <strong>{money.format(part.value)}</strong>
            </div>
          ))}
          <div className="liability-row"><i /><span>Asset-backed debt<small>Registered mortgage and vehicle debt only</small></span><strong>−{money.format(structuralDebt)}</strong></div>
          <div className="card-balance-row"><i /><span>Current card amount due<small>Temporary bill for cash planning; excluded from long-term net worth</small></span><strong>{money.format(currentCardBalance)}</strong></div>
        </div>
      </div>
      <p className="visual-note">Children’s custodial investments are intentionally outside this household total.</p>
    </section>
  );
}

function NetWorthProjection({ dashboard, readiness, assets }: { dashboard: DashboardResult; readiness: DashboardResult["retirement_readiness"]; assets: AssetsResult | null }) {
  const metrics = new Map(dashboard.metrics.map((metric) => [metric.key, metric.value]));
  const currentNetWorth = metrics.get("net_worth") || 0;
  const investedToday = metrics.get("investment_value") || 0;
  const householdAssetValue = metrics.get("household_asset_value") || 0;
  const currentHomeValue = (assets?.assets || [])
    .filter((asset) => asset.kind === "home")
    .reduce((total, asset) => total + (asset.effective_valuation?.amount || asset.valuation.amount), 0);
  const currentVehicleValue = (assets?.assets || [])
    .filter((asset) => asset.kind === "vehicle")
    .reduce((total, asset) => total + (asset.effective_valuation?.amount || asset.valuation.amount), 0);
  const otherFixedValue = currentNetWorth - investedToday - currentHomeValue - currentVehicleValue;
  const observedSurplus = dashboard.sections.cash_flow.available
    ? dashboard.sections.cash_flow.operating_surplus
    : 0;
  const [years, setYears] = useState(10);
  const [returnPercent, setReturnPercent] = useState(6.5);
  const [annualSavings, setAnnualSavings] = useState(Math.round(observedSurplus / 100) * 100);
  const [homeGrowthPercent, setHomeGrowthPercent] = useState(3);
  const [vehicleDepreciationPercent, setVehicleDepreciationPercent] = useState(7);
  const annualReturn = returnPercent / 100;
  const annualHomeGrowth = homeGrowthPercent / 100;
  const annualVehicleDepreciation = vehicleDepreciationPercent / 100;
  const investmentValueAt = (year: number) => investedToday * Math.pow(1 + annualReturn, year)
    + annualSavings * (annualReturn === 0
      ? year
      : (Math.pow(1 + annualReturn, year) - 1) / annualReturn);
  const homeValueAt = (year: number) => currentHomeValue * Math.pow(1 + annualHomeGrowth, year);
  const vehicleValueAt = (year: number) => currentVehicleValue * Math.pow(Math.max(0, 1 - annualVehicleDepreciation), year);
  const futureInvestments = investedToday * Math.pow(1 + annualReturn, years)
    + annualSavings * (annualReturn === 0
      ? years
      : (Math.pow(1 + annualReturn, years) - 1) / annualReturn);
  const futureHome = homeValueAt(years);
  const futureVehicles = vehicleValueAt(years);
  const futureNetWorth = otherFixedValue + futureInvestments + futureHome + futureVehicles;
  const points = Array.from({ length: years + 1 }, (_, year) => {
    return {
      year,
      value: otherFixedValue + investmentValueAt(year) + homeValueAt(year) + vehicleValueAt(year),
    };
  });
  const max = Math.max(...points.map((point) => point.value), currentNetWorth, 1);
  const min = Math.min(...points.map((point) => point.value), currentNetWorth, 0);
  const range = max - min || 1;
  const chartWidth = 780;
  const chartHeight = 210;
  const padding = { top: 18, right: 20, bottom: 30, left: 74 };
  const chartPoint = (point: { year: number; value: number }) => {
    const x = padding.left + (point.year / Math.max(years, 1)) * (chartWidth - padding.left - padding.right);
    const y = padding.top + (chartHeight - padding.top - padding.bottom)
      - ((point.value - min) / range) * (chartHeight - padding.top - padding.bottom);
    return `${x},${y}`;
  };
  const resetToObserved = () => setAnnualSavings(Math.round(observedSurplus / 100) * 100);
  return (
    <section className="dashboard-panel net-worth-projection" aria-labelledby="net-worth-projection-heading">
      <div className="section-title">
        <div><p className="eyebrow">Looking forward</p><h2 id="net-worth-projection-heading">What could the whole balance sheet become?</h2></div>
        <strong>{money.format(futureNetWorth)}</strong>
      </div>
      <p className="quiet">A nominal, pre-tax scenario—not a prediction. It compounds tracked investments, adds the savings you choose at each year-end, applies your home and vehicle assumptions below, and holds cash, other assets, and liabilities fixed.</p>
      {!readiness.available && <p className="projection-retirement-link">Want to model retirement taxes, early access, and the years after you stop working? <a href={readiness.action_href}>Build a retirement comparison</a>.</p>}
      <div className="projection-controls">
        <label>Years ahead <strong>{years}</strong><input aria-label="Years ahead" type="range" min="1" max="40" step="1" value={years} onChange={(event) => setYears(Number(event.target.value))} /></label>
        <label>Annual investment return <strong>{returnPercent.toFixed(1)}%</strong><input aria-label="Annual investment return" type="range" min="0" max="10" step="0.25" value={returnPercent} onChange={(event) => setReturnPercent(Number(event.target.value))} /></label>
        <label>Annual amount invested <strong>{money.format(annualSavings)}</strong><input aria-label="Annual amount invested" type="range" min="-50000" max="200000" step="500" value={annualSavings} onChange={(event) => setAnnualSavings(Number(event.target.value))} /></label>
        {currentHomeValue > 0 && <label>Annual home value change <strong>{homeGrowthPercent.toFixed(1)}%</strong><input aria-label="Annual home value change" type="range" min="-5" max="8" step="0.25" value={homeGrowthPercent} onChange={(event) => setHomeGrowthPercent(Number(event.target.value))} /></label>}
        {currentVehicleValue > 0 && <label>Annual vehicle depreciation <strong>{vehicleDepreciationPercent.toFixed(1)}%</strong><input aria-label="Annual vehicle depreciation" type="range" min="0" max="20" step="0.5" value={vehicleDepreciationPercent} onChange={(event) => setVehicleDepreciationPercent(Number(event.target.value))} /></label>}
      </div>
      <div className="projection-composition" aria-label="Projection component breakdown">
        <article><span>Investments</span><strong>{money.format(investedToday)} → {money.format(futureInvestments)}</strong><small>{money.format(futureInvestments - investedToday)} from modeled return and contributions</small></article>
        {currentHomeValue > 0 && <article><span>Home scenario</span><strong>{money.format(currentHomeValue)} → {money.format(futureHome)}</strong><small>{homeGrowthPercent.toFixed(1)}% nominal annual value change</small></article>}
        {currentVehicleValue > 0 && <article><span>Vehicles scenario</span><strong>{money.format(currentVehicleValue)} → {money.format(futureVehicles)}</strong><small>{vehicleDepreciationPercent.toFixed(1)}% annual depreciation</small></article>}
        <article><span>Cash, other assets, and liabilities</span><strong>{money.format(otherFixedValue)} → {money.format(otherFixedValue)}</strong><small>Held flat; includes any registered debt as a negative amount</small></article>
      </div>
      {!assets && householdAssetValue > 0 && <p className="visual-note">Loading the dated home and vehicle valuations used to split this asset total. Until they arrive, the registered asset portion remains in the fixed component.</p>}
      <figure className="projection-chart">
        <svg viewBox={`0 0 ${chartWidth} ${chartHeight}`} role="img" aria-label={`Projected household net worth in ${years} years: ${money.format(futureNetWorth)}`}>
          {[0, .5, 1].map((fraction) => {
            const value = max - range * fraction;
            const y = padding.top + (chartHeight - padding.top - padding.bottom) * fraction;
            return <g key={fraction}><line className="chart-grid" x1={padding.left} x2={chartWidth - padding.right} y1={y} y2={y} /><text className="chart-label" x={padding.left - 10} y={y + 4} textAnchor="end">{money.format(value)}</text></g>;
          })}
          <polyline points={points.map(chartPoint).join(" ")} className="net-worth-projection-line" />
          <circle cx={chartPoint(points.at(-1)!).split(",")[0]} cy={chartPoint(points.at(-1)!).split(",")[1]} r="4" className="net-worth-projection-dot" />
          <text className="chart-label" x={padding.left} y={chartHeight - 10}>Today</text>
          <text className="chart-label" x={chartWidth - padding.right} y={chartHeight - 10} textAnchor="end">{years} years</text>
        </svg>
        <figcaption>Investments: {money.format(investedToday)} today → {money.format(futureInvestments)}. Home: {money.format(currentHomeValue)} → {money.format(futureHome)}. {annualSavings === observedSurplus ? "The contribution rate matches your observed trailing-12-month operating surplus." : "The contribution rate is a scenario you set."}</figcaption>
      </figure>
      <div className="projection-summary"><span>{money.format(currentNetWorth)} today</span><i /><span>{money.format(futureNetWorth)} in {years} years</span><button type="button" onClick={resetToObserved}>Use observed saving rate</button></div>
    </section>
  );
}

function FinancialChangeStory({ dashboard }: { dashboard: DashboardResult }) {
  const story = dashboard.net_worth_change;
  const operating = story.drivers.find((driver) => driver.key === "operating_surplus")?.value || 0;
  const residual = story.drivers.find((driver) => driver.key === "valuation_and_balance_change")?.value || 0;
  if (!story.available || story.opening_net_worth == null || story.change == null) {
    return null;
  }
  const opening = story.opening_net_worth;
  const afterOperating = opening + operating;
  const ending = story.ending_net_worth;
  const values = [0, opening, afterOperating, ending];
  const low = Math.min(...values);
  const high = Math.max(...values);
  const range = high - low || 1;
  const chartTop = 22;
  const chartHeight = 188;
  const y = (value: number) => chartTop + chartHeight - ((value - low) / range) * chartHeight;
  const columns = [
    { label: "One year ago", start: 0, end: opening, value: opening, kind: "total" },
    { label: "Income − spending", start: opening, end: afterOperating, value: operating, kind: operating >= 0 ? "positive" : "negative" },
    { label: "Markets + values", start: afterOperating, end: ending, value: residual, kind: residual >= 0 ? "positive" : "negative" },
    { label: "Now", start: 0, end: ending, value: ending, kind: "total" },
  ];
  return (
    <section className="dashboard-panel financial-change-story" aria-labelledby="change-heading">
      <div className="change-story-head"><div><p className="eyebrow">The first question</p><h2 id="change-heading">Are you financially stronger than one year ago?</h2></div><div className={story.change >= 0 ? "change-positive" : "change-negative"}><span>{story.direction}</span><strong>{story.change >= 0 ? "+" : ""}{money.format(story.change)}</strong></div></div>
      <figure className="change-waterfall"><svg viewBox="0 0 820 270" role="img" aria-label={`Net worth changed from ${money.format(opening)} to ${money.format(ending)}`}>
        <line className="waterfall-axis" x1="45" x2="785" y1={y(0)} y2={y(0)} />
        {columns.map((column, index) => {
          const x = 75 + index * 190;
          const top = Math.min(y(column.start), y(column.end));
          const height = Math.max(Math.abs(y(column.start) - y(column.end)), 3);
          return <g key={column.label}>
            {index < columns.length - 1 && <line className="waterfall-connector" x1={x + 115} x2={x + 190} y1={y(column.end)} y2={y(column.end)} />}
            <rect className={`waterfall-bar ${column.kind}`} x={x} y={top} width="115" height={height} rx="2" />
            <text className="waterfall-value" x={x + 57.5} y={Math.max(top - 9, 13)} textAnchor="middle">{column.value >= 0 && index > 0 && index < 3 ? "+" : ""}{money.format(column.value)}</text>
            <text className="waterfall-label" x={x + 57.5} y="250" textAnchor="middle">{column.label}</text>
          </g>;
        })}
      </svg><figcaption>Opening net worth plus operating surplus plus market, asset-value, liability, and timing changes equals today’s net worth.</figcaption></figure>
      <div className="change-driver-grid">{story.drivers.map((driver) => <article key={driver.key}><span>{driver.label}</span><strong>{driver.value == null ? "—" : `${driver.value >= 0 ? "+" : ""}${money.format(driver.value)}`}</strong><p>{driver.definition}</p></article>)}</div>
      <details className="story-method"><summary>Reconciliation and limitations</summary><p>The waterfall reconciles exactly to {money.format(story.ending_net_worth)}; rounding difference {money.format(story.reconciliation_difference || 0)}.</p>{story.limitations.map((item) => <p key={item}>{item}</p>)}</details>
    </section>
  );
}

function RetirementReadiness({ readiness }: { readiness: DashboardResult["retirement_readiness"] }) {
  if (!readiness.available) {
    return <section className="dashboard-panel readiness-panel">
      <div className="section-title"><div><p className="eyebrow">Retirement readiness</p><h2>Is the plan on track?</h2></div></div>
      <strong className="readiness-pending">Needs a saved plan</strong>
      <p>{readiness.explanation}</p>
      <a className="dashboard-action" href={readiness.action_href}>Build retirement comparison</a>
    </section>;
  }
  const probability = readiness.success_probability_percent ?? 0;
  return <section className="dashboard-panel readiness-panel">
    <div className="section-title"><div><p className="eyebrow">Retirement readiness</p><h2>Can this plan fund the years?</h2></div><strong>{probability}%</strong></div>
    <div className="readiness-track" aria-label={`${probability}% of modeled paths fund every year`}><i style={{ width: `${probability}%` }} /></div>
    <div className="readiness-details">
      <p><span>Saved scenario</span><strong>{readiness.scenario_name}</strong></p>
      {readiness.retirement_age != null && <p><span>Retire at</span><strong>Age {readiness.retirement_age}</strong></p>}
      {readiness.bridge_gap != null && <p><span>Taxable bridge gap</span><strong>{readiness.bridge_gap > 0 ? money.format(readiness.bridge_gap) : "Covered"}</strong></p>}
      {readiness.stress_success_probability_percent != null && <p><span>Lower-return stress case</span><strong>{readiness.stress_success_probability_percent}%</strong></p>}
    </div>
    <p className="visual-note">{readiness.explanation}</p>
    <a className="dashboard-action" href={readiness.action_href}>Review retirement plan</a>
  </section>;
}

function RecommendationQueue({ data, saved }: { data: DashboardResult["recommendations"]; saved: () => void }) {
  const [working, setWorking] = useState<string | null>(null);
  const [error, setError] = useState("");
  const respond = async (item: SpendingRecommendation, action: "useful" | "essential" | "not_useful" | "later" | "incorrectly_categorized") => {
    setWorking(item.id);
    setError("");
    const body: { action: string; snoozed_until?: string } = { action };
    if (action === "later") {
      const date = new Date();
      date.setDate(date.getDate() + 90);
      body.snoozed_until = date.toISOString().slice(0, 10);
    }
    try {
      const response = await fetch(`/api/private/recommendations/${encodeURIComponent(item.id)}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error("feedback unavailable");
      saved();
    } catch {
      setError("Your feedback could not be saved. Try again before leaving this page.");
    } finally {
      setWorking(null);
    }
  };
  return <section className="dashboard-panel recommendation-panel">
    <div className="section-title"><div><p className="eyebrow">Potential savings</p><h2>Where a review may save money</h2></div><span>{data.opportunities.length} open</span></div>
    <p className="quiet">{data.method.definition}</p>
    {data.opportunities.length === 0 ? <div className="empty-signal"><strong>Nothing needs a recommendation right now</strong><p>That means no high-confidence fee, recurring-price, or sustained-spending signal is currently open.</p></div> : <div className="recommendation-list">{data.opportunities.slice(0, 5).map((item) => <article key={item.id}>
      <div><span>{item.kind.replaceAll("_", " ")}</span><strong>{item.title}</strong></div>
      <p>{item.explanation}</p>
      <dl><div><dt>Potential annual impact</dt><dd>{money.format(item.estimated_impact.annual)}</dd></div><div><dt>Confidence</dt><dd>{item.confidence.level}</dd></div></dl>
      <details><summary>Why this is here</summary><p>{item.suggested_action}</p><p>{item.confidence.rationale}</p><p>{item.estimated_impact.investment_assumption} If the full annual amount were invested, the illustrative 10-year value is {money.format(item.estimated_impact.ten_year_investment_value)}.</p></details>
      <div className="recommendation-actions"><button type="button" onClick={() => void respond(item, "useful")} disabled={working === item.id}>Useful</button><button type="button" onClick={() => void respond(item, "essential")} disabled={working === item.id}>Essential</button><button type="button" onClick={() => void respond(item, "incorrectly_categorized")} disabled={working === item.id}>Wrong category</button><button type="button" onClick={() => void respond(item, "later")} disabled={working === item.id}>Later</button></div>
    </article>)}</div>}
    {data.reviewed.length > 0 && <details className="reviewed-recommendations"><summary>{data.reviewed.length} remembered decision{data.reviewed.length === 1 ? "" : "s"}</summary><div>{data.reviewed.map((item) => <p key={item.id}><strong>{item.title}</strong><span>{item.owner_feedback?.action.replaceAll("_", " ")}</span></p>)}</div></details>}
    {error && <p className="inline-error" role="alert">{error}</p>}
    <p className="visual-note">{data.method.boundaries}</p>
  </section>;
}

function InvestmentOverview({ data: initialData }: { data: InvestmentResult }) {
  const [data, setData] = useState(initialData);
  const [fidelityImport, setFidelityImport] = useState<{ content: string; count: number; label: string; endpoint: string } | null>(null);
  const [lotImportStatus, setLotImportStatus] = useState("");
  useEffect(() => setData(initialData), [initialData]);
  const previewLots = async (file?: File) => {
    if (!file) return;
    setLotImportStatus("Checking Fidelity export…");
    const content = await file.text();
    const isPositionExport = content.replace(/^\ufeff/, "").startsWith("Account number,Account name,");
    const endpoint = isPositionExport ? "fidelity-positions" : "fidelity";
    const response = await fetch(`/api/private/investments/imports/${endpoint}/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    const result = await response.json();
    if (!response.ok) {
      setFidelityImport(null);
      setLotImportStatus(result.detail || "That file could not be read.");
      return;
    }
    const count = isPositionExport ? result.counts.holdings : result.counts.tax_lots;
    const label = isPositionExport ? "positions" : "lots";
    setFidelityImport({ content, count, label, endpoint });
    setLotImportStatus(`${count} ${label} are ready to import.`);
  };
  const commitLots = async () => {
    if (!fidelityImport) return;
    setLotImportStatus(`Importing reviewed ${fidelityImport.label}…`);
    const response = await fetch(`/api/private/investments/imports/${fidelityImport.endpoint}/commit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: fidelityImport.content }),
    });
    const result = await response.json();
    if (!response.ok) {
      setLotImportStatus(result.detail || "The lots could not be imported.");
      return;
    }
    const refreshed = await fetch("/api/private/investments/positions");
    if (refreshed.ok) setData(await refreshed.json());
    setFidelityImport(null);
    setLotImportStatus(`${result.result.created} ${fidelityImport.label} added; ${result.result.unchanged} were already present.`);
  };
  const householdAccounts = data.account_summaries.filter((item) => item.ownership_scope === "household");
  const householdHoldings = data.holdings.filter((item) => item.ownership_scope === "household");
  const comparableHoldings = householdHoldings.filter(hasBenchmarkComparison);
  const unavailableComparisons = householdHoldings.length - comparableHoldings.length;
  const comparisonBasis = comparableHoldings.reduce((sum, holding) => sum + holding.benchmark_comparison.basis_covered, 0);
  const actualComparedValue = comparableHoldings.reduce((sum, holding) => sum + holding.benchmark_comparison.actual_covered_value, 0);
  const spyComparedValue = comparableHoldings.reduce((sum, holding) => sum + holding.benchmark_comparison.benchmark_value, 0);
  const comparisonDifference = actualComparedValue - spyComparedValue;
  const actualComparedReturn = comparisonBasis ? (actualComparedValue / comparisonBasis - 1) * 100 : 0;
  const spyComparedReturn = comparisonBasis ? (spyComparedValue / comparisonBasis - 1) * 100 : 0;
  const comparisonMax = Math.max(actualComparedValue, spyComparedValue, 1);
  const maxAccount = Math.max(...householdAccounts.map((item) => item.market_value), 1);
  const treatment = {
    taxable: "Taxable brokerage",
    tax_deferred: "Tax deferred",
    roth: "Roth",
    hsa: "Health savings account",
    custodial: "Child-owned custodial",
  };
  return (
    <section className="dashboard-panel investment-overview" aria-labelledby="investment-heading">
      <div className="section-title">
        <div><p className="eyebrow">Investments</p><h2 id="investment-heading">What you own and how it has done</h2></div>
        <strong>{money.format(data.summary.household_market_value)}</strong>
      </div>
      <div className="investment-summary-grid">
        <article><span>Household investments</span><strong>{money.format(data.summary.household_market_value)}</strong><small>{data.summary.household_position_count} positions · children’s custodial accounts excluded</small></article>
        <article><span>Gain on known basis</span><strong className={data.summary.unrealized_gain_on_known_basis < 0 ? "negative" : "positive"}>{money.format(data.summary.unrealized_gain_on_known_basis)}{data.summary.unrealized_gain_percent !== null ? ` · ${data.summary.unrealized_gain_percent.toFixed(1)}%` : ""}</strong><small>Current value minus basis for covered positions</small></article>
        <article><span>Basis coverage</span><strong>{data.summary.basis_coverage_percent.toFixed(1)}%</strong><small>{money.format(data.summary.known_basis_market_value)} of current value has known basis</small></article>
      </div>
      <p className="visual-note">Basis gain is not the same as annualized performance. It does not fully account for the timing of deposits, withdrawals, dividends, or fees. Nightly valuations are now building the history needed for proper time-weighted returns.</p>
      {comparableHoldings.length > 0 && <section className="spy-comparison" aria-labelledby="spy-comparison-heading">
        <div className="section-title">
          <div><p className="eyebrow">S&amp;P 500 comparison</p><h3 id="spy-comparison-heading">Did your covered holdings beat SPY?</h3></div>
          <strong className={comparisonDifference >= 0 ? "positive" : "negative"}>{comparisonDifference >= 0 ? "Ahead " : "Behind "}{money.format(Math.abs(comparisonDifference))}</strong>
        </div>
        <p className="quiet">This matches each imported tax lot’s reported cost basis and acquisition date against the same dollars in dividend- and split-adjusted SPY. It is a comparison of the {comparableHoldings.length} positions with complete lots—not your whole portfolio.</p>
        <figure className="spy-comparison-chart" aria-label={`Covered holdings returned ${actualComparedReturn.toFixed(1)} percent compared with ${spyComparedReturn.toFixed(1)} percent for SPY`}>
          <div><span>Your covered holdings <strong>{money.format(actualComparedValue)}</strong><small>{actualComparedReturn.toFixed(1)}% since each lot’s purchase date</small></span><i><b style={{ width: `${actualComparedValue / comparisonMax * 100}%` }} /></i></div>
          <div><span>Same dollars in SPY <strong>{money.format(spyComparedValue)}</strong><small>{spyComparedReturn.toFixed(1)}% with dividends and splits adjusted</small></span><i className="spy-bar"><b style={{ width: `${spyComparedValue / comparisonMax * 100}%` }} /></i></div>
          <figcaption>{money.format(comparisonBasis)} across {comparableHoldings.reduce((sum, holding) => sum + holding.benchmark_comparison.covered_lots, 0)} tax lots · weekly benchmark alignment · through {new Date(`${comparableHoldings[0].benchmark_comparison.as_of}T00:00:00`).toLocaleDateString()}</figcaption>
        </figure>
        <div className="spy-holding-grid" aria-label="Individual holdings compared with SPY">
          {comparableHoldings.slice().sort((left, right) => Math.abs(right.benchmark_comparison.excess_value) - Math.abs(left.benchmark_comparison.excess_value)).map((holding) => {
            const comparison = holding.benchmark_comparison;
            const ahead = comparison.excess_value >= 0;
            return <article key={`${holding.account_id}-${holding.security_id}`}>
              <span>{holding.ticker_symbol || holding.security_name || "Investment"}</span>
              <strong className={ahead ? "positive" : "negative"}>{ahead ? "Ahead " : "Behind "}{money.format(Math.abs(comparison.excess_value))}</strong>
              <small>{comparison.actual_return_percent?.toFixed(1)}% you · {comparison.benchmark_return_percent?.toFixed(1)}% SPY</small>
            </article>;
          })}
        </div>
        {unavailableComparisons > 0 && <p className="visual-note">{unavailableComparisons} other household positions stay out of this comparison until their Fidelity lot dates and basis are imported; Finance does not fill those gaps with guesses.</p>}
      </section>}
      <div className="account-performance-list">
        {householdAccounts.map((account) => {
          const accountHoldings = householdHoldings
            .filter((holding) => holding.account_id === account.account_id)
            .sort((a, b) => (b.institution_value || 0) - (a.institution_value || 0));
          return <article key={account.account_id}>
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
            <div className="account-activity-grid">
              <span>Observed contributions<strong>{money.format(account.observed_activity.contributions)}</strong></span>
              <span>Observed withdrawals<strong>{money.format(account.observed_activity.withdrawals)}</strong></span>
              <span>Observed dividends / interest<strong>{money.format(account.observed_activity.dividends_and_interest)}</strong></span>
            </div>
            <details className="position-details">
              <summary>See {account.position_count} positions and individual gains</summary>
              <div className="position-list">
                {accountHoldings.map((holding) => {
                  const gain = holding.analytics.unrealized_gain;
                  const lots = data.tax_lots
                    .filter((lot) => lot.linked_account_id === holding.account_id && lot.symbol === holding.ticker_symbol)
                    .sort((a, b) => a.acquired_date.localeCompare(b.acquired_date));
                  return <div key={`${holding.account_id}-${holding.security_id}`}>
                    <span><strong>{holding.ticker_symbol || holding.security_name || "Investment"}</strong><small>{holding.security_name} · {holding.analytics.account_weight_percent.toFixed(1)}% of account</small></span>
                    <span>{money.format(holding.institution_value || 0)}<small>{holding.cost_basis === null ? "Cost basis unavailable" : `${money.format(holding.cost_basis)} basis · ${gain !== null && gain >= 0 ? "+" : ""}${gain === null ? "" : money.format(gain)} · ${holding.analytics.unrealized_gain_percent?.toFixed(1)}%`}</small></span>
                    <div className={`holding-benchmark ${holding.benchmark_comparison.status}`}>
                      {holding.benchmark_comparison.status === "available" ? <>
                        <span><strong>{holding.benchmark_comparison.excess_value >= 0 ? "Beat" : "Trailed"} {holding.benchmark_comparison.benchmark} by {money.format(Math.abs(holding.benchmark_comparison.excess_value))}</strong><small>Same {money.format(holding.benchmark_comparison.basis_covered)} invested on the covered lot dates</small></span>
                        <span><strong>{holding.benchmark_comparison.actual_return_percent?.toFixed(1)}% vs. {holding.benchmark_comparison.benchmark_return_percent?.toFixed(1)}%</strong><small>{holding.benchmark_comparison.covered_lots} of {holding.benchmark_comparison.total_lots} lots · {holding.benchmark_comparison.cadence}-adjusted, through {new Date(`${holding.benchmark_comparison.as_of}T00:00:00`).toLocaleDateString()}</small></span>
                      </> : <><span><strong>{holding.benchmark_comparison.benchmark} comparison collecting data</strong><small>{holding.benchmark_comparison.reason}</small></span></>}
                    </div>
                    {lots.length > 0 && <details className="lot-details"><summary>{lots.length} Fidelity tax {lots.length === 1 ? "lot" : "lots"}</summary><div>{lots.map((lot) => <p key={`${lot.symbol}-${lot.acquired_date}`}><span><strong>{new Date(`${lot.acquired_date}T00:00:00`).toLocaleDateString()}</strong><small>{lot.quantity.toLocaleString()} shares</small></span><span><strong>{lot.cost_basis === null ? "Unknown basis" : money.format(lot.cost_basis)}</strong><small>{lot.cost_basis === null || !lot.quantity ? "" : `${money.format(lot.cost_basis / lot.quantity)} per share`}</small></span></p>)}</div></details>}
                  </div>;
                })}
              </div>
            </details>
            <p className="performance-status">{account.performance_tracking.definition}{account.observed_activity.start ? ` Activity history currently begins ${new Date(`${account.observed_activity.start}T00:00:00`).toLocaleDateString()}.` : ""}</p>
          </article>
        })}
      </div>
      <details className="lot-import">
        <summary>Import a Fidelity positions or tax-lot CSV</summary>
        <p>A Fidelity positions export updates current value and reported basis. A lot-level export also adds acquisition dates, quantities, and basis for the exact SPY comparison. Finance never guesses lots from trade history. The file is sent only to your private Finance service.</p>
        <input type="file" accept=".csv,text/csv" onChange={(event) => void previewLots(event.target.files?.[0])} />
        {lotImportStatus && <small>{lotImportStatus}</small>}
        {fidelityImport && <button type="button" onClick={() => void commitLots()}>Import {fidelityImport.count} reviewed {fidelityImport.label}</button>}
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
  const effective = home.effective_valuation;
  const currentValue = effective?.amount || home.valuation.amount;
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
        <article><span>Current market consensus</span><strong>{money.format(currentValue)}</strong><small>{effective?.source_label || home.valuation.source_label} · {effective ? new Date(effective.valued_at).toLocaleDateString() : new Date(home.valuation.valued_at).toLocaleDateString()}</small>{effective?.estimate_range && <div className="estimate-range"><i /><p>{money.format(effective.estimate_range.low)} <span>accepted estimates</span> {money.format(effective.estimate_range.high)}</p></div>}</article>
        <article><span>County / registered value</span><strong>{money.format(home.valuation.amount)}</strong><small>{home.valuation.source_label}</small></article>
        <article><span>Known annual carrying cost</span><strong>{money.format(home.cost_summary.annual_ownership_total || 0)}</strong><small>{Object.keys(costs).map((key) => key.replaceAll("_", " ")).join(" + ") || "No costs recorded"}. Maintenance, insurance, utilities, and improvements remain excluded until linked.</small></article>
      </div>
      {effective?.excluded_outliers?.map((outlier) => (
        <div className="valuation-warning" key={`${outlier.source_label}-${outlier.valued_at}`}>
          <strong>{outlier.source_label} is being treated as an outlier</strong>
          <p>{money.format(outlier.amount)} is materially outside the cluster of independent estimates, so it remains visible but does not drive net worth or the projection.</p>
        </div>
      ))}
      {effective?.components && (
        <details className="valuation-sources">
          <summary>See the estimates behind the consensus</summary>
          <div>{effective.components.map((component) => <p key={`${component.source_label}-${component.valued_at}`}><span>{component.source_label}</span><strong>{money.format(component.amount)}</strong></p>)}</div>
        </details>
      )}
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

function VehicleOverview({ vehicles }: { vehicles: HomeAsset[] }) {
  const total = vehicles.reduce(
    (sum, vehicle) => sum + (vehicle.effective_valuation?.amount || vehicle.valuation.amount),
    0,
  );
  return (
    <section className="dashboard-panel vehicle-overview" aria-labelledby="vehicle-heading">
      <div className="section-title">
        <div><p className="eyebrow">Vehicles</p><h2 id="vehicle-heading">Current value and ownership cost</h2></div>
        <strong>{money.format(total)}</strong>
      </div>
      <div className="vehicle-grid">
        {vehicles.map((vehicle) => {
          const estimate = vehicle.valuation_automation?.latest_estimates?.[0];
          const effective = vehicle.effective_valuation || vehicle.valuation;
          const identifiers = vehicle.identifiers || {};
          const missing = [!identifiers.vin && "VIN", !identifiers.mileage && "current mileage"].filter(Boolean);
          return (
            <article key={vehicle.id}>
              <div className="vehicle-title"><div><span>{identifiers.year} {identifiers.make}</span><h3>{vehicle.name}</h3></div><strong>{money.format(effective.amount)}</strong></div>
              <p className="vehicle-trim">{identifiers.trim || "Trim not recorded"} · owned outright</p>
              {estimate?.estimate_range && <div className="estimate-range"><i /><p>{money.format(estimate.estimate_range.low)} <span>model range</span> {money.format(estimate.estimate_range.high)}</p></div>}
              <dl>
                <div><dt>Value source</dt><dd>{effective.source_label}</dd></div>
                <div><dt>As of</dt><dd>{new Date(effective.valued_at).toLocaleDateString()}</dd></div>
                <div><dt>Known annual operating cost</dt><dd>{money.format(vehicle.cost_summary.annual_operating_total || 0)}</dd></div>
                <div><dt>Recorded depreciation</dt><dd>{vehicle.cost_summary.depreciation_to_date == null ? "Purchase price needed" : money.format(vehicle.cost_summary.depreciation_to_date)}</dd></div>
              </dl>
              <p className={`vehicle-quality ${missing.length ? "needs-detail" : ""}`}>
                {missing.length
                  ? `Provisional value: add ${missing.join(" and ")} for a vehicle-specific live appraisal.`
                  : "Vehicle identity and mileage are ready for a live appraisal provider."}
              </p>
            </article>
          );
        })}
      </div>
      <p className="visual-note">Current figures are private-party-value estimates, not guaranteed offers. Until VIN, mileage, condition, and a permitted live provider are connected, the monthly refresh applies the displayed depreciation model to dated market anchors.</p>
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
      fetch("/api/private/dashboard?include_insights=false").then((response) => {
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
      if (dashboardResult.status === "fulfilled") {
        setDashboard(dashboardResult.value);
        void fetch("/api/private/dashboard/insights")
          .then((response) => response.ok ? response.json() as Promise<DashboardInsights> : null)
          .then((insights) => {
            if (!insights) return;
            setDashboard((current) => current ? {
              ...current,
              ...insights,
              insights_loading: false,
            } : current);
          });
      }
      else setError("The financial overview could not be loaded. Check connection health and try again.");
      if (investmentResult.status === "fulfilled") setInvestments(investmentResult.value);
      if (assetResult.status === "fulfilled") {
        setAssets(assetResult.value);
        void Promise.allSettled(
          assetResult.value.assets.map((asset) =>
            fetch(`/api/private/assets/${asset.id}/valuations/refresh`, { method: "POST" }),
          ),
        ).then(() =>
          fetch("/api/private/assets")
            .then((response) => response.ok ? response.json() as Promise<AssetsResult> : null)
            .then((updated) => { if (updated) setAssets(updated); }),
        );
      }
    });
  }, []);
  const byKey = new Map(
    dashboard?.metrics.map((metric) => [metric.key, metric]),
  );
  const headlineKeys = [
    "cash",
    "investment_value",
    "true_monthly_cost",
    "current_card_balance",
  ];
  const bankCash = byKey.get("cash")?.value || 0;
  const brokerageCash = byKey.get("taxable_brokerage_cash")?.value || 0;
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
          {brokerageCash > 0 && <section className="liquidity-bridge" aria-label="Liquid cash position">
            <div>
              <p className="eyebrow">Liquid cash position</p>
              <h2>{money.format(bankCash + brokerageCash)}</h2>
              <p>Bank cash plus taxable brokerage cash equivalents.</p>
            </div>
            <dl>
              <div><dt>Bank cash</dt><dd>{money.format(bankCash)}</dd></div>
              <div><dt>Brokerage cash equivalents</dt><dd>{money.format(brokerageCash)}</dd></div>
            </dl>
            <p className="visual-note">The brokerage amount is already part of Household investment value and Long-term net worth. It appears here only to make available liquidity clear—not to count it twice.</p>
          </section>}
          <NetWorthVisual dashboard={dashboard} />
          <NetWorthProjection dashboard={dashboard} readiness={dashboard.retirement_readiness} assets={assets} />
          <FinancialChangeStory dashboard={dashboard} />
          {dashboard.retirement_readiness.available && <RetirementReadiness readiness={dashboard.retirement_readiness} />}
          {investments && <InvestmentOverview data={investments} />}
          {assets?.assets.find((asset) => asset.kind === "home") && (
            <HomeOverview home={assets.assets.find((asset) => asset.kind === "home")!} />
          )}
          {assets && assets.assets.some((asset) => asset.kind === "vehicle") && (
            <VehicleOverview vehicles={assets.assets.filter((asset) => asset.kind === "vehicle")} />
          )}
          <div className="dashboard-columns">
            <section className="dashboard-panel">
              <div className="section-title">
                <div>
                  <p className="eyebrow">Spending &amp; saving</p>
                  <h2>What you spent and saved</h2>
                </div>
              </div>
              <div className="flow-compare">
                <article>
                  <span>Amount saved · trailing 12 months</span>
                  <strong>
                    {dashboard.sections.cash_flow.available
                      ? money.format(dashboard.sections.cash_flow.operating_surplus)
                      : "Not available yet"}
                  </strong>
                  {dashboard.sections.cash_flow.available && <p>
                    Income left after personal spending
                  </p>}
                  <small>{dashboard.sections.cash_flow.context}</small>
                  {dashboard.sections.cash_flow.available && <details>
                    <summary>Why this differs from bank cash movement</summary>
                    <p>
                      <strong>{money.format(dashboard.sections.cash_flow.net)}</strong>{" "}
                      observed bank movement: {money.format(dashboard.sections.cash_flow.depository_credits)} credits minus{" "}
                      {money.format(dashboard.sections.cash_flow.depository_debits)} debits.
                    </p>
                    <ul>
                      <li>{money.format(dashboard.sections.cash_flow.income_credits)} income credits</li>
                      <li>{money.format(dashboard.sections.cash_flow.transfer_credits)} transfer credits</li>
                      <li>{money.format(dashboard.sections.cash_flow.refund_credits)} refund credits</li>
                      <li>{money.format(dashboard.sections.cash_flow.purchase_debits)} direct bank purchases</li>
                      <li>{money.format(dashboard.sections.cash_flow.card_payment_debits)} card payments</li>
                      <li>{money.format(dashboard.sections.cash_flow.investment_transfer_debits)} moved to investments</li>
                      <li>{money.format(dashboard.sections.cash_flow.other_transfer_debits)} other transfer debits</li>
                    </ul>
                  </details>}
                </article>
                <article>
                  <span>Amount spent · trailing 12 months</span>
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
                <span>{dashboard.insights_loading ? "analyzing" : `${dashboard.unusual_activity.length} signals`}</span>
              </div>
              <p className="quiet">
                {dashboard.unusual_activity_method.definition}{" "}
                {dashboard.unusual_activity_method.limitations}
              </p>
              {dashboard.insights_loading ? (
                <div className="empty-signal"><strong>Analyzing your full transaction history…</strong><p>Your current balances and trailing-year spending are ready; review signals arrive separately so they do not delay the dashboard.</p></div>
              ) : dashboard.unusual_activity.length === 0 ? (
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
          {dashboard.insights_loading ? <section className="dashboard-panel recommendation-panel"><div className="section-title"><div><p className="eyebrow">Potential savings</p><h2>Where a review may save money</h2></div><span>analyzing</span></div><p className="quiet">Checking full transaction history for explicit fees, recurring-price changes, and sustained category changes.</p></section> : <RecommendationQueue data={dashboard.recommendations} saved={() => {
            void fetch("/api/private/dashboard")
              .then((response) => response.ok ? response.json() as Promise<DashboardResult> : null)
              .then((updated) => { if (updated) setDashboard(updated); });
          }} />}
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
