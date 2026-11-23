import { useEffect, useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { getJson } from "../api.js";
import { useStore } from "../store.jsx";
import { PageIntro, Card, DataTable, DecisionBadge, ErrorState, Loading, SkeletonPage, StatTile } from "../components/ui.jsx";
import { ACCENT, CHART, DECISION_COLORS, fmtMoney, fmtPct } from "../theme.js";

const tooltipStyle = {
  backgroundColor: CHART.tooltipBg, border: `1px solid ${CHART.border}`,
  borderRadius: "0.5rem", fontSize: "0.75rem",
};
const OV_CODES = ["99212", "99213", "99214", "99215"];
const OV_COLORS = { 99212: "#38bdf8", 99213: "#0ea5e9", 99214: "#0284c7", 99215: "#075985" };

export default function ProviderTriage() {
  const { dataset } = useStore();
  const [list, setList] = useState(null);
  const [pick, setPick] = useState(null);
  const [q, setQ] = useState("");
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setList(null); setPick(null); setDetail(null); setError(null);
    getJson("/api/providers", { dataset, page_size: 200 }).then(setList).catch(setError);
  }, [dataset]);

  useEffect(() => {
    if (!pick) return;
    setDetail(null);
    getJson(`/api/providers/${pick}`, { dataset }).then(setDetail).catch(setError);
  }, [pick, dataset]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) return <ErrorState error={error} />;
  if (!list) return <SkeletonPage tiles={4} />;

  const filtered = list.rows.filter((r) => r.provider_id.toLowerCase().includes(q.toLowerCase()));

  return (
    <div className="space-y-4">
      <PageIntro>Peer comparison of billing behaviour across facilities — case mix matters, read the ratios with care.</PageIntro>
      <Card caption="Claims and FWA flags grouped by provider. Peer comparison is exploratory — a signal to review, not an accusation.">
        <div className="grid grid-cols-4 gap-3">
          <div className="col-span-2">
            <label className="text-xs text-zinc-500 block mb-1">Provider (searchable; sorted by flags)</label>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter provider ID…"
                   className="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1.5 text-sm text-zinc-200 num" />
            <div className="mt-2 max-h-40 overflow-auto rounded-lg border border-zinc-800">
              {filtered.map((r) => (
                <button key={r.provider_id} onClick={() => setPick(r.provider_id)}
                        className={`w-full text-left px-3 py-1.5 text-xs num hover:bg-zinc-800/60 ${
                          pick === r.provider_id ? "bg-sky-500/10 text-sky-300" : "text-zinc-300"
                        }`}>
                  {r.provider_id} · {r.flagged} flagged · {r.claims.toLocaleString()} claims
                </button>
              ))}
              {!filtered.length && <div className="px-3 py-2 text-xs text-zinc-500">No providers match.</div>}
            </div>
          </div>
        </div>
      </Card>

      {!pick && <Card><div className="text-zinc-500 text-sm py-6 text-center">Pick a provider to see its triage card.</div></Card>}

      {pick && !detail && <Loading text="Loading provider…" />}
      {pick && detail && (
        <>
          <div className="grid grid-cols-6 gap-3">
            <StatTile label="Claims" value={detail.metrics.claims.toLocaleString()} />
            <StatTile label="Members" value={detail.metrics.members.toLocaleString()} />
            <StatTile label="FWA-flagged" value={detail.metrics.flagged.toLocaleString()}
                      sub={fmtPct(detail.metrics.flagged_share)} color="#ef4444" />
            <StatTile label="Billed" value={fmtMoney(detail.metrics.billed)} />
            <StatTile label="Plan pays" value={fmtMoney(detail.metrics.plan_paid)} />
            <StatTile label="Flagged share" value={fmtPct(detail.metrics.flagged_share)} />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card title="Top codes vs peers"
                  caption="Services per patient vs the peer median for the same code. ⚠ = 4× or more the median — an exploratory signal, not a finding.">
              <DataTable
                cols={[
                  { key: "code", label: "Code", render: (r) => <span className="num">{r.code}</span> },
                  { key: "count", label: "Services", numeric: true },
                  { key: "patients", label: "Patients", numeric: true },
                  { key: "rate", label: "Per patient", numeric: true, render: (r) => Number(r.rate).toFixed(2) },
                  { key: "peer_median", label: "Peer median", numeric: true, render: (r) => Number(r.peer_median ?? 0).toFixed(2) },
                  { key: "ratio", label: "Ratio", numeric: true, render: (r) => Number(r.ratio).toFixed(1) },
                  { key: "warning", label: "", render: (r) => (r.warning ? "⚠️" : "") },
                ]}
                rows={detail.peer_codes}
                maxHeight="16rem"
                empty="No code-level data for this provider."
              />
            </Card>

            <div className="space-y-4">
              <Card title="Office-visit mix by year" caption="99212–99215: a shift toward higher-level codes over time is an upcoding signal.">
                {detail.office_visit_mix.length ? (
                  <ResponsiveContainer width="100%" height={170}>
                    <BarChart data={detail.office_visit_mix} margin={{ left: 0, right: 8, top: 4 }}>
                      <CartesianGrid stroke={CHART.grid} vertical={false} />
                      <XAxis dataKey="year" tick={{ fill: CHART.tick, fontSize: 10 }} axisLine={false} tickLine={false} />
                      <YAxis tick={{ fill: CHART.tick, fontSize: 10 }} axisLine={false} tickLine={false} width={32} />
                      <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "#ffffff08" }} />
                      {OV_CODES.map((c) => (
                        <Bar key={c} dataKey={c} stackId="ov" fill={OV_COLORS[c]} />
                      ))}
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="text-zinc-500 text-xs py-6 text-center">No office-visit codes for this provider.</div>
                )}
              </Card>

              <Card title="FWA flags by month"
                    caption="A rising line means the provider's flag rate is accelerating — the shape matters more than any single month.">
                {detail.flags_by_month.length ? (
                  <ResponsiveContainer width="100%" height={140}>
                    <AreaChart data={detail.flags_by_month} margin={{ left: 0, right: 8, top: 4 }}>
                      <defs>
                        <linearGradient id="flagFill" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor={ACCENT} stopOpacity={0.22} />
                          <stop offset="100%" stopColor={ACCENT} stopOpacity={0.04} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid stroke={CHART.grid} vertical={false} />
                      <XAxis dataKey="month" tick={{ fill: CHART.tick, fontSize: 9 }} axisLine={false} tickLine={false} />
                      <YAxis tick={{ fill: CHART.tick, fontSize: 10 }} axisLine={false} tickLine={false} width={28} allowDecimals={false} />
                      <Tooltip contentStyle={tooltipStyle} />
                      <Area type="monotone" dataKey="count" stroke={ACCENT} strokeWidth={2} fill="url(#flagFill)" />
                    </AreaChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="text-zinc-500 text-xs py-4 text-center">No flagged claims.</div>
                )}
              </Card>
            </div>
          </div>

          <Card title={`Flagged claims (${detail.flagged_claims.length.toLocaleString()})`}>
            <DataTable
              cols={[
                { key: "claim_id", label: "Claim", render: (r) => <span className="num text-sky-300">{r.claim_id}</span> },
                { key: "member_id", label: "Member", render: (r) => <span className="num text-zinc-500">{r.member_id}</span> },
                { key: "from_dt", label: "Date", numeric: true },
                { key: "decision", label: "Decision", render: (r) => <DecisionBadge decision={r.decision} /> },
                { key: "fwa_score", label: "Score", numeric: true, render: (r) => Number(r.fwa_score).toFixed(2) },
                { key: "fwa_reasons", label: "Reasons", render: (r) => <span className="text-zinc-400">{r.fwa_reasons}</span> },
              ]}
              rows={detail.flagged_claims}
              maxHeight="18rem"
              empty="No flagged claims for this provider."
            />
          </Card>
        </>
      )}
    </div>
  );
}
