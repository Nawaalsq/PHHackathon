import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import { getJson, postJson } from "../api.js";
import { PageIntro, Card, DataTable, ErrorState, SkeletonPage, RuleTag } from "../components/ui.jsx";

export default function Conflicts() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [expln, setExpln] = useState(null);
  const [busy, setBusy] = useState(null);   // index or "all"

  useEffect(() => {
    getJson("/api/conflicts").then(setData).catch(setError);
  }, []);

  const doExplain = async (index, all = false) => {
    setBusy(all ? "all" : index); setExpln(null);
    try {
      const r = all ? await postJson("/api/ai/explain-conflicts", {})
                    : await postJson("/api/ai/explain-conflict", { index });
      setExpln({ ...r, index, all });
    } catch (e) {
      setExpln({ text: String(e.message), source: "error", warnings: [], index, all });
    } finally {
      setBusy(null);
    }
  };

  if (error) return <ErrorState error={error} />;
  if (!data) return <SkeletonPage tiles={4} />;

  return (
    <div className="space-y-4">
      <PageIntro>Clashes found before any claim runs. The precedence policy picks a winner; AI explains each clash.</PageIntro>
      {data.conflicts.length === 0 ? (
        <Card><div className="text-emerald-300 text-sm py-4 text-center">No conflicts in the current rule set.</div></Card>
      ) : (
        <Card
          title="Clashes in the rule set"
          caption="Clashes in the rule set itself, before any claim is run. The precedence policy picks a winner for each clash."
        >
          <div className="flex justify-end mb-2">
            <button onClick={() => doExplain(0, true)} disabled={busy !== null}
                    className="bg-sky-500/20 text-sky-300 border border-sky-900/60 rounded-md px-3 py-1.5 text-xs hover:bg-sky-500/30 disabled:opacity-40">
              {busy === "all" ? "Summarising…" : "✨ Explain all conflicts"}
            </button>
          </div>
          <DataTable
            cols={[
              { key: "rule_a", label: "Rule A", render: (r) => <span className="text-sky-300 num">{r.rule_a}</span> },
              { key: "rule_b", label: "Rule B", render: (r) => <span className="text-sky-300 num">{r.rule_b ?? "—"}</span> },
              { key: "type", label: "Type" },
              { key: "detail", label: "Detail", render: (r) => <span className="text-zinc-400">{r.detail}</span> },
              { key: "winner", label: "Winner", render: (r) => <span className="num text-emerald-300">{r.winner ?? "none"}</span> },
              { key: "criterion", label: "Criterion" },
              { key: "why", label: "Why", render: (r) => <span className="text-zinc-400">{r.why}</span> },
              { key: "explain", label: "", render: (r) => (
                  <button onClick={() => doExplain(data.conflicts.indexOf(r))} disabled={busy !== null}
                          className="text-sky-300 hover:underline disabled:opacity-40">
                    {busy === data.conflicts.indexOf(r) ? "…" : "✨ Explain"}
                  </button>
                ) },
            ]}
            rows={data.conflicts}
            maxHeight="28rem"
          />
        </Card>
      )}

      {expln && (
        <Card title={expln.all ? "✨ Conflict summary"
          : `✨ Why these rules clash${data.conflicts[expln.index] ? ` · ${data.conflicts[expln.index].rule_a} vs ${data.conflicts[expln.index].rule_b ?? "—"}` : ""}`}>
          <div className="text-sm leading-relaxed">
            <Markdown>{expln.text}</Markdown>
          </div>
          <div className="text-xs text-zinc-500 mt-2">Source: {expln.source}.</div>
          {expln.warnings?.map((w, i) => <div key={i} className="text-amber-300 text-xs mt-1">⚠ {w}</div>)}
        </Card>
      )}

      <Card title="Precedence policy" caption="How the winner of each clash is decided.">
        <div className="space-y-2">
          {data.precedence.map((p) => (
            <div key={p.id} className="text-sm">
              <span className="text-sky-300 num">{p.id}</span>{" "}
              <span className="text-zinc-300">{p.rule}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
