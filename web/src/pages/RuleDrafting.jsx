import { useEffect, useState } from "react";
import { getJson, postJson } from "../api.js";
import { useStore } from "../store.jsx";
import { PageIntro, Card, DataTable, ErrorState } from "../components/ui.jsx";

const EXAMPLES = [
  "Silver members get 8 physiotherapy sessions (97110) per policy year.",
  "The Basic plan does not cover therapeutic activities (97530).",
  "Blood draws (36415) are limited to 2 units per claim.",
  "Gold members have a 30-day waiting period for physiotherapy (97110).",
  "HbA1c tests (83036) are limited to 2 per year, or 6 for members with diabetes.",
  "MRI scans (70553) are limited to 1 per policy year.",
  "Home nursing visits (99600) are limited to 3 units per claim for Basic members.",
  "The Basic plan has a 60-day waiting period for dialysis drugs (Q4081).",
  "X-rays (71020) are not covered by the Basic plan.",
  "Gold members get up to 20 physiotherapy sessions (97110) per policy year.",
];

export default function RuleDrafting() {
  const { dataset, drafts, setDrafts } = useStore();
  const [clause, setClause] = useState("");
  const [draft, setDraft] = useState(null);      // {yaml, source}
  const [draftBusy, setDraftBusy] = useState(false);
  const [edited, setEdited] = useState("");
  const [check, setCheck] = useState(null);      // validate result
  const [error, setError] = useState(null);
  const [approved, setApproved] = useState(null);

  const doDraft = async () => {
    setDraftBusy(true); setDraft(null); setCheck(null); setApproved(null); setError(null);
    try {
      const r = await postJson("/api/ai/draft", { clause });
      setDraft(r);
      setEdited(r.yaml ?? "");
    } catch (e) { setError(e); } finally { setDraftBusy(false); }
  };

  const doValidate = async () => {
    setCheck(null); setApproved(null);
    try {
      setCheck(await postJson("/api/ai/validate-draft", { yaml: edited }));
    } catch (e) { setCheck({ ok: false, problems: [String(e.message)] }); }
  };

  const doApprove = async () => {
    if (!check?.ok || !check.rule) return;
    try {
      const r = await postJson("/api/ai/approve-draft", { rule: check.rule });
      setDrafts([...drafts, check.rule]);   // full rule dict - the What-if runs these
      setApproved(r.approved);
    } catch (e) { setError(e); }
  };

  return (
    <div className="space-y-4">
      <PageIntro>Turn a policy sentence into a machine-readable, conflict-checked rule. A person approves every step.</PageIntro>
      <Card title="AI rule drafting"
            caption="Type a policy change in plain words. The AI drafts a machine-readable rule, the app checks it, you approve it — nothing goes live without a person. The What-if tab shows its impact.">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="text-xs text-zinc-500 block mb-1">Start from an example</label>
            <select className="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1.5 text-sm text-zinc-200"
                    value="" onChange={(e) => e.target.value && setClause(e.target.value)}>
              <option value="">—</option>
              {EXAMPLES.map((x) => <option key={x} value={x}>{x.slice(0, 70)}…</option>)}
            </select>
            <textarea value={clause} onChange={(e) => setClause(e.target.value)} placeholder="Policy clause…"
                      className="mt-2 w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1.5 text-sm text-zinc-200 h-24" />
            <button onClick={doDraft} disabled={!clause.trim() || draftBusy}
                    className="mt-2 bg-sky-500/20 text-sky-300 border border-sky-900/60 rounded-md px-4 py-1.5 text-sm hover:bg-sky-500/30 disabled:opacity-40">
              {draftBusy ? "Drafting…" : "Draft rule"}
            </button>
          </div>
          <div>
            <label className="text-xs text-zinc-500 block mb-1">Draft rule (YAML — edit if needed, then validate)</label>
            <textarea value={edited} onChange={(e) => setEdited(e.target.value)}
                      className="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1.5 text-xs text-zinc-200 h-32 num" />
            <button onClick={doValidate} disabled={!edited.trim()}
                    className="mt-2 bg-zinc-800 text-zinc-200 border border-zinc-700 rounded-md px-3 py-1.5 text-sm hover:bg-zinc-700 disabled:opacity-40">
              Validate
            </button>
          </div>
        </div>
        {draft?.source && <div className="text-xs text-zinc-500 mt-2">Drafted by: {draft.source}.</div>}
      </Card>

      {error && <ErrorState error={error} />}

      {check && (
        <Card title="Validation">
          {check.problems?.length ? (
            check.problems.map((p, i) => (
              <div key={i} className="rounded-lg border border-red-900/50 bg-red-950/30 text-red-300 text-sm p-3 mb-2">{p}</div>
            ))
          ) : (
            <>
              <div className="text-emerald-300 text-sm mb-2">✅ Valid rule {check.rule_id}</div>
              {check.conflicts?.length ? (
                <>
                  <div className="text-amber-300 text-sm mb-2">⚠ Clashes with {check.conflicts.length} existing rule(s):</div>
                  <DataTable
                    cols={[
                      { key: "rule_a", label: "Rule A" }, { key: "rule_b", label: "Rule B" },
                      { key: "type", label: "Type" }, { key: "detail", label: "Detail" },
                      { key: "winner", label: "Winner" }, { key: "why", label: "Why" },
                    ]}
                    rows={check.conflicts} maxHeight="14rem"
                  />
                </>
              ) : (
                <div className="text-zinc-400 text-sm mb-2">No conflicts with the current rule set.</div>
              )}
              <button onClick={doApprove} disabled={approved}
                      className="mt-2 bg-emerald-500/20 text-emerald-300 border border-emerald-900/60 rounded-md px-3 py-1.5 text-sm hover:bg-emerald-500/30 disabled:opacity-40">
                {approved ? "✅ Approved" : "Approve: add to What-if"}
              </button>
            </>
          )}
        </Card>
      )}

      {drafts.length > 0 && (
        <Card title="Approved drafts in the What-if"
              caption="Open the What-if tab to see the impact. These drafts are machine-readable rules the engine can run.">
          <div className="flex flex-wrap gap-1.5">
            {drafts.map((d) => <span key={d.id} className="rounded-full bg-sky-500/10 text-sky-300 border border-sky-900/40 px-2.5 py-1 text-xs num">{d.id}</span>)}
          </div>
        </Card>
      )}
    </div>
  );
}
