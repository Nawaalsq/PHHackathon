import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Markdown from "react-markdown";
import { getJson, postJson } from "../api.js";
import { useStore } from "../store.jsx";
import { useToast } from "../components/toast.jsx";
import { PageIntro, Card, DataBar, DataTable, DecisionBadge, ErrorState, SkeletonPage, StatTile } from "../components/ui.jsx";
import ClaimDetail from "./ClaimDetail.jsx";
import { fmtMoney, fmtMoneyCompact } from "../theme.js";
import { dxName } from "../dxNames.js";

// The 12 chronic-condition flags (+ ESRD) as filter chips.
const CONDITIONS = [
  ["cc_alzheimers", "Alzheimer's"], ["cc_heart_failure", "Heart failure"],
  ["cc_chronic_kidney", "Chronic kidney"], ["cc_cancer", "Cancer"],
  ["cc_copd", "COPD"], ["cc_depression", "Depression"], ["cc_diabetes", "Diabetes"],
  ["cc_ischemic_heart", "Ischemic heart"], ["cc_osteoporosis", "Osteoporosis"],
  ["cc_ra_oa", "RA/OA"], ["cc_stroke_tia", "Stroke/TIA"], ["esrd", "ESRD"],
];

// profile condition name -> filter key, so chips drill back into the list
const COND_KEY = {
  "Alzheimer's": "cc_alzheimers", "Heart failure": "cc_heart_failure",
  "Chronic kidney disease": "cc_chronic_kidney", "Cancer": "cc_cancer",
  "COPD": "cc_copd", "Depression": "cc_depression", "Diabetes": "cc_diabetes",
  "Ischemic heart disease": "cc_ischemic_heart", "Osteoporosis": "cc_osteoporosis",
  "Rheumatoid/Osteo arthritis": "cc_ra_oa", "Stroke/TIA": "cc_stroke_tia", "ESRD": "esrd",
};

export default function Members() {
  const { dataset } = useStore();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState("");
  const [condition, setCondition] = useState(null);
  const [page, setPage] = useState(1);
  const [list, setList] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(params.get("m"));   // deep-link ?m=<member_id>
  const [profile, setProfile] = useState(null);
  const [profError, setProfError] = useState(null);
  const [brief, setBrief] = useState(null);
  const [briefBusy, setBriefBusy] = useState(false);
  const [claimId, setClaimId] = useState(null);
  const [ctxMember, setCtxMember] = useState(null);
  const [ctxProfile, setCtxProfile] = useState(null);
  const pageSize = 25;

  // list
  useEffect(() => {
    if (selected) return undefined;
    setList(null);
    getJson("/api/members", { dataset, q: q || undefined, condition: condition || undefined,
                              page, page_size: pageSize })
      .then(setList).catch(setError);
    return undefined;
  }, [dataset, q, condition, page, selected]); // eslint-disable-line react-hooks/exhaustive-deps

  // profile
  useEffect(() => {
    if (!selected) { setProfile(null); return undefined; }
    setProfile(null); setProfError(null); setBrief(null);
    getJson(`/api/member/${encodeURIComponent(selected)}`, { dataset })
      .then(setProfile).catch(setProfError);
    return undefined;
  }, [dataset, selected]);

  const writeBrief = async () => {
    setBriefBusy(true); setBrief(null);
    try {
      setBrief(await postJson("/api/ai/member-brief", { dataset, member_id: selected }));
      toast("Member brief ready");
    } catch (e) {
      setBrief({ text: String(e.message), source: "error", warnings: [] });
    } finally {
      setBriefBusy(false);
    }
  };

  const openMember = (mid) => {
    setSelected(mid);
    setParams({ m: mid });
  };
  const backToList = () => {
    setSelected(null); setProfile(null); setBrief(null);
    setParams({});
  };

  // list-level context pane
  useEffect(() => {
    if (!ctxMember) { setCtxProfile(null); return undefined; }
    setCtxProfile(null);
    getJson(`/api/member/${encodeURIComponent(ctxMember)}`, { dataset }).then(setCtxProfile).catch(() => {});
    return undefined;
  }, [dataset, ctxMember]);

  if (error) return <ErrorState error={error} />;

  // ------------------------------------------------------------ profile view
  if (selected) {
    if (profError) return <ErrorState error={profError} />;
    if (!profile) return <SkeletonPage tiles={4} />;
    const yearConditions = profile.timeline.filter((t) => t.conditions.length);
    return (
      <div className="space-y-4">
      <PageIntro>Patient profiles: chronic conditions, diagnoses and claim history per member. Synthetic data.</PageIntro>
        <div className="flex items-center gap-3">
          <button onClick={backToList} className="text-sm text-sky-300 hover:underline">← All members</button>
        </div>

        <Card hover={false}>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            <div>
              <div className="text-xs text-zinc-500">Member</div>
              <div className="text-xl font-semibold num">{profile.member_id}</div>
            </div>
            <div className="text-sm text-zinc-400">
              <span className="text-zinc-500">Plan:</span> {profile.plan ?? "—"} ·{" "}
              <span className="text-zinc-500">Age:</span> {profile.age ?? "—"} ·{" "}
              <span className="text-zinc-500">Sex:</span> {profile.sex ?? "—"} ·{" "}
              <span className="text-zinc-500">Policy start:</span> {profile.policy_start ?? "—"}
              {profile.state ? <> · <span className="text-zinc-500">State:</span> {profile.state}</> : null}
            </div>
            {profile.died && (
              <span className="rounded-full px-2.5 py-1 text-xs bg-zinc-500/10 text-zinc-500 border border-zinc-700">
                Deceased {profile.died}
              </span>
            )}
            {profile.fwa_flags > 0 && (
              <span className="rounded-full px-2.5 py-1 text-xs bg-amber-500/10 text-amber-300 border border-amber-900/40">
                {profile.fwa_flags} claim(s) flagged for review
              </span>
            )}
          </div>

          {/* conditions */}
          <div className="flex flex-wrap gap-1.5 mt-4">
            {profile.conditions.length === 0 && <span className="text-xs text-zinc-500">No flagged conditions.</span>}
            {profile.conditions.map((c) => (
              <button key={c} title={`Show all members with ${c}`}
                      onClick={() => { if (COND_KEY[c]) { setCondition(COND_KEY[c]); setSelected(null); setProfile(null); setParams({}); setPage(1); } }}
                      className="rounded-full px-2.5 py-1 text-xs bg-teal-500/10 text-sky-300 border border-teal-900/40 hover:bg-teal-500/20 transition-colors">
                {c}
              </button>
            ))}
          </div>
        </Card>

        <div className="grid grid-cols-4 gap-3">
          <StatTile label="Claims" value={profile.total_claims} />
          <StatTile label="Billed" value={fmtMoney(profile.billed)} />
          <StatTile label="Plan pays" value={fmtMoney(profile.plan_paid)} />
          <StatTile label="Providers seen" value={profile.providers} />
        </div>

        {/* condition timeline */}
        <Card title="Condition timeline"
              caption="Flagged chronic conditions per year — shows how the member's conditions developed.">
          {yearConditions.length === 0 ? (
            <div className="text-xs text-zinc-500">No conditions flagged in any year.</div>
          ) : (
            <div className="space-y-2">
              {yearConditions.map((t) => (
                <div key={t.year} className="flex items-start gap-3 text-xs">
                  <span className="text-zinc-500 num w-12 pt-1">{t.year} <span className="text-zinc-400">({t.age})</span></span>
                  <div className="flex flex-wrap gap-1.5">
                    {t.conditions.map((c) => (
                      <span key={c} className="rounded-full px-2 py-0.5 bg-teal-500/10 text-sky-300 border border-teal-900/30">{c}</span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* top diagnoses */}
          <Card title="Top diagnoses" caption="Most frequent diagnosis codes across the member's claims.">
            {profile.top_dx.length === 0 ? (
              <div className="text-xs text-zinc-500">No diagnoses recorded.</div>
            ) : (
              <div className="space-y-1.5">
                {profile.top_dx.map((d) => (
                  <div key={d.dx_code} className="flex items-center justify-between text-sm">
                    <span>
                      <span className="num text-zinc-200">{d.dx_code}</span>
                      <span className="text-zinc-400 ml-2">{dxName(d.dx_code)}</span>
                    </span>
                    <span className="text-xs text-zinc-500 num">{d.count} claim{d.count === 1 ? "" : "s"}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>

          {/* spend per year */}
          <Card title="Spend by year" caption="Billed vs plan-paid amounts per policy year.">
            {profile.spend.length === 0 ? (
              <div className="text-xs text-zinc-500">No claims.</div>
            ) : (
              <div className="space-y-1.5">
                {profile.spend.map((s) => (
                  <div key={s.year} className="flex items-center justify-between text-sm">
                    <span className="num text-zinc-400">{s.year}</span>
                    <span className="num text-zinc-200">{fmtMoney(s.billed)} billed
                      <span className="text-zinc-500 ml-2">{fmtMoney(s.plan_paid)} paid</span>
                      <span className="text-zinc-500 ml-2">{s.claims} claims</span>
                    </span>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>

        {/* AI brief */}
        <Card title="✨ AI member brief" glow>
          <div className="flex items-center gap-3 mb-3">
            <button onClick={writeBrief} disabled={briefBusy}
                    className="btn">
              {briefBusy ? "Writing…" : "Explain this member's profile"}
            </button>
            <span className="text-xs text-zinc-500">Conditions, utilization and decision history, in plain English.</span>
          </div>
          {brief && (
            <div>
              <div className="text-sm leading-relaxed"><Markdown>{brief.text}</Markdown></div>
              <div className="text-xs text-zinc-500 mt-2">Source: {brief.source}. Descriptive only — not medical advice.</div>
              {brief.warnings?.map((w, i) => <div key={i} className="text-amber-300 text-xs mt-1">⚠ {w}</div>)}
            </div>
          )}
        </Card>

        {/* claim history */}
        <Card title="Claim history"
              caption={`Newest first, up to 200 of ${profile.total_claims}. Click a claim for the full trace.`}>
          <DataTable
            cols={[
              { key: "claim_id", label: "Claim", render: (r) => (
                  <button className="text-sky-300 num hover:underline"
                          onClick={() => setClaimId(r.claim_id)}>
                    {r.claim_id}
                  </button>
                ) },
              { key: "from_dt", label: "Date" },
              { key: "provider_id", label: "Provider", render: (r) => <span className="num">{r.provider_id}</span> },
              { key: "decision", label: "Decision", render: (r) => <DecisionBadge decision={r.decision} /> },
              { key: "primary_rule", label: "Rule", render: (r) => <span className="num text-sky-300">{r.primary_rule || "-"}</span> },
              { key: "allowed_amt", label: "Billed", numeric: true, render: (r) => fmtMoney(r.allowed_amt) },
              { key: "plan_paid_amt", label: "Plan pays", numeric: true, render: (r) => fmtMoney(r.plan_paid_amt) },
              { key: "fwa_score", label: "FWA", numeric: true, render: (r) => (r.fwa_score ? r.fwa_score.toFixed(2) : "-") },
            ]}
            rows={profile.claims}
            keyField="claim_id"
            maxHeight="24rem"
          />
        </Card>

        {claimId && <ClaimDetail claimId={claimId} dataset={dataset} onClose={() => setClaimId(null)} />}
      </div>
    );
  }

  // ------------------------------------------------------------ list view
  if (!list) return <SkeletonPage tiles={4} />;
  const { rows, total } = list;

  return (
    <div className="space-y-4">
      {/* KPI band */}
      <div className="grid grid-cols-4 gap-3">
        <StatTile label="Members (filtered)" value={total.toLocaleString()} />
        <StatTile label="With flagged claims" value={list.flagged_members.toLocaleString()}
                  color={list.flagged_members ? "#d97706" : undefined} />
        <StatTile label="With flagged conditions" value={list.with_condition.toLocaleString()} />
        <StatTile label="Billed (filtered)" value={fmtMoney(list.billed_total)} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5 items-start">
        {/* left: filters + list */}
        <div className="xl:col-span-2">
      <Card caption="Patient profiles: chronic conditions, diagnoses and claim history per member. Synthetic data — a flag is a prompt for review, not a finding.">
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <input value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }}
                 placeholder="Search member ID…"
                 className="bg-zinc-900 border border-zinc-700 rounded-md px-3 py-1.5 text-sm text-zinc-200 w-56" />
          <div className="flex flex-wrap gap-1.5">
            {CONDITIONS.map(([key, label]) => (
              <button key={key} onClick={() => { setCondition(condition === key ? null : key); setPage(1); }}
                      className={`rounded-full px-2.5 py-1 text-xs border transition-colors ${
                        condition === key
                          ? "bg-teal-500/15 text-sky-300 border-teal-900/50"
                          : "bg-zinc-900 text-zinc-400 border-zinc-700 hover:text-zinc-200"
                      }`}>
                {label}
              </button>
            ))}
          </div>
        </div>
        <DataTable
          cols={[
            { key: "member_id", label: "Member", render: (r) => (
                <button className="text-sky-300 num hover:underline" onClick={() => setCtxMember(r.member_id)}>
                  {r.member_id}
                </button>
              ) },
            { key: "plan", label: "Plan" },
            { key: "age", label: "Age", numeric: true },
            { key: "conditions", label: "Conditions", render: (r) => (
                <span className="flex flex-wrap gap-1">
                  {r.conditions.slice(0, 3).map((c) => (
                    <span key={c} className="rounded-full px-2 py-0.5 text-[10px] bg-teal-500/10 text-sky-300 border border-teal-900/30">{c}</span>
                  ))}
                  {r.conditions.length > 3 && (
                    <span className="text-[10px] text-zinc-500 pt-0.5">+{r.conditions.length - 3}</span>
                  )}
                  {r.conditions.length === 0 && <span className="text-[10px] text-zinc-500">none</span>}
                </span>
              ) },
            { key: "claims", label: "Claims", numeric: true },
            { key: "billed", label: "Billed", numeric: true, render: (r) => (
                <DataBar value={r.billed} display={fmtMoney(r.billed)}
                         max={Math.max(...rows.map((x) => x.billed), 1)} color="#14b8a6" />
              ) },
            { key: "fwa_flags", label: "Flags", numeric: true, render: (r) => (
                <span className={r.fwa_flags ? "text-amber-300 num" : "text-zinc-500 num"}>{r.fwa_flags}</span>
              ) },
          ]}
          rows={rows}
          keyField="member_id"
          onRowClick={(r) => setCtxMember(r.member_id)}
          maxHeight="34rem"
        />
        <div className="flex items-center gap-3 text-xs text-zinc-500 mt-2 num">
          {total.toLocaleString()} members · page {page} / {Math.max(1, Math.ceil(total / pageSize))}
          <button className="rounded-md border border-zinc-700 px-2 py-1 hover:bg-zinc-800 disabled:opacity-30"
                  disabled={page <= 1} onClick={() => setPage(page - 1)}>← Prev</button>
          <button className="rounded-md border border-zinc-700 px-2 py-1 hover:bg-zinc-800 disabled:opacity-30"
                  disabled={page >= Math.ceil(total / pageSize)} onClick={() => setPage(page + 1)}>Next →</button>
        </div>
      </Card>
        </div>

        {/* right: live context pane */}
        <div className="xl:sticky xl:top-36 space-y-4">
          {ctxProfile ? (
            <>
              <Card hover={false}>
                <div className="flex items-center justify-between gap-2 mb-2">
                  <span className="font-semibold num text-sm">{ctxProfile.member_id}</span>
                  <button onClick={() => openMember(ctxProfile.member_id)}
                          className="text-xs text-sky-300 hover:underline">Full profile →</button>
                </div>
                <div className="text-xs text-zinc-500 mb-3">
                  {ctxProfile.plan ?? "—"} plan · age {ctxProfile.age ?? "—"} · {ctxProfile.sex ?? "—"}
                  {ctxProfile.died ? ` · deceased ${ctxProfile.died}` : ""}
                </div>
                <div className="flex flex-wrap gap-1.5 mb-3">
                  {ctxProfile.conditions.length === 0 && <span className="text-xs text-zinc-500">No flagged conditions.</span>}
                  {ctxProfile.conditions.map((c) => (
                    <span key={c} className="rounded-full px-2 py-0.5 text-[10px] bg-teal-500/10 text-sky-300 border border-teal-900/30">{c}</span>
                  ))}
                </div>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div className="glass rounded-lg px-3 py-2">
                    <div className="text-zinc-500">Claims</div>
                    <div className="font-semibold num">{ctxProfile.total_claims}</div>
                  </div>
                  <div className="glass rounded-lg px-3 py-2">
                    <div className="text-zinc-500">Billed</div>
                    <div className="font-semibold num">{fmtMoneyCompact(ctxProfile.billed)}</div>
                  </div>
                </div>
              </Card>
              <Card title="Top diagnoses">
                {ctxProfile.top_dx.slice(0, 4).map((d) => (
                  <div key={d.dx_code} className="flex items-center justify-between text-xs py-0.5">
                    <span><span className="num text-zinc-200">{d.dx_code}</span>
                      <span className="text-zinc-400 ml-2">{dxName(d.dx_code)}</span></span>
                    <span className="text-zinc-500 num">{d.count}</span>
                  </div>
                ))}
                {ctxProfile.top_dx.length === 0 && <div className="text-xs text-zinc-500">None recorded.</div>}
              </Card>
            </>
          ) : (
            <Card className="border-dashed">
              <div className="text-xs text-zinc-500 py-6 text-center">
                Select a member on the left — their conditions, diagnoses and stats open here,
                without leaving the page.
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
