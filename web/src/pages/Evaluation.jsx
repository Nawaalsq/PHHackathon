import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getJson, postJson } from "../api.js";
import { useStore } from "../store.jsx";
import { useToast } from "../components/toast.jsx";
import { PageIntro, Card, DataTable, DecisionBadge, ErrorState, SkeletonPage, StatTile } from "../components/ui.jsx";
import { fmtPct } from "../theme.js";

export default function Evaluation() {
  const { dataset, keysDir } = useStore();
  const toast = useToast();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [shadow, setShadow] = useState({ ran: false });
  const [shadowBusy, setShadowBusy] = useState(false);
  const [shadowN, setShadowN] = useState(9);
  const [shadowError, setShadowError] = useState(null);

  useEffect(() => {
    setData(null); setError(null);
    getJson("/api/evaluation", { dataset, keys_dir: keysDir }).then(setData).catch(setError);
  }, [dataset, keysDir]);

  useEffect(() => {
    setShadow({ ran: false }); setShadowError(null);
    getJson("/api/ai/shadow-review", { dataset }).then(setShadow).catch(() => {});
  }, [dataset]);

  const runShadow = async () => {
    setShadowBusy(true); setShadowError(null);
    try {
      const r = await postJson("/api/ai/shadow-review", { dataset, n: shadowN });
      setShadow(r);
      toast(`Shadow review complete — ${r.agreement !== null ? `${Math.round(r.agreement * 100)}% agreement` : "no readable claims"}`);
    } catch (e) {
      setShadowError(e);
    } finally {
      setShadowBusy(false);
    }
  };

  if (error) return <ErrorState error={error} />;
  if (!data) return <SkeletonPage tiles={4} />;
  const { boundary, anomalies, conflicts } = data;

  return (
    <div className="space-y-4">
      <PageIntro>The scorecard: the engine graded against known planted cases, plus an independent AI audit.</PageIntro>
      <div className="grid grid-cols-4 gap-3">
        <StatTile label="Boundary decisions correct" value={fmtPct(boundary.decision_accuracy, 0)} sub={`${boundary.cases} cases`} />
        <StatTile label="Right rule cited" value={fmtPct(boundary.rule_trace_accuracy, 0)} />
        <StatTile label="Seeded anomalies found" value={fmtPct(anomalies.overall_recall, 0)} />
        <StatTile label="Planted conflicts found" value={`${conflicts.detected}/${conflicts.expected}`}
                  sub={`winner right ${fmtPct(conflicts.winner_accuracy, 0)}`} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card title="Boundary cases (RD02 / RD04)"
              caption="All planted edge cases: what each one tests, the expected decision and rule, and what the engine got. Failures sort first.">
          <DataTable
            cols={[
              { key: "case_id", label: "Case", render: (r) => <span className="num">{r.case_id}</span> },
              { key: "description", label: "Description", render: (r) => <span className="text-zinc-400">{r.description}</span> },
              { key: "expected_decision", label: "Expected" },
              { key: "decision", label: "Got" },
              { key: "expected_rule", label: "Exp. rule", render: (r) => <span className="num">{r.expected_rule || "-"}</span> },
              { key: "primary_rule", label: "Got rule", render: (r) => <span className="num">{r.primary_rule || "-"}</span> },
              { key: "claim", label: "", render: (r) => (r.claim_id ? (
                  <Link className="text-sky-300 hover:underline text-xs whitespace-nowrap"
                        to={`/claims?q=${encodeURIComponent(r.claim_id)}` +
                            (r.expected_rule ? `&exp_rule=${encodeURIComponent(r.expected_rule)}` : "") +
                            (r.expected_decision ? `&exp_dec=${encodeURIComponent(r.expected_decision)}` : "")}>
                    open ↗
                  </Link>
                ) : null) },
              { key: "decision_ok", label: "", render: (r) => (r.decision_ok && r.rule_ok
                  ? <span className="text-emerald-300">✅ pass</span>
                  : <span className="text-red-300 font-medium">❌ fail</span>) },
            ]}
            rows={(boundary.all_cases?.length
              ? [...boundary.all_cases].sort((a, b) => (a.decision_ok && a.rule_ok ? 1 : 0) - (b.decision_ok && b.rule_ok ? 1 : 0))
              : boundary.failures)}
            keyField="case_id"
            maxHeight="22rem"
            empty="No boundary cases."
          />
          {boundary.failures.length > 0 && (
            <div className="text-xs text-red-300 mt-2">{boundary.failures.length} case(s) fail.</div>
          )}
        </Card>

        <div className="space-y-4">
          <Card title="Seeded anomalies by difficulty (RD03)"
                caption="Recall is expected to be high: the detectors and the seeded patterns were written together.">
            <div className="grid grid-cols-3 gap-3">
              {Object.entries(anomalies.by_difficulty).map(([k, v]) => (
                <StatTile key={k} label={k} value={fmtPct(v.recall, 0)} sub={`${v.detected}/${v.seeded} claims`} />
              ))}
            </div>
            <div className="mt-3">
              <DataTable
                cols={[
                  { key: "difficulty", label: "Difficulty" },
                  { key: "pattern", label: "Pattern" },
                  { key: "detector", label: "Detector", render: (r) => <span className="num">{r.detector}</span> },
                  { key: "seeded", label: "Seeded", numeric: true },
                  { key: "detected", label: "Found", numeric: true },
                  { key: "recall", label: "Recall", numeric: true, render: (r) => fmtPct(r.recall, 0) },
                ]}
                rows={Object.values(anomalies.by_pattern)}
                maxHeight="18rem"
              />
            </div>
          </Card>

          <Card title="Reviewer workload (RD03)"
                caption={`Reviewer opens ${anomalies.claims_to_open_for_80pct ?? "-"} claims to catch 80% of ${anomalies.fwa_seeded_claims} easy seeded FWA claims (queue of ${anomalies.fwa_queue_size}).`}>
            <DataTable
              cols={[
                { key: "pair", label: "Pair", render: (r) => <span className="num">{r.pair}</span> },
                { key: "type", label: "Type" },
                { key: "winner_expected", label: "Exp. winner", render: (r) => <span className="num">{r.winner_expected ?? "-"}</span> },
                { key: "winner_got", label: "Got winner", render: (r) => <span className="num">{r.winner_got ?? "-"}</span> },
                { key: "detected", label: "", render: (r) => (r.detected ? "✅" : "❌") },
              ]}
              rows={conflicts.detail}
              maxHeight="12rem"
              empty="No conflicts in the live rule set."
            />
          </Card>
        </div>
      </div>

      {/* AI shadow review */}
      <Card title="✨ AI shadow review" glow hover={false}>
        <div className="flex flex-wrap items-center gap-3 mb-3">
          <button onClick={runShadow} disabled={shadowBusy}
                  className="btn">
            {shadowBusy ? "Deciding claims…" : "Run shadow review"}
          </button>
          <select value={shadowN} onChange={(e) => setShadowN(Number(e.target.value))}
                  className="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1.5 text-xs text-zinc-200">
            {[6, 9, 12].map((v) => <option key={v} value={v}>{v} claims</option>)}
          </select>
          <span className="text-xs text-zinc-500">
            An independent AI reads the same rule book and states what it would decide — the engine
            still decides. Agreement is a governance signal; disagreements are rule ambiguity
            candidates for a person to check. Takes ~1 minute.
          </span>
        </div>
        {shadowError && (
          <div className="text-xs text-red-300 mb-2">{String(shadowError.message || shadowError)}</div>
        )}
        {shadow.ran && (
          <>
            <div className="grid grid-cols-3 gap-3 mb-3">
              <StatTile label="Independent AI agreement"
                        value={shadow.agreement !== null ? fmtPct(shadow.agreement, 0) : "-"} />
              <StatTile label="Claims audited" value={shadow.n} />
              <StatTile label="Disagreements (ambiguity candidates)"
                        value={shadow.rows.filter((r) => r.match === false).length}
                        color={shadow.rows.some((r) => r.match === false) ? "#d97706" : undefined} />
            </div>
            <DataTable
              cols={[
                { key: "claim_id", label: "Claim", render: (r) => <span className="num">{r.claim_id}</span> },
                { key: "engine", label: "Engine", render: (r) => <DecisionBadge decision={r.engine === "pay" ? "paid" : r.engine === "deny" ? "denied" : "pended"} /> },
                { key: "ai", label: "Independent AI", render: (r) => (r.ai === "unparsed"
                    ? <span className="text-zinc-500 text-xs">unparsed</span>
                    : <DecisionBadge decision={r.ai === "pay" ? "paid" : r.ai === "deny" ? "denied" : "pended"} />) },
                { key: "match", label: "", render: (r) => (r.match === null ? "—" : r.match ? "✅" : "❌") },
                { key: "ai_reason", label: "AI reading", render: (r) => <span className="text-zinc-400">{r.ai_reason}</span> },
              ]}
              rows={shadow.rows}
              keyField="claim_id"
              maxHeight="20rem"
            />
            {shadow.rows.some((r) => r.match === false) && (
              <div className="text-xs text-amber-300 mt-2">
                ⚠ Disagreements above are rule ambiguity candidates — a person should check whether
                the rule is ambiguous or missing. The engine's decision stands.
              </div>
            )}
          </>
        )}
        {!shadow.ran && !shadowBusy && !shadowError && (
          <div className="text-xs text-zinc-500">Not run yet for this dataset.</div>
        )}
      </Card>
    </div>
  );
}
