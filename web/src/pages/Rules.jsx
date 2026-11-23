import { useEffect, useState } from "react";
import { getJson } from "../api.js";
import { PageIntro, Card, DataTable, ErrorState, SkeletonPage } from "../components/ui.jsx";

const SOURCE_BADGE = { on: "text-emerald-300", proposal: "text-amber-300" };

export default function Rules() {
  const [rules, setRules] = useState(null);
  const [error, setError] = useState(null);
  const [layer, setLayer] = useState("");
  const [status, setStatus] = useState("");

  useEffect(() => { getJson("/api/rules").then(setRules).catch(setError); }, []);

  if (error) return <ErrorState error={error} />;
  if (!rules) return <SkeletonPage tiles={4} />;

  const layers = [...new Set(rules.map((r) => r.layer))];
  const filtered = rules.filter((r) =>
    (!layer || r.layer === layer) && (!status || r.status === status));

  return (
    <Card caption="Every rule with its clause text and source. Edit rules in rd_engine/rules/*.yaml, then restart the API to rerun.">
      <div className="flex gap-3 mb-3">
        <select value={layer} onChange={(e) => setLayer(e.target.value)}
                className="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1.5 text-sm text-zinc-200">
          <option value="">all layers</option>
          {layers.map((l) => <option key={l} value={l}>{l}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)}
                className="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1.5 text-sm text-zinc-200">
          <option value="">all statuses</option>
          <option value="on">on</option>
          <option value="proposal">proposal</option>
        </select>
        <span className="text-xs text-zinc-500 num self-center">{filtered.length} rules</span>
      </div>
      <DataTable
        cols={[
          { key: "id", label: "ID", render: (r) => (
              <span className={`num ${SOURCE_BADGE[r.status] ?? "text-zinc-300"}`}>{r.id}</span>
            ) },
          { key: "status", label: "Status" },
          { key: "layer", label: "Layer" },
          { key: "kind", label: "Kind" },
          { key: "outcome", label: "Outcome" },
          { key: "priority", label: "Priority", numeric: true },
          { key: "effective", label: "Effective", numeric: true },
          { key: "clause", label: "Clause", render: (r) => <span className="text-zinc-400">{r.clause}</span> },
        ]}
        rows={filtered}
        keyField="id"
        maxHeight="36rem"
      />
    </Card>
  );
}
