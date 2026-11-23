import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import {
  Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { getJson, postJson, delJson } from "../api.js";
import { useStore } from "../store.jsx";
import { useToast } from "../components/toast.jsx";
import { PageIntro, Card, DataTable, ErrorState, Loading, StatTile } from "../components/ui.jsx";
import { ACCENT, CHART, DECISIONS, DECISION_COLORS, fmtMoney } from "../theme.js";

const tooltipStyle = {
  backgroundColor: CHART.tooltipBg, border: `1px solid ${CHART.border}`,
  borderRadius: "0.5rem", fontSize: "0.75rem",
};

export default function WhatIf() {
  const { dataset, drafts, setDrafts } = useStore();
  const toast = useToast();
  const [rules, setRules] = useState(null);
  const [disabled, setDisabled] = useState([]);
  const [added, setAdded] = useState([]);
  const [scenarios, setScenarios] = useState([]);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [name, setName] = useState("");
  const [saveMsg, setSaveMsg] = useState(null);
  const [narr, setNarr] = useState(null);
  const [narrBusy, setNarrBusy] = useState(false);

  const live = rules?.filter((r) => r.status === "on") ?? [];
  const proposals = rules?.filter((r) => r.status === "proposal") ?? [];

  const refreshRules = () => getJson("/api/rules").then(setRules).catch(setError);
  const refreshScenarios = () => getJson("/api/scenarios").then(setScenarios).catch(() => {});

  useEffect(() => { refreshRules(); refreshScenarios(); }, []);

  const changedIds = result?.changed_rule_ids ?? [...disabled, ...added, ...drafts.map((d) => d.id)];

  const run = async () => {
    setBusy(true); setError(null); setSaveMsg(null); setNarr(null);
    try {
      const r = await postJson("/api/whatif", {
        dataset, disabled_rule_ids: disabled, added_proposal_ids: added,
        drafts, run_dataset: devDefault(dataset), confirm_full: false,
      });
      setResult(r);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!name.trim() || !result) return;
    try {
      const r = await postJson("/api/scenarios", {
        name, dataset, disabled_rule_ids: disabled, added_proposal_ids: added,
        drafts, run_dataset: devDefault(dataset), confirm_full: true,
      });
      setSaveMsg(`Saved '${r.saved}'.`);
      toast(`Scenario '${r.saved}' saved`);
      setName("");
      refreshScenarios();
    } catch (e) {
      setSaveMsg(String(e.message));
    }
  };

  const compare = async (scen) => {
    setBusy(true); setError(null); setNarr(null);
    try {
      const r = await postJson(`/api/scenarios/${encodeURIComponent(scen.name)}/compare`, {
        dataset, run_dataset: devDefault(dataset), confirm_full: true,
      });
      setResult({ ...r, ran_on: devDefault(dataset), conflicts: scen.conflicts });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const narrate = async () => {
    setNarrBusy(true); setNarr(null);
    try {
      setNarr(await postJson("/api/ai/narrate-scenario", { diff: result, changed_rule_ids: changedIds }));
      toast("Briefing ready");
    } catch (e) {
      setNarr({ text: String(e.message), source: "error", warnings: [] });
    } finally {
      setNarrBusy(false);
    }
  };

  const remove = async (n) => {
    await delJson(`/api/scenarios/${encodeURIComponent(n)}`);
    refreshScenarios();
  };

  // One-click scenario presets, built from the live rule set by kind.
  const PRESETS = [
    { label: "🚫 Remove all exceptions", pick: (rs) => ({ disabled: rs.filter((r) => r.kind === "grant").map((r) => r.id), added: [] }) },
    { label: "📈 No frequency limits", pick: (rs) => ({ disabled: rs.filter((r) => r.kind === "frequency_limit").map((r) => r.id), added: [] }) },
    { label: "⏳ Skip waiting periods", pick: (rs) => ({ disabled: rs.filter((r) => r.kind === "waiting_period").map((r) => r.id), added: [] }) },
    { label: "👯 No duplicate edits", pick: () => ({ disabled: ["EDIT-03", "EDIT-04"], added: [] }) },
    { label: "🎁 Add all proposed rules", pick: (rs) => ({ disabled: [], added: rs.filter((r) => r.status === "proposal").map((r) => r.id) }) },
  ];
  const applyPreset = (p) => {
    const got = p.pick(rules ?? []);
    setDisabled(got.disabled);
    setAdded(got.added);
  };

  const multi = (title, options, value, setValue) => (
    <div>
      <label className="text-xs text-zinc-500 block mb-1">{title}</label>
      <div className="max-h-40 overflow-auto rounded-lg border border-zinc-800 p-1.5 space-y-0.5">
        {options.map((r) => (
          <label key={r.id} className="flex items-start gap-2 text-xs px-1 py-0.5 cursor-pointer hover:bg-zinc-800/40 rounded">
            <input type="checkbox" className="mt-0.5" checked={value.includes(r.id)}
                   onChange={() => setValue(value.includes(r.id) ? value.filter((x) => x !== r.id) : [...value, r.id])} />
            <span><span className="text-sky-300 num">{r.id}</span> <span className="text-zinc-500">{r.clause.slice(0, 80)}</span></span>
          </label>
        ))}
      </div>
    </div>
  );

  return (
    <div className="space-y-4">
      <PageIntro>Test a rule change on every claim before it goes live: who moves, what it costs, any new conflicts.</PageIntro>
      <Card title="What-if simulator"
            caption="Add proposed rules or switch off live rules, then run. What-if runs on the dev subset (~6 s); the board reruns every claim and diffs against the baseline.">
        {drafts.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 mb-3">
            <span className="text-xs text-zinc-500">AI-drafted rules in the what-if:</span>
            {drafts.map((r) => (
              <span key={r.id} className="inline-flex items-center gap-1.5 rounded-full bg-sky-500/10 text-sky-300 border border-sky-900/40 px-2.5 py-1 text-xs num">
                {r.id}
                <button className="text-zinc-500 hover:text-red-300"
                        title={`Remove ${r.id} (${r.kind})`}
                        onClick={() => setDrafts(drafts.filter((x) => x.id !== r.id))}>
                  ✕
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-1.5 mb-3">
          <span className="text-xs text-zinc-500">Try a scenario:</span>
          {PRESETS.map((p) => (
            <button key={p.label} onClick={() => applyPreset(p)}
                    className="rounded-full px-2.5 py-1 text-xs border border-zinc-700 bg-zinc-900 text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100 transition-colors">
              {p.label}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-4 mb-4">
          {multi("Switch off live rules", live, disabled, setDisabled)}
          {multi("Add proposed rules", proposals, added, setAdded)}
        </div>
        <div className="flex items-center gap-3">
          <button onClick={run} disabled={busy || (!disabled.length && !added.length && !drafts.length)}
                  className="bg-sky-500/20 text-sky-300 border border-sky-900/60 rounded-md px-4 py-1.5 text-sm hover:bg-sky-500/30 disabled:opacity-40">
            {busy ? "Running the rules over every claim…" : "Run what-if"}
          </button>
          {(disabled.length || added.length || drafts.length) ? (
            <span className="text-xs text-zinc-500">
              Change: {[
                ...disabled.map((i) => `off ${i}`), ...added.map((i) => `add ${i}`),
                ...drafts.map((r) => `add ${r.id}`),
              ].join("; ")}
            </span>
          ) : (
            <span className="text-xs text-zinc-500">Tick rules above to build a scenario.</span>
          )}
        </div>
      </Card>

      {error && <ErrorState error={error} />}
      {busy && <Loading text="Running the rules over every claim…" />}

      {result && !busy && (
        <>
          {result.superseded?.length > 0 && (
            <Card className="border-amber-900/50 bg-amber-950/20">
              <div className="text-xs text-amber-300">
                Precedence applied at run time:{" "}
                {result.superseded.map((x) =>
                  `${x.winner} replaces ${x.superseded} for ${x.for} (${x.from} to ${x.to})`).join("; ")}
              </div>
            </Card>
          )}
          <div className="grid grid-cols-4 gap-3">
            <StatTile label="Claims that change" value={result.claims_changed.toLocaleString()} />
            <StatTile label="Plan cost change" value={fmtMoney(result.plan_cost_delta)}
                      color={result.plan_cost_delta < 0 ? "#10b981" : "#f97316"} />
            <StatTile label="Members affected" value={result.members_affected.toLocaleString()} />
            <StatTile label="Conflicts in new rule set" value={String(result.conflicts ?? 0)}
                      color={result.conflicts ? "#f59e0b" : "#10b981"} />
          </div>

          <Card title="✨ Scenario briefing"
                caption="A plain-language stakeholder summary of the diff, grounded in the numbers above.">
            <div className="flex items-center gap-3 mb-3">
              <button onClick={narrate} disabled={narrBusy}
                      className="btn">
                {narrBusy ? "Writing…" : "Narrate this scenario"}
              </button>
              <span className="text-xs text-zinc-500">What changed, who is affected, cost impact, risks.</span>
            </div>
            {narr && (
              <div>
                <div className="text-sm leading-relaxed">
                  <Markdown>{narr.text}</Markdown>
                </div>
                <div className="text-xs text-zinc-500 mt-2">Source: {narr.source}.</div>
                {narr.warnings?.map((w, i) => <div key={i} className="text-amber-300 text-xs mt-1">⚠ {w}</div>)}
              </div>
            )}
          </Card>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card title="Decisions before and after"
                  caption={result.ran_on ? `Ran on ${result.ran_on} in ${result.seconds}s.` : null}>
              <ResponsiveContainer width="100%" height={230}>
                <BarChart data={DECISIONS.map((d) => ({ decision: d, before: result.before[d] ?? 0, after: result.after[d] ?? 0 }))}
                          margin={{ left: 0, right: 8, top: 4 }}>
                  <CartesianGrid stroke={CHART.grid} vertical={false} />
                  <XAxis dataKey="decision" tick={{ fill: CHART.tick, fontSize: 10 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: CHART.tick, fontSize: 10 }} axisLine={false} tickLine={false} width={44} />
                  <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "#ffffff08" }} />
                  <Bar dataKey="before" name="Before" fill="#52525b" radius={[3, 3, 0, 0]} />
                  <Bar dataKey="after" name="After" fill={ACCENT} radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </Card>
            <Card title="How claims move">
              <DataTable
                cols={[
                  { key: "decision_before", label: "Before", render: (r) => <span style={{ color: DECISION_COLORS[r.decision_before] }}>{r.decision_before}</span> },
                  { key: "decision_after", label: "After", render: (r) => <span style={{ color: DECISION_COLORS[r.decision_after] }}>{r.decision_after}</span> },
                  { key: "claims", label: "Claims", numeric: true },
                ]}
                rows={result.moves}
                maxHeight="14rem"
                empty="No claims change decision."
              />
            </Card>
          </div>

          <Card title="Changed claims" caption="First 1,000.">
            <DataTable
              cols={[
                { key: "claim_id", label: "Claim", render: (r) => <span className="num">{r.claim_id}</span> },
                { key: "member_id", label: "Member", render: (r) => <span className="num text-zinc-500">{r.member_id}</span> },
                { key: "decision_before", label: "Before" },
                { key: "decision_after", label: "After" },
                { key: "plan_paid_amt_before", label: "Plan pays before", numeric: true, render: (r) => fmtMoney(r.plan_paid_amt_before) },
                { key: "plan_paid_amt_after", label: "Plan pays after", numeric: true, render: (r) => fmtMoney(r.plan_paid_amt_after) },
              ]}
              rows={result.changed_claims}
              maxHeight="18rem"
            />
          </Card>

          <Card title="Save this scenario" caption="Named scenarios persist for this session and can be re-compared against the baseline.">
            <div className="flex items-center gap-3">
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Scenario name…"
                     className="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1.5 text-sm text-zinc-200" />
              <button onClick={save} disabled={!name.trim()}
                      className="btn">
                Save scenario
              </button>
              {saveMsg && <span className="text-xs text-zinc-400">{saveMsg}</span>}
            </div>
          </Card>
        </>
      )}

      {scenarios.length > 0 && (
        <Card title="Saved scenarios">
          <DataTable
            cols={[
              { key: "name", label: "Name" },
              { key: "change", label: "Change", render: (r) => <span className="text-zinc-400">{r.change}</span> },
              { key: "claims_changed", label: "Claims that change", numeric: true },
              { key: "plan_cost_delta", label: "Cost change", numeric: true, render: (r) => fmtMoney(r.plan_cost_delta) },
              { key: "conflicts", label: "Conflicts", numeric: true },
              { key: "actions", label: "", render: (r) => (
                  <span className="space-x-2 whitespace-nowrap">
                    <button onClick={() => compare(r)} className="text-sky-300 hover:underline">Compare</button>
                    <button onClick={() => remove(r.name)} className="text-zinc-500 hover:text-red-300">Delete</button>
                  </span>
                ) },
            ]}
            rows={scenarios}
            keyField="name"
          />
        </Card>
      )}
    </div>
  );
}

// What-if defaults to the dev subset for speed; full samples need confirm_full.
// sample1 has a seeded dev set (precomputed); other samples fall back to their dev subset.
export function devDefault(dataset) {
  if (dataset.includes("_dev")) return dataset;
  const m = dataset.match(/sample(\d)/);
  if (!m) return "clean/sample1_dev_seeded";
  return m[1] === "1" ? "clean/sample1_dev_seeded" : `clean/sample${m[1]}_dev`;
}
