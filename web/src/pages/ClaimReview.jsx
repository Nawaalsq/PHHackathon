import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { getJson } from "../api.js";
import { useStore } from "../store.jsx";
import { PageIntro, Card, DataBar, DataTable, DecisionBadge, ErrorState, Pager, SkeletonPage, StatTile } from "../components/ui.jsx";
import { fmtMoney, fmtPct } from "../theme.js";
import ClaimDetail from "./ClaimDetail.jsx";

export default function ClaimReview() {
  const { dataset } = useStore();
  const [searchParams] = useSearchParams();
  const [decision, setDecision] = useState("");
  const [rule, setRule] = useState("");
  const [q, setQ] = useState("");
  const [firedRules, setFiredRules] = useState([]);
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);
  const pageSize = 25;

  // drill-down: ?decision=paid (Overview chart) and ?q=<claim_id> (boundary cases)
  useEffect(() => {
    const d = searchParams.get("decision");
    setDecision(d && ["paid", "partial", "pended", "denied", "rejected"].includes(d) ? d : "");
    setQ(searchParams.get("q") ?? "");
    setPage(1);
  }, [searchParams]);

  useEffect(() => {
    getJson("/api/fwa", { dataset, page_size: 1 }).then((r) => setFiredRules(r.signals)).catch(() => {});
  }, [dataset]);

  useEffect(() => {
    // arriving from a boundary-case link puts ?q= in the URL — highlight + open that claim
    const urlQ = searchParams.get("q");
    const t = setTimeout(() => {
      setData(null); setError(null);
      getJson("/api/claims", { dataset, decision, rule, q, page, page_size: pageSize })
        .then((d) => {
          setData(d);
          if (urlQ && d.claims?.length) setSelected((s) => s ?? d.claims[0].claim_id);
        })
        .catch(setError);
    }, q ? 300 : 0);
    return () => clearTimeout(t);
  }, [dataset, decision, rule, q, page]);

  const cols = [
    { key: "claim_id", label: "Claim", render: (r) => <span className="num">{r.claim_id}</span> },
    { key: "member_id", label: "Member", render: (r) => <span className="num text-zinc-500">{r.member_id}</span> },
    { key: "from_dt", label: "Service date", numeric: true },
    { key: "plan", label: "Plan" },
    { key: "decision", label: "Decision", render: (r) => <DecisionBadge decision={r.decision} /> },
    { key: "primary_rule", label: "Rule", render: (r) => <span className="text-sky-300 num">{r.primary_rule ?? "-"}</span> },
    { key: "allowed_amt", label: "Billed", numeric: true, render: (r) => fmtMoney(r.allowed_amt) },
    { key: "payable_amt", label: "Payable", numeric: true, render: (r) => fmtMoney(r.payable_amt) },
    { key: "fwa_score", label: "FWA", numeric: true, render: (r) => (r.fwa_score > 0 ? r.fwa_score.toFixed(2) : "-") },
  ];

  return (
    <div className="space-y-4">
      <PageIntro>Every claim, filtered and traceable to the clause that decided it. Click a row — the full context opens on the right.</PageIntro>

      {/* KPI band */}
      {data && (
        <div className="grid grid-cols-4 gap-3">
          <StatTile label="Matching claims" value={data.total.toLocaleString()} />
          <StatTile label="Billed (filtered)" value={fmtMoney(data.billed)} />
          <StatTile label="Plan pays (filtered)" value={fmtMoney(data.plan_paid)} />
          <StatTile label="Denied / rejected share"
                    value={fmtPct(data.denied_share, 1)}
                    color={data.denied_share > 0.2 ? "#ef4444" : undefined} />
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5 items-start">
        {/* left: filters + list */}
        <div className="xl:col-span-2 space-y-4">
          <Card>
            <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
              <div>
                <label className="text-xs text-zinc-500 block mb-1">Decision</label>
                <select value={decision} onChange={(e) => { setDecision(e.target.value); setPage(1); }}
                        className="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1.5 text-sm text-zinc-200">
                  <option value="">all</option>
                  {["paid", "partial", "pended", "denied", "rejected"].map((d) => (
                    <option key={d} value={d}>{d}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs text-zinc-500 block mb-1">Rule fired</label>
                <select value={rule} onChange={(e) => { setRule(e.target.value); setPage(1); }}
                        className="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1.5 text-sm text-zinc-200">
                  <option value="">any</option>
                  {firedRules.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
              <div className="col-span-2">
                <label className="text-xs text-zinc-500 block mb-1">Claim or member ID contains</label>
                <input value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} placeholder="e.g. 391892…"
                       className="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1.5 text-sm text-zinc-200 num" />
              </div>
            </div>
            <div className="text-xs text-zinc-500 mt-2 num">
              {data ? `${data.total.toLocaleString()} claims match` : "…"}
            </div>
          </Card>

          {error && <ErrorState error={error} />}
          {!error && !data && <SkeletonPage tiles={4} />}
          {data && (
            <>
              <DataTable cols={cols} rows={data.claims} keyField="claim_id"
                         onRowClick={(r) => setSelected(r.claim_id)} selectedKey={selected} />
              <Pager page={page} pageSize={pageSize} total={data.total} onPage={setPage} />
            </>
          )}
        </div>

        {/* right: live context pane */}
        <div className="xl:sticky xl:top-36 space-y-4">
          {selected ? (
            <ClaimDetail claimId={selected} dataset={dataset}
                         expectRule={searchParams.get("exp_rule") ?? ""}
                         expectDecision={searchParams.get("exp_dec") ?? ""}
                         onClose={() => setSelected(null)} />
          ) : (
            <Card className="border-dashed">
              <div className="text-xs text-zinc-500 py-6 text-center">
                Select a claim on the left — its trace, member conditions and AI explanation open here,
                without leaving the page.
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
