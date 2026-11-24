"""
engine.py - Rule engine for the Claim Decision Engine (RD02/RD03/RD04).

Runs the YAML rules in ./rules on the clean tables written by load_desynpuf.py
(or the seeded tables written by seed.py) and returns, for every claim:
a decision, the rule behind it, a full trace, payable amounts and an FWA score.
Also checks the rule set itself for conflicts (RD04).

Notebook use
------------
    import engine
    res = engine.run("clean/sample1_dev")                 # live rules
    res["decisions"].decision.value_counts()
    engine.explain(res, "542012280836537")                # one claim's trace
    res_p = engine.run("clean/sample1_dev", include_proposals=True)
    engine.compare(res, res_p)                            # what-if: what changes
    res_p["conflicts"]                                    # rule conflicts

Command line
------------
    python engine.py --data clean/sample1_dev --out results/sample1_dev
    python engine.py --data clean/sample1_dev --with-proposals --out results/p

Decision order for a claim: rejected (data quality) > denied > pended
(refer) > partial (some lines denied) > paid. A claim whose every line is
denied counts as denied.
"""

import argparse
import hashlib
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd
import yaml

from load_desynpuf import add_flags

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_DIR = os.path.join(HERE, "rules")
LIVE_FILES = ["coverage.yaml", "edits.yaml", "anomalies.yaml"]
PROPOSAL_FILE = "conflict_seeds.yaml"
SEVERITY = {"reject": 5, "deny": 4, "refer": 3, "deny_line": 2, "allow": 0}
HIT_COLS = ["claim_id", "code", "rule_id", "layer", "outcome", "priority",
            "reason", "fwa", "score"]
LINE_LEVEL_KINDS = {"frequency_limit", "requires_condition", "waiting_period", "exclusion",
                    "grant", "unit_cap", "pair_edit", "requires_companion",
                    "duplicate_line_same_day", "modifier_exception"}


# ====================================================================== setup
def load_rules(rules_dir=RULES_DIR, include_proposals=False):
    files = LIVE_FILES + ([PROPOSAL_FILE] if include_proposals else [])
    rules = []
    for f in files:
        with open(os.path.join(rules_dir, f)) as fh:
            for r in yaml.safe_load(fh)["rules"]:
                r["source_file"] = f
                r.setdefault("scope", {})
                r.setdefault("params", {})
                r.setdefault("effective", ["1900-01-01", "2099-12-31"])
                r.setdefault("fwa", False)
                rules.append(r)
    ids = [r["id"] for r in rules]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"Duplicate rule ids: {dupes}")
    return rules


def load_config(rules_dir=RULES_DIR):
    with open(os.path.join(rules_dir, "plans.yaml")) as fh:
        plans = yaml.safe_load(fh)
    with open(os.path.join(rules_dir, "precedence.yaml")) as fh:
        prec = yaml.safe_load(fh)["precedence"]
    return plans, prec


def _unit(member_id, salt):
    h = hashlib.md5(f"{salt}:{member_id}".encode()).hexdigest()
    return int(h[:12], 16) / float(16 ** 12)


def assign_plans(member_ids, plans_cfg, salt="plans-v1"):
    """Deterministic plan + policy start per member (same result every run)."""
    ids = pd.Series(pd.unique(member_ids), name="member_id")
    names = list(plans_cfg["plans"].keys())
    cuts = np.cumsum([plans_cfg["plans"][p]["share"] for p in names])
    u = ids.map(lambda m: _unit(m, salt)).to_numpy()
    idx = np.searchsorted(cuts / cuts[-1], u, side="right").clip(0, len(names) - 1)
    lo, hi = pd.to_datetime(plans_cfg["policy_start_window"])
    span = (hi - lo).days
    days = ids.map(lambda m: int(_unit(m, salt + "-start") * (span + 1))).to_numpy()
    return pd.DataFrame({"member_id": ids, "plan": np.array(names)[idx],
                         "policy_start": lo + pd.to_timedelta(days, unit="D")})


def load_tables(folder):
    t = {}
    for name in ["members", "claims", "claim_lines", "stays"]:
        t[name] = pd.read_parquet(os.path.join(folder, f"{name}.parquet"))
    for opt in ["member_plans", "exclusions"]:
        p = os.path.join(folder, f"{opt}.parquet")
        t[opt] = pd.read_parquet(p) if os.path.exists(p) else None
    return t


def policy_year(svc, start):
    return ((svc.dt.year - start.dt.year)
            - ((svc.dt.month * 100 + svc.dt.day) < (start.dt.month * 100 + start.dt.day)).astype(int))


def build_context(t, plans_cfg):
    members, claims, lines = t["members"], t["claims"].copy(), t["claim_lines"].copy()

    # recompute loader flags so seeded / edited data is always consistent
    claims = claims.drop(columns=[c for c in claims.columns if c.startswith("flag_")])
    claims = add_flags(claims, t["stays"], members)

    mp = t["member_plans"]
    if mp is None:
        mp = assign_plans(members["member_id"], plans_cfg)
    coins = {k: v["coinsurance"] for k, v in plans_cfg["plans"].items()}
    mp = mp.assign(coinsurance=mp["plan"].map(coins))

    year_cols = ["member_id", "year", "esrd", "months_part_b"] + \
                [c for c in members.columns if c.startswith("cc_")]
    my = members[year_cols]
    death = members.dropna(subset=["death_dt"]).groupby("member_id")["death_dt"].min()

    claims["service_dt"] = claims["from_dt"]
    # member year follows CMS convention: the year of the claim's through date
    claims["year"] = claims["thru_dt"].dt.year.fillna(claims["from_dt"].dt.year)
    claims = (claims.merge(mp, on="member_id", how="left")
                    .merge(my, on=["member_id", "year"], how="left"))
    claims["death_dt"] = claims["member_id"].map(death)
    claims["has_member_year"] = claims["months_part_b"].notna()

    keep = ["claim_id", "plan", "policy_start", "esrd", "months_part_b", "has_member_year"] + \
           [c for c in claims.columns if c.startswith("cc_")]
    lines = lines.merge(claims[keep], on="claim_id", how="left")
    for df in (claims, lines):
        df["policy_year"] = policy_year(df["service_dt"], df["policy_start"])
    lines["code_num"] = pd.to_numeric(lines["code"], errors="coerce")
    return claims, lines, mp


# ====================================================================== helpers
def _in_effective(df, rule, date_col="service_dt"):
    lo, hi = pd.to_datetime(rule["effective"])
    return df[date_col].between(lo, hi)


def _scope_lines(lines, rule):
    s = rule["scope"]
    m = _in_effective(lines, rule)
    if s.get("codes"):
        m &= lines["code"].isin(s["codes"])
    if s.get("code_prefix"):
        m &= lines["code"].str.startswith(tuple(s["code_prefix"]))
    if s.get("plans"):
        m &= lines["plan"].isin(s["plans"])
    return lines[m]


def _scope_claims(claims, rule):
    s = rule["scope"]
    m = _in_effective(claims, rule)
    if s.get("claim_types"):
        m &= claims["claim_type"].isin(s["claim_types"])
    if s.get("plans"):
        m &= claims["plan"].isin(s["plans"])
    return claims[m]


def _fmt(df, template):
    if df.empty:
        return pd.Series([], dtype=str)

    def one(row):
        d = {k: (v.date() if isinstance(v, pd.Timestamp) else v) for k, v in row.items()}
        try:
            return template.format(**d)
        except (KeyError, ValueError, IndexError, TypeError):
            return template
    return df.apply(one, axis=1)


def _hits(df, rule, outcome=None, line_level=True, score=None):
    if df.empty:
        return pd.DataFrame(columns=HIT_COLS)
    return pd.DataFrame({
        "claim_id": df["claim_id"].to_numpy(),
        "code": df["code"].to_numpy() if line_level else None,
        "rule_id": rule["id"],
        "layer": rule["layer"],
        "outcome": outcome or rule.get("outcome", "flag"),
        "priority": rule.get("priority", 0),
        "reason": _fmt(df, rule["reason"]).to_numpy(),
        "fwa": bool(rule.get("fwa", False)) or rule["layer"] == "anomaly",
        "score": score if score is not None else (0.9 if rule.get("fwa") else 0.0),
    })


# ====================================================================== rule kinds
def k_eligibility_part_b(rule, claims, lines, t):
    c = _scope_claims(claims, rule)
    out = [_hits(c[c["months_part_b"] == 0], rule, line_level=False)]
    if rule.get("refer_if_no_record"):
        nr = c[~c["has_member_year"]]
        r2 = dict(rule, reason="No eligibility record for {service_year}")
        out.append(_hits(nr, r2, outcome="refer", line_level=False))
    return pd.concat(out, ignore_index=True)


def k_after_death(rule, claims, lines, t):
    c = _scope_claims(claims, rule)
    return _hits(c[c["from_dt"] > c["death_dt"]], rule, line_level=False)


def k_excluded_provider(rule, claims, lines, t):
    ex = t.get("exclusions")
    if ex is None or ex.empty:
        return pd.DataFrame(columns=HIT_COLS)
    c = _scope_claims(claims, rule).merge(ex, on="provider_id")
    return _hits(c[c["from_dt"] >= c["excl_dt"]], rule, line_level=False)


def k_frequency_limit(rule, claims, lines, t):
    p = rule["params"]
    l = _scope_lines(lines, rule)
    l = l[l["plan"].isin(p["limits"].keys())]
    if l.empty:
        return pd.DataFrame(columns=HIT_COLS)
    days = (l[["member_id", "policy_year", "service_dt"]].drop_duplicates()
            .sort_values(["member_id", "policy_year", "service_dt"]))
    days["rank"] = days.groupby(["member_id", "policy_year"]).cumcount() + 1
    l = l.merge(days, on=["member_id", "policy_year", "service_dt"])
    l["limit"] = l["plan"].map(p["limits"])
    for ex in p.get("exceptions", []):
        cond = l[ex["condition"]].fillna(False).astype(bool)
        l.loc[cond, "limit"] = l.loc[cond, "limit"].clip(lower=ex["limit"])
    return _hits(l[l["rank"] > l["limit"]], rule)


def k_requires_condition(rule, claims, lines, t):
    p = rule["params"]
    l = _scope_lines(lines, rule)
    ok = l[p["conditions_any"]].fillna(False).astype(bool).any(axis=1)
    if p.get("refer_if_any"):
        ref = l[p["refer_if_any"]].fillna(False).astype(bool).any(axis=1)
    else:
        ref = pd.Series(False, index=l.index)
    no_rec = ~l["has_member_year"].fillna(False).astype(bool)
    return pd.concat([
        _hits(l[~ok & ~ref & ~no_rec], rule),
        _hits(l[~ok & ref & ~no_rec],
              dict(rule, reason=rule["reason"] + "; chronic kidney disease on record, refer for review"),
              outcome="refer"),
        _hits(l[no_rec], dict(rule, reason="No member record for the service year"), outcome="refer"),
    ], ignore_index=True)


def k_waiting_period(rule, claims, lines, t):
    l = _scope_lines(lines, rule)
    end = l["policy_start"] + pd.to_timedelta(rule["params"]["days"], unit="D")
    return _hits(l[l["service_dt"] < end], rule)


def k_exclusion(rule, claims, lines, t):
    return _hits(_scope_lines(lines, rule), rule)


def k_grant(rule, claims, lines, t):
    l = _scope_lines(lines, rule)
    conds = rule["params"].get("conditions_any")
    if conds:
        l = l[l[conds].fillna(False).astype(bool).any(axis=1)]
    return _hits(l, rule, outcome="allow")


def k_flag(rule, claims, lines, t):
    c = _scope_claims(claims, rule)
    return _hits(c[c[rule["params"]["column"]].fillna(False).astype(bool)], rule, line_level=False)


def k_duplicate_line_same_day(rule, claims, lines, t):
    l = _scope_lines(lines, rule).sort_values("claim_id")
    first = l.groupby(["member_id", "code", "service_dt"])["claim_id"].transform("first")
    return _hits(l[l["claim_id"] != first], rule)


def k_unit_cap(rule, claims, lines, t):
    l = _scope_lines(lines, rule).copy()
    l["max_units"] = rule["params"]["max_units"]
    l["units"] = l["repeat_count"]
    return _hits(l[l["units"] > l["max_units"]], rule)


def k_pair_edit(rule, claims, lines, t):
    p = rule["params"]
    l = lines[_in_effective(lines, rule)]
    has1 = set(l.loc[l["code"] == p["column1"], "claim_id"])
    return _hits(l[(l["code"] == p["column2"]) & l["claim_id"].isin(has1)], rule)


def k_requires_companion(rule, claims, lines, t):
    l = _scope_lines(lines, rule)
    ok_claims = set()
    for lo, hi in rule["params"]["companion_ranges"]:
        ok_claims |= set(lines.loc[lines["code_num"].between(lo, hi), "claim_id"])
    return _hits(l[~l["claim_id"].isin(ok_claims)], rule)


def k_modifier_exception(rule, claims, lines, t):
    # DE-SynPUF has no modifiers, so this never fires at run time.
    return pd.DataFrame(columns=HIT_COLS)


def k_consecutive_days(rule, claims, lines, t):
    """Repeated same-code billing patterns for one member (FWA-01): consecutive
    daily runs (params.min_run) and every-other-day runs (params.min_run_alt).
    Runs shorter than 4 (or alternate-day runs) score params.short_score so they
    rank below the strongest flags and reviewer workload stays stable."""
    p = rule["params"]
    d = (lines[["member_id", "code", "service_dt"]]
         .drop_duplicates().sort_values(["member_id", "code", "service_dt"]))
    gap = d.groupby(["member_id", "code"])["service_dt"].diff().dt.days

    # consecutive daily runs: rows with gap == 1 continue a run
    d1 = d.assign(gap=gap)
    d1["run_id"] = (d1["gap"] != 1).cumsum()
    d1["run"] = d1.groupby("run_id")["service_dt"].transform("size")
    cons = d1[d1["run"] >= p["min_run"]]

    # every-other-day runs: rows with gap == 2 continue a run; include the run's
    # start row (any gap except 1 — gap-1 rows belong to the consecutive pattern)
    d2 = d.assign(gap=gap)
    d2["run_id"] = (d2["gap"] != 2).cumsum()
    d2["run"] = d2.groupby("run_id")["service_dt"].transform("size")
    alt = d2[(d2["gap"] != 1) & (d2["run"] >= p.get("min_run_alt", 10**9))]

    short = p.get("short_score", rule["score"])
    hits = []
    if len(cons):
        keys = cons[["member_id", "code", "service_dt", "run"]]
        l = lines.merge(keys, on=["member_id", "code", "service_dt"])
        s = np.where(l["run"] >= 4, rule["score"], short)
        hits.append(_hits(l, rule, outcome="flag", score=s))
    if len(alt):
        keys = alt[["member_id", "code", "service_dt", "run"]]
        r = dict(rule, reason=p.get("alt_reason", "{code} billed every other day {run} times"))
        l = lines.merge(keys, on=["member_id", "code", "service_dt"])
        hits.append(_hits(l, r, outcome="flag", score=short))
    if not hits:
        return pd.DataFrame(columns=HIT_COLS)
    out = pd.concat(hits, ignore_index=True)
    return out.drop_duplicates(["claim_id", "code", "rule_id"])


def k_near_duplicate(rule, claims, lines, t):
    """Same member and provider, service dates within `days`, billed amount within
    `amount_pct`, sharing at least one service code. Flags the later claim."""
    p = rule["params"]
    days = int(p.get("days", 3))
    pct = float(p.get("amount_pct", 0.05))
    # claim_lines holds outpatient claims only, so the detector covers outpatient
    cols = ["claim_id", "member_id", "provider_id", "from_dt", "pmt_amt_total"]
    c = claims[cols].copy()
    c = c[c["from_dt"].notna() & c["pmt_amt_total"].notna() & (c["pmt_amt_total"] > 0)]
    c = c[c["claim_id"].isin(lines["claim_id"].unique())]
    c = c.sort_values(["member_id", "provider_id", "from_dt", "claim_id"])
    pairs = c.merge(c, on=["member_id", "provider_id"], suffixes=("_a", "_b"))
    pairs = pairs[pairs["claim_id_a"] < pairs["claim_id_b"]].copy()
    if pairs.empty:
        return pd.DataFrame(columns=HIT_COLS)
    pairs["gap_days"] = (pairs["from_dt_b"] - pairs["from_dt_a"]).abs().dt.days
    ref = pairs[["pmt_amt_total_a", "pmt_amt_total_b"]].max(axis=1)
    pairs["amt_diff"] = (pairs["pmt_amt_total_a"] - pairs["pmt_amt_total_b"]).abs() / ref
    near = pairs[(pairs["gap_days"] <= days) & (pairs["amt_diff"] <= pct)]
    if near.empty:
        return pd.DataFrame(columns=HIT_COLS)
    # keep only pairs that share at least one service code
    codes = lines.drop_duplicates(["claim_id", "code"]).groupby("claim_id")["code"].agg(set)
    share = [bool(codes.get(a, frozenset()) & codes.get(b, frozenset()))
             for a, b in zip(near["claim_id_a"], near["claim_id_b"])]
    near = near[share]
    if near.empty:
        return pd.DataFrame(columns=HIT_COLS)
    later = (near["from_dt_b"] >= near["from_dt_a"]).to_numpy()
    h = pd.DataFrame({
        "claim_id": np.where(later, near["claim_id_b"], near["claim_id_a"]),
        "other_id": np.where(later, near["claim_id_a"], near["claim_id_b"]),
        "gap_days": near["gap_days"].to_numpy(),
        "amt_a": near["pmt_amt_total_a"].to_numpy(),
        "amt_b": near["pmt_amt_total_b"].to_numpy(),
    })
    return _hits(h, rule, line_level=False, score=rule.get("score", 0.7))


def k_peer_outlier(rule, claims, lines, t):
    """Services per patient for (facility, code), compared with the median facility."""
    p = rule["params"]
    g = (lines.groupby(["code", "provider_id"])
              .agg(count=("claim_id", "size"), patients=("member_id", "nunique")).reset_index())
    g["rate"] = g["count"] / g["patients"]
    ref = g[g["patients"] >= p["min_patients"]].groupby("code")["rate"].agg(["median", "size"])
    g = g.join(ref, on="code")
    g["ratio"] = g["rate"] / g["median"]
    bad = g[(g["size"] >= p["min_peers"]) & (g["count"] >= p["min_count"]) &
            (g["patients"] >= p["min_patients"]) & (g["ratio"] >= p["ratio"])]
    l = lines.merge(bad[["code", "provider_id", "count", "rate", "ratio"]],
                    on=["code", "provider_id"])
    # more extreme outliers score higher: score = ratio x score_per_ratio, capped
    if "score_per_ratio" in p:
        score = (l["ratio"] * p["score_per_ratio"]).clip(upper=p.get("score_cap", 0.95)).to_numpy()
    else:
        score = rule["score"]
    return _hits(l, rule, outcome="flag", score=score)


def k_same_day_mass(rule, claims, lines, t):
    """One provider billing the same code to many different members on the same
    day (FWA-04) — mass billing with no care relationship."""
    p = rule["params"]
    l = lines[lines["service_dt"].notna() & lines["provider_id"].notna()]
    if l.empty:
        return pd.DataFrame(columns=HIT_COLS)
    g = (l.groupby(["provider_id", "code", "service_dt"])
          ["member_id"].nunique().reset_index(name="members"))
    bad = g[g["members"] >= p["min_members"]]
    if bad.empty:
        return pd.DataFrame(columns=HIT_COLS)
    l = l.merge(bad[["provider_id", "code", "service_dt", "members"]],
                on=["provider_id", "code", "service_dt"])
    return _hits(l, rule, outcome="flag", score=rule.get("score", 0.9))


RULE_KINDS = {name[2:]: fn for name, fn in list(globals().items()) if name.startswith("k_")}


# ====================================================================== decide
def apply_grants(hits):
    """A grant suppresses lower-priority coverage-layer denials on the same line."""
    hits = hits.copy()
    hits["suppressed_by"] = None
    g = hits[hits["outcome"] == "allow"]
    if g.empty:
        return hits
    g = (g.groupby(["claim_id", "code"])
          .agg(g_pri=("priority", "max"), g_id=("rule_id", "first")).reset_index())
    cand = hits.reset_index().merge(g, on=["claim_id", "code"])
    sup = cand[(cand["layer"] == "coverage") & (cand["outcome"] != "allow") &
               (cand["priority"] < cand["g_pri"])]
    hits.loc[sup["index"], "suppressed_by"] = sup["g_id"].to_numpy()
    return hits


def decide(claims, lines, hits):
    act = hits[(hits["suppressed_by"].isna()) & (hits["outcome"].isin(SEVERITY)) &
               (hits["outcome"] != "allow") & (hits["layer"] != "anomaly")].copy()
    act["sev"] = act["outcome"].map(SEVERITY)

    n_codes = lines.groupby("claim_id")["code"].nunique()
    denied_codes = act[act["outcome"] == "deny_line"].groupby("claim_id")["code"].nunique()
    share = (denied_codes / n_codes).reindex(claims["claim_id"]).fillna(0).to_numpy()

    top = act.groupby("claim_id")["sev"].max().reindex(claims["claim_id"]).fillna(0).to_numpy()
    dec = np.select(
        [top == 5, top == 4, (top >= 2) & (share >= 1), top == 3, top == 2],
        ["rejected", "denied", "denied", "pended", "partial"], "paid")

    d = claims[["claim_id", "member_id", "claim_type", "from_dt", "provider_id", "plan",
                "coinsurance", "pmt_amt_total"]].copy()
    d["decision"] = dec
    d["denied_line_share"] = share

    # primary rule = highest-priority hit at the severity that decided
    want = {"rejected": [5], "denied": [4, 2], "pended": [3], "partial": [2], "paid": []}
    act = act.merge(d[["claim_id", "decision"]], on="claim_id")
    act = act[[s in want[x] for s, x in zip(act["sev"], act["decision"])]]
    prim = (act.sort_values(["sev", "priority"], ascending=False)
               .drop_duplicates("claim_id")[["claim_id", "rule_id", "reason"]]
               .rename(columns={"rule_id": "primary_rule", "reason": "primary_reason"}))
    d = d.merge(prim, on="claim_id", how="left")

    allowed = d["pmt_amt_total"].clip(lower=0).fillna(0)
    d["allowed_amt"] = allowed
    d["payable_amt"] = np.select(
        [d["decision"].isin(["rejected", "denied", "pended"]), d["decision"] == "partial"],
        [0.0, allowed * (1 - d["denied_line_share"])], allowed)
    d["held_amt"] = np.where(d["decision"] == "pended", allowed, 0.0)
    d["plan_paid_amt"] = d["payable_amt"] * (1 - d["coinsurance"].fillna(0))

    # FWA score: 1 - prod(1 - s) over distinct FWA rules hit on the claim
    f = hits[hits["fwa"].astype(bool) & hits["suppressed_by"].isna()]
    f = f.drop_duplicates(["claim_id", "rule_id"]).copy()
    f["score"] = f["score"].astype(float)
    fs = f.groupby("claim_id")["score"].apply(lambda s: 1 - np.prod(1 - s.to_numpy()))
    fr = f.groupby("claim_id")["reason"].apply(lambda s: " | ".join(s))
    d["fwa_score"] = d["claim_id"].map(fs).fillna(0.0)
    d["fwa_reasons"] = d["claim_id"].map(fr)

    rf = hits.groupby("claim_id")["rule_id"].apply(lambda s: ",".join(sorted(set(s))))
    d["rules_fired"] = d["claim_id"].map(rf)
    return d


# ====================================================================== conflicts
def _code_scope(r):
    if r["kind"] in ("pair_edit", "modifier_exception"):
        return {"pair": (r["params"]["column1"], r["params"]["column2"])}
    s = r["scope"]
    return {"codes": set(s.get("codes", [])), "prefix": tuple(s.get("code_prefix", []))}


def _codes_overlap(a, b):
    ca, cb = _code_scope(a), _code_scope(b)
    if "pair" in ca or "pair" in cb:
        return ca == cb
    if not (ca["codes"] or ca["prefix"]) or not (cb["codes"] or cb["prefix"]):
        return False   # rules without a code scope apply to every line; not compared
    if ca["codes"] & cb["codes"]:
        return True
    if cb["prefix"] and any(c.startswith(cb["prefix"]) for c in ca["codes"]):
        return True
    if ca["prefix"] and any(c.startswith(ca["prefix"]) for c in cb["codes"]):
        return True
    return bool(ca["prefix"] and cb["prefix"] and set(ca["prefix"]) & set(cb["prefix"]))


def _plans(r):
    if r["scope"].get("plans"):
        return set(r["scope"]["plans"])
    if r["kind"] == "frequency_limit":
        return set(r["params"]["limits"].keys())
    return None   # all plans


def _plans_overlap(a, b):
    pa, pb = _plans(a), _plans(b)
    return pa is None or pb is None or bool(pa & pb)


def _dates_overlap(a, b):
    a0, a1 = pd.to_datetime(a["effective"])
    b0, b1 = pd.to_datetime(b["effective"])
    return a0 <= b1 and b0 <= a1


def _specificity(r):
    named_codes = bool(r["scope"].get("codes")) or r["kind"] in ("pair_edit", "modifier_exception")
    return int(bool(r["scope"].get("plans"))) + int(named_codes)


def _strictness(r):
    o = SEVERITY.get(r.get("outcome"), 0)
    lim = r["params"].get("max_units")
    if lim is None and r["kind"] == "frequency_limit":
        lim = min(r["params"]["limits"].values())
    if r["kind"] == "exclusion":
        lim = 0
    return (o, -(lim if lim is not None else 10 ** 6))


def resolve(a, b, prec):
    """Return (winner_id, criterion_id, explanation) under the precedence policy."""
    gap = prec[0].get("min_priority_gap", 10)
    pa, pb = a.get("priority", 0), b.get("priority", 0)
    if abs(pa - pb) >= gap:
        w = a if pa > pb else b
        return w["id"], "P1", f"Priority {max(pa, pb)} vs {min(pa, pb)}: deliberate override"
    sa, sb = _specificity(a), _specificity(b)
    if sa != sb:
        w = a if sa > sb else b
        return w["id"], "P2", f"{w['id']} has the more specific scope"
    if {a["layer"], b["layer"]} == {"edit", "coverage"}:
        w = a if a["layer"] == "edit" else b
        return w["id"], "P3", "Edit rule wins over coverage rule on the same line"
    ea, eb = pd.to_datetime(a["effective"][0]), pd.to_datetime(b["effective"][0])
    if ea != eb:
        w = a if ea > eb else b
        return w["id"], "P4", f"{w['id']} has the newer effective date ({w['effective'][0]})"
    ta, tb = _strictness(a), _strictness(b)
    if ta != tb:
        w = a if ta > tb else b
        return w["id"], "P5", f"{w['id']} is stricter"
    return None, "-", "Tie: needs a person to decide"


def _same_rule(a, b):
    return all(a.get(k) == b.get(k) for k in ["kind", "scope", "params", "outcome", "layer"])


def detect_conflicts(rules, prec):
    rows = []

    def add(a, b, ctype, detail, winner=None):
        w, crit, why = winner if winner is not None else resolve(a, b, prec)
        rows.append({"rule_a": a["id"], "rule_b": b["id"], "type": ctype, "detail": detail,
                     "winner": w, "criterion": crit, "why": why,
                     "files": f"{a['source_file']} / {b['source_file']}"})

    line_rules = [r for r in rules if r["kind"] in LINE_LEVEL_KINDS and r["layer"] != "anomaly"]
    for i, a in enumerate(line_rules):
        for b in line_rules[i + 1:]:
            if _same_rule(a, b):
                live = a if a["source_file"] != PROPOSAL_FILE else b
                add(a, b, "redundant", "Identical scope, parameters and outcome",
                    (live["id"], "P6", f"Keep live rule {live['id']}"))
                continue
            if not (_codes_overlap(a, b) and _plans_overlap(a, b) and _dates_overlap(a, b)):
                continue
            kinds = {a["kind"], b["kind"]}
            if kinds == {"frequency_limit"}:
                common = (_plans(a) or set()) & (_plans(b) or set())
                diff = [p for p in sorted(common)
                        if a["params"]["limits"][p] != b["params"]["limits"][p]]
                if diff:
                    ctype = ("version_overlap" if a.get("family") == b.get("family")
                             and a["effective"] != b["effective"] else "limit_clash")
                    add(a, b, ctype, "Different limits for " + ", ".join(
                        f"{p}: {a['params']['limits'][p]} vs {b['params']['limits'][p]}" for p in diff))
            elif kinds == {"unit_cap"}:
                if a["params"]["max_units"] != b["params"]["max_units"]:
                    add(a, b, "unit_cap_clash",
                        f"Max units {a['params']['max_units']} vs {b['params']['max_units']}")
            elif "exclusion" in kinds and a["layer"] == b["layer"] == "coverage":
                ex, other = (a, b) if a["kind"] == "exclusion" else (b, a)
                if other["kind"] == "grant":
                    w = resolve(ex, other, prec)
                    ctype = "unreachable_exception" if w[0] == ex["id"] else "exception_overrides_exclusion"
                    add(ex, other, ctype, f"{other['id']} allows lines that {ex['id']} excludes", w)
                elif other["kind"] != "exclusion":
                    add(ex, other, "exclusion_shadows_rule",
                        f"{ex['id']} excludes lines that {other['id']} ({other['kind']}) regulates")
            elif "grant" in kinds:
                g, other = (a, b) if a["kind"] == "grant" else (b, a)
                if other["layer"] != "coverage" or other.get("outcome") == "allow":
                    continue
                if other["kind"] == "requires_condition" and \
                        set(g["params"].get("conditions_any", [])) <= set(other["params"]["conditions_any"]):
                    continue   # consistent: the grant covers members the rule already allows
                w = resolve(g, other, prec)
                ctype = "unreachable_exception" if w[0] == other["id"] else "exception_overrides_rule"
                add(g, other, ctype, f"{g['id']} allows lines that {other['id']} denies", w)

    for r in rules:
        if r["kind"] == "modifier_exception":
            pair = (r["params"]["column1"], r["params"]["column2"])
            base = [x for x in rules if x["kind"] == "pair_edit" and
                    (x["params"]["column1"], x["params"]["column2"]) == pair]
            if not base:
                rows.append({"rule_a": r["id"], "rule_b": None, "type": "orphan_exception",
                             "detail": f"Exception for pair {pair[0]} + {pair[1]} but no pair edit exists",
                             "winner": None, "criterion": "-",
                             "why": "Nothing to make an exception to: retire it or add the base edit",
                             "files": r["source_file"]})
    return pd.DataFrame(rows, columns=["rule_a", "rule_b", "type", "detail", "winner",
                                       "criterion", "why", "files"])


# ====================================================================== runtime precedence
def _segments(loser, w_from, w_to):
    """Split a rule's effective window into parts outside / inside [w_from, w_to]."""
    lo, hi = pd.to_datetime(loser["effective"])
    out, inside = [], None
    if lo < w_from:
        out.append([str(lo.date()), str((min(hi, w_from - pd.Timedelta(days=1))).date())])
    if hi > w_to:
        out.append([str((max(lo, w_to + pd.Timedelta(days=1))).date()), str(hi.date())])
    if lo <= w_to and w_from <= hi:
        inside = [str(max(lo, w_from).date()), str(min(hi, w_to).date())]
    return out, inside


def apply_precedence(rules, prec):
    """Make the precedence policy govern run time: where two limit rules (or two unit caps)
    clash, the losing rule stops applying to the overlapping plans/codes during the
    winner's effective window. Returns (rules to run, list of supersessions)."""
    conf = detect_conflicts(rules, prec)
    conf = conf[conf["type"].isin(["limit_clash", "version_overlap", "unit_cap_clash"]) &
                conf["winner"].notna()]
    by_id = {r["id"]: [dict(r)] for r in rules}
    notes = []
    for c in conf.itertuples():
        loser_id = c.rule_b if c.winner == c.rule_a else c.rule_a
        win = next(r for r in rules if r["id"] == c.winner)
        w_from, w_to = pd.to_datetime(win["effective"])
        new_parts = []
        for part in by_id[loser_id]:
            outside, inside = _segments(part, w_from, w_to)
            new_parts += [dict(part, effective=e) for e in outside]
            if inside is None:
                continue
            if part["kind"] == "frequency_limit":
                common = set(_plans(win) or []) & set(part["params"]["limits"])
                keep = {k: v for k, v in part["params"]["limits"].items() if k not in common}
                if keep:
                    new_parts.append(dict(part, effective=inside, params=dict(part["params"], limits=keep)))
                what = ", ".join(sorted(common))
            else:  # unit_cap
                codes = [x for x in part["scope"].get("codes", []) if x not in win["scope"].get("codes", [])]
                if codes:
                    new_parts.append(dict(part, effective=inside, scope=dict(part["scope"], codes=codes)))
                what = ", ".join(win["scope"].get("codes", []))
        by_id[loser_id] = new_parts
        notes.append({"winner": c.winner, "superseded": loser_id, "for": what,
                      "from": str(w_from.date()), "to": str(w_to.date()), "criterion": c.criterion})
    out = [p for r in rules for p in by_id[r["id"]]]
    return out, notes


# ====================================================================== run
def run(data_folder, rules_dir=RULES_DIR, include_proposals=False, rules=None, verbose=True):
    t0 = datetime.now()
    plans_cfg, prec = load_config(rules_dir)
    rules = rules if rules is not None else load_rules(rules_dir, include_proposals)
    conflicts = detect_conflicts(rules, prec)
    run_rules, superseded = apply_precedence(rules, prec)
    t = load_tables(data_folder)
    claims, lines, mp = build_context(t, plans_cfg)

    parts, timing = [], {}
    for r in run_rules:
        fn = RULE_KINDS.get(r["kind"])
        if fn is None:
            raise ValueError(f"Unknown rule kind {r['kind']} in {r['id']}")
        s = datetime.now()
        h = fn(r, claims, lines, t)
        timing[r["id"]] = timing.get(r["id"], 0) + round((datetime.now() - s).total_seconds(), 2)
        if len(h):
            parts.append(h)
    hits = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=HIT_COLS)
    hits = apply_grants(hits)
    decisions = decide(claims, lines, hits)

    summary = {
        "data": data_folder, "rules": len(rules), "proposals": include_proposals,
        "claims": int(len(decisions)),
        "decisions": decisions["decision"].value_counts().to_dict(),
        "hits_by_rule": hits["rule_id"].value_counts().to_dict(),
        "fwa_flagged_claims": int((decisions["fwa_score"] > 0).sum()),
        "allowed_amt": round(float(decisions["allowed_amt"].sum()), 2),
        "payable_amt": round(float(decisions["payable_amt"].sum()), 2),
        "plan_paid_amt": round(float(decisions["plan_paid_amt"].sum()), 2),
        "conflicts": int(len(conflicts)),
        "superseded": superseded,
        "seconds": round((datetime.now() - t0).total_seconds(), 1),
    }
    if verbose:
        print(json.dumps(summary, indent=2, default=str))
    return {"decisions": decisions, "hits": hits, "conflicts": conflicts,
            "summary": summary, "rules": rules, "timing": timing, "superseded": superseded}


def explain(res, claim_id):
    d = res["decisions"].set_index("claim_id").loc[claim_id]
    h = res["hits"][res["hits"]["claim_id"] == claim_id].sort_values("priority", ascending=False)
    print(f"Claim {claim_id} | {d['claim_type']} | {pd.Timestamp(d['from_dt']).date()} | "
          f"member {d['member_id']} | plan {d['plan']}")
    print(f"DECISION: {d['decision'].upper()}"
          + (f"  ({d['primary_rule']}: {d['primary_reason']})" if pd.notna(d["primary_rule"]) else ""))
    print(f"Allowed {d['allowed_amt']:.2f} | payable {d['payable_amt']:.2f} | "
          f"plan pays {d['plan_paid_amt']:.2f} | FWA score {d['fwa_score']:.2f}")
    if h.empty:
        print("No rules fired.")
    for _, r in h.iterrows():
        sup = f"  [overridden by {r['suppressed_by']}]" if pd.notna(r["suppressed_by"]) else ""
        code = f" {r['code']}" if pd.notna(r["code"]) else ""
        print(f"  - {r['rule_id']:<9} {r['outcome']:<9}{code:<7} {r['reason']}{sup}")


def compare(base, new):
    """What-if: how decisions and money change between two runs."""
    a = base["decisions"][["claim_id", "decision", "plan_paid_amt"]]
    b = new["decisions"][["claim_id", "decision", "plan_paid_amt"]]
    m = a.merge(b, on="claim_id", suffixes=("_before", "_after"))
    moved = m[m["decision_before"] != m["decision_after"]]
    table = (moved.groupby(["decision_before", "decision_after"]).size()
                  .rename("claims").reset_index().sort_values("claims", ascending=False))
    delta = m["plan_paid_amt_after"].sum() - m["plan_paid_amt_before"].sum()
    print(f"{len(moved):,} claims change decision; plan cost change {delta:+,.2f}")
    return table


def save(res, out):
    os.makedirs(out, exist_ok=True)
    res["decisions"].to_parquet(os.path.join(out, "decisions.parquet"), index=False)
    h = res["hits"].copy()
    for c in ["code", "suppressed_by", "reason"]:
        h[c] = h[c].astype("string")
    h.to_parquet(os.path.join(out, "hits.parquet"), index=False)
    res["conflicts"].to_csv(os.path.join(out, "conflicts.csv"), index=False)
    with open(os.path.join(out, "summary.json"), "w") as fh:
        json.dump(res["summary"], fh, indent=2, default=str)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="folder with clean or seeded tables")
    ap.add_argument("--rules", default=RULES_DIR)
    ap.add_argument("--with-proposals", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    res = run(a.data, a.rules, a.with_proposals)
    if a.out:
        save(res, a.out)
        print(f"Saved to {a.out}")
