import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import { getJson, postJson } from "../api.js";
import { Card, DataTable, DecisionBadge, ErrorState, SkeletonPage, StatTile } from "../components/ui.jsx";
import { fmtMoney, fmtMoneyCompact } from "../theme.js";

export default function ClaimDetail({ claimId, dataset, onClose, expectRule = "", expectDecision = "" }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [audience, setAudience] = useState("member");
  const [language, setLanguage] = useState("English");
  const [aiOut, setAiOut] = useState(null);
  const [aiBusy, setAiBusy] = useState(false);

  useEffect(() => {
    setData(null); setError(null); setAiOut(null);
    getJson(`/api/claims/${claimId}`, { dataset }).then(setData).catch(setError);
  }, [claimId, dataset]);

  if (error) return <ErrorState error={error} />;
  if (!data) return <SkeletonPage tiles={4} />;
  const { claim, trace, explanation, appeal, member, claim_info: info, services } = data;

  const writeExplanation = async () => {
    setAiBusy(true); setAiOut(null);
    try {
      setAiOut(await postJson("/api/ai/explain", { dataset, claim_id: claimId, audience, language }));
    } catch (e) {
      setAiOut({ text: String(e.message), source: "error", warnings: [], dir: "ltr" });
    } finally {
      setAiBusy(false);
    }
  };

  // the decisive rule's fired code — the service line where the policy was breached
  const firedCode = expectRule ? (trace.find((t) => t.rule_id === expectRule)?.code ?? null) : null;
  const firedOutcome = expectRule ? (trace.find((t) => t.rule_id === expectRule)?.outcome ?? null) : null;

  return (
    <Card className="mt-4 border-sky-900/40">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <div className="flex flex-wrap items-center gap-2 min-w-0">
          <DecisionBadge decision={claim.decision} />
          <span className="font-semibold num">{claimId}</span>
          <span className="text-zinc-500 text-sm">
            {claim.claim_type} · {claim.from_dt} · {claim.provider_id} · {claim.plan} plan
          </span>
          {expectDecision && claim.decision === expectDecision && (
            <span className="rounded-full px-2.5 py-0.5 text-xs bg-teal-500/10 text-sky-300 border border-teal-900/40">
              ✓ matches expected ({expectDecision})
            </span>
          )}
          {expectDecision && claim.decision !== expectDecision && (
            <span className="rounded-full px-2.5 py-0.5 text-xs bg-red-500/10 text-red-300 border border-red-900/40">
              ✗ expected {expectDecision}
            </span>
          )}
        </div>
        <button onClick={onClose} className="text-xs text-zinc-500 hover:text-zinc-200 border border-zinc-700 rounded-md px-2 py-1">
          Close ✕
        </button>
      </div>

      <div className="grid grid-cols-3 gap-3 mb-4">
        <StatTile label="Billed" value={fmtMoneyCompact(claim.allowed_amt)} />
        <StatTile label="Payable" value={fmtMoneyCompact(claim.payable_amt)} />
        <StatTile label="Plan pays" value={fmtMoneyCompact(claim.plan_paid_amt)} />
        <StatTile label="Held" value={fmtMoneyCompact(claim.held_amt)} />
        <StatTile label="FWA score" value={(claim.fwa_score ?? 0).toFixed(2)}
                  color={claim.fwa_score > 0 ? "#f97316" : undefined} />
      </div>

      <div className="grid grid-cols-1 gap-4">
        <div className="space-y-4">
          <div className="rounded-lg border border-zinc-800 bg-zinc-950/50 p-4 text-sm leading-relaxed">
            <Markdown>{explanation.text}</Markdown>
          </div>

          {appeal && (
            <div className="rounded-lg border border-amber-900/50 bg-amber-950/20 p-4 text-sm">
              <div className="text-amber-300 font-medium mb-1.5">📌 {appeal.title}</div>
              <div className="text-zinc-300 leading-relaxed">
                <Markdown>{appeal.body}</Markdown>
              </div>
            </div>
          )}

          <div className="rounded-lg border border-zinc-800 bg-zinc-950/50 p-4">
            <div className="text-sm font-medium mb-2">✨ AI explanation</div>
            <div className="flex items-end gap-3 mb-3">
              <div>
                <label className="text-xs text-zinc-500 block mb-1">Written for</label>
                <div className="flex gap-3 text-sm">
                  {["member", "provider"].map((a) => (
                    <label key={a} className="flex items-center gap-1.5 cursor-pointer">
                      <input type="radio" checked={audience === a} onChange={() => setAudience(a)} />
                      {a}
                    </label>
                  ))}
                </div>
              </div>
              <div>
                <label className="text-xs text-zinc-500 block mb-1">Language</label>
                <select value={language} onChange={(e) => setLanguage(e.target.value)}
                        className="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-sm text-zinc-200">
                  <option>English</option><option>Arabic</option>
                </select>
              </div>
              <button onClick={writeExplanation} disabled={aiBusy}
                      className="bg-sky-500/20 text-sky-300 border border-sky-900/60 rounded-md px-3 py-1.5 text-sm hover:bg-sky-500/30 disabled:opacity-40">
                {aiBusy ? "Writing…" : "Write explanation"}
              </button>
            </div>
            {aiOut && (
              <div>
                <div dir={aiOut.dir} className="text-sm leading-relaxed whitespace-pre-wrap">
                  <Markdown>{aiOut.text}</Markdown>
                </div>
                <div className="text-xs text-zinc-500 mt-2">
                  Source: {aiOut.source}. Written only from the decision trace; rule IDs are checked against it.
                </div>
                {aiOut.warnings?.map((w, i) => (
                  <div key={i} className="text-amber-300 text-xs mt-1">⚠ {w}</div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="rounded-lg border border-zinc-800 bg-zinc-950/50 p-4">
              <div className="text-sm font-medium mb-2">Claim</div>
              <dl className="text-xs space-y-1">
                <Row k="Type" v={info.type} />
                <Row k="Service date" v={info.service_dt} />
                <Row k="Provider" v={info.provider} />
                {info.admitted && <Row k="Admitted" v={info.admitted} />}
                {info.discharged && <Row k="Discharged" v={info.discharged} />}
                {info.drg && <Row k="DRG" v={info.drg} />}
                <Row k="Diagnoses" v={(info.diagnoses ?? []).join(", ") || "-"} />
                <Row k="Procedures" v={(info.procedures ?? []).join(", ") || "-"} />
              </dl>
            </div>
            <div className="rounded-lg border border-zinc-800 bg-zinc-950/50 p-4">
              <div className="text-sm font-medium mb-2">Member</div>
              <dl className="text-xs space-y-1">
                <Row k="Member" v={<span className="num">{member.member}</span>} />
                <Row k="Plan" v={member.plan} />
                <Row k="Policy start" v={member.policy_start ?? "-"} />
                <Row k="Part B months" v={member.part_b_months ?? "-"} />
                <Row k="ESRD" v={String(member.esrd ?? "-")} />
                <Row k="Conditions" v={(member.conditions ?? []).join(", ") || "none"} />
                <Row k="Died" v={member.died ?? "-"} />
                {member.record && <Row k="Member record" v={member.record} />}
              </dl>
            </div>
          </div>

          <div>
            <div className="text-sm font-medium mb-2">Decision trace <span className="text-zinc-500 font-normal">(highest priority first)</span></div>
            <DataTable
              cols={[
                { key: "rule_id", label: "Rule", render: (r) => (
                    <span>
                      <span className="text-sky-300 num">{r.rule_id}</span>
                      {expectRule && r.rule_id === expectRule && (
                        <span className="ml-2 rounded-full px-2 py-0.5 text-[10px] bg-teal-500/10 text-sky-300 border border-teal-900/30">
                          ★ expected rule
                        </span>
                      )}
                      {expectRule && r.rule_id === expectRule && (r.outcome === "allow" ? (
                        <span className="ml-1.5 rounded-full px-2 py-0.5 text-[10px] bg-teal-500/10 text-sky-300 border border-teal-900/30">
                          ✓ within policy
                        </span>
                      ) : r.outcome === "refer" ? (
                        <span className="ml-1.5 rounded-full px-2 py-0.5 text-[10px] bg-amber-500/10 text-amber-300 border border-amber-900/30">
                          ⚠ pended for review
                        </span>
                      ) : (
                        <span className="ml-1.5 rounded-full px-2 py-0.5 text-[10px] bg-red-500/10 text-red-300 border border-red-900/30">
                          ⚠ policy breached
                        </span>
                      ))}
                    </span>
                  ) },
                { key: "layer", label: "Layer" },
                { key: "outcome", label: "Outcome" },
                { key: "code", label: "Code", render: (r) => <span className="num">{r.code ?? "-"}</span> },
                { key: "reason", label: "Reason", render: (r) => <span className="text-zinc-400">{r.reason}</span> },
                { key: "suppressed_by", label: "Overridden by", render: (r) => r.suppressed_by ?? "-" },
              ]}
              rows={trace}
              rowClassFn={(r) => (expectRule && r.rule_id === expectRule ? "bg-emerald-500/10" : "")}
              maxHeight="18rem"
            />
          </div>

          <div>
            <div className="text-sm font-medium mb-2">
              Services on the claim
              {firedCode && firedOutcome && firedOutcome !== "allow" && (
                <span className="text-zinc-500 font-normal"> — the breached line is highlighted</span>
              )}
            </div>
            <DataTable
              cols={[
                { key: "code", label: "Code", render: (r) => (
                    <span>
                      <span className="num">{r.code}</span>
                      {firedCode && r.code === firedCode && firedOutcome !== "allow" && (
                        <span className="ml-2 rounded-full px-2 py-0.5 text-[10px] bg-red-500/10 text-red-300 border border-red-900/30">
                          ⚠ policy breached
                        </span>
                      )}
                    </span>
                  ) },
                { key: "repeat_count", label: "Units", numeric: true },
                { key: "code_type", label: "Type" },
                { key: "is_drug_code", label: "Drug", render: (r) => (r.is_drug_code ? "yes" : "-") },
              ]}
              rows={services}
              rowClassFn={(r) => (firedCode && r.code === firedCode && firedOutcome !== "allow" ? "bg-red-500/10" : "")}
              maxHeight="12rem"
            />
          </div>
        </div>
      </div>
    </Card>
  );
}

function Row({ k, v }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-zinc-500 shrink-0">{k}</dt>
      <dd className="text-right text-zinc-200">{v}</dd>
    </div>
  );
}
