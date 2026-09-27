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

export function HousingVisuals({ years, initialCash, stage = 0 }: { years: HousingYear[]; initialCash: InitialCashAllocation; stage?: number }) {
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

const demoCategories = [
  ["Home + utilities", 3380, "34%"],
  ["Food + household", 1670, "17%"],
  ["Transportation", 920, "9%"],
  ["Family + education", 780, "8%"],
  ["Everything else", 1120, "11%"],
] as const;

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
    <div className="dashboard-columns demo-columns"><section className="dashboard-panel"><div className="section-title"><div><p className="eyebrow">Where the money went</p><h2>Observed spending</h2></div><span>Last 12 months</span></div><p className="quiet">Transfers, refunds, card payments, and investment purchases are separated from consumption so they do not inflate spending.</p><div className="demo-bars">{demoCategories.map(([label, value, percent]) => <div key={label}><span>{label}</span><div><i style={{ width: `${value / max * 100}%` }} /></div><strong>{money.format(value)}<small>{percent}</small></strong></div>)}</div></section>
      <section className="dashboard-panel"><div className="section-title"><div><p className="eyebrow">What needs attention</p><h2>Decision queue</h2></div><span>3 items</span></div><div className="decision-list"><article><span>Recurring cost</span><strong>Insurance renewal is 18% higher</strong><p>Review the new premium before next month's annual payment.</p></article><article><span>Cash flow</span><strong>Quarterly tax payment due</strong><p>$4,200 is already included in the true monthly cost.</p></article><article><span>Data quality</span><strong>One transaction needs a category</strong><p>The dashboard shows uncertainty instead of silently guessing.</p></article></div></section></div>
    <section className="preview-principles"><article><strong>Actuals first</strong><p>It reports what happened before suggesting what “should” happen.</p></article><article><strong>Definitions attached</strong><p>Every headline says what it includes, excludes, and how fresh it is.</p></article><article><strong>Private by default</strong><p>The real dashboard sits behind the shared household passkey login.</p></article></section>
  </main>;
}
