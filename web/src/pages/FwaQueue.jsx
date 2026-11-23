import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { getJson, postJson } from "../api.js";
import { useStore } from "../store.jsx";
import { useToast } from "../components/toast.jsx";
import { PageIntro, Card, DataBar, DataTable, DecisionBadge, ErrorState, Pager, SkeletonPage, StatTile } from "../components/ui.jsx";
import { ACCENT, CHART, fmtMoney } from "../theme.js";

const tooltipStyle = {
  backgroundColor: CHART.tooltipBg, border: `1px solid ${CHART.border}`,
  borderRadius: "0.5rem", fontSize: "0.75rem",
};

export default function FwaQueue() {
  const { dataset, keysDir } = useStore();
  const toast = useToast();
  const [signals, setSignals] = useState(null);
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);
  const [note, setNote] = useState(null);
  const [noteBusy, setNoteBusy] = useState(false);
  const [checked, setChecked] = useState([]);
  const [view, setView] = useState("flat");
  const [caseDetail, setCaseDetail] = useState(null);
  const pageSize = 50;

  useEffect(() => {
    let alive = true;   // ignore stale responses (e.g. old view still in flight)
    setData(null); setError(null); setSelected(null); setNote(null); setChecked([]); setCaseDetail(null);
    getJson("/api/fwa", { dataset, keys_dir: keysDir, signals, page, page_size: pageSize,
                          group: view === "grouped" ? 1 : 0 })
      .then((r) => { if (alive) { setData(r); if (!signals) setSignals(r.signals); } })
      .catch((e) => { if (alive) setError(e); });
    return () => { alive = false; };
  }, [dataset, keysDir, page, view]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleSignal = (s) => {
    const next = signals.includes(s) ? signals.filter((x) => x !== s) : [...signals, s];
    setSignals(next); setPage(1);
  };

  const switchView = (v) => {
    if (v !== view) { setView(v); setPage(1); }
  };

  const setReview = async (claimId, status) => {
    // optimistic update, then persist
    setData((d) => ({ ...d, rows: d.rows.map((r) => (r.claim_id === claimId ? { ...r, review: status } : r)) }));
    setCaseDetail((cd) => (cd ? { ...cd, rows: cd.rows.map((r) => (r.claim_id === claimId ? { ...r, review: status } : r)) } : cd));
    try {
      await postJson("/api/fwa/review", { dataset, reviews: [{ claim_id: claimId, status }] });
      toast(`Review saved as "${status}"`);
    } catch (e) {
      setError(e);
    }
  };

  const bulkReview = async (status) => {
    // one call for everything selected; optimistic like setReview
    const ids = new Set(checked);
    setData((d) => ({ ...d, rows: d.rows.map((r) => (ids.has(r.claim_id) ? { ...r, review: status } : r)) }));
    setCaseDetail((cd) => (cd ? { ...cd, rows: cd.rows.map((r) => (ids.has(r.claim_id) ? { ...r, review: status } : r)) } : cd));
    try {
      await postJson("/api/fwa/review", { dataset, reviews: checked.map((claim_id) => ({ claim_id, status })) });
      toast(`${checked.length} review(s) saved as "${status}"`);
    } catch (e) {
      setError(e);
    }
    setChecked([]);
  };

  const openCase = async (c) => {
    setCaseDetail(null);
    setSelected(c.claim_ids?.[0] ?? null);
    try {
      const r = await getJson("/api/fwa/case", { dataset, provider_id: c.provider_id,
                                                member_id: c.member_id, rule_id: c.rule_id });
      setCaseDetail({ ...c, rows: r.rows });
    } catch (e) {
      setError(e);
    }
  };

  const writeNote = async () => {
    setNoteBusy(true); setNote(null);
    try {
      setNote(await postJson("/api/ai/case-note", { dataset, claim_id: selected }));
      toast("Case note ready");
    } catch (e) {
      setNote({ text: String(e.message), source: "error", warnings: [] });
    } finally {
      setNoteBusy(false);
    }
  };

  if (error) return <ErrorState error={error} />;
  if (!data) return <SkeletonPage tiles={4} />;
  const { rows, total, workload, billed, signals: allSignals } = data;
  const grouped = view === "grouped";
  const worked = rows.filter((r) => r.review !== "pending").length;

  // Guided demo picks: jump straight to an interesting case or pattern.
  const DEMO = [
    { label: "🎯 Show me the worst case", go: () => setSelected(rows[0]?.claim_id ?? null), show: () => true },
    { label: "🏥 Facility outliers", go: () => { setSignals(["FWA-02"]); setPage(1); }, show: () => allSignals.includes("FWA-02") },
    { label: "👯 Near-duplicate pairs", go: () => { setSignals(["FWA-03"]); setPage(1); }, show: () => allSignals.includes("FWA-03") },
    { label: "📅 Repeat-day billing", go: () => { setSignals(["FWA-01"]); setPage(1); }, show: () => allSignals.includes("FWA-01") },
  ];

  return (
    <div className="space-y-4">
      <PageIntro>Flagged claims ranked for review — a flag is a prompt for a person, never a finding of fraud.</PageIntro>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5 items-start">
        {/* left: controls + queue */}
        <div className="xl:col-span-2 space-y-4">
      <Card caption={grouped
        ? "Flagged claims grouped into cases by provider, member and pattern. A flag is a prompt for review, not a finding of fraud."
        : "Claims ranked by FWA score. A flag is a prompt for review, not a finding of fraud."}>
        <div className="flex flex-wrap items-center gap-1.5 mb-3">
          <div className="flex rounded-lg border border-zinc-700 overflow-hidden mr-2">
            <button onClick={() => switchView("flat")}
                    className={`px-3 py-1 text-xs transition-colors ${!grouped ? "bg-sky-500/20 text-sky-300" : "bg-zinc-900 text-zinc-400 hover:text-zinc-200"}`}>
              Flat queue
            </button>
            <button onClick={() => switchView("grouped")}
                    className={`px-3 py-1 text-xs transition-colors ${grouped ? "bg-sky-500/20 text-sky-300" : "bg-zinc-900 text-zinc-400 hover:text-zinc-200"}`}>
              Grouped cases
            </button>
          </div>
          {DEMO.filter((d) => d.show()).map((d) => (
            <button key={d.label} onClick={d.go}
                    className="rounded-full px-2.5 py-1 text-xs border border-sky-900/50 bg-sky-500/10 text-sky-300 hover:bg-sky-500/20 transition-colors">
              {d.label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-1.5 mb-3">
          {allSignals.map((s) => (
            <button key={s} onClick={() => toggleSignal(s)}
                    className={`rounded-full px-2.5 py-1 text-xs border num transition-colors ${
                      signals?.includes(s)
                        ? "bg-sky-500/20 text-sky-300 border-sky-900/60"
                        : "bg-zinc-900 text-zinc-500 border-zinc-800 hover:text-zinc-300"
                    }`}>
              {s}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-4 gap-3">
          <StatTile label={grouped ? "Cases in queue" : "Claims in queue"} value={total.toLocaleString()} />
          <StatTile label="Billed value in queue" value={fmtMoney(billed)} />
          <StatTile label="Worked (visible page)" value={`${worked} / ${rows.length}`} />
          <StatTile label="Confirmed on page" value={String(rows.filter((r) => r.review === "confirmed").length)}
                    color="#ef4444" />
        </div>
      </Card>

      {workload && !grouped && (
        <Card title="Reviewer workload"
              caption={workload.k80
                ? `Open ${workload.k80.toLocaleString()} claims (highest score first) to catch 80% of the ${workload.target_count.toLocaleString()} seeded easy FWA claims.`
                : "Fewer than 80% of seeded FWA claims are in the queue."}>
          {workload.k80 && total > 0 && (
            <div className="mb-3">
              <div className="flex justify-between text-xs text-zinc-500 mb-1">
                <span>Share of the queue a reviewer must open</span>
                <span className="num">{workload.k80.toLocaleString()} of {total.toLocaleString()} ({Math.round(workload.k80 / total * 100)}%)</span>
              </div>
              <div className="h-2.5 rounded-full overflow-hidden" style={{ backgroundColor: "rgba(13, 148, 136, 0.15)" }}>
                <div className="h-full rounded-full" style={{ width: `${Math.min(100, workload.k80 / total * 100)}%`, backgroundColor: "#0d9488" }} />
              </div>
            </div>
          )}
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={workload.curve} margin={{ left: 0, right: 12, top: 4 }}>
              <CartesianGrid stroke={CHART.grid} vertical={false} />
              <XAxis dataKey="rank" tick={{ fill: CHART.tick, fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis domain={[0, 100]} tick={{ fill: CHART.tick, fontSize: 10 }} axisLine={false} tickLine={false}
                     tickFormatter={(v) => `${v}%`} width={40} />
              <Tooltip contentStyle={tooltipStyle} formatter={(v) => `${Number(v).toFixed(0)}% of seeded FWA caught`} />
              <Line type="monotone" dataKey="pct" stroke={ACCENT} strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      )}

      <div>
        <div className="flex items-center justify-between mb-2">
          <div className="text-sm font-medium">
            {grouped ? "Cases" : "Queue"}{" "}
            <span className="text-zinc-500 font-normal">(set the Review column, then export)</span>
          </div>
          <a href={`/api/fwa/export.csv?dataset=${encodeURIComponent(dataset)}&group=${grouped ? 1 : 0}`}
             className="text-xs bg-sky-500/20 text-sky-300 border border-sky-900/60 rounded-md px-3 py-1.5 hover:bg-sky-500/30">
            Export CSV ⬇
          </a>
        </div>
        {checked.length > 0 && (
          <div className="flex items-center gap-2 mb-2 text-xs">
            <span className="text-zinc-400">{checked.length} selected</span>
            <button onClick={() => bulkReview("confirmed")}
                    className="bg-red-500/10 text-red-300 border border-red-900 rounded-md px-2.5 py-1 hover:bg-red-500/20">
              Confirm selected
            </button>
            <button onClick={() => bulkReview("dismissed")}
                    className="bg-zinc-900 text-zinc-300 border border-zinc-700 rounded-md px-2.5 py-1 hover:bg-zinc-800">
              Dismiss selected
            </button>
            <button onClick={() => setChecked([])}
                    className="text-zinc-500 hover:text-zinc-300">
              Clear
            </button>
          </div>
        )}
        <DataTable
          cols={grouped ? [
            { key: "case_id", label: "Case (provider · member · pattern)", render: (r) => (
                <span className="num text-zinc-300">{r.provider_id} · {r.member_id} · </span>
              ) },
            { key: "rule_id", label: "Pattern", render: (r) => <span className="text-sky-300 num">{r.rule_id}</span> },
            { key: "claims", label: "Claims", numeric: true },
            { key: "billed", label: "Billed", numeric: true, render: (r) => fmtMoney(r.billed) },
            { key: "max_fwa_score", label: "Max score", numeric: true, render: (r) => (r.max_fwa_score ?? 0).toFixed(2) },
            { key: "confirmed", label: "Confirmed", numeric: true, render: (r) => (
                <span className={r.confirmed ? "text-red-300 num" : "text-zinc-500 num"}>{r.confirmed}</span>
              ) },
            { key: "pending", label: "Pending", numeric: true },
            { key: "open", label: "", render: (r) => (
                <button className="text-sky-300 hover:underline" onClick={() => openCase(r)}>
                  {caseDetail?.case_id === r.case_id ? "Open ✓" : "Open"}
                </button>
              ) },
          ] : [
            { key: "sel", label: "", render: (r) => (
                <input type="checkbox" checked={checked.includes(r.claim_id)}
                       onChange={() => setChecked((c) => c.includes(r.claim_id)
                         ? c.filter((x) => x !== r.claim_id) : [...c, r.claim_id])} />
              ) },
            { key: "rank", label: "#", numeric: true },
            { key: "claim_id", label: "Claim", render: (r) => (
                <button className="text-sky-300 num hover:underline" onClick={() => setSelected(r.claim_id)}>
                  {r.claim_id}
                </button>
              ) },
            { key: "member_id", label: "Member", render: (r) => <span className="num text-zinc-500">{r.member_id}</span> },
            { key: "provider_id", label: "Provider", render: (r) => <span className="num">{r.provider_id}</span> },
            { key: "decision", label: "Decision", render: (r) => <DecisionBadge decision={r.decision} /> },
            { key: "fwa_score", label: "Score", numeric: true, render: (r) => (
                <DataBar value={r.fwa_score} display={(r.fwa_score ?? 0).toFixed(2)}
                         max={Math.max(...rows.map((x) => x.fwa_score), 1)} color="#d97706" />
              ) },
            { key: "allowed_amt", label: "Billed", numeric: true, render: (r) => (
                <DataBar value={r.allowed_amt} display={fmtMoney(r.allowed_amt)}
                         max={Math.max(...rows.map((x) => x.allowed_amt), 1)} color="#14b8a6" />
              ) },
            { key: "fwa_reasons", label: "Reasons", render: (r) => <span className="text-zinc-400">{r.fwa_reasons}</span> },
            { key: "review", label: "Review", render: (r) => (
                <select value={r.review} onChange={(e) => setReview(r.claim_id, e.target.value)}
                        className={`bg-zinc-900 border rounded-md px-1.5 py-0.5 text-xs ${
                          r.review === "confirmed" ? "border-red-900 text-red-300"
                          : r.review === "dismissed" ? "border-zinc-600 text-zinc-500"
                          : "border-zinc-700 text-zinc-400"
                        }`}>
                  <option value="pending">pending</option>
                  <option value="confirmed">confirmed</option>
                  <option value="dismissed">dismissed</option>
                </select>
              ) },
          ]}
          rows={rows}
          keyField={grouped ? "case_id" : "claim_id"}
          maxHeight="34rem"
        />
        <Pager page={page} pageSize={pageSize} total={total} onPage={setPage} />
      </div>
        </div>

        {/* right: live context pane */}
        <div className="xl:sticky xl:top-36 space-y-4">
      {grouped && caseDetail && (
        <Card title={`Case · ${caseDetail.provider_id} · ${caseDetail.member_id} · ${caseDetail.rule_id}`}
              caption="All claims in this case. Set the Review column per claim — the case counts above update on reload.">
          <DataTable
            cols={[
              { key: "claim_id", label: "Claim", render: (r) => (
                  <button className="text-sky-300 num hover:underline" onClick={() => setSelected(r.claim_id)}>
                    {r.claim_id}
                  </button>
                ) },
              { key: "decision", label: "Decision", render: (r) => <DecisionBadge decision={r.decision} /> },
              { key: "fwa_score", label: "Score", numeric: true, render: (r) => (r.fwa_score ?? 0).toFixed(2) },
              { key: "allowed_amt", label: "Billed", numeric: true, render: (r) => fmtMoney(r.allowed_amt) },
              { key: "fwa_reasons", label: "Reasons", render: (r) => <span className="text-zinc-400">{r.fwa_reasons}</span> },
              { key: "review", label: "Review", render: (r) => (
                  <select value={r.review} onChange={(e) => setReview(r.claim_id, e.target.value)}
                          className={`bg-zinc-900 border rounded-md px-1.5 py-0.5 text-xs ${
                            r.review === "confirmed" ? "border-red-900 text-red-300"
                            : r.review === "dismissed" ? "border-zinc-600 text-zinc-500"
                            : "border-zinc-700 text-zinc-400"
                          }`}>
                    <option value="pending">pending</option>
                    <option value="confirmed">confirmed</option>
                    <option value="dismissed">dismissed</option>
                  </select>
                ) },
            ]}
            rows={caseDetail.rows}
            keyField="claim_id"
            maxHeight="18rem"
          />
        </Card>
      )}

      {selected && (
        <Card title={`✨ AI case note · ${selected}`} glow>
          <div className="flex items-center gap-3 mb-3">
            <button onClick={writeNote} disabled={noteBusy}
                    className="btn">
              {noteBusy ? "Writing…" : "Write case note"}
            </button>
            <span className="text-xs text-zinc-500">Why flagged, what to check, possible innocent explanations.</span>
          </div>
          {note && (
            <div>
              <div className="text-sm leading-relaxed whitespace-pre-wrap">
                <Markdown>{note.text}</Markdown>
              </div>
              <div className="text-xs text-zinc-500 mt-2">Source: {note.source}. A flag is a prompt for review, not a finding.</div>
              {note.warnings?.map((w, i) => <div key={i} className="text-amber-300 text-xs mt-1">⚠ {w}</div>)}
            </div>
          )}
        </Card>
      )}

      {!selected && !caseDetail && (
        <Card className="border-dashed">
          <div className="text-xs text-zinc-500 py-6 text-center">
            Select a claim on the left — the AI case note and case context open here, without leaving the page.
          </div>
        </Card>
      )}
        </div>
      </div>
    </div>
  );
}
