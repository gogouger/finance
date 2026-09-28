import { useMemo, useState } from "react";
import { FinanceNav } from "./navigation";

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

type HousingYear = {
  year: number;
  buyer_net_wealth: number;
  renter_investments: number;
  buyer_unrecoverable_cost: number;
  renter_unrecoverable_cost: number;
  buyer_principal_contributed: number;
  buyer_appreciation: number;
  renter_net_contributions: number;
  renter_investment_growth: number;
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

type RetirementYear = {
  age: number;
  phase: "working" | "retired";
  taxable_balance: number;
  traditional_balance: number;
  roth_balance: number;
  hsa_balance: number;
  total_balance: number;
  spendable_after_tax: number;
  spending: number;
  unmet_spending: number;
};

type Series<T> = {
  label: string;
  color: string;
  value: (row: T) => number;
};

function LineChart<T>({
  rows,
  x,
  series,
  label,
}: {
  rows: T[];
  x: (row: T) => number;
  series: Series<T>[];
  label: string;
}) {
  const width = 820;
  const height = 300;
  const pad = { left: 72, right: 20, top: 24, bottom: 42 };
  const values = rows.flatMap((row) => series.map((item) => item.value(row)));
  const min = Math.min(0, ...values);
  const max = Math.max(1, ...values);
  const range = max - min || 1;
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const point = (row: T, index: number, item: Series<T>) => {
    const px = pad.left + (rows.length === 1 ? innerWidth : (index / (rows.length - 1)) * innerWidth);
    const py = pad.top + innerHeight - ((item.value(row) - min) / range) * innerHeight;
    return `${px},${py}`;
  };
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((fraction) => ({
    value: max - range * fraction,
    y: pad.top + innerHeight * fraction,
  }));
  const firstX = rows.length ? x(rows[0]) : 0;
  const lastX = rows.length ? x(rows.at(-1)!) : 0;

  return <figure className="story-chart">
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
      {ticks.map((tick) => <g key={tick.y}>
        <line className="chart-grid" x1={pad.left} x2={width - pad.right} y1={tick.y} y2={tick.y} />
        <text className="chart-label" x={pad.left - 10} y={tick.y + 4} textAnchor="end">{money.format(tick.value)}</text>
      </g>)}
      {series.map((item) => <polyline key={item.label} points={rows.map((row, index) => point(row, index, item)).join(" ")} style={{ stroke: item.color }} />)}
      <text className="chart-label" x={pad.left} y={height - 12}>{firstX}</text>
      <text className="chart-label" x={width - pad.right} y={height - 12} textAnchor="end">{lastX}</text>
    </svg>
    <figcaption className="chart-legend">{series.map((item) => <span key={item.label}><i style={{ background: item.color }} />{item.label}</span>)}</figcaption>
  </figure>;
}

function Composition({ pieces }: { pieces: { label: string; value: number; color: string; note: string }[] }) {
  const total = pieces.reduce((sum, piece) => sum + Math.max(piece.value, 0), 0) || 1;
  return <div className="composition">
    <div className="composition-bar" aria-label="Composition bar">{pieces.map((piece) => <span key={piece.label} style={{ width: `${Math.max(piece.value, 0) / total * 100}%`, background: piece.color }} title={`${piece.label}: ${money.format(piece.value)}`} />)}</div>
    <div className="composition-list">{pieces.map((piece) => <div key={piece.label}><i style={{ background: piece.color }} /><span>{piece.label}<small>{piece.note}</small></span><strong>{money.format(piece.value)}</strong></div>)}</div>
  </div>;
}

type HousingSensitivity = { field: string; label: string; unit: string; base: number; lower: { assumption: number; buyer_advantage: number }; higher: { assumption: number; buyer_advantage: number }; swing: number };

export function HousingVisuals({ years, initialCash, sensitivity, stage = 0 }: { years: HousingYear[]; initialCash: InitialCashAllocation; sensitivity: HousingSensitivity[]; stage?: number }) {
  const [selected, setSelected] = useState(years.length - 1);
  const index = Math.min(selected, years.length - 1);
  const row = years[index];
  const crossover = years.find((year) => year.buyer_advantage >= 0)?.year;
  if (!row) return null;
  const stages = [
    {
      eyebrow: "Equal starting cash",
      title: `${money.format(initialCash.shared_starting_cash)} takes two paths`,
      note: "The buyer moves cash into equity and purchase costs. The renter invests the same starting cash.",
      evidence: [
        ["Buyer down payment", initialCash.buyer_down_payment_to_home],
        ["Buyer purchase costs", initialCash.buyer_purchase_costs],
        ["Renter starts invested", initialCash.renter_starting_investment],
      ],
    },
    {
      eyebrow: `By year ${row.year}`,
      title: `${money.format(row.buyer_components.principal_paid)} of mortgage principal came back as equity`,
      note: "The rest of the scheduled mortgage cost was interest. Principal lowers the balance still owed at sale.",
      evidence: [
        ["Down payment", row.buyer_components.down_payment],
        ["Principal paid", row.buyer_components.principal_paid],
        ["Loan still owed", row.buyer_components.loan_balance],
      ],
    },
    {
      eyebrow: "Market value",
      title: `${money.format(row.buyer_components.appreciation)} came from modeled appreciation`,
      note: "Appreciation is forecast value, not contributed cash or a guaranteed return.",
      evidence: [
        ["Home value", row.buyer_components.home_value],
        ["Appreciation", row.buyer_components.appreciation],
        ["Equity before sale", row.buyer_components.home_value - row.buyer_components.loan_balance],
      ],
    },
    {
      eyebrow: "Unrecoverable ownership cost",
      title: `${money.format(row.buyer_unrecoverable_cost)} does not remain in the house`,
      note: "This total includes the estimated cost to sell, net of modeled tax benefit.",
      evidence: [
        ["Interest", row.buyer_components.interest],
        ["Property tax", row.buyer_components.property_tax],
        ["Insurance + maintenance", row.buyer_components.insurance + row.buyer_components.maintenance],
        ["HOA + utilities", row.buyer_components.hoa + row.buyer_components.utilities],
        ["Purchase + sale costs", row.buyer_components.purchase_costs + row.buyer_components.sale_cost],
      ],
    },
    {
      eyebrow: "Rent changes over time",
      title: `${money.format(row.renter_components.ending_monthly_rent)}/mo by year ${row.year}`,
      note: "Rent and renter utilities compound using their displayed growth assumptions.",
      evidence: [
        ["Rent paid", row.renter_components.rent],
        ["Renter utilities", row.renter_components.utilities],
        ["Total housing cost", row.renter_unrecoverable_cost],
      ],
    },
    {
      eyebrow: "Invest the cash difference",
      title: `${money.format(row.renter_investments)} remains in the renter portfolio`,
      note: "The portfolio starts with avoided cash-to-close, then receives or funds the monthly housing-cost difference.",
      evidence: [
        ["Net contributions", row.renter_components.net_contributions],
        ["Investment growth", row.renter_components.investment_growth],
        ["Estimated tax drag", row.renter_components.estimated_investment_tax_drag],
      ],
    },
    {
      eyebrow: "Wealth after moving out",
      title: row.buyer_advantage >= 0 ? `Buying is ${money.format(row.buyer_advantage)} ahead` : `Renting is ${money.format(Math.abs(row.buyer_advantage))} ahead`,
      note: "Buyer wealth is what remains after the mortgage and sale costs. Renter wealth is the liquid investment portfolio.",
      evidence: [
        ["Buyer after sale", row.buyer_net_wealth],
        ["Renter portfolio", row.renter_investments],
        ["Buyer sale cost", row.buyer_components.sale_cost],
      ],
    },
  ] as const;
  const active = stages[Math.min(stage, stages.length - 1)];
  return <section className="visual-story housing-visual" aria-label="Housing projection explained visually">
    <div className="story-heading"><div><p className="eyebrow">The race over time</p><h3>Wealth after moving out</h3></div><p>The graph stays fixed while the walkthrough explains where both paths came from.</p></div>
    <LineChart rows={years} x={(year) => year.year} label="Buyer net wealth and renter investment wealth over time" series={[
      { label: "Buy: equity after sale", color: "#83d7ad", value: (year) => year.buyer_net_wealth },
      { label: "Rent: invested difference", color: "#d6a866", value: (year) => year.renter_investments },
    ]} />
    <div className="story-callout"><strong>{crossover ? `Buying first pulls ahead in year ${crossover}.` : "Buying never pulls ahead inside this window."}</strong><span>The highlighted explanation follows the walkthrough on the left.</span></div>
    <label className="year-scrubber"><span>Explain year {row.year}</span><input type="range" min={0} max={years.length - 1} value={index} onChange={(event) => setSelected(Number(event.target.value))} /></label>
    <article className="housing-stage-evidence" aria-live="polite"><p className="eyebrow">{active.eyebrow}</p><h4>{active.title}</h4><p>{active.note}</p><dl>{active.evidence.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{money.format(value)}</dd></div>)}</dl></article>
    {stage === 6 && sensitivity.length > 0 && <article className="housing-sensitivity"><p className="eyebrow">What matters most</p><h4>These are the assumptions moving the answer.</h4><p>Each row changes one assumption up and down while every other input stays fixed.</p><div>{sensitivity.slice(0, 3).map((item) => <p key={item.field}><span>{item.label} ±{Math.abs(item.higher.assumption - item.base).toFixed(1)} points</span><strong>{money.format(item.swing)} swing</strong></p>)}</div></article>}
  </section>;
}

export function RetirementVisuals({ years, retirementAge }: { years: RetirementYear[]; retirementAge: number }) {
  const final = years.at(-1);
  const retirement = years.find((year) => year.age >= retirementAge);
  const firstShortfall = years.find((year) => year.unmet_spending > 0);
  if (!final) return null;
  return <section className="visual-story" aria-label="Retirement projection explained visually">
    <div className="story-heading"><div><p className="eyebrow">Your runway</p><h3>Headline balance vs. money you can spend</h3></div><p>The distance between these lines is embedded tax. Retirement plans should be compared with the lower, spendable line.</p></div>
    <LineChart rows={years} x={(year) => year.age} label="Total and after-tax retirement wealth by age" series={[
      { label: "Headline balance", color: "#83d7ad", value: (year) => year.total_balance },
      { label: "Spendable after tax", color: "#d6a866", value: (year) => year.spendable_after_tax },
    ]} />
    <div className={`story-callout ${firstShortfall ? "warning" : ""}`}><strong>{firstShortfall ? `The plan first runs short at age ${firstShortfall.age}.` : `The modeled spending is funded through age ${final.age}.`}</strong><span>{retirement ? `At retirement, the model has ${money.format(retirement.spendable_after_tax)} available after embedded taxes.` : "The retirement date is outside the modeled period."}</span></div>
    <div className="visual-columns">
      <article><p className="eyebrow">At retirement</p><h4>{retirement ? money.format(retirement.total_balance) : "—"}</h4>{retirement && <Composition pieces={[
        { label: "Taxable", value: retirement.taxable_balance, color: "#d6a866", note: "Flexible bridge money; gains may be taxed" },
        { label: "Traditional", value: retirement.traditional_balance, color: "#4f9f79", note: "Tax-deferred; withdrawals are generally taxable" },
        { label: "Roth", value: retirement.roth_balance, color: "#83d7ad", note: "Tax-free qualified growth" },
        { label: "HSA", value: retirement.hsa_balance, color: "#8bb8d4", note: "Tax-free for qualified medical costs" },
      ]} />}</article>
      <article className="why-card"><p className="eyebrow">How to read this</p><ol><li><strong>Before retirement</strong><span>Contributions and compounding grow each account.</span></li><li><strong>At retirement</strong><span>The model stops contributions and begins inflation-adjusted spending.</span></li><li><strong>During retirement</strong><span>Taxable assets fund the bridge first, then traditional, Roth, and HSA balances.</span></li></ol></article>
    </div>
  </section>;
}

type RetirementComparisonResult = {
  retirement_age: number;
  comparison_basis: { annual_take_home_sacrifice: number; accumulation_years: number; ranking_metric: string };
  strategies: Array<{
    key: "taxable" | "roth" | "traditional" | "hsa";
    label: string;
    annual_primary_contribution: number;
    annual_taxable_overflow: number;
    employer_match: number;
    caveat: string;
    years: Array<{ age: number; headline_balance: number; after_tax_value: number; accessible_basis: number }>;
    at_retirement: { age: number; headline_balance: number; after_tax_value: number; accessible_basis: number };
  }>;
  ranking: Array<{ key: string; label: string; after_tax_value: number }>;
  optimized_mix: {
    annual_allocations: Record<"traditional" | "roth" | "hsa" | "taxable", number>;
    employer_match: number;
    match_protected: boolean;
    bridge: { projected_gap: number };
    at_retirement: { after_tax_value: number };
    allocation_order: string[];
  };
  uncertainty: {
    simulations: number;
    success_probability_percent: number;
    ending_balance_distribution: { p10: number; p25: number; p50: number; p75: number; p90: number };
    first_failure_age_distribution: Record<string, number> | null;
    sequence_risk: Record<string, number>;
    yearly: Array<{ age: number; p10: number; p50: number; p90: number; path_success_percent: number }>;
    stress_case: { name: string; success_probability_percent: number; change_from_baseline_points: number };
    assumptions: { starting_wealth_basis: string; social_security_included: boolean; pension_income_included: boolean; healthcare_costs_included: boolean; return_distribution: string };
    exclusions: string[];
    disclaimer: string;
  };
  bridge: { years: number; annual_spending_at_retirement: number; required_spending: number; accessible_at_retirement: number; existing_gap: number };
  early_access: {
    ruleset: { version: string; effective_date: string };
    ranking: Array<{ strategy: string; spendable_value: number; eligible: boolean }>;
    strategies: Array<{ strategy: string; eligible: boolean; spendable_value: number; failure_reason: string | null }>;
  };
};

const retirementColors = {
  taxable: "#d6a866",
  roth: "#83d7ad",
  traditional: "#4f9f79",
  hsa: "#8bb8d4",
} as const;

const accessLabels: Record<string, string> = {
  penalized_traditional: "Pay penalty",
  taxable_bridge: "Taxable bridge",
  roth_contribution_basis: "Roth basis",
  roth_conversion_ladder: "Conversion ladder",
  rule_of_55: "Rule of 55",
  sepp_72t: "72(t) / SEPP",
};

export function RetirementComparisonVisuals({ result, stage = 0 }: { result: RetirementComparisonResult; stage?: number }) {
  const [selected, setSelected] = useState(Math.max(result.comparison_basis.accumulation_years - 1, 0));
  const maxIndex = Math.max(result.comparison_basis.accumulation_years - 1, 0);
  const index = Math.min(selected, maxIndex);
  const rows = result.strategies[0]?.years.map((year, yearIndex) => ({
    age: year.age,
    taxable: result.strategies.find((item) => item.key === "taxable")?.years[yearIndex]?.after_tax_value || 0,
    roth: result.strategies.find((item) => item.key === "roth")?.years[yearIndex]?.after_tax_value || 0,
    traditional: result.strategies.find((item) => item.key === "traditional")?.years[yearIndex]?.after_tax_value || 0,
    hsa: result.strategies.find((item) => item.key === "hsa")?.years[yearIndex]?.after_tax_value || 0,
  })) || [];
  const selectedAge = rows[index]?.age || result.retirement_age;
  const byKey = Object.fromEntries(result.strategies.map((item) => [item.key, item]));
  const best = result.ranking[0];
  const bestAccess = result.early_access.ranking.find((item) => item.eligible);
  const stages = [
    {
      eyebrow: "Equal household cost",
      title: `${money.format(result.comparison_basis.annual_take_home_sacrifice)} leaves the paycheck each year`,
      note: "Pre-tax accounts can invest more because their current tax savings are included in the comparison.",
      evidence: result.strategies.map((item) => [item.label, item.annual_primary_contribution + item.annual_taxable_overflow + item.employer_match] as [string, number]),
    },
    {
      eyebrow: `Flexible at age ${selectedAge}`,
      title: `${money.format(byKey.taxable?.years[index]?.after_tax_value || 0)} is modeled spendable brokerage value`,
      note: byKey.taxable?.caveat || "Taxable gains and annual drag remain explicit.",
      evidence: [
        ["Headline balance", byKey.taxable?.years[index]?.headline_balance || 0],
        ["Spendable after gains tax", byKey.taxable?.years[index]?.after_tax_value || 0],
        ["Contributed basis", byKey.taxable?.years[index]?.accessible_basis || 0],
      ] as [string, number][],
    },
    {
      eyebrow: "Tax timing",
      title: best ? `${best.label} leads at retirement in this scenario` : "Compare after-tax value",
      note: "The line ranking can flip when current and retirement tax rates, contribution limits, or employer match change.",
      evidence: ["traditional", "roth", "taxable"].map((key) => [byKey[key]?.label || key, byKey[key]?.at_retirement.after_tax_value || 0] as [string, number]),
    },
    {
      eyebrow: "Qualified medical use",
      title: `${money.format(byKey.hsa?.at_retirement.after_tax_value || 0)} modeled spendable HSA value`,
      note: byKey.hsa?.caveat || "Qualified use drives the HSA tax advantage.",
      evidence: [
        ["Headline HSA balance", byKey.hsa?.at_retirement.headline_balance || 0],
        ["After-tax estimate", byKey.hsa?.at_retirement.after_tax_value || 0],
        ["Annual HSA contribution", byKey.hsa?.annual_primary_contribution || 0],
      ] as [string, number][],
    },
    {
      eyebrow: `${result.bridge.years}-year bridge`,
      title: result.bridge.existing_gap > 0 ? `${money.format(result.bridge.existing_gap)} remains after directing new savings to brokerage` : "The taxable-saving path covers the modeled bridge",
      note: bestAccess ? `${accessLabels[bestAccess.strategy] || bestAccess.strategy} produces the highest modeled spendable withdrawals among eligible single-path tests.` : "No tested early-access path is eligible.",
      evidence: [
        ["Bridge spending", result.bridge.required_spending],
        ["Accessible on taxable path", result.bridge.accessible_at_retirement],
        ["First-year retirement spending", result.bridge.annual_spending_at_retirement],
      ] as [string, number][],
    },
    {
      eyebrow: `Ruleset · ${result.early_access.ruleset.effective_date}`,
      title: "The law assumption is pinned and replaceable",
      note: `${result.early_access.ruleset.version} controls the access tests. This is an educational scenario, not a promise that today's rules survive unchanged.`,
      evidence: result.early_access.ranking.slice(0, 3).map((item) => [accessLabels[item.strategy] || item.strategy, item.spendable_value] as [string, number]),
    },
  ];
  const active = stages[Math.min(stage, stages.length - 1)];
  return <section className="visual-story housing-visual retirement-visual" aria-label="Retirement account choices explained visually">
    <div className="story-heading"><div><p className="eyebrow">Same sacrifice, four paths</p><h3>Spendable value by age</h3></div><p>Each line values embedded tax and early-access friction. Headline account balances are intentionally not the comparison metric.</p></div>
    <LineChart rows={rows} x={(row) => row.age} label="After-tax value of equal take-home retirement saving strategies" series={[
      { label: "Taxable", color: retirementColors.taxable, value: (row) => row.taxable },
      { label: "Roth", color: retirementColors.roth, value: (row) => row.roth },
      { label: "Traditional", color: retirementColors.traditional, value: (row) => row.traditional },
      { label: "HSA", color: retirementColors.hsa, value: (row) => row.hsa },
    ]} />
    <div className={`story-callout ${result.bridge.existing_gap > 0 ? "warning" : ""}`}><strong>{best ? `${best.label} leads by modeled spendable value at ${result.retirement_age}.` : "Building the ranking."}</strong><span>{result.bridge.existing_gap > 0 ? `The taxable-saving path leaves a ${money.format(result.bridge.existing_gap)} bridge gap.` : "The taxable-saving path covers the modeled bridge."}</span></div>
    <label className="year-scrubber"><span>Explain age {selectedAge}</span><input type="range" min={0} max={maxIndex} value={index} onChange={(event) => setSelected(Number(event.target.value))} /></label>
    <article className="housing-stage-evidence" aria-live="polite"><p className="eyebrow">{active.eyebrow}</p><h4>{active.title}</h4><p>{active.note}</p><dl>{active.evidence.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{money.format(value)}</dd></div>)}</dl></article>
    {(stage === 2 || stage === 4) && <article className="optimized-mix"><div><p className="eyebrow">Practical mix</p><h4>Protect the match, then fund access.</h4><span>{result.optimized_mix.match_protected ? "Full modeled match protected" : "Take-home budget cannot protect the full match"}</span></div><dl>{Object.entries(result.optimized_mix.annual_allocations).map(([account, amount]) => <div key={account}><dt>{account}</dt><dd>{money.format(amount)}/yr</dd></div>)}<div><dt>Employer match</dt><dd>{money.format(result.optimized_mix.employer_match)}/yr</dd></div><div><dt>Projected bridge gap</dt><dd>{money.format(result.optimized_mix.bridge.projected_gap)}</dd></div></dl></article>}
    {stage === 4 && <div className="access-paths" aria-label="Early access strategy status">{result.early_access.strategies.map((item) => <p key={item.strategy} className={item.eligible ? "eligible" : "ineligible"}><span>{accessLabels[item.strategy] || item.strategy}</span><strong>{item.eligible ? money.format(item.spendable_value) : "Not eligible"}</strong></p>)}</div>}
    {stage === 5 && <article className="uncertainty-card"><div><p className="eyebrow">{result.uncertainty.simulations} return paths</p><h4>{result.uncertainty.success_probability_percent}% fund every modeled year</h4><span>Lower returns plus 10% more spending: {result.uncertainty.stress_case.success_probability_percent}%. Social Security, pensions, and healthcare are excluded until explicitly modeled.</span></div><div className="uncertainty-range"><p><span>Conservative</span><strong>{money.format(result.uncertainty.ending_balance_distribution.p10)}</strong></p><p><span>Middle</span><strong>{money.format(result.uncertainty.ending_balance_distribution.p50)}</strong></p><p><span>Optimistic</span><strong>{money.format(result.uncertainty.ending_balance_distribution.p90)}</strong></p></div></article>}
  </section>;
}

const demoCategories = [
  ["Home + utilities", 3380, "34%"],
  ["Food + household", 1670, "17%"],
  ["Transportation", 920, "9%"],
  ["Family + education", 780, "8%"],
  ["Everything else", 1120, "11%"],
] as const;

const demoNetWorthHistory = [
  ["Oct", 621900], ["Nov", 628400], ["Dec", 631800], ["Jan", 638200],
  ["Feb", 641100], ["Mar", 650700], ["Apr", 655900], ["May", 661300],
  ["Jun", 668800], ["Jul", 671500], ["Aug", 679400], ["Sep", 684300],
].map(([month, value], index) => ({ month: String(month), index: index + 1, value: Number(value) }));

export function DashboardPreview({ embedded = false }: { embedded?: boolean }) {
  const max = Math.max(...demoCategories.map((item) => item[1]));
  if (embedded) return <main className="embed-preview">
    <header><div><p className="kicker">Public product tour · fictional data</p><h1>A financial picture that explains itself.</h1></div><a href="/preview" target="_top">Explore the full demo →</a></header>
    <p className="embed-note">Illustrative household only. No accounts, balances, transactions, or assumptions from the private dashboard are used here.</p>
    <section className="embed-metrics" aria-label="Example financial overview">{[["Net worth", "$684,300", "+$62,400 this year"], ["Cash runway", "6.1 months", "$41,800 available"], ["True monthly cost", "$8,940", "Annual costs included"]].map(([label, value, note]) => <article key={label}><span>{label}</span><strong>{value}</strong><small>{note}</small></article>)}</section>
    <section className="embed-bottom"><article><div className="section-title"><div><p className="eyebrow">Observed spending</p><h2>Where it went</h2></div><span>12 months</span></div><div className="demo-bars">{demoCategories.slice(0, 4).map(([label, value, percent]) => <div key={label}><span>{label}</span><div><i style={{ width: `${value / max * 100}%` }} /></div><strong>{money.format(value)}<small>{percent}</small></strong></div>)}</div></article><article><p className="eyebrow">Decision queue</p><h2>What needs attention</h2><div className="embed-decisions"><p><strong>Insurance renewal</strong><span>18% higher than last year</span></p><p><strong>Quarterly taxes</strong><span>Already included in true cost</span></p><p><strong>One uncertain purchase</strong><span>Waiting for owner review</span></p></div></article></section>
  </main>;
  return <main><FinanceNav /><header className="dashboard-head"><a className="back-link" href="/">← Finance</a><p className="kicker">Public product tour · fictional data</p><h1>A financial picture that explains itself.</h1><p className="lede">This preview uses a made-up household. It shows the shape of the private dashboard without exposing Gordon's balances, accounts, transactions, or financial assumptions.</p></header>
    <div className="demo-banner"><strong>Demo household</strong><span>Illustrative values only · no live personal data</span><a href="/dashboard">Owner sign in →</a></div>
    <section className="dashboard-metrics" aria-label="Example financial overview">
      {[["Net worth", "$684,300", "+$62,400 over 12 months"], ["Cash", "$41,800", "6.1 months of current spending"], ["Investments", "$327,600", "78% tax-advantaged"], ["Debt", "$286,900", "Mostly fixed-rate mortgage"], ["Observed income", "$142,700", "Trailing 12 months"], ["True monthly cost", "$8,940", "Includes annual costs spread monthly"]].map(([label, value, note]) => <article className="dashboard-metric demo-metric" key={label}><div><p>{label}</p><span className="freshness">example</span></div><strong>{value}</strong><p>{note}</p></article>)}
    </section>
    <section className="dashboard-panel demo-strength-story"><div className="section-title"><div><p className="eyebrow">The first question</p><h2>Is this household stronger than one year ago?</h2></div><strong className="positive">+$62,400</strong></div><div className="demo-strength-layout"><div><LineChart rows={demoNetWorthHistory} x={(row) => row.index} label="Fictional household net worth over twelve months" series={[{ label: "Net worth", color: "#83d7ad", value: (row) => row.value }]} /></div><div className="demo-driver-list"><p><span>Income minus spending</span><strong>+$48,900</strong><small>Transfers and refunds excluded</small></p><p><span>Investments vs. contributions</span><strong>+$17,300</strong><small>Market movement after deposits</small></p><p><span>Home, vehicles, and liabilities</span><strong>−$3,800</strong><small>Valuation and balance change</small></p></div></div><p className="visual-note">The private dashboard reconciles the opening value, known cash generation, market and asset changes, and today’s value. If history is incomplete, it shows coverage instead of inventing a result.</p></section>
    <div className="dashboard-columns demo-product-widgets"><section className="dashboard-panel"><div className="section-title"><div><p className="eyebrow">Investments</p><h2>Was stock-picking worth it?</h2></div><strong>$327,600</strong></div><div className="demo-comparison-list"><article><span>Broad-market funds</span><strong>+$8,420 vs. S&amp;P 500</strong><i style={{ width: "72%" }} /><small>Same dollars invested on covered lot dates</small></article><article><span>Employer retirement plan</span><strong>−$1,180 vs. S&amp;P 500</strong><i className="behind" style={{ width: "46%" }} /><small>11 of 13 lots have basis and dates</small></article><article><span>Single-stock positions</span><strong>Comparison collecting</strong><i style={{ width: "28%" }} /><small>Two acquisition dates still missing</small></article></div></section><section className="dashboard-panel"><div className="section-title"><div><p className="eyebrow">Retirement readiness</p><h2>Can the bridge reach 59½?</h2></div><strong>82%</strong></div><div className="demo-readiness"><i style={{ width: "82%" }} /><p><span>Accessible bridge</span><strong>$412,000 of $503,000</strong></p><p><span>Modeled first shortfall</span><strong>Age 56</strong></p><p><span>Most useful lever</span><strong>Two more working years</strong></p></div></section></div>
    <div className="dashboard-columns demo-columns"><section className="dashboard-panel"><div className="section-title"><div><p className="eyebrow">Where the money went</p><h2>Observed spending</h2></div><span>Last 12 months</span></div><p className="quiet">Transfers, refunds, card payments, and investment purchases are separated from consumption so they do not inflate spending.</p><div className="demo-bars">{demoCategories.map(([label, value, percent]) => <div key={label}><span>{label}</span><div><i style={{ width: `${value / max * 100}%` }} /></div><strong>{money.format(value)}<small>{percent}</small></strong></div>)}</div></section>
      <section className="dashboard-panel"><div className="section-title"><div><p className="eyebrow">What needs attention</p><h2>Decision queue</h2></div><span>3 items</span></div><div className="decision-list"><article><span>Recurring cost</span><strong>Insurance renewal is 18% higher</strong><p>Review the new premium before next month's annual payment.</p></article><article><span>Cash flow</span><strong>Quarterly tax payment due</strong><p>$4,200 is already included in the true monthly cost.</p></article><article><span>Data quality</span><strong>One transaction needs a category</strong><p>The dashboard shows uncertainty instead of silently guessing.</p></article></div></section></div>
    <div className="dashboard-columns demo-product-widgets"><section className="dashboard-panel"><div className="section-title"><div><p className="eyebrow">Home + vehicles</p><h2>Large assets still cost money</h2></div><strong>$382,700</strong></div><div className="demo-asset-list"><p><span>Home equity</span><strong>$341,000</strong><small>$18,600 annual tax, insurance, and maintenance</small></p><p><span>Two vehicles</span><strong>$41,700</strong><small>$8,900 annual ownership cost · no loans</small></p></div></section><section className="dashboard-panel"><div className="section-title"><div><p className="eyebrow">Data confidence</p><h2>What the picture still cannot know</h2></div><strong>91%</strong></div><div className="demo-quality"><p><i className="complete" />12-month transaction coverage</p><p><i className="complete" />Household assets refreshed</p><p><i />Two investment lots lack basis</p><p><i />Paystub detail not connected</p></div></section></div>
    <section className="liquidity-bridge demo-widget" aria-label="Example liquid cash position"><div><p className="eyebrow">Liquid cash position</p><h2>$58,200</h2><p>Bank cash plus taxable brokerage cash equivalents.</p></div><dl><div><dt>Bank cash</dt><dd>$41,800</dd></div><div><dt>Brokerage cash equivalents</dt><dd>$16,400</dd></div></dl><p className="visual-note">Already included in investments and net worth; shown here only to clarify accessible cash.</p></section>
    <section className="dashboard-panel net-worth-projection demo-widget"><div className="section-title"><div><p className="eyebrow">Looking forward</p><h2>What could the whole balance sheet become?</h2></div><strong>$1,128,600</strong></div><p className="quiet">A transparent 10-year scenario, not a prediction. It compounds investments, adds the fictional household’s chosen saving rate, and applies stated home and vehicle assumptions.</p><div className="projection-controls"><label><span>Years ahead</span><strong>10</strong><input type="range" min="1" max="30" value="10" readOnly /></label><label><span>Annual return</span><strong>6.5%</strong><input type="range" min="0" max="12" value="6.5" step=".5" readOnly /></label><label><span>Annual amount invested</span><strong>$31,200</strong><input type="range" min="0" max="75000" value="31200" readOnly /></label></div><LineChart rows={demoNetWorthHistory} x={(row) => row.index} label="Example ten-year household net worth projection" series={[{ label: "Illustrative net worth", color: "#83d7ad", value: (row) => row.value }]} /><div className="projection-summary"><span>$684,300 today</span><i /><span>$1,128,600 in 10 years</span></div></section>
    <div className="dashboard-columns demo-columns"><section className="dashboard-panel"><div className="section-title"><div><p className="eyebrow">Spending &amp; saving</p><h2>What was spent and saved</h2></div><span>Trailing 12 months</span></div><div className="flow-compare"><article><span>Amount saved</span><strong>$48,900</strong><p>Income left after personal spending</p><small>Transfers, card payments, and brokerage funding are excluded from consumption.</small></article><article><span>Amount spent</span><strong>$93,800</strong><p>$95,100 raw purchases · $1,300 adjustments</p><small>Observed spending, not a budget target.</small></article></div><h3>Spending by category</h3><div className="category-list">{demoCategories.map(([label, value]) => <div key={label}><span>{label}</span><strong>{money.format(value * 12)}</strong></div>)}</div></section><section className="dashboard-panel"><div className="section-title"><div><p className="eyebrow">Review, not a verdict</p><h2>Unusual activity</h2></div><span>2 signals</span></div><p className="quiet">Example review prompts are based on confirmed history; they are never presented as a verdict.</p><div className="signal-list"><article><div><span>Recurring increase</span><time>Sep 18</time></div><h3>Home insurance renewed higher</h3><strong>$1,860</strong><p>Fictional annual premium is 16% above its prior comparable payment.</p><small>high confidence · same merchant and cadence</small></article><article><div><span>Category change</span><time>Sep 09</time></div><h3>Dining trend increased</h3><strong>$420</strong><p>Fictional monthly spend is above the household’s prior observed range.</p><small>medium confidence · review before acting</small></article></div></section></div>
    <section className="dashboard-panel recommendation-panel demo-widget"><div className="section-title"><div><p className="eyebrow">Potential savings</p><h2>Where a review may save money</h2></div><span>2 open</span></div><p className="quiet">Illustrative recommendations use the same evidence-first structure as the private dashboard.</p><div className="recommendation-list"><article><div><span>Recurring cost</span><strong>Compare the home-insurance renewal</strong></div><p>The fictional policy increased 16%; the preview does not assume that the increase is avoidable.</p><dl><div><dt>Potential annual impact</dt><dd>$260</dd></div><div><dt>Confidence</dt><dd>medium</dd></div></dl></article><article><div><span>Fee</span><strong>Review managed-account fee</strong></div><p>A fictional recurring fee is visible in account activity and merits a comparison.</p><dl><div><dt>Potential annual impact</dt><dd>$480</dd></div><div><dt>Confidence</dt><dd>high</dd></div></dl></article></div></section>
    <section className="metric-catalog demo-widget"><div className="section-title"><div><p className="eyebrow">Metric registry</p><h2>Definitions and data gaps</h2></div><span>6 example metrics</span></div><div className="table-wrap"><table><thead><tr><th>Metric</th><th>Value</th><th>Freshness</th><th>Confidence</th><th>Known gaps</th></tr></thead><tbody><tr><td><strong>Net worth</strong><small>Assets less registered asset-backed debt</small></td><td>$684,300</td><td>example</td><td>high</td><td>Property estimate is modeled</td></tr><tr><td><strong>Amount saved</strong><small>Income minus adjusted personal spending</small></td><td>$48,900</td><td>example</td><td>medium</td><td>Paystub source not connected</td></tr><tr><td><strong>Investments</strong><small>Current fictional household investments</small></td><td>$327,600</td><td>example</td><td>high</td><td>Two tax lots incomplete</td></tr></tbody></table></div></section>
    <section className="preview-principles"><article><strong>Same dashboard shape</strong><p>This public view uses the same widget hierarchy with deliberately fictional numbers.</p></article><article><strong>Definitions attached</strong><p>Every headline says what it includes, excludes, and how fresh it is.</p></article><article><strong>Private by default</strong><p>The real dashboard sits behind the shared household passkey login.</p></article></section>
  </main>;
}
