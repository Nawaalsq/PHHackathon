"""
app.py - Reviewer board for the Claim Decision Engine.

Run from the folder that holds clean/ and answer_keys/:
    streamlit run rd_engine/app.py

The base run (live rules) loads precomputed results from results/<folder-name>/
when they exist and match the rule book — run `python precompute.py --data
<folder>` to create them. Without precomputed results, or for what-if
scenarios and AI drafts, the engine runs live.

Tabs: Overview | Claim review | FWA queue | Provider triage | What-if | AI rule drafting |
      Rule conflicts | Rules | Evaluation
"""

import json
import os

import numpy as np
import pandas as pd
import streamlit as st

import yaml

import ai
import engine
import evaluate

st.set_page_config(page_title="Claim Decision Engine", layout="wide")

DECISIONS = ["paid", "partial", "pended", "denied", "rejected"]
BADGE = {"paid": "🟢", "partial": "🟡", "pended": "🟠", "denied": "🔴", "rejected": "⚫"}
NOTE = "Synthetic CMS DE-SynPUF claims with fictional rules. Amounts are in the data's units."


# ====================================================================== cached loaders
@st.cache_data(show_spinner=False)
def all_rules():
    return engine.load_rules(include_proposals=True)


@st.cache_data(show_spinner="Running the rules over every claim…")
def run_engine(data, rule_ids, drafts_yaml=""):
    rules = [r for r in all_rules() if r["id"] in rule_ids]
    for r in (yaml.safe_load(drafts_yaml) or []):
        r = dict(r, source_file="ai_drafts", fwa=False)
        r.setdefault("scope", {}); r.setdefault("params", {})
        rules.append(r)
    res = engine.run(data, rules=rules, verbose=False)
    return {k: res[k] for k in ["decisions", "hits", "conflicts", "summary"]}


@st.cache_data(show_spinner="Loading precomputed results…")
def load_precomputed(data):
    """Results written by precompute.py to results/<folder-name>/, or None."""
    out = os.path.join("results", os.path.basename(os.path.normpath(data)))
    if not os.path.exists(os.path.join(out, "decisions.parquet")):
        return None
    res = {
        "decisions": pd.read_parquet(os.path.join(out, "decisions.parquet")),
        "hits": pd.read_parquet(os.path.join(out, "hits.parquet")),
        "conflicts": pd.read_csv(os.path.join(out, "conflicts.csv")),
    }
    with open(os.path.join(out, "summary.json")) as fh:
        res["summary"] = json.load(fh)
    with open(os.path.join(out, "rule_ids.json")) as fh:
        res["rule_ids"] = json.load(fh)
    return res


def base_results(data, rule_ids):
    """Precomputed base run for this data + live rule set, if it still matches."""
    pre = load_precomputed(data)
    if pre is not None and pre["rule_ids"] == sorted(rule_ids):
        return pre
    return None


@st.cache_data(show_spinner="Loading claim details…")
def side_tables(data):
    t = engine.load_tables(data)
    plans_cfg, _ = engine.load_config()
    mp = t["member_plans"] if t["member_plans"] is not None else \
        engine.assign_plans(t["members"]["member_id"], plans_cfg)
    return t["claim_lines"], t["members"], mp, t["claims"]


@st.cache_data(show_spinner=False)
def load_keys(keys_dir):
    p = os.path.join(keys_dir, "anomalies.csv")
    return pd.read_csv(p, dtype=str) if os.path.exists(p) else None


@st.cache_data(show_spinner="Computing peer comparisons…")
def peer_rates(data):
    """Services per patient per (code, provider) against the peer median, for the
    provider triage tab."""
    lines = engine.load_tables(data)["claim_lines"]
    g = (lines.groupby(["code", "provider_id"])
              .agg(count=("claim_id", "size"), patients=("member_id", "nunique")).reset_index())
    g["rate"] = g["count"] / g["patients"]
    med = g[g["patients"] >= 5].groupby("code")["rate"].median()
    g["peer_median"] = g["code"].map(med)
    g["ratio"] = g["rate"] / g["peer_median"]
    return g


def money(x):
    return f"{x:,.0f}"


def rule_label(r):
    return f"{r['id']} · {r['clause'][:80]}"


# ====================================================================== explanation
def plain_explanation(row, h, rules_by_id):
    """Plain-language explanation built only from the decision trace."""
    act = h[h["suppressed_by"].isna() & (h["layer"] != "anomaly") & (h["outcome"] != "allow")]
    dec = row["decision"]
    parts = []
    if dec == "paid":
        parts.append("**Paid.** No coverage or edit rule stopped this claim.")
    elif dec == "rejected":
        r = act[act["outcome"] == "reject"].iloc[0]
        parts.append(f"**Rejected for correction.** {r['reason']} (rule {r['rule_id']}). "
                     "The provider needs to fix and resubmit the claim.")
    elif dec == "denied":
        claim_level = act[act["outcome"] == "deny"]
        if len(claim_level):
            r = claim_level.sort_values("priority", ascending=False).iloc[0]
            parts.append(f"**Denied.** {r['reason']} (rule {r['rule_id']}).")
        else:
            parts.append("**Denied.** Every service on the claim was refused:")
    elif dec == "pended":
        refs = act[act["outcome"] == "refer"].drop_duplicates("rule_id")
        parts.append("**Held for review.** " + " ".join(
            f"{r['reason']} (rule {r['rule_id']})." for _, r in refs.iterrows()))
    elif dec == "partial":
        parts.append(f"**Partly paid.** {row['denied_line_share']:.0%} of the services were refused:")

    lines = act[act["outcome"] == "deny_line"].drop_duplicates(["code", "rule_id"])
    if dec in ("partial", "denied") and len(lines):
        for _, r in lines.iterrows():
            parts.append(f"- **{r['code']}**: {r['reason']} (rule {r['rule_id']})")

    sup = h[h["suppressed_by"].notna()]
    for _, r in sup.drop_duplicates("rule_id").iterrows():
        parts.append(f"- Rule {r['rule_id']} would have refused {r['code']}, but exception "
                     f"{r['suppressed_by']} overrides it.")

    if row["fwa_score"] > 0:
        parts.append(f"**Fraud review:** score {row['fwa_score']:.2f}. {row['fwa_reasons']}.")
    if dec in ("paid", "partial"):
        parts.append(f"Plan pays **{money(row['plan_paid_amt'])}** of {money(row['allowed_amt'])} "
                     f"({row['plan']} plan, member coinsurance {row['coinsurance']:.0%}).")
    return "\n\n".join(parts)


# ====================================================================== appeal guidance
# Next-steps for a denied/rejected claim (use case P026): the evidence or fix that
# would change the decision. Deterministic, derived from the trace - the rules decide,
# this only restates what the trace already says.
APPEAL_HINTS = {
    "COV-1.1": "Provide a valid eligibility (Part B) record for the service year and resubmit.",
    "COV-1.2": "The service date is after the recorded death date. Appeal only with corrected service dates.",
    "COV-1.3": "The provider was excluded on the service date. Appeal only if the exclusion date or the provider on the claim is wrong.",
    "COV-2.1": "The policy-year limit was reached by earlier services. Check the earlier claims; if one is wrong, correct it first - or reschedule in the next policy year.",
    "COV-2.2": "Provide clinical evidence of ESRD (or chronic kidney disease) on record for re-adjudication.",
    "COV-2.3": "Provide evidence of diabetes on record for the higher limit - or reschedule in the next policy year.",
    "COV-2.4": "Provide evidence of diabetes on record for the higher limit - or reschedule in the next policy year.",
    "COV-3.1": "The service fell inside the waiting period from the policy start date. Check the policy start date and the service date.",
    "EDIT-01": "The service was inside an inpatient stay. If the stay dates are wrong, correct the stay first and resubmit.",
    "EDIT-03": "If this is not a true duplicate, correct the claim dates, provider or amount and resubmit.",
    "EDIT-04": "If the earlier same-day claim is not the same service, correct one of the two claims.",
}


def appeal_guidance(row, rules_by_id):
    if row["decision"] not in ("denied", "rejected"):
        return None
    if row["decision"] == "rejected":
        return ("**Rejected for correction** - a data-quality rejection, not a coverage decision. "
                "Fix the reported issue and resubmit; no appeal is needed.")
    rid = row["primary_rule"]
    hint = APPEAL_HINTS.get(rid) if isinstance(rid, str) else None
    if not hint:
        hint = "Appeal with documents showing this rule does not apply to the claim as submitted."
    return (f"**Denial reason:** {row['primary_reason']} (rule {rid}).\n\n"
            f"**To appeal or fix:** {hint}")


@st.cache_data(show_spinner="Loading clinical details…")
def clinical_tables(data):
    out = []
    for name in ["diagnoses", "procedures"]:
        p = os.path.join(data, f"{name}.parquet")
        out.append(pd.read_parquet(p) if os.path.exists(p) else None)
    return out[0], out[1]


# ====================================================================== sidebar
rules = all_rules()
rules_by_id = {r["id"]: r for r in rules}
live_ids = [r["id"] for r in rules if r["source_file"] != engine.PROPOSAL_FILE]
prop_ids = [r["id"] for r in rules if r["source_file"] == engine.PROPOSAL_FILE]

st.sidebar.title("Claim Decision Engine")
default_data = next((p for p in ["clean/sample2_seeded", "clean/sample1_dev_seeded", "clean/sample1_dev"]
                     if os.path.exists(p)), "clean/sample1_dev")
data = st.sidebar.text_input("Data folder", default_data)
default_keys = next((p for p in ["answer_keys/sample2", "answer_keys/sample1_dev"]
                     if os.path.exists(p)), "answer_keys/sample2")
keys_dir = st.sidebar.text_input("Answer keys folder (for evaluation)", default_keys)

if not os.path.exists(os.path.join(data, "claims.parquet")):
    st.error(f"No claims.parquet in '{data}'. Check the data folder path (relative to "
             f"{os.getcwd()}).")
    st.stop()

if "drafts" not in st.session_state:
    st.session_state.drafts = []
if "ai_out" not in st.session_state:
    st.session_state.ai_out = {}
if "scenarios" not in st.session_state:
    st.session_state.scenarios = {}
for r in st.session_state.drafts:
    rules_by_id[r["id"]] = dict(r, source_file="ai_drafts")

st.sidebar.markdown("### What-if")
add = st.sidebar.multiselect("Add proposed rules", prop_ids,
                             format_func=lambda i: rule_label(rules_by_id[i]))
off = st.sidebar.multiselect("Switch off live rules", live_ids,
                             format_func=lambda i: rule_label(rules_by_id[i]))
if st.session_state.drafts:
    st.sidebar.markdown("**AI-drafted rules in the what-if:** " +
                        ", ".join(r["id"] for r in st.session_state.drafts))
base_ids = tuple(live_ids)
scen_ids = tuple([i for i in live_ids if i not in off] + list(add))
drafts_yaml = yaml.safe_dump(st.session_state.drafts, sort_keys=True) if st.session_state.drafts else ""
changed = set(scen_ids) != set(base_ids) or bool(drafts_yaml)

st.sidebar.markdown("### AI")
st.sidebar.caption(f"Backend: **{ai.backend()}**. AI drafts rules and writes explanations; "
                   "the rules make every decision.")
if st.sidebar.button("Test AI connection"):
    ok, msg = ai.check_backend()
    (st.sidebar.success if ok else st.sidebar.warning)(msg)
st.sidebar.caption(NOTE)

base = base_results(data, base_ids)
if base is None:
    base = run_engine(data, base_ids)
scen = run_engine(data, scen_ids, drafts_yaml) if changed else base
if load_precomputed(data) is not None:
    st.sidebar.caption("✅ Precomputed base results (precompute.py)")
else:
    st.sidebar.caption("Base run executed live — run `python precompute.py --data <folder>` "
                       "to speed up start-up.")
d, h = base["decisions"], base["hits"]
d_ix = d.set_index("claim_id")
keys = load_keys(keys_dir)

tabs = st.tabs(["Overview", "Claim review", "FWA queue", "Provider triage", "What-if",
                "AI rule drafting", "Rule conflicts", "Rules", "Evaluation"])

# ====================================================================== overview
with tabs[0]:
    st.header("Overview (live rules)")
    s = base["summary"]
    counts = d["decision"].value_counts().reindex(DECISIONS).fillna(0).astype(int)
    c = st.columns(6)
    c[0].metric("Claims", f"{len(d):,}")
    for i, k in enumerate(DECISIONS):
        c[i + 1].metric(f"{BADGE[k]} {k.title()}", f"{counts[k]:,}", f"{counts[k] / len(d):.1%}",
                        delta_color="off")
    c = st.columns(4)
    c[0].metric("Billed (allowed)", money(s["allowed_amt"]))
    c[1].metric("Payable", money(s["payable_amt"]))
    c[2].metric("Plan pays", money(s["plan_paid_amt"]))
    c[3].metric("Held (pended)", money(d["held_amt"].sum()))

    left, right = st.columns([1, 2])
    with left:
        st.subheader("Decisions")
        st.bar_chart(counts, horizontal=True)
        st.subheader("Where the billed money goes")
        mb = d.groupby("decision")[["allowed_amt", "payable_amt"]].sum().reindex(DECISIONS)
        mb.columns = ["Billed", "Payable"]
        st.bar_chart(mb, horizontal=True)
        st.caption("Billed vs payable by decision: denied and rejected claims keep most of their "
                   "billed value unpaid; pended amounts are held, not paid.")
    with right:
        st.subheader("Rules that fired")
        top = (h.groupby("rule_id").agg(hits=("claim_id", "size"), claims=("claim_id", "nunique"))
                .sort_values("claims", ascending=False).reset_index())
        top["clause"] = top["rule_id"].map(lambda i: rules_by_id[i]["clause"])
        top["layer"] = top["rule_id"].map(lambda i: rules_by_id[i]["layer"])
        st.dataframe(top[["rule_id", "layer", "claims", "hits", "clause"]], hide_index=True,
                     height=420)

# ====================================================================== claim review
with tabs[1]:
    st.header("Claim review")
    f1, f2, f3 = st.columns(3)
    dsel = f1.selectbox("Decision", ["all"] + DECISIONS, index=0)
    rsel = f2.selectbox("Rule fired", ["any"] + sorted(h["rule_id"].unique()))
    q = f3.text_input("Claim or member ID contains", "")
    view = d
    if dsel != "all":
        view = view[view["decision"] == dsel]
    if rsel != "any":
        view = view[view["claim_id"].isin(set(h.loc[h["rule_id"] == rsel, "claim_id"]))]
    if q:
        view = view[view["claim_id"].str.contains(q, case=False) |
                    view["member_id"].str.contains(q, case=False)]
    st.caption(f"{len(view):,} claims match")
    if view.empty:
        st.info("No claims match these filters.")
    else:
        # labels built once per render - a format_func that re-indexes `view`
        # per option takes minutes on a full sample
        vv = view.head(1000)[["claim_id", "decision", "primary_rule"]].copy()
        vv["primary_rule"] = vv["primary_rule"].fillna("")
        labels = {r.claim_id: f"{r.claim_id} · {r.decision} · {r.primary_rule}"
                  for r in vv.itertuples()}
        pick = st.selectbox("Claim", vv["claim_id"].tolist(),
                            format_func=lambda cid: labels.get(cid, cid))
        row = d_ix.loc[pick].copy()
        row["primary_rule"] = row["primary_rule"] if pd.notna(row["primary_rule"]) else ""
        ch = h[h["claim_id"] == pick].sort_values("priority", ascending=False)

        row["claim_id"] = pick
        st.subheader(f"{BADGE[row['decision']]} {row['decision'].upper()} · claim {pick}")
        base_text = plain_explanation(row, ch, rules_by_id)
        st.markdown(base_text)
        guide = appeal_guidance(row, rules_by_id)
        if guide:
            with st.expander("📌 Appeal / next steps", expanded=False):
                st.markdown(guide)

        with st.expander("✨ AI explanation for the member or provider", expanded=False):
            e1, e2, e3 = st.columns([1, 1, 1])
            aud = e1.radio("Written for", ["member", "provider"], horizontal=True, key="aud")
            lang = e2.selectbox("Language", ["English", "Arabic"], key="lang")
            akey = f"exp|{pick}|{aud}|{lang}"
            if e3.button("Write explanation", key="btn_exp"):
                with st.spinner("Asking the AI…"):
                    st.session_state.ai_out[akey] = ai.explain_claim(
                        row, ch, rules_by_id, aud, lang,
                        fallback=base_text.replace("**", ""))
            if akey in st.session_state.ai_out:
                text, src, warns = st.session_state.ai_out[akey]
                st.markdown(f"<div dir=\"{'rtl' if lang == 'Arabic' and 'offline' not in src else 'ltr'}\">"
                            f"{text}</div>", unsafe_allow_html=True)
                st.caption(f"Source: {src}. Written only from the decision trace above; "
                           "rule IDs are checked against it.")
                for w in warns:
                    st.warning(w)

        lines_t, members_t, mp_t, claims_t = side_tables(data)
        dx_t, pr_t = clinical_tables(data)
        a, b2 = st.columns(2)
        with a:
            st.markdown("**Claim**")
            cl = claims_t.set_index("claim_id").loc[pick]
            info = {"Type": row["claim_type"], "Service date": pd.Timestamp(row["from_dt"]).date(),
                    "Provider": row["provider_id"], "Billed": money(row["allowed_amt"]),
                    "Payable": money(row["payable_amt"]), "Plan pays": money(row["plan_paid_amt"]),
                    "Held": money(row["held_amt"]), "FWA score": f"{row['fwa_score']:.2f}"}
            if row["claim_type"] == "inpatient":
                info.update({"Admitted": pd.Timestamp(cl["admit_dt"]).date(),
                             "Discharged": pd.Timestamp(cl["discharge_dt"]).date(),
                             "DRG": cl["drg_cd"]})
            if dx_t is not None:
                dx = dx_t[dx_t["claim_id"] == pick]
                info["Diagnoses"] = ", ".join(dx["dx_code"].astype(str).head(8)) or "-"
            if pr_t is not None:
                pr = pr_t[pr_t["claim_id"] == pick]
                info["Procedures"] = ", ".join(pr["proc_code"].astype(str).head(8)) or "-"
            st.dataframe(pd.DataFrame(info.items(), columns=["Field", "Value"]).astype(str),
                         hide_index=True)
        with b2:
            st.markdown("**Member**")
            mid = row["member_id"]
            yr = pd.Timestamp(cl["thru_dt"] if pd.notna(cl["thru_dt"]) else row["from_dt"]).year
            mrow = members_t[(members_t["member_id"] == mid) & (members_t["year"] == yr)]
            plan = mp_t[mp_t["member_id"] == mid]
            minfo = {"Member": mid, "Plan": row["plan"],
                     "Policy start": pd.Timestamp(plan["policy_start"].iloc[0]).date() if len(plan) else "-"}
            if len(mrow):
                m = mrow.iloc[0]
                conds = [c[3:].replace("_", " ") for c in members_t.columns
                         if c.startswith("cc_") and bool(m[c])]
                minfo.update({f"Part B months ({yr})": m["months_part_b"], "ESRD": bool(m["esrd"]),
                              "Conditions": ", ".join(conds) or "none",
                              "Died": pd.Timestamp(m["death_dt"]).date() if pd.notna(m["death_dt"]) else "-"})
            else:
                minfo["Member record"] = f"none for {yr}"
            st.dataframe(pd.DataFrame(minfo.items(), columns=["Field", "Value"]).astype(str),
                         hide_index=True)

        st.markdown("**Decision trace** (every rule that fired, highest priority first)")
        if ch.empty:
            st.caption("No rules fired.")
        else:
            tr = ch[["rule_id", "layer", "outcome", "code", "priority", "reason", "suppressed_by"]].copy()
            tr["clause"] = tr["rule_id"].map(lambda i: rules_by_id[i]["clause"])
            st.dataframe(tr, hide_index=True)
        cl_lines = lines_t[lines_t["claim_id"] == pick]
        if len(cl_lines):
            st.markdown("**Services on the claim**")
            st.dataframe(cl_lines[["code", "repeat_count", "code_type", "is_drug_code"]],
                         hide_index=True)

# ====================================================================== FWA queue
with tabs[2]:
    st.header("Fraud, waste and abuse review queue")
    st.caption("Claims ranked by FWA score. A score combines every FWA signal on the claim; "
               "facility outliers score higher the more extreme they are. Flags are prompts "
               "for review, not findings of fraud.")
    qd = d[d["fwa_score"] > 0].sort_values(["fwa_score", "allowed_amt"], ascending=False).copy()
    det = sorted(h.loc[h["fwa"].astype(bool), "rule_id"].unique())
    pick_det = st.multiselect("Signals", det, default=det,
                              format_func=lambda i: rule_label(rules_by_id[i]))
    sig = set(h.loc[h["rule_id"].isin(pick_det), "claim_id"])
    qd = qd[qd["claim_id"].isin(sig)]
    qd["rank"] = np.arange(1, len(qd) + 1)
    if keys is not None:
        pat = keys.drop_duplicates("claim_id").set_index("claim_id")["pattern"]
        qd["seeded_pattern"] = qd["claim_id"].map(pat)
    c = st.columns(3)
    c[0].metric("Claims in queue", f"{len(qd):,}")
    c[1].metric("Share of all claims", f"{len(qd) / len(d):.2%}")
    c[2].metric("Billed value in queue", money(qd["allowed_amt"].sum()))
    if keys is not None:
        target = evaluate.workload_target(keys)
        if target:
            found = np.cumsum(qd["claim_id"].isin(target).to_numpy())
            curve = pd.DataFrame({"claims opened": qd["rank"], "% of seeded FWA caught":
                                  100 * found / len(target)}).set_index("claims opened")
            k80 = int(np.argmax(found >= 0.8 * len(target))) + 1 if found[-1] >= 0.8 * len(target) else None
            st.markdown(f"**Reviewer workload:** open **{k80:,}** claims to catch 80% of "
                        f"{len(target):,} seeded FWA claims." if k80 else
                        "Fewer than 80% of seeded FWA claims are in the queue.")
            st.line_chart(curve, height=220)
    cols = ["rank", "claim_id", "member_id", "provider_id", "decision", "fwa_score",
            "allowed_amt", "fwa_reasons"] + (["seeded_pattern"] if keys is not None else [])

    # reviewer workflow: work the queue, then save/export (use case P027)
    if "reviewed" not in st.session_state:
        st.session_state.reviewed = {}
    qd["review"] = qd["claim_id"].map(st.session_state.reviewed).fillna("pending")
    review_cols = cols + ["review"]
    edited = st.data_editor(
        qd[review_cols].head(2000), hide_index=True, height=480, key="queue_editor",
        disabled=review_cols[:-1],
        column_config={"review": st.column_config.SelectboxColumn(
            "Review", options=["pending", "confirmed", "dismissed"],
            default="pending", width="small")})
    done = edited[edited["review"] != "pending"]
    c2 = st.columns(3)
    c2[0].metric("Confirmed for investigation", int((edited["review"] == "confirmed").sum()))
    c2[1].metric("Dismissed", int((edited["review"] == "dismissed").sum()))
    c2[2].metric("Worked so far", f"{int((edited['review'] != 'pending').sum()):,} / {len(qd):,}")
    a1, a2 = st.columns(2)
    if a1.button("Save review decisions"):
        st.session_state.reviewed.update(dict(zip(done["claim_id"], done["review"])))
        st.success(f"Saved {len(done)} review decisions.")
    a2.download_button("Export the queue (CSV)",
                       edited.to_csv(index=False).encode("utf-8"),
                       file_name="fwa_queue.csv", mime="text/csv")

    if len(qd):
        st.subheader("✨ AI case note for a reviewer")
        qh = qd.head(300)[["claim_id", "rank", "fwa_score"]].copy()
        qlabels = {r.claim_id: f"#{int(r.rank)} · {r.claim_id} · score {r.fwa_score:.2f}"
                   for r in qh.itertuples()}
        fpick = st.selectbox("Claim from the queue", qh["claim_id"].tolist(),
                             format_func=lambda cid: qlabels.get(cid, cid))
        fkey = f"note|{fpick}"
        if st.button("Write case note", key="btn_note"):
            frow = d_ix.loc[fpick].copy()
            frow["claim_id"] = fpick
            fh = h[h["claim_id"] == fpick].sort_values("priority", ascending=False)
            pdd = d[d["provider_id"] == frow["provider_id"]]
            lines_t, _, _, _ = side_tables(data)
            top_codes = (lines_t[lines_t["provider_id"] == frow["provider_id"]]["code"]
                         .value_counts().head(3))
            pstats = (f"{len(pdd):,} claims in this data, {int((pdd['fwa_score'] > 0).sum())} flagged, "
                      f"{pdd['member_id'].nunique():,} distinct members; most billed codes: "
                      + ", ".join(f"{c} ({n})" for c, n in top_codes.items()))
            with st.spinner("Asking the AI…"):
                st.session_state.ai_out[fkey] = ai.fwa_case_note(frow, fh, rules_by_id, pstats)
        if fkey in st.session_state.ai_out:
            text, src, warns = st.session_state.ai_out[fkey]
            st.markdown(text)
            st.caption(f"Source: {src}. A flag is a prompt for review, not a finding.")
            for w in warns:
                st.warning(w)

# ====================================================================== provider triage
with tabs[3]:
    st.header("Provider triage")
    st.caption("Claims and FWA flags grouped by provider. The peer comparison is this provider's "
               "services per patient against the median facility for the same code; ratios far "
               "above the median are prompts for review, not findings of fraud.")
    prov = (d.groupby("provider_id")
              .agg(claims=("claim_id", "size"), members=("member_id", "nunique"),
                   flagged=("fwa_score", lambda s: int((s > 0).sum())),
                   billed=("allowed_amt", "sum"))
              .sort_values(["flagged", "billed"], ascending=False))
    top_provs = prov.head(300).reset_index()
    top_provs["label"] = [f"{r['provider_id']} · {r['flagged']} flagged · {r['claims']:,} claims"
                          for _, r in top_provs.iterrows()]
    pick_p = st.selectbox("Provider", top_provs["provider_id"],
                          format_func=lambda p: top_provs.set_index("provider_id").at[p, "label"])
    prow = d[d["provider_id"] == pick_p]
    pcount = prov.loc[pick_p]
    c = st.columns(5)
    c[0].metric("Claims", f"{int(pcount['claims']):,}")
    c[1].metric("Members", f"{int(pcount['members']):,}")
    c[2].metric("FWA-flagged", f"{int(pcount['flagged']):,}",
                f"{int(pcount['flagged']) / max(1, int(pcount['claims'])):.1%}", delta_color="off")
    c[3].metric("Billed", money(pcount["billed"]))
    c[4].metric("Plan pays", money(prow["plan_paid_amt"].sum()))

    pcodes = peer_rates(data)
    pcodes = pcodes[pcodes["provider_id"] == pick_p].sort_values("count", ascending=False).head(10)
    pcodes["flag"] = np.where(pcodes["ratio"] >= 4, "⚠️", "")
    st.markdown("**Top codes vs peers** (services per patient; peer median for the same code)")
    st.dataframe(pcodes[["code", "count", "patients", "rate", "peer_median", "ratio", "flag"]],
                 hide_index=True)

    lines_t, _, _, _ = side_tables(data)
    a, b = st.columns(2)
    with a:
        st.markdown("**Office-visit mix by year** (99212–99215)")
        ov = lines_t[(lines_t["provider_id"] == pick_p) &
                     lines_t["code"].isin(["99212", "99213", "99214", "99215"])]
        if len(ov):
            mix = ov.groupby([ov["service_dt"].dt.year, "code"]).size().unstack(fill_value=0)
            mix.index.name = "year"
            st.bar_chart(mix)
        else:
            st.caption("No office-visit codes for this provider.")
    with b:
        st.markdown("**FWA flags by month**")
        fl = prow[prow["fwa_score"] > 0].copy()
        fl["month"] = pd.to_datetime(fl["from_dt"]).dt.to_period("M").astype(str)
        if len(fl):
            st.line_chart(fl.groupby("month").size())
        else:
            st.caption("No flagged claims for this provider.")

    fc = prow[prow["fwa_score"] > 0].sort_values("fwa_score", ascending=False)
    st.markdown(f"**Flagged claims for this provider** ({len(fc):,})")
    st.dataframe(fc[["claim_id", "member_id", "from_dt", "decision", "fwa_score", "fwa_reasons"]]
                 .head(500), hide_index=True)

# ====================================================================== what-if
with tabs[3]:
    st.header("What-if simulator")
    if not changed:
        st.info("Use the sidebar to add proposed rules or switch off live rules. "
                "The board reruns every claim and shows what would change.")
    else:
        st.markdown("**Change:** " + "; ".join(
            [f"add {i}" for i in add] + [f"switch off {i}" for i in off] +
            [f"add AI draft {r['id']}" for r in st.session_state.drafts]))
        a_ = base["decisions"][["claim_id", "decision", "plan_paid_amt", "primary_rule"]]
        b_ = scen["decisions"][["claim_id", "decision", "plan_paid_amt", "primary_rule"]]
        m = a_.merge(b_, on="claim_id", suffixes=("_before", "_after"))
        moved = m[m["decision_before"] != m["decision_after"]]
        delta = m["plan_paid_amt_after"].sum() - m["plan_paid_amt_before"].sum()
        c = st.columns(4)
        c[0].metric("Claims that change decision", f"{len(moved):,}")
        c[1].metric("Plan cost change", money(delta), delta=money(delta), delta_color="inverse")
        c[2].metric("Members affected", f"{scen['decisions'].loc[scen['decisions']['claim_id'].isin(moved['claim_id']), 'member_id'].nunique():,}")
        c[3].metric("Conflicts in new rule set", len(scen["conflicts"]))
        before = base["decisions"]["decision"].value_counts().reindex(DECISIONS).fillna(0)
        after = scen["decisions"]["decision"].value_counts().reindex(DECISIONS).fillna(0)
        st.subheader("Decisions before and after")
        st.bar_chart(pd.DataFrame({"before": before, "after": after}), stack=False)
        st.subheader("How claims move")
        mv = (moved.groupby(["decision_before", "decision_after"]).size().rename("claims")
                   .reset_index().sort_values("claims", ascending=False))
        st.dataframe(mv, hide_index=True)
        st.subheader("Changed claims")
        st.dataframe(moved.head(1000), hide_index=True)
        sup = scen["summary"].get("superseded", [])
        if sup:
            st.info("Precedence applied at run time: " + "; ".join(
                f"{x['winner']} replaces {x['superseded']} for {x['for']} ({x['from']} to {x['to']}, {x['criterion']})"
                for x in sup))
        if len(scen["conflicts"]):
            st.warning(f"This rule set has {len(scen['conflicts'])} conflicts. "
                       "See the Rule conflicts tab.")

        # save the scenario and compare it against the baseline later (use case P024)
        st.subheader("Save this scenario")
        name = st.text_input("Scenario name", key="scen_name",
                             help="Saved for this session; compare it against the baseline below.")
        if st.button("Save scenario", key="save_scen") and name.strip():
            st.session_state.scenarios[name.strip()] = {
                "change": "; ".join([f"add {i}" for i in add] + [f"switch off {i}" for i in off] +
                                    [f"add AI draft {r['id']}" for r in st.session_state.drafts]),
                "conflicts": len(scen["conflicts"]),
                "decisions": scen["decisions"][["claim_id", "decision", "plan_paid_amt"]].copy(),
            }
            st.success(f"Saved '{name.strip()}'.")
        if st.session_state.scenarios:
            pick_s = st.selectbox("Saved scenarios (compared against the baseline)",
                                  list(st.session_state.scenarios))
            sv = st.session_state.scenarios[pick_s]
            m2 = base["decisions"][["claim_id", "member_id", "decision", "plan_paid_amt"]].merge(
                sv["decisions"], on="claim_id", suffixes=("_base", "_scenario"))
            mv2 = m2[m2["decision_base"] != m2["decision_scenario"]]
            dl2 = m2["plan_paid_amt_scenario"].sum() - m2["plan_paid_amt_base"].sum()
            c3 = st.columns(4)
            c3[0].metric("Claims that change", f"{len(mv2):,}")
            c3[1].metric("Plan cost change", money(dl2), delta=money(dl2), delta_color="inverse")
            c3[2].metric("Members affected",
                         f"{m2.loc[m2['claim_id'].isin(mv2['claim_id']), 'member_id'].nunique():,}")
            c3[3].metric("Conflicts in scenario", sv["conflicts"])
            st.caption("Change: " + sv["change"])
            if len(mv2):
                mv_table = (mv2.groupby(["decision_base", "decision_scenario"]).size()
                            .rename("claims").reset_index().sort_values("claims", ascending=False))
                st.dataframe(mv_table, hide_index=True)
            d1, _ = st.columns([1, 3])
            if d1.button(f"Delete '{pick_s}'", key="del_scen"):
                del st.session_state.scenarios[pick_s]
                st.rerun()

# ====================================================================== AI rule drafting
with tabs[4]:
    st.header("AI rule drafting")
    st.caption("Type a policy change in plain words. The AI drafts a machine-readable rule, the "
               "app checks it, you approve it, and the What-if tab shows its impact on every claim. "
               "Nothing goes live without a person approving it.")
    examples = ["Silver members get 8 physiotherapy sessions (97110) per policy year.",
                "The Basic plan does not cover therapeutic activities (97530).",
                "Blood draws (36415) are limited to 2 units per claim.",
                "Gold members have a 30-day waiting period for physiotherapy (97110).",
                "HbA1c tests (83036) are limited to 2 per year, or 6 for members with diabetes."]
    ex = st.selectbox("Start from an example (or write your own below)", ["—"] + examples)
    clause = st.text_area("Policy clause", value="" if ex == "—" else ex, height=90)
    if st.button("Draft rule", type="primary", disabled=not clause.strip()):
        existing = set(rules_by_id)
        with st.spinner("Drafting…"):
            rule, ytext, probs, src = ai.draft_rule(clause, existing)
        st.session_state.draft_buf = {"yaml": ytext, "src": src, "clause": clause}
    buf = st.session_state.get("draft_buf")
    if buf:
        st.caption(f"Drafted by: {buf['src']}. Edit the YAML if needed, then approve.")
        edited = st.text_area("Draft rule (YAML)", buf["yaml"], height=300, key="draft_yaml")
        try:
            cand = yaml.safe_load(edited) or {}
        except yaml.YAMLError as e:
            cand, probs = {}, [f"YAML error: {e}"]
        else:
            n = len(st.session_state.drafts) + 1
            cand["id"] = f"DRAFT-{n}"
            probs = ai.validate_rule(cand, set(rules_by_id) - {cand['id']})
        if probs:
            for p_ in probs:
                st.error(p_)
        else:
            st.success(f"Valid rule {cand['id']}: {cand['kind']} on "
                       f"{', '.join(cand['scope'].get('codes', cand['scope'].get('code_prefix', [])))}")
            _, prec_ = engine.load_config()
            live_plus = [r for r in all_rules() if r["id"] in scen_ids] + \
                        [dict(x, source_file="ai_drafts") for x in st.session_state.drafts] + \
                        [dict(cand, source_file="ai_drafts", fwa=False)]
            cc_ = engine.detect_conflicts(live_plus, prec_)
            cc_ = cc_[(cc_["rule_a"] == cand["id"]) | (cc_["rule_b"] == cand["id"])]
            if len(cc_):
                st.warning(f"This draft clashes with {len(cc_)} existing rule(s):")
                st.dataframe(cc_[["rule_a", "rule_b", "type", "detail", "winner", "why"]], hide_index=True)
            else:
                st.info("No conflicts with the current rule set.")
            if st.button("✅ Approve: add to What-if"):
                st.session_state.drafts.append(cand)
                st.session_state.draft_buf = None
                st.rerun()
    if st.session_state.drafts:
        st.subheader("Approved drafts in the What-if")
        for i, r in enumerate(st.session_state.drafts):
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"**{r['id']}** · {r['kind']} · {r['clause']}")
            if c2.button("Remove", key=f"rm{i}"):
                st.session_state.drafts.pop(i)
                st.rerun()
        st.caption("Open the What-if tab to see the impact. To make a draft permanent, copy its YAML "
                   "into rules/coverage.yaml or rules/edits.yaml.")

# ====================================================================== conflicts
with tabs[5]:
    st.header("Rule conflicts")
    _, prec = engine.load_config()
    st.caption("Checks the rule set itself, before any claim is run. The precedence policy "
               "picks a winner for each clash.")
    conf = scen["conflicts"]
    if conf.empty:
        st.success("No conflicts in the current rule set. Add proposed rules in the sidebar to test them.")
    else:
        st.dataframe(conf[["rule_a", "rule_b", "type", "detail", "winner", "criterion", "why"]],
                     hide_index=True)
        for _, r in conf.iterrows():
            with st.expander(f"{r['rule_a']} vs {r['rule_b'] or '—'} · {r['type']}"):
                for rid in [r["rule_a"], r["rule_b"]]:
                    if isinstance(rid, str):
                        st.markdown(f"**{rid}** ({rules_by_id[rid]['source_file']}): "
                                    f"{rules_by_id[rid]['clause']}")
                st.markdown(f"**Winner:** {r['winner'] or 'none'} · {r['why']}")
    with st.expander("Precedence policy"):
        for p in prec:
            st.markdown(f"**{p['id']}** {p['rule']}")

# ====================================================================== rules
with tabs[6]:
    st.header("Rule set")
    rt = pd.DataFrame([{
        "id": r["id"], "status": "on" if (r["id"] in scen_ids or r["source_file"] == "ai_drafts") else "off",
        "source": {"ai_drafts": "AI draft", engine.PROPOSAL_FILE: "proposal"}.get(r["source_file"], "live"),
        "layer": r["layer"], "kind": r["kind"], "outcome": r.get("outcome", "flag"),
        "priority": r.get("priority"), "effective": " to ".join(r["effective"]),
        "clause": r["clause"]} for r in rules + [dict(x, source_file="ai_drafts")
                                                   for x in st.session_state.drafts]])
    st.dataframe(rt, hide_index=True, height=600)
    st.caption("Edit rules in rd_engine/rules/*.yaml, then run `python precompute.py --data <folder>` "
               "to refresh the precomputed results, or restart the app / clear the cache "
               "(menu ⋮ → Clear cache) to rerun live.")

# ====================================================================== evaluation
with tabs[7]:
    st.header("Evaluation against the answer keys")
    if not os.path.exists(os.path.join(keys_dir, "boundary_expected.csv")):
        st.info(f"No answer keys in '{keys_dir}'. Run seed.py on this sample to create them.")
    else:
        bm, bt = evaluate.boundary_metrics(base, keys_dir)
        am = evaluate.anomaly_metrics(base, keys_dir)
        cm = evaluate.conflict_metrics(keys_dir)
        c = st.columns(4)
        c[0].metric("Boundary decisions correct", f"{bm['decision_accuracy']:.0%}", f"{bm['cases']} cases",
                    delta_color="off")
        c[1].metric("Right rule cited", f"{bm['rule_trace_accuracy']:.0%}")
        c[2].metric("Seeded anomalies found", f"{am['overall_recall']:.0%}")
        c[3].metric("Planted conflicts found", f"{cm['detected']}/{cm['expected']}",
                    f"winner right {cm['winner_accuracy']:.0%}", delta_color="off")
        st.subheader("Boundary cases (RD02 / RD04)")
        bt["result"] = np.where(bt["decision_ok"] & bt["rule_ok"], "✅", "❌")
        st.dataframe(bt[["result", "case_id", "description", "expected_decision", "decision",
                         "expected_rule", "primary_rule"]], hide_index=True)
        st.subheader("Seeded anomalies (RD03)")
        lv = am["by_difficulty"]
        cc = st.columns(len(lv))
        for i, (k, v) in enumerate(lv.items()):
            cc[i].metric(f"{k.title()} patterns", f"{v['recall']:.0%}",
                         f"{v['detected']}/{v['seeded']} claims", delta_color="off")
        st.dataframe(pd.DataFrame(am["by_pattern"]).T.reset_index(names="pattern")[
            ["difficulty", "pattern", "detector", "seeded", "detected", "recall"]], hide_index=True)
        if am.get("subtle_novel_fwa_claims_reaching_queue"):
            st.caption("Subtle and novel FWA claims that reach the review queue at all: "
                       + am["subtle_novel_fwa_claims_reaching_queue"])
        st.markdown(f"Reviewer opens **{am['claims_to_open_for_80pct']}** claims to catch 80% of "
                    f"{am['fwa_seeded_claims']} easy seeded FWA claims (queue of {am['fwa_queue_size']}).")
        st.subheader("Rule conflicts (RD04)")
        st.dataframe(pd.DataFrame(cm["detail"]), hide_index=True)
        st.caption("Recall is expected to be high: the detectors and the seeded patterns were "
                   "written together. Reviewer workload and results on an unseen sample are the "
                   "meaningful numbers.")
