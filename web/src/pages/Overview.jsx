import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { getJson } from "../api.js";
import { useStore } from "../store.jsx";
import { Card, DataTable, ErrorState, SkeletonPage } from "../components/ui.jsx";
import { IconFileText, IconFlag, IconArrows, IconActivity } from "../components/icons.jsx";
import { GLOW, ACCENT, CHART, DECISIONS, DECISION_COLORS, fmtMoney, fmtCompact } from "../theme.js";

const tooltipStyle = {
  backgroundColor: CHART.tooltipBg,
  border: `1px solid ${CHART.border}`,
  borderRadius: "0.5rem",
  fontSize: "0.75rem",
};

// Guided demo tour: one button per story beat, in speaking order.
const TOUR = [
  { n: 1, to: "/claims", label: "Claim review", desc: "See why a claim was refused, clause by clause",
    icon: <IconFileText size={15} /> },
  { n: 2, to: "/whatif", label: "What-if simulator", desc: "Test a rule change with a cost delta in seconds",
    icon: <IconArrows size={15} /> },
  { n: 3, to: "/fwa", label: "FWA queue", desc: "Work the ranked review queue with AI case notes",
    icon: <IconFlag size={15} /> },
];

export default function Overview() {
  const { dataset } = useStore();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setData(null); setError(null);
    getJson("/api/overview", { dataset }).then(setData).catch(setError);
  }, [dataset]);

  if (error) return <ErrorState error={error} />;
  if (!data) return <SkeletonPage tiles={5} />;
  const { counts, money, money_by_decision, top_rules, summary } = data;

  const countRows = DECISIONS.map((d) => ({ decision: d, claims: counts[d] ?? 0 }));
  const flow = [
    { label: "Billed", value: money.allowed, color: "#0d9488" },
    { label: "Payable", value: money.payable, color: "#0f766e" },
    { label: "Plan pays", value: money.plan_paid, color: "#047857" },
    { label: "Held for review", value: money.held, color: "#d97706" },
  ];

  return (
    <div className="space-y-5">
      {/* hero: full-width immersive panel with the big numbers */}
      <div data-tour="hero" className="relative overflow-hidden rounded-3xl border border-zinc-800/70 bg-zinc-900 px-8 py-8"
           style={{ boxShadow: GLOW.sky }}>
        <div className="absolute inset-0 pointer-events-none"
             style={{ background: "linear-gradient(115deg, rgba(13,148,136,0.16), rgba(20,184,166,0.10) 40%, rgba(217,119,6,0.06) 78%, transparent)" }} />
        <div className="hero-shimmer" />
        <div className="relative">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <span className="icon-chip px-3 py-1 text-[11px] uppercase tracking-widest font-medium">
              ClaimLens · RD02 · RD03 · RD04
            </span>
            <span className="text-xs text-zinc-500 num glass rounded-full px-3 py-1">
              {dataset.replace("clean/", "")}
            </span>
          </div>

          <h2 className="text-4xl font-semibold tracking-tight leading-tight max-w-3xl">
            Every claim decision, <span className="hero-text">traceable to its rule</span>.
          </h2>
          <p className="text-sm text-zinc-500 mt-2 max-w-2xl">
            Simulate policy changes before they go live, catch clashing rules early, and focus
            reviewer time on the claims most worth checking. The rules engine decides — AI explains.
          </p>

          {/* huge headline numbers */}
          <div className="flex flex-wrap items-end gap-x-10 gap-y-4 mt-7">
            <div>
              <div className="text-5xl font-semibold num flex items-center gap-3">
                {(summary.claims ?? 0).toLocaleString()}
                <span className="opacity-40"><IconActivity size={22} /></span>
              </div>
              <div className="text-xs text-zinc-500 mt-1 uppercase tracking-wider">claims decided</div>
            </div>
            <div>
              <div className="text-5xl font-semibold num" style={{ color: "#14b8a6" }}>
                ${((money.allowed ?? 0) / 1e6).toFixed(1)}M
              </div>
              <div className="text-xs text-zinc-500 mt-1 uppercase tracking-wider">billed value</div>
            </div>
            {DECISIONS.map((d) => (
              <div key={d}>
                <div className="text-3xl font-semibold num" style={{ color: DECISION_COLORS[d] }}>
                  {(counts[d] ?? 0).toLocaleString()}
                </div>
                <div className="text-xs text-zinc-500 mt-1">
                  {d} <span className="num">· {summary.claims ? ((counts[d] ?? 0) / summary.claims * 100).toFixed(1) : 0}%</span>
                </div>
              </div>
            ))}
          </div>

          <div className="flex flex-wrap gap-2.5 mt-7">
            {TOUR.map((t) => (
              <button key={t.to} onClick={() => navigate(t.to)}
                      className="group flex items-center gap-2.5 rounded-lg border border-zinc-700/80 bg-zinc-900/70 px-3 py-2 text-left transition-all hover:border-sky-900/60 hover:bg-sky-500/5">
                <span className="icon-chip w-7 h-7 text-xs font-semibold">{t.icon}</span>
                <span>
                  <span className="block text-sm text-zinc-200 group-hover:text-sky-300 transition-colors">
                    <span className="text-zinc-500 num mr-1">{t.n}</span>{t.label}
                  </span>
                  <span className="block text-[11px] text-zinc-500">{t.desc}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* money flow strip */}
      <div data-tour="money" className="flex flex-wrap items-stretch gap-2">
        {flow.map((f, i) => (
          <div key={f.label} className="flex items-center gap-2 flex-1 min-w-[180px]">
            <div className="glass rounded-xl px-4 py-3 w-full transition-all duration-200 hover:-translate-y-px"
                 style={{ borderColor: `${f.color}30` }}>
              <div className="text-xs text-zinc-500">{f.label}</div>
              <div className="text-xl font-semibold num mt-0.5" style={{ color: f.color }}>
                {fmtMoney(f.value)}
              </div>
            </div>
            {i < flow.length - 1 && <span className="text-zinc-400 text-lg px-0.5 select-none">›</span>}
          </div>
        ))}
      </div>

      {/* bento: charts big, table narrow */}
      <div data-tour="charts" className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <Card title="Decisions"
              caption="Claims by decision (live rules). Click a bar to open those claims in Claim review."
              className="lg:col-span-2">
          {/* part-to-whole mix, stacked with 2px surface gaps */}
          <div className="mb-4">
            <div className="flex h-5 rounded-md overflow-hidden">
              {countRows.filter((r) => r.claims > 0).map((r, i) => (
                <div key={r.decision}
                     title={`${r.decision}: ${r.claims.toLocaleString()} (${((r.claims / (summary.claims || 1)) * 100).toFixed(1)}%)`}
                     style={{ width: `${(r.claims / (summary.claims || 1)) * 100}%`,
                              backgroundColor: DECISION_COLORS[r.decision],
                              borderLeft: i > 0 ? "2px solid #fff" : undefined,
                              cursor: "pointer" }}
                     onClick={() => navigate(`/claims?decision=${r.decision}`)} />
              ))}
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-[10px] text-zinc-500">
              {countRows.map((r) => (
                <span key={r.decision} className="inline-flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-sm" style={{ backgroundColor: DECISION_COLORS[r.decision] }} />
                  {r.decision} <span className="num">{((r.claims / (summary.claims || 1)) * 100).toFixed(1)}%</span>
                </span>
              ))}
            </div>
          </div>
          <ResponsiveContainer width="100%" height={230}>
            <BarChart data={countRows} layout="vertical" margin={{ left: 8, right: 40 }}>
              <CartesianGrid stroke={CHART.grid} horizontal={false} />
              <XAxis type="number" hide />
              <YAxis type="category" dataKey="decision" width={70} tick={{ fill: CHART.tick, fontSize: 11 }}
                     axisLine={false} tickLine={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "#0ea5e908" }} formatter={(v) => fmtCompact(v)} />
              <Bar dataKey="claims" barSize={22} radius={[0, 4, 4, 0]}
                   style={{ cursor: "pointer" }}
                   onClick={(bar) => {
                     const d = bar?.payload?.decision;
                     if (d) navigate(`/claims?decision=${d}`);
                   }}>
                {countRows.map((r) => (
                  <Cell key={r.decision} fill={DECISION_COLORS[r.decision]} />
                ))}
                <LabelList dataKey="claims" position="right"
                           content={(p) => (p.value === Math.max(...countRows.map((r) => r.claims))
                             ? <text x={p.x + p.width + 6} y={p.y + 14} fill="#5f837e" fontSize={11}>{Number(p.value).toLocaleString()}</text>
                             : null)} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>

        <Card title="Rules that fired" caption="Claims and hits per rule.">
          <DataTable
            cols={[
              { key: "rule_id", label: "Rule", render: (r) => <span className="text-sky-300 num">{r.rule_id}</span> },
              { key: "layer", label: "Layer" },
              { key: "claims", label: "Claims", numeric: true },
              { key: "hits", label: "Hits", numeric: true },
            ]}
            rows={top_rules}
            maxHeight="17rem"
          />
        </Card>

        <Card title="Where the billed money goes"
              caption="Billed vs payable by decision — denied and rejected claims keep most of their value unpaid."
              className="lg:col-span-3">
          <ResponsiveContainer width="100%" height={230}>
            <BarChart data={money_by_decision} layout="vertical" margin={{ left: 8, right: 16 }}>
              <CartesianGrid stroke={CHART.grid} horizontal={false} />
              <XAxis type="number" hide />
              <YAxis type="category" dataKey="decision" width={70} tick={{ fill: CHART.tick, fontSize: 11 }}
                     axisLine={false} tickLine={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "#0ea5e908" }} formatter={(v) => fmtCompact(v)} />
              <Bar dataKey="billed" name="Billed" fill={ACCENT} radius={[0, 4, 4, 0]} />
              <Bar dataKey="payable" name="Payable" fill="#94a3b8" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>
      </div>
    </div>
  );
}
