"""
api.py - FastAPI backend for the Daman Claim Decision Engine.

Serves the precomputed results written by precompute.py (results/<name>/) as
JSON for the React frontend in web/. Falls back to a live engine.run for
datasets without precomputed results and for what-if scenarios. AI helpers
(drafting, explanations, case notes) wrap rd_engine/ai.py; the rules engine
makes every decision.

Run from the project root (the folder that holds clean/ and results/):
    PY -m uvicorn rd_engine.api:app --reload --port 8000
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)   # "results", "clean/", .ai_cache resolve as in app.py

import functools
import json
from glob import glob

import numpy as np
import pandas as pd
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import ai
import engine
import evaluate
import guidance

app = FastAPI(title="Daman Claim Decision Engine")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])

DECISIONS = guidance.DECISIONS


# ====================================================================== serializers
def records(df, columns=None, n=None):
    """DataFrame -> list[dict] with dates as YYYY-MM-DD strings and NA -> null."""
    out = df.loc[:, columns] if columns is not None else df
    if n is not None:
        out = out.head(n)
    out = out.copy()
    for c in out.columns:
        if str(out[c].dtype).startswith("datetime"):
            out[c] = out[c].dt.strftime("%Y-%m-%d")
    return out.replace({pd.NA: None, np.nan: None}).to_dict("records")


def jsonable(obj):
    """Recursively convert NaN/numpy scalars so json.dumps never chokes."""
    if isinstance(obj, dict):
        return {k: jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, (np.floating, np.integer)):
        return jsonable(obj.item())
    if isinstance(obj, float) and np.isnan(obj):
        return None
    return obj


def series_records(row):
    """One Series -> dict with dates as strings and NA -> null."""
    rec = {}
    for k, v in row.items():
        if isinstance(v, pd.Timestamp):
            rec[k] = None if pd.isna(v) else v.strftime("%Y-%m-%d")
        elif pd.isna(v):
            rec[k] = None
        elif hasattr(v, "item"):
            rec[k] = v.item()
        else:
            rec[k] = v
    return rec


# ====================================================================== cached loaders
@functools.lru_cache(maxsize=8)
def get_res(dataset):
    """Decisions + hits + conflicts + summary, precomputed or live."""
    out = os.path.join("results", os.path.basename(os.path.normpath(dataset)))
    if not os.path.exists(os.path.join(out, "decisions.parquet")):
        res = engine.run(dataset, verbose=False)
        return {"decisions": res["decisions"], "hits": res["hits"],
                "conflicts": res["conflicts"], "summary": res["summary"]}
    decisions = pd.read_parquet(os.path.join(out, "decisions.parquet"))
    hits = pd.read_parquet(os.path.join(out, "hits.parquet"))
    conflicts = pd.read_csv(os.path.join(out, "conflicts.csv"))
    with open(os.path.join(out, "summary.json")) as fh:
        summary = json.load(fh)
    return {"decisions": decisions, "hits": hits, "conflicts": conflicts, "summary": summary}


@functools.lru_cache(maxsize=8)
def get_dix(dataset):
    """Decisions indexed by claim_id."""
    return get_res(dataset)["decisions"].set_index("claim_id")


@functools.lru_cache(maxsize=8)
def get_claims_ix(dataset):
    """Raw claims table indexed by claim_id."""
    return get_tables(dataset)["claims"].set_index("claim_id")


@functools.lru_cache(maxsize=8)
def get_tables(dataset):
    t = engine.load_tables(dataset)
    plans_cfg, _ = engine.load_config()
    mp = (t["member_plans"] if t["member_plans"] is not None
          else engine.assign_plans(t["members"]["member_id"], plans_cfg))
    dx = pd.read_parquet(os.path.join(dataset, "diagnoses.parquet")) \
        if os.path.exists(os.path.join(dataset, "diagnoses.parquet")) else None
    pr = pd.read_parquet(os.path.join(dataset, "procedures.parquet")) \
        if os.path.exists(os.path.join(dataset, "procedures.parquet")) else None
    return {"claim_lines": t["claim_lines"], "members": t["members"], "claims": t["claims"],
            "mp": mp, "diagnoses": dx, "procedures": pr}


@functools.lru_cache(maxsize=1)
def get_rules():
    return engine.load_rules(include_proposals=True)


def rules_by_id():
    return {r["id"]: r for r in get_rules()}


@functools.lru_cache(maxsize=8)
def rule_claims(dataset):
    """rule_id -> set(claim_id), for the claims-by-rule filter."""
    h = get_res(dataset)["hits"]
    return h.groupby("rule_id")["claim_id"].agg(set).to_dict()


@functools.lru_cache(maxsize=8)
def get_peer_rates(dataset):
    """Services per patient per (code, provider) against the peer median."""
    lines = get_tables(dataset)["claim_lines"]
    g = (lines.groupby(["code", "provider_id"])
              .agg(count=("claim_id", "size"), patients=("member_id", "nunique")).reset_index())
    g["rate"] = g["count"] / g["patients"]
    med = g[g["patients"] >= 5].groupby("code")["rate"].median()
    g["peer_median"] = g["code"].map(med)
    g["ratio"] = g["rate"] / g["peer_median"]
    return g


@functools.lru_cache(maxsize=4)
def get_keys(keys_dir):
    p = os.path.join(keys_dir, "anomalies.csv")
    return pd.read_csv(p, dtype=str) if os.path.exists(p) else None


@functools.lru_cache(maxsize=16)
def run_scenario(dataset, rule_ids, drafts_yaml):
    """Live engine run for a what-if scenario (hashable args, cached)."""
    ids = set(rule_ids)
    rules = [dict(r) for r in get_rules() if r["id"] in ids]
    for r in (yaml.safe_load(drafts_yaml) or []):
        r = dict(r, source_file="ai_drafts", fwa=False)
        r.setdefault("scope", {}); r.setdefault("params", {})
        rules.append(r)
    res = engine.run(dataset, rules=rules, verbose=False)
    return {"decisions": res["decisions"], "hits": res["hits"],
            "conflicts": res["conflicts"], "summary": res["summary"]}


# ====================================================================== state (in-memory, demo)
REVIEWED = {}    # dataset -> {claim_id: confirmed|dismissed}
SCENARIOS = {}   # name -> {change, conflicts, diff, moves}
DRAFTS = []      # approved AI-drafted rule dicts
DRAFT_COUNTER = [0]
SHADOW = {}      # dataset -> AI shadow-review results (cached after run)


# ====================================================================== request bodies
class ReviewIn(BaseModel):
    dataset: str
    reviews: list[dict]


class WhatIfIn(BaseModel):
    dataset: str
    disabled_rule_ids: list[str] = []
    added_proposal_ids: list[str] = []
    drafts: list[dict] = []
    run_dataset: str | None = None
    confirm_full: bool = False


class ScenarioIn(WhatIfIn):
    name: str


class ExplainIn(BaseModel):
    dataset: str
    claim_id: str
    audience: str = "member"
    language: str = "English"


class CaseNoteIn(BaseModel):
    dataset: str
    claim_id: str


class DraftIn(BaseModel):
    clause: str


class ValidateIn(BaseModel):
    yaml: str


class ApproveIn(BaseModel):
    rule: dict


class ExplainConflictIn(BaseModel):
    index: int          # index into the GET /api/conflicts list


class NarrateIn(BaseModel):
    diff: dict
    changed_rule_ids: list[str] = []


class MemberBriefIn(BaseModel):
    dataset: str
    member_id: str


# ====================================================================== helpers
def build_ids(disabled, added):
    live = [r["id"] for r in get_rules() if r["source_file"] != engine.PROPOSAL_FILE]
    return [i for i in live if i not in set(disabled)] + list(added)


def _conflicts_payload():
    """Conflict table + precedence policy, shared by the conflicts and AI routes."""
    _, prec = engine.load_config()
    return engine.detect_conflicts(get_rules(), prec), prec


def diff_summary(base_decisions, scen_decisions):
    a = base_decisions[["claim_id", "member_id", "decision", "plan_paid_amt"]]
    b = scen_decisions[["claim_id", "decision", "plan_paid_amt"]]
    m = a.merge(b, on="claim_id", suffixes=("_before", "_after"))
    moved = m[m["decision_before"] != m["decision_after"]]
    delta = m["plan_paid_amt_after"].sum() - m["plan_paid_amt_before"].sum()
    before = base_decisions["decision"].value_counts().reindex(DECISIONS).fillna(0).astype(int)
    after = scen_decisions["decision"].value_counts().reindex(DECISIONS).fillna(0).astype(int)
    moves = (moved.groupby(["decision_before", "decision_after"]).size().rename("claims")
             .reset_index().sort_values("claims", ascending=False))
    return {
        "claims_changed": int(len(moved)),
        "plan_cost_delta": round(float(delta), 2),
        "members_affected": int(m.loc[m["claim_id"].isin(moved["claim_id"]), "member_id"].nunique()),
        "before": before.to_dict(),
        "after": after.to_dict(),
        "moves": moves.to_dict("records"),
        "changed_claims": records(moved.head(1000)),
    }


def run_whatif(body: WhatIfIn):
    run_ds = body.run_dataset or body.dataset
    if not os.path.exists(os.path.join(run_ds, "claims.parquet")):
        raise HTTPException(404, f"Data folder '{run_ds}' not found. Check the dataset path.")
    if "dev" not in os.path.basename(os.path.normpath(run_ds)) and not body.confirm_full:
        raise HTTPException(409, detail={
            "message": "What-if on a full sample takes ~45 s. Re-send with confirm_full=true.",
            "run_dataset": run_ds})
    ids = build_ids(body.disabled_rule_ids, body.added_proposal_ids)
    drafts_yaml = yaml.safe_dump(body.drafts, sort_keys=True) if body.drafts else ""
    scen = run_scenario(run_ds, tuple(sorted(ids)), drafts_yaml)
    base = get_res(run_ds)
    diff = diff_summary(base["decisions"], scen["decisions"])
    diff["conflicts"] = int(len(scen["conflicts"]))
    diff["superseded"] = scen["summary"].get("superseded", [])
    diff["ran_on"] = run_ds
    diff["seconds"] = scen["summary"].get("seconds")
    return diff


def provider_stats(dataset, provider_id):
    """Provider context string for the AI case note, as in app.py."""
    pdd = get_res(dataset)["decisions"]
    pdd = pdd[pdd["provider_id"] == provider_id]
    lines = get_tables(dataset)["claim_lines"]
    top_codes = lines[lines["provider_id"] == provider_id]["code"].value_counts().head(3)
    return (f"{len(pdd):,} claims in this data, {int((pdd['fwa_score'] > 0).sum())} flagged, "
            f"{pdd['member_id'].nunique():,} distinct members; most billed codes: "
            + ", ".join(f"{c} ({n})" for c, n in top_codes.items()))


# ====================================================================== meta routes
@app.get("/api/health")
def health():
    return {"ok": True, "datasets": datasets(), "ai_backend": ai.backend()}


@app.get("/api/datasets")
def datasets():
    rows = []
    for p in sorted(glob(os.path.join("clean", "*", "claims.parquet"))):
        name = os.path.dirname(p).replace("\\", "/")
        base = os.path.basename(name)
        sp = os.path.join("results", base, "summary.json")
        s = {}
        if os.path.exists(sp):
            with open(sp) as fh:
                s = json.load(fh)
        rows.append({"id": name, "precomputed": bool(s),
                     "claims": s.get("claims"), "decisions": s.get("decisions")})
    return rows


# ====================================================================== overview
@app.get("/api/overview")
def overview(dataset: str):
    res = get_res(dataset)
    d, h, s = res["decisions"], res["hits"], res["summary"]
    rbi = rules_by_id()
    counts = d["decision"].value_counts().reindex(DECISIONS).fillna(0).astype(int)
    mb = d.groupby("decision")[["allowed_amt", "payable_amt"]].sum().reindex(DECISIONS).fillna(0)
    top = (h.groupby("rule_id")
            .agg(hits=("claim_id", "size"), claims=("claim_id", "nunique"))
            .sort_values("claims", ascending=False).reset_index())
    top["clause"] = top["rule_id"].map(lambda i: rbi[i]["clause"] if i in rbi else "")
    top["layer"] = top["rule_id"].map(lambda i: rbi[i]["layer"] if i in rbi else "")
    return {
        "summary": s,
        "counts": counts.to_dict(),
        "money": {"allowed": round(float(d["allowed_amt"].sum()), 2),
                  "payable": round(float(d["payable_amt"].sum()), 2),
                  "plan_paid": round(float(d["plan_paid_amt"].sum()), 2),
                  "held": round(float(d["held_amt"].sum()), 2)},
        "money_by_decision": [{"decision": k, "billed": round(float(mb.loc[k, "allowed_amt"]), 2),
                               "payable": round(float(mb.loc[k, "payable_amt"]), 2)}
                              for k in DECISIONS],
        "top_rules": records(top, ["rule_id", "layer", "clause", "claims", "hits"], 20),
    }


# ====================================================================== claims
@app.get("/api/claims")
def claims(dataset: str, decision: str | None = None, rule: str | None = None,
           q: str | None = None, page: int = 1, page_size: int = 25):
    view = get_res(dataset)["decisions"]
    cols = ["claim_id", "member_id", "claim_type", "from_dt", "provider_id", "plan",
            "decision", "primary_rule", "primary_reason", "allowed_amt", "payable_amt",
            "fwa_score"]
    if decision:
        view = view[view["decision"] == decision]
    if rule:
        view = view[view["claim_id"].isin(rule_claims(dataset).get(rule, set()))]
    if q:
        view = view[view["claim_id"].str.contains(q, case=False) |
                    view["member_id"].str.contains(q, case=False)]
    total = int(len(view))
    # KPI sums over the filtered view (for the page's KPI band)
    billed = round(float(view["allowed_amt"].sum()), 2) if total else 0.0
    plan_paid = round(float(view["plan_paid_amt"].sum()), 2) if total else 0.0
    denied_share = round(float(view["decision"].isin(["denied", "rejected"]).mean()), 3) if total else 0.0
    start = max(0, (page - 1) * page_size)
    page_df = view.iloc[start:start + page_size]
    return {"total": total, "page": page, "page_size": page_size,
            "billed": billed, "plan_paid": plan_paid, "denied_share": denied_share,
            "claims": records(page_df, cols)}


@app.get("/api/claims/{claim_id}")
def claim_detail(claim_id: str, dataset: str):
    res = get_res(dataset)
    try:
        row = get_dix(dataset).loc[claim_id].copy()
    except KeyError:
        raise HTTPException(404, f"Claim {claim_id} not in {dataset}")
    if not isinstance(row, pd.Series):   # duplicate claim ids would return a frame
        row = row.iloc[0]
    row["claim_id"] = claim_id           # the index name, not a column on the Series
    h = res["hits"]
    ch = h[h["claim_id"] == claim_id].sort_values("priority", ascending=False)
    rbi = rules_by_id()

    trace = ch[["rule_id", "layer", "outcome", "code", "priority", "reason", "suppressed_by"]].copy()
    trace["clause"] = trace["rule_id"].map(lambda i: rbi[i]["clause"] if i in rbi else "")
    trace = records(trace)

    explanation = guidance.plain_explanation(row, ch, rbi)
    appeal = guidance.appeal_guidance(row, rbi)

    t = get_tables(dataset)
    members, mp = t["members"], t["mp"]
    yr = pd.Timestamp(row["thru_dt"] if pd.notna(row.get("thru_dt")) else row["from_dt"]).year
    mrow = members[(members["member_id"] == row["member_id"]) & (members["year"] == yr)]
    minfo = {"member": row["member_id"], "plan": row["plan"], "year": int(yr)}
    if len(mrow):
        m = mrow.iloc[0]
        conds = [c[3:].replace("_", " ") for c in members.columns
                 if c.startswith("cc_") and bool(m[c])]
        minfo.update({"part_b_months": int(m["months_part_b"]), "esrd": bool(m["esrd"]),
                      "conditions": conds,
                      "died": None if pd.isna(m["death_dt"]) else pd.Timestamp(m["death_dt"]).strftime("%Y-%m-%d")})
    else:
        minfo["record"] = f"none for {yr}"
    plan_row = mp[mp["member_id"] == row["member_id"]]
    if len(plan_row):
        minfo["policy_start"] = pd.Timestamp(plan_row["policy_start"].iloc[0]).strftime("%Y-%m-%d")

    info = {"claim_id": claim_id, "type": row["claim_type"],
            "service_dt": pd.Timestamp(row["from_dt"]).strftime("%Y-%m-%d"),
            "provider": row["provider_id"], "plan": row["plan"],
            "billed": float(row["allowed_amt"]), "payable": float(row["payable_amt"]),
            "plan_paid": float(row["plan_paid_amt"]), "held": float(row["held_amt"]),
            "fwa_score": float(row["fwa_score"])}
    try:
        cl = get_claims_ix(dataset).loc[claim_id]
        if isinstance(cl, pd.DataFrame):
            cl = cl.iloc[0]
        if row["claim_type"] == "inpatient" and pd.notna(cl.get("admit_dt")):
            info.update({"admitted": pd.Timestamp(cl["admit_dt"]).strftime("%Y-%m-%d"),
                         "discharged": pd.Timestamp(cl["discharge_dt"]).strftime("%Y-%m-%d"),
                         "drg": cl["drg_cd"]})
    except KeyError:
        pass
    dx_t, pr_t = t["diagnoses"], t["procedures"]
    if dx_t is not None:
        info["diagnoses"] = sorted(dx_t.loc[dx_t["claim_id"] == claim_id, "dx_code"].astype(str).unique().tolist())
    if pr_t is not None:
        info["procedures"] = sorted(pr_t.loc[pr_t["claim_id"] == claim_id, "proc_code"].astype(str).unique().tolist())

    services = records(t["claim_lines"][t["claim_lines"]["claim_id"] == claim_id],
                       ["code", "repeat_count", "code_type", "is_drug_code"])
    return {"claim": series_records(row), "trace": trace,
            "explanation": explanation, "appeal": appeal, "member": minfo,
            "claim_info": info, "services": services}


# ====================================================================== FWA queue
def fwa_frame(dataset, signals=None):
    res = get_res(dataset)
    d, h = res["decisions"], res["hits"]
    flagged = h[h["fwa"].astype(bool) & h["suppressed_by"].isna()]
    det = sorted(flagged["rule_id"].unique())
    picked = [s for s in (signals.split(",") if signals else det) if s in set(det)]
    sig = set(flagged.loc[flagged["rule_id"].isin(picked), "claim_id"])
    qd = d[d["fwa_score"] > 0].sort_values(["fwa_score", "allowed_amt"], ascending=False).copy()
    qd = qd[qd["claim_id"].isin(sig)]
    qd["rank"] = np.arange(1, len(qd) + 1)
    qd["review"] = qd["claim_id"].map(REVIEWED.get(dataset, {})).fillna("pending")
    return qd, det, picked


def fwa_cases(dataset, signals=None):
    """Group the flat FWA queue into cases by (provider_id, member_id, primary FWA rule)."""
    qd, det, picked = fwa_frame(dataset, signals)
    h = get_res(dataset)["hits"]
    fl = h[h["fwa"].astype(bool) & h["suppressed_by"].isna() & h["rule_id"].isin(picked)]
    # one rule per claim: highest-priority picked FWA rule, so cases are disjoint
    prim = (fl.sort_values("priority", ascending=False)
              .drop_duplicates("claim_id")[["claim_id", "rule_id"]])
    qd = qd.merge(prim, on="claim_id", how="inner")
    g = (qd.groupby(["provider_id", "member_id", "rule_id"])
           .agg(claims=("claim_id", "size"), billed=("allowed_amt", "sum"),
                max_fwa_score=("fwa_score", "max"), claim_ids=("claim_id", list),
                reviews=("review", list))
           .reset_index()
           .sort_values("max_fwa_score", ascending=False))
    g["case_id"] = g["provider_id"] + "|" + g["member_id"] + "|" + g["rule_id"]
    n = {s: g["reviews"].apply(lambda rs: sum(1 for r in rs if r == s)) for s in ("confirmed", "dismissed")}
    g["confirmed"] = n["confirmed"]
    g["dismissed"] = n["dismissed"]
    g["pending"] = g["claims"] - g["confirmed"] - g["dismissed"]
    return g[["case_id", "provider_id", "member_id", "rule_id", "claims", "billed",
              "max_fwa_score", "claim_ids", "confirmed", "dismissed", "pending"]], det, picked


@app.get("/api/fwa")
def fwa_queue(dataset: str, signals: str | None = None, keys_dir: str | None = None,
              group: int = 0, page: int = 1, page_size: int = 50):
    start = max(0, (page - 1) * page_size)
    if group:
        cases, det, picked = fwa_cases(dataset, signals)
        cols = ["case_id", "provider_id", "member_id", "rule_id", "claims", "billed",
                "max_fwa_score", "claim_ids", "confirmed", "dismissed", "pending"]
        return {"signals": det, "picked": picked, "total": int(len(cases)),
                "rows": records(cases.iloc[start:start + page_size], cols),
                "billed": round(float(cases["billed"].sum()), 2), "workload": None}
    qd, det, picked = fwa_frame(dataset, signals)
    total = int(len(qd))
    cols = ["rank", "claim_id", "member_id", "provider_id", "decision", "fwa_score",
            "allowed_amt", "fwa_reasons", "review"]
    workload = None
    keys = get_keys(keys_dir) if keys_dir else None
    if keys is not None:
        target = evaluate.workload_target(keys)
        if target:
            found = np.cumsum(qd["claim_id"].isin(target).to_numpy())
            need80 = int(np.ceil(0.8 * len(target)))
            k80 = (int(np.argmax(found >= need80)) + 1) if len(found) and found[-1] >= need80 else None
            curve = [{"rank": int(r), "pct": round(float(p), 1)}
                     for r, p in zip(qd["rank"], 100.0 * found / len(target))]
            workload = {"target_count": len(target), "in_queue": int(found[-1]) if len(found) else 0,
                        "k80": k80, "curve": curve}
    return {"signals": det, "picked": picked, "total": total,
            "rows": records(qd.iloc[start:start + page_size], cols),
            "billed": round(float(qd["allowed_amt"].sum()), 2),
            "workload": workload}


@app.get("/api/fwa/case")
def fwa_case(dataset: str, provider_id: str, member_id: str, rule_id: str):
    """All claims of one (provider, member, pattern) case, review status live."""
    qd, _, _ = fwa_frame(dataset, None)
    # keep only claims whose primary FWA rule is the case's rule
    h = get_res(dataset)["hits"]
    fl = h[h["fwa"].astype(bool) & h["suppressed_by"].isna() & (h["rule_id"] == rule_id)]
    prim_ids = set(fl["claim_id"])
    view = qd[(qd["provider_id"] == provider_id) & (qd["member_id"] == member_id) &
              (qd["claim_id"].isin(prim_ids))]
    cols = ["claim_id", "member_id", "provider_id", "decision", "fwa_score",
            "allowed_amt", "fwa_reasons", "review"]
    return {"rows": records(view, cols)}


@app.get("/api/fwa/export.csv")
def fwa_export(dataset: str, signals: str | None = None, group: int = 0):
    if group:
        cases, _, _ = fwa_cases(dataset, signals)
        out = cases.head(20000).copy()
        out["claim_ids"] = out["claim_ids"].apply(lambda ids: ";".join(ids))
        out["billed"] = out["billed"].round(2)
        out["max_fwa_score"] = out["max_fwa_score"].round(2)
        csv = out.drop(columns=["case_id"]).to_csv(index=False)
        return Response(csv, media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=fwa_cases.csv"})
    qd, _, _ = fwa_frame(dataset, signals)
    cols = ["rank", "claim_id", "member_id", "provider_id", "decision", "fwa_score",
            "allowed_amt", "fwa_reasons", "review"]
    csv = qd[cols].head(20000).to_csv(index=False)
    return Response(csv, media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=fwa_queue.csv"})


@app.post("/api/fwa/review")
def fwa_review(body: ReviewIn):
    store = REVIEWED.setdefault(body.dataset, {})
    for r in body.reviews:
        if r.get("status") in ("confirmed", "dismissed", "pending"):
            store[r["claim_id"]] = r["status"]
    return {"saved": len(body.reviews)}


# ====================================================================== providers
@app.get("/api/providers")
def providers(dataset: str, q: str | None = None, page: int = 1, page_size: int = 25):
    d = get_res(dataset)["decisions"]
    prov = (d.groupby("provider_id")
              .agg(claims=("claim_id", "size"), members=("member_id", "nunique"),
                   flagged=("fwa_score", lambda s: int((s > 0).sum())),
                   billed=("allowed_amt", "sum"))
              .sort_values(["flagged", "billed"], ascending=False).reset_index())
    if q:
        prov = prov[prov["provider_id"].str.contains(q, case=False)]
    total = int(len(prov))
    start = max(0, (page - 1) * page_size)
    rows = records(prov.iloc[start:start + page_size])
    for r in rows:
        r["billed"] = round(float(r["billed"]), 2)
    return {"total": total, "rows": rows}


@app.get("/api/providers/{provider_id}")
def provider_detail(provider_id: str, dataset: str):
    d = get_res(dataset)["decisions"]
    prow = d[d["provider_id"] == provider_id]
    if prow.empty:
        raise HTTPException(404, f"Provider {provider_id} not in {dataset}")
    peer = get_peer_rates(dataset)
    pcodes = peer[peer["provider_id"] == provider_id].sort_values("count", ascending=False).head(10).copy()
    pcodes["warning"] = pcodes["ratio"] >= 4
    ov = get_tables(dataset)["claim_lines"]
    ov = ov[(ov["provider_id"] == provider_id) &
            ov["code"].isin(["99212", "99213", "99214", "99215"])]
    mix = []
    if len(ov):
        m = ov.groupby([ov["service_dt"].dt.year, "code"]).size().unstack(fill_value=0)
        for yr, row in m.iterrows():
            mix.append({"year": int(yr), **{c: int(row.get(c, 0)) for c in ["99212", "99213", "99214", "99215"]}})
    fl = prow[prow["fwa_score"] > 0].copy()
    monthly = (fl.assign(month=pd.to_datetime(fl["from_dt"]).dt.to_period("M").astype(str))
                 .groupby("month").size())
    return {
        "metrics": {"claims": int(len(prow)), "members": int(prow["member_id"].nunique()),
                    "flagged": int((prow["fwa_score"] > 0).sum()),
                    "flagged_share": round(float((prow["fwa_score"] > 0).mean()), 3),
                    "billed": round(float(prow["allowed_amt"].sum()), 2),
                    "plan_paid": round(float(prow["plan_paid_amt"].sum()), 2)},
        "peer_codes": records(pcodes, ["code", "count", "patients", "rate", "peer_median",
                                       "ratio", "warning"]),
        "office_visit_mix": mix,
        "flags_by_month": [{"month": k, "count": int(v)} for k, v in monthly.items()],
        "flagged_claims": records(fl.sort_values("fwa_score", ascending=False)
                                  .head(500), ["claim_id", "member_id", "from_dt", "decision",
                                               "fwa_score", "fwa_reasons"]),
    }


# ====================================================================== members
CC_NAMES = {
    "cc_alzheimers": "Alzheimer's", "cc_heart_failure": "Heart failure",
    "cc_chronic_kidney": "Chronic kidney disease", "cc_cancer": "Cancer",
    "cc_copd": "COPD", "cc_depression": "Depression", "cc_diabetes": "Diabetes",
    "cc_ischemic_heart": "Ischemic heart disease", "cc_osteoporosis": "Osteoporosis",
    "cc_ra_oa": "Rheumatoid/Osteo arthritis", "cc_stroke_tia": "Stroke/TIA",
    "esrd": "ESRD",
}
SEX = {"1": "Male", "2": "Female", "M": "Male", "F": "Female", 1: "Male", 2: "Female"}


@functools.lru_cache(maxsize=8)
def get_member_index(dataset):
    """Per-member index: latest demographics, any-year conditions, decision aggregates.
    Read-only, cacheable."""
    t = get_tables(dataset)
    members, mp = t["members"], t["mp"]
    d = get_res(dataset)["decisions"]
    cc_cols = [c for c in members.columns if c.startswith("cc_")]
    # any-year condition flags
    cond_any = members.groupby("member_id")[cc_cols + ["esrd"]].max()
    # latest year demographics
    latest = members.sort_values("year").groupby("member_id").tail(1).set_index("member_id")[
        ["year", "sex", "age_at_year_end", "death_dt", "state_cd"]]
    # decision aggregates
    agg = (d.groupby("member_id")
             .agg(claims=("claim_id", "size"), billed=("allowed_amt", "sum"),
                  plan_paid=("plan_paid_amt", "sum"),
                  fwa_flags=("fwa_score", lambda s: int((s > 0).sum()))))
    idx = latest.join(cond_any).join(agg).join(mp.set_index("member_id")[["plan", "policy_start"]])
    idx["claims"] = idx["claims"].fillna(0)
    idx["billed"] = idx["billed"].fillna(0.0)
    idx["plan_paid"] = idx["plan_paid"].fillna(0.0)
    idx["fwa_flags"] = idx["fwa_flags"].fillna(0)
    return idx, cc_cols


def _member_conditions(row, cc_cols):
    return [CC_NAMES.get(c, c) for c in cc_cols + ["esrd"] if bool(row.get(c, 0))]


@app.get("/api/members")
def members(dataset: str, q: str | None = None, condition: str | None = None,
            page: int = 1, page_size: int = 25):
    idx, cc_cols = get_member_index(dataset)
    view = idx
    if q:
        view = view[view.index.astype(str).str.contains(q, case=False)]
    if condition:
        if condition not in view.columns:
            raise HTTPException(404, f"Unknown condition '{condition}'")
        view = view[view[condition].astype(bool)]
    # most interesting members first: flagged, then spend
    view = view.sort_values(["fwa_flags", "billed"], ascending=False)
    total = int(len(view))
    # KPI aggregates over the filtered view (for the page's KPI band)
    flagged_members = int((view["fwa_flags"] > 0).sum()) if total else 0
    billed_total = round(float(view["billed"].sum()), 2) if total else 0.0
    with_condition = int((view[cc_cols + ["esrd"]].astype(bool).any(axis=1)).sum()) if total else 0
    start = max(0, (page - 1) * page_size)
    rows = []
    for mid, row in view.iloc[start:start + page_size].iterrows():
        rows.append({
            "member_id": str(mid), "plan": row.get("plan"), "age": row.get("age_at_year_end"),
            "sex": SEX.get(row.get("sex")), "conditions": _member_conditions(row, cc_cols),
            "claims": int(row["claims"]), "billed": round(float(row["billed"]), 2),
            "plan_paid": round(float(row["plan_paid"]), 2), "fwa_flags": int(row["fwa_flags"]),
        })
    return {"total": total, "page": page, "page_size": page_size,
            "flagged_members": flagged_members, "billed_total": billed_total,
            "with_condition": with_condition, "rows": rows}


def member_profile_data(member_id: str, dataset: str):
    """Full profile dict, shared by the GET route and the AI member brief."""
    t = get_tables(dataset)
    members, mp, dx_t = t["members"], t["mp"], t["diagnoses"]
    res = get_res(dataset)
    d, h = res["decisions"], res["hits"]
    mrows = members[members["member_id"] == member_id].sort_values("year")
    if mrows.empty:
        raise HTTPException(404, f"Member {member_id} not in {dataset}")
    cc_cols = [c for c in members.columns if c.startswith("cc_")]

    # condition timeline per year
    timeline = []
    for _, r in mrows.iterrows():
        timeline.append({"year": int(r["year"]),
                         "age": None if pd.isna(r.get("age_at_year_end")) else int(r["age_at_year_end"]),
                         "conditions": [CC_NAMES.get(c, c) for c in cc_cols if bool(r[c])]})
    plan_row = mp[mp["member_id"] == member_id]
    policy_start = None
    plan = None
    if len(plan_row):
        policy_start = pd.Timestamp(plan_row["policy_start"].iloc[0]).strftime("%Y-%m-%d")
        plan = plan_row["plan"].iloc[0]

    # claim history
    ch = d[d["member_id"] == member_id].sort_values("from_dt", ascending=False)
    claim_cols = ["claim_id", "from_dt", "claim_type", "provider_id", "decision",
                  "primary_rule", "primary_reason", "allowed_amt", "payable_amt",
                  "plan_paid_amt", "fwa_score"]
    claims = records(ch.head(200), [c for c in claim_cols if c in ch.columns])

    # diagnoses: top codes with counts
    top_dx = []
    if dx_t is not None:
        vc = dx_t[dx_t["member_id"] == member_id]["dx_code"].astype(str).value_counts().head(10)
        top_dx = [{"dx_code": k, "count": int(v)} for k, v in vc.items()]

    # per-year spend
    spend = []
    if len(ch):
        by_year = (ch.assign(year=pd.to_datetime(ch["from_dt"]).dt.year)
                     .groupby("year").agg(billed=("allowed_amt", "sum"),
                                          plan_paid=("plan_paid_amt", "sum"),
                                          claims=("claim_id", "size")))
        spend = [{"year": int(y), "billed": round(float(r["billed"]), 2),
                  "plan_paid": round(float(r["plan_paid"]), 2), "claims": int(r["claims"])}
                 for y, r in by_year.iterrows()]

    # rules that fired on this member's claims (AI grounding)
    rule_ids = sorted(set(h[h["claim_id"].isin(set(ch["claim_id"]))]["rule_id"]))

    latest = mrows.iloc[-1]
    return {
        "member_id": member_id, "plan": plan, "policy_start": policy_start,
        "sex": SEX.get(latest.get("sex")),
        "age": None if pd.isna(latest.get("age_at_year_end")) else int(latest["age_at_year_end"]),
        "state": latest.get("state_cd"),
        "died": None if pd.isna(latest.get("death_dt")) else pd.Timestamp(latest["death_dt"]).strftime("%Y-%m-%d"),
        "timeline": timeline, "conditions": _member_conditions(latest, cc_cols),
        "claims": claims, "total_claims": int(len(ch)),
        "billed": round(float(ch["allowed_amt"].sum()), 2),
        "plan_paid": round(float(ch["plan_paid_amt"].sum()), 2),
        "fwa_flags": int((ch["fwa_score"] > 0).sum()),
        "top_dx": top_dx, "spend": spend,
        "providers": int(ch["provider_id"].nunique()),
        "rule_ids": rule_ids,
    }


@app.get("/api/member/{member_id}")
def member_profile(member_id: str, dataset: str):
    return member_profile_data(member_id, dataset)


# ====================================================================== what-if + scenarios
@app.post("/api/whatif")
def whatif(body: WhatIfIn):
    return run_whatif(body)


@app.get("/api/scenarios")
def scenarios():
    return [{"name": n, "change": s["change"], "conflicts": s["conflicts"],
             "claims_changed": s["diff"]["claims_changed"],
             "plan_cost_delta": s["diff"]["plan_cost_delta"]}
            for n, s in SCENARIOS.items()]


@app.post("/api/scenarios")
def save_scenario(body: ScenarioIn):
    diff = run_whatif(body)
    run_ds = body.run_dataset or body.dataset
    ids = tuple(sorted(build_ids(body.disabled_rule_ids, body.added_proposal_ids)))
    drafts_yaml = yaml.safe_dump(body.drafts, sort_keys=True) if body.drafts else ""
    scen = run_scenario(run_ds, ids, drafts_yaml)   # lru_cache hit from run_whatif
    SCENARIOS[body.name.strip()] = {"change": "; ".join(
        [f"add {i}" for i in body.added_proposal_ids] +
        [f"switch off {i}" for i in body.disabled_rule_ids] +
        [f"add AI draft {r.get('id')}" for r in body.drafts]),
        "conflicts": diff["conflicts"], "diff": diff,
        "rule_ids": list(ids) + [r.get("id") for r in body.drafts],
        "decisions": scen["decisions"][["claim_id", "decision", "plan_paid_amt"]].copy()}
    return {"saved": body.name.strip(), "diff": diff}


@app.post("/api/scenarios/{name}/compare")
def compare_scenario(name: str, body: WhatIfIn):
    sv = SCENARIOS.get(name)
    if sv is None:
        raise HTTPException(404, f"Scenario '{name}' not found")
    out = diff_summary(get_res(body.run_dataset or body.dataset)["decisions"], sv["decisions"])
    out["changed_rule_ids"] = sv.get("rule_ids", [])
    return out


@app.delete("/api/scenarios/{name}")
def delete_scenario(name: str):
    SCENARIOS.pop(name, None)
    return {"deleted": name}


# ====================================================================== rules + conflicts
@app.get("/api/rules")
def rules():
    rbi = rules_by_id()
    rows = []
    for r in get_rules():
        status = "on" if r["source_file"] != engine.PROPOSAL_FILE else "proposal"
        rows.append({"id": r["id"], "status": status, "layer": r["layer"], "kind": r["kind"],
                     "outcome": r.get("outcome", "flag"), "priority": r.get("priority"),
                     "effective": " to ".join(r["effective"]), "clause": r["clause"]})
    return rows


@app.get("/api/conflicts")
def conflicts():
    found, prec = _conflicts_payload()
    return {"conflicts": records(found), "precedence": prec}


# ====================================================================== evaluation
@app.get("/api/evaluation")
def evaluation(dataset: str, keys_dir: str):
    if not os.path.exists(os.path.join(keys_dir, "boundary_expected.csv")):
        raise HTTPException(404, f"No answer keys in '{keys_dir}'")
    res = get_res(dataset)
    b, _ = evaluate.boundary_metrics(res, keys_dir)
    an = evaluate.anomaly_metrics(res, keys_dir)
    co = evaluate.conflict_metrics(keys_dir)
    return jsonable({"boundary": b, "anomalies": an, "conflicts": co})


# ====================================================================== AI routes
@app.get("/api/ai/status")
def ai_status():
    ok, msg = ai.check_backend()
    return {"backend": ai.backend(), "ok": ok, "message": msg}


@app.post("/api/ai/explain")
def ai_explain(body: ExplainIn):
    res = get_res(body.dataset)
    try:
        row = get_dix(body.dataset).loc[body.claim_id].copy()
    except KeyError:
        raise HTTPException(404, f"Claim {body.claim_id} not in {body.dataset}")
    row["claim_id"] = body.claim_id      # the index name, not a column on the Series
    ch = res["hits"][res["hits"]["claim_id"] == body.claim_id].sort_values("priority", ascending=False)
    rbi = rules_by_id()
    fallback = guidance.plain_explanation(row, ch, rbi)["text"].replace("**", "")
    text, src, warns = ai.explain_claim(row, ch, rbi, body.audience, body.language,
                                        fallback=fallback)
    rtl = body.language == "Arabic" and "offline" not in src
    return {"text": text, "source": src, "warnings": warns, "dir": "rtl" if rtl else "ltr"}


@app.post("/api/ai/case-note")
def ai_case_note(body: CaseNoteIn):
    res = get_res(body.dataset)
    try:
        row = get_dix(body.dataset).loc[body.claim_id].copy()
    except KeyError:
        raise HTTPException(404, f"Claim {body.claim_id} not in {body.dataset}")
    row["claim_id"] = body.claim_id      # the index name, not a column on the Series
    fh = res["hits"][res["hits"]["claim_id"] == body.claim_id].sort_values("priority", ascending=False)
    pstats = provider_stats(body.dataset, row["provider_id"])
    text, src, warns = ai.fwa_case_note(row, fh, rules_by_id(), pstats)
    return {"text": text, "source": src, "warnings": warns}


@app.post("/api/ai/draft")
def ai_draft(body: DraftIn):
    existing = set(rules_by_id())
    rule, ytext, probs, src = ai.draft_rule(body.clause, existing)
    return {"rule": rule, "yaml": ytext, "problems": probs, "source": src}


@app.post("/api/ai/validate-draft")
def ai_validate_draft(body: ValidateIn):
    try:
        cand = yaml.safe_load(body.yaml) or {}
    except yaml.YAMLError as e:
        return {"ok": False, "rule_id": None, "problems": [f"YAML error: {e}"], "conflicts": None}
    DRAFT_COUNTER[0] += 1
    cand["id"] = f"DRAFT-{DRAFT_COUNTER[0]}"
    existing = set(rules_by_id()) - {cand["id"]}
    probs = ai.validate_rule(cand, existing)
    if probs:
        return {"ok": False, "rule_id": cand["id"], "problems": probs, "conflicts": None}
    _, prec = engine.load_config()
    live_plus = [r for r in get_rules()] + [dict(x, source_file="ai_drafts") for x in DRAFTS] + \
                [dict(cand, source_file="ai_drafts", fwa=False)]
    cc = engine.detect_conflicts(live_plus, prec)
    cc = cc[(cc["rule_a"] == cand["id"]) | (cc["rule_b"] == cand["id"])]
    return {"ok": True, "rule_id": cand["id"], "problems": [], "conflicts": records(cc),
            "rule": jsonable(cand)}


@app.post("/api/ai/approve-draft")
def ai_approve_draft(body: ApproveIn):
    rule = dict(body.rule, source_file="ai_drafts", fwa=False)
    DRAFTS.append(rule)
    return {"approved": [d["id"] for d in DRAFTS]}


@app.get("/api/ai/drafts")
def ai_drafts():
    return DRAFTS


@app.delete("/api/ai/drafts/{draft_id}")
def ai_delete_draft(draft_id: str):
    global DRAFTS
    DRAFTS = [d for d in DRAFTS if d["id"] != draft_id]
    return {"drafts": [d["id"] for d in DRAFTS]}


@app.post("/api/ai/explain-conflict")
def ai_explain_conflict(body: ExplainConflictIn):
    found, prec = _conflicts_payload()
    if body.index < 0 or body.index >= len(found):
        raise HTTPException(404, f"Conflict {body.index} not found")
    c = jsonable(found.iloc[body.index].to_dict())
    rbi = rules_by_id()
    ra = rbi.get(c["rule_a"])
    rb = rbi.get(c["rule_b"]) if c.get("rule_b") else None
    text, src, warns = ai.explain_conflict(c, ra, rb, prec)
    return {"text": text, "source": src, "warnings": warns}


@app.post("/api/ai/explain-conflicts")
def ai_explain_conflicts():
    found, prec = _conflicts_payload()
    if not len(found):
        return {"text": "No conflicts in the current rule set.", "source": "engine", "warnings": []}
    text, src, warns = ai.explain_conflicts_summary(records(found), prec)
    return {"text": text, "source": src, "warnings": warns}


@app.post("/api/ai/narrate-scenario")
def ai_narrate_scenario(body: NarrateIn):
    diff = {k: body.diff.get(k) for k in ("claims_changed", "plan_cost_delta", "members_affected",
                                          "before", "after", "moves", "conflicts",
                                          "superseded", "ran_on", "seconds")}
    diff["changed_claims"] = (body.diff.get("changed_claims") or [])[:10]
    text, src, warns = ai.narrate_scenario(jsonable(diff), body.changed_rule_ids)
    return {"text": text, "source": src, "warnings": warns}


@app.post("/api/ai/member-brief")
def ai_member_brief(body: MemberBriefIn):
    profile = member_profile_data(body.member_id, body.dataset)
    text, src, warns = ai.member_brief(jsonable(profile))
    return {"text": text, "source": src, "warnings": warns}


# ====================================================================== AI shadow review
class ShadowIn(BaseModel):
    dataset: str
    n: int = 9


def _shadow_facts(dataset, row):
    """Compact claim facts for the shadow prompt — never the engine's decision."""
    t = get_tables(dataset)
    lines, members = t["claim_lines"], t["members"]
    cl = lines[lines["claim_id"] == row["claim_id"]]
    codes = ", ".join(f"{c} x{int(rc)}" for c, rc in zip(cl["code"], cl["repeat_count"])) \
        if len(cl) else "none recorded"
    yr = pd.Timestamp(row["from_dt"]).year
    mrow = members[(members["member_id"] == row["member_id"]) & (members["year"] == yr)]
    conds, months_a, months_b, esrd = [], None, None, False
    death, dq = "", ""
    if len(mrow):
        m = mrow.iloc[0]
        conds = [CC_NAMES.get(c, c) for c in members.columns if c.startswith("cc_") and bool(m[c])]
        months_a, months_b, esrd = int(m["months_part_a"]), int(m["months_part_b"]), bool(m["esrd"])
        if pd.notna(m.get("death_dt")):
            death = f"Member deceased {pd.Timestamp(m['death_dt']).strftime('%Y-%m-%d')}.\n"
        else:
            death = ""
        if bool(m.get("flag_negative_annual_amount", 0)):
            dq = "Member record has a data-quality flag (negative annual amount in the source data).\n"
        else:
            dq = ""
    # diagnoses (claim inputs)
    dx_t = t.get("diagnoses")
    dx = ""
    if dx_t is not None:
        dxs = sorted(dx_t.loc[dx_t["claim_id"] == row["claim_id"], "dx_code"].astype(str).unique())[:8]
        dx = f"Diagnoses (ICD-9): {', '.join(dxs)}.\n" if dxs else ""
    # inpatient extras
    ip = ""
    if row["claim_type"] == "inpatient":
        try:
            cl_ix = get_claims_ix(dataset).loc[row["claim_id"]]
            if isinstance(cl_ix, pd.DataFrame):
                cl_ix = cl_ix.iloc[0]
            drg = cl_ix.get("drg_cd")
            if pd.notna(drg):
                ip = f"Inpatient stay, DRG {drg}.\n"
        except KeyError:
            pass
    # per-code policy-year count for the member
    d = get_res(dataset)["decisions"]
    mclaims = d[(d["member_id"] == row["member_id"]) &
                (pd.to_datetime(d["from_dt"]).dt.year == yr)]
    mcodes = lines[lines["claim_id"].isin(set(mclaims["claim_id"]))]["code"].value_counts()
    freq = ", ".join(f"{c}: {n} time(s) this policy year" for c, n in mcodes.head(8).items())
    # same code billed in the 3 days before (or on the same day as) this service
    # by another claim of the member (duplicate-edit input)
    svc = pd.Timestamp(row["from_dt"])
    recent = d[(d["member_id"] == row["member_id"]) &
               (pd.to_datetime(d["from_dt"]) >= svc - pd.Timedelta(days=3)) &
               (pd.to_datetime(d["from_dt"]) <= svc) &
               (d["claim_id"] != row["claim_id"])]
    recent_codes = lines[lines["claim_id"].isin(set(recent["claim_id"]))]
    dup = ""
    if len(recent_codes):
        rd = [f"{c} on {str(dt)[:10]}" for c, dt in
              zip(recent_codes["code"], recent_codes["service_dt"])][:6]
        dup = f"Same member billed these codes in the 3 days before this claim: {', '.join(rd)}.\n"
    # overlapping inpatient stay
    _, stays = _shadow_context(dataset)
    stay = ""
    if len(stays):
        st = stays[(stays["member_id"] == row["member_id"]) &
                   (stays["admit_dt"] <= svc) & (stays["discharge_dt"] >= svc)]
        if len(st):
            s = st.iloc[0]
            stay = (f"Member had an inpatient stay overlapping this service "
                    f"({pd.Timestamp(s['admit_dt']).strftime('%Y-%m-%d')} to "
                    f"{pd.Timestamp(s['discharge_dt']).strftime('%Y-%m-%d')}).\n")
    return (f"Claim {row['claim_id']} ({row['claim_type']}, service date {str(row['from_dt'])[:10]}), "
            f"member plan {row['plan']}, member coinsurance {row['coinsurance']:.0%}.\n"
            + ip + dx + death + dq + stay + dup
            + f"Member conditions: {', '.join(conds) or 'none flagged'}. "
            f"Months enrolled in Part A {months_a if months_a is not None else '?'}, "
            f"Part B {months_b if months_b is not None else '?'}"
            + (", ESRD" if esrd else "") + ".\n"
            + f"Services billed: {codes}. Total billed amount: {row['allowed_amt']:.2f}.\n"
            + f"Member's service usage this policy year: {freq or 'none recorded'}.")


def _shadow_sample(dataset, n):
    """Deterministic, representative sample: evenly spaced claims from each
    decision bucket (pay / deny / pend) plus FWA-flagged claims."""
    d = get_res(dataset)["decisions"].sort_values("claim_id")

    def spaced(sub, k):
        sub = sub.reset_index(drop=True)
        if len(sub) <= k:
            return sub
        idx = sorted({int(round(i * (len(sub) - 1) / max(1, k - 1))) for i in range(k)})
        return sub.iloc[idx]

    per = max(1, n // 3)
    buckets = [d[d["decision"].isin(["paid", "partial"])],
               d[d["decision"].isin(["denied", "rejected"])],
               d[d["decision"] == "pended"]]
    picked = pd.concat([spaced(b, per) for b in buckets])
    fw = d[d["fwa_score"] > 0]
    if len(fw):
        picked = pd.concat([picked, spaced(fw, 2)])
    return picked.drop_duplicates("claim_id")


_BUCKET = {"paid": "pay", "partial": "pay", "denied": "deny", "rejected": "deny", "pended": "pend"}


@functools.lru_cache(maxsize=8)
def _shadow_context(dataset):
    """Exclusion list + inpatient stays for the dataset (shadow-review inputs)."""
    ex = pd.read_parquet(os.path.join(dataset, "exclusions.parquet")) \
        if os.path.exists(os.path.join(dataset, "exclusions.parquet")) else pd.DataFrame()
    st = pd.read_parquet(os.path.join(dataset, "stays.parquet")) \
        if os.path.exists(os.path.join(dataset, "stays.parquet")) else pd.DataFrame()
    return ex, st


@app.post("/api/ai/shadow-review")
def ai_shadow_review(body: ShadowIn):
    if ai.backend() == "offline":
        raise HTTPException(503, "AI backend offline — shadow review needs a live AI "
                                 "(set AI_BACKEND=api with ANTHROPIC_API_KEY, or run where claude is logged in).")
    n = max(3, min(15, body.n))
    rules = get_rules()
    allowed = {r["id"] for r in rules}
    rules_text = ai._rulebook_text(rules)
    # the exclusion list is an input the adjudicator would legitimately see
    ex, _ = _shadow_context(body.dataset)
    if len(ex):
        excl_lines = [f"- {r['provider_id']} excluded from {pd.Timestamp(r['excl_dt']).strftime('%Y-%m-%d')}"
                      for _, r in ex.iterrows()]
        rules_text += ("\n\nEXCLUDED PROVIDERS (services on/after the exclusion date are not "
                       "covered):\n" + "\n".join(excl_lines))
    sampled = _shadow_sample(body.dataset, n)
    rows = []
    for _, row in sampled.iterrows():
        engine = _BUCKET.get(row["decision"])
        try:
            decision, reason, cited, src, warns = ai.shadow_decide(
                _shadow_facts(body.dataset, row), rules_text, allowed)
            rows.append({"claim_id": row["claim_id"], "engine": engine, "ai": decision,
                         "match": engine == decision, "ai_reason": reason, "ai_rules": cited,
                         "warnings": warns, "source": src})
        except ai.AIError as e:
            rows.append({"claim_id": row["claim_id"], "engine": engine, "ai": "unparsed",
                         "match": None, "ai_reason": str(e)[:200], "ai_rules": [],
                         "warnings": [], "source": "error"})
    ran = [r for r in rows if r["match"] is not None]
    result = {"ran": True, "n": len(rows),
              "agreement": round(sum(r["match"] for r in ran) / len(ran), 3) if ran else None,
              "rows": rows, "backend": ai.backend()}
    SHADOW[body.dataset] = result
    return result


@app.get("/api/ai/shadow-review")
def get_shadow_review(dataset: str):
    return SHADOW.get(dataset) or {"ran": False}


# ====================================================================== built frontend
# Serve web/dist (npm/node build output) from the same origin so the demo runs
# with a single command. API routes above take precedence; the route below is a
# SPA fallback so client-side deep links (/claims?q=...) resolve to index.html.
_dist = os.path.join(ROOT, "web", "dist")


@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str):
    if full_path.startswith("api"):
        raise HTTPException(404, "Not Found")
    if _dist and full_path:
        f = os.path.join(_dist, full_path)
        if os.path.isfile(f):
            return FileResponse(f)
    index = os.path.join(_dist or "", "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    raise HTTPException(404, "Not Found")


if os.path.exists(_dist):
    app.mount("/", StaticFiles(directory=_dist, html=True), name="web")

