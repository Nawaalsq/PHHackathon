"""
seed.py - Add the fictional parts to a clean DE-SynPUF sample and write the
answer keys used by evaluate.py.

    python seed.py --clean clean/sample2_full --out clean/sample2_seeded --keys answer_keys/sample2

What it adds (all reproducible with --seed):
  1. member_plans   plan (Gold/Silver/Basic) and policy start date per member
  2. exclusions     a fictional excluded-provider list (LEIE-style fields)
  3. seeded anomalies injected into real claims (RD03):
        service_after_death, consecutive_days, excluded_provider,
        facility_over_servicing, unbundling, exact_duplicate
  3b. with --hard: subtle variants (weaker signals, near the detector thresholds)
      and novel patterns no detector was written for (upcoding drift, same-day
      mass billing). Keys record difficulty = easy | subtle | novel.
  4. boundary cases: synthetic members (ids BND..) with hand-built claims that
     sit exactly on rule edges, each with the expected decision and rule (RD02/RD04)

Answer keys go to --keys, separate from the data, so the engine can't see them:
  anomalies.csv          claim_id, pattern, expected_detector, difficulty, fwa
  boundary_expected.csv  case_id, description, claim_id, expected_decision, expected_rule
  seed_report.json       what was injected
"""

import argparse
import json
import os
import shutil

import numpy as np
import pandas as pd

import engine
from load_desynpuf import add_flags, code_type

HERE = os.path.dirname(os.path.abspath(__file__))
TABLES = ["members", "claims", "claim_lines", "stays", "diagnoses", "procedures"]


class Builder:
    """Collects new claims + lines with the same columns as the clean tables."""

    def __init__(self, claims_cols, lines_cols):
        self.cc, self.lc = claims_cols, lines_cols
        self.claims, self.lines = [], []
        self.n = 0

    def new_id(self, prefix):
        self.n += 1
        return f"{prefix}{self.n:07d}"

    def clone(self, claim, lines, new_id, new_from, keep_codes=None, extra_codes=None,
              amount=None):
        """Copy a real claim to a new id/date (optionally only some codes, plus extra codes)."""
        c = claim.copy()
        shift = new_from - c["from_dt"]
        c["claim_id"], c["from_dt"] = new_id, new_from
        c["thru_dt"] = c["thru_dt"] + shift if pd.notna(c["thru_dt"]) else new_from
        c["service_year"] = c["thru_dt"].year
        if amount is not None:
            c["pmt_amt_total"] = c["pmt_amt_seg1"] = amount
        l = lines.copy()
        if keep_codes is not None:
            l = l[l["code"].isin(keep_codes)]
        if extra_codes:
            add = pd.DataFrame([{"code": k, "repeat_count": 1, "first_position": 99}
                                for k in extra_codes])
            l = pd.concat([l, add], ignore_index=True)
        l = l.assign(claim_id=new_id, service_dt=new_from, member_id=c["member_id"],
                     provider_id=c["provider_id"], claim_type=c["claim_type"])
        l["code_type"] = code_type(l["code"])
        l["is_drug_code"] = l["code"].str.match(r"^[JQ]\d{4}$")
        self.claims.append(c.to_dict())
        self.lines.append(l)

    def make(self, claim_id, member_id, date, codes, provider, amount=100.0,
             claim_type="outpatient", admit=None, discharge=None):
        """Build a synthetic claim. codes = {code: repeat_count}."""
        date = pd.Timestamp(date)
        c = {k: np.nan for k in self.cc}
        c.update({"claim_id": claim_id, "member_id": member_id, "claim_type": claim_type,
                  "from_dt": date, "thru_dt": pd.Timestamp(discharge) if discharge else date,
                  "provider_id": provider, "pmt_amt_seg1": amount, "pmt_amt_total": amount,
                  "n_segments": 1, "orphan_segment": False})
        if claim_type == "inpatient":
            c.update({"admit_dt": pd.Timestamp(admit), "discharge_dt": pd.Timestamp(discharge),
                      "util_day_cnt": (pd.Timestamp(discharge) - pd.Timestamp(admit)).days,
                      "drg_cd": "OTH"})
        c["service_year"] = c["thru_dt"].year
        self.claims.append(c)
        if codes:
            l = pd.DataFrame([{"claim_id": claim_id, "member_id": member_id, "code": k,
                               "repeat_count": v, "first_position": i + 1, "service_dt": date,
                               "provider_id": provider, "claim_type": claim_type}
                              for i, (k, v) in enumerate(codes.items())])
            l["code_type"] = code_type(l["code"])
            l["is_drug_code"] = l["code"].str.match(r"^[JQ]\d{4}$")
            self.lines.append(l)

    def frames(self):
        c = pd.DataFrame(self.claims).reindex(columns=self.cc)
        l = (pd.concat(self.lines, ignore_index=True).reindex(columns=self.lc)
             if self.lines else pd.DataFrame(columns=self.lc))
        return c, l


# ====================================================================== anomalies
def seed_anomalies(t, b, rng, scale, keys, report):
    claims, lines, members = t["claims"], t["claim_lines"], t["members"]
    op = claims[(claims["claim_type"] == "outpatient") & claims["from_dt"].notna()]
    lines_by = lines.groupby("claim_id")
    death = members.dropna(subset=["death_dt"]).groupby("member_id")["death_dt"].min()
    dead_ids = set(death.index)

    def n(x):
        return max(1, int(round(x * scale)))

    def pick(df, k):
        return df.sample(min(k, len(df)), random_state=int(rng.integers(1e9)))

    def lines_of(cid):
        return lines_by.get_group(cid) if cid in lines_by.groups else lines.iloc[0:0]

    # A1 service after death
    cand = op[op["member_id"].isin(dead_ids)]
    cand = cand[cand["from_dt"] < cand["member_id"].map(death)].drop_duplicates("member_id")
    for _, c in pick(cand, n(25)).iterrows():
        nid = b.new_id("SA")
        new_from = death[c["member_id"]] + pd.Timedelta(days=int(rng.integers(5, 60)))
        b.clone(c, lines_of(c["claim_id"]), nid, new_from)
        keys.append((nid, "service_after_death", "COV-1.2"))
    report["service_after_death"] = int(sum(k[1] == "service_after_death" for k in keys))

    # A2 same claim on 5 consecutive days (members still alive)
    small = op[~op["member_id"].isin(dead_ids)]
    ncodes = lines.groupby("claim_id").size()
    small = small[small["claim_id"].map(ncodes).between(1, 3)].drop_duplicates("member_id")
    for _, c in pick(small, n(15)).iterrows():
        start = c["from_dt"] + pd.Timedelta(days=int(rng.integers(30, 200)))
        for k in range(5):
            nid = b.new_id("SC")
            b.clone(c, lines_of(c["claim_id"]), nid, start + pd.Timedelta(days=k))
            keys.append((nid, "consecutive_days", "FWA-01"))
    report["consecutive_days"] = int(sum(k[1] == "consecutive_days" for k in keys))

    # A3 excluded providers (label = their real claims on/after the exclusion date)
    vol = op.groupby("provider_id").size()
    provs = vol[(vol >= 20) & (vol <= 400)].index.to_series()
    chosen = provs.sample(min(n(8), len(provs)), random_state=int(rng.integers(1e9)))
    excl = []
    for p in chosen:
        d = pd.Timestamp("2009-01-01") + pd.Timedelta(days=int(rng.integers(0, 365)))
        excl.append({"provider_id": p, "excl_dt": d, "excl_type": "1128a1",
                     "specialty": "FACILITY", "reinstated_dt": pd.NaT})
    decoys = provs.drop(chosen.index, errors="ignore").sample(
        min(3, max(len(provs) - len(chosen), 0)), random_state=int(rng.integers(1e9)))
    for p in decoys:   # excluded after the data ends: should NOT fire
        excl.append({"provider_id": p, "excl_dt": pd.Timestamp("2011-01-01"),
                     "excl_type": "1128b4", "specialty": "FACILITY", "reinstated_dt": pd.NaT})
    t["exclusions"] = pd.DataFrame(excl)
    report["excluded_providers"] = len(chosen)

    # A4 facility over-servicing: one code billed ~6x per patient vs ~1x for peers
    opl = lines[lines["claim_id"].isin(op["claim_id"])]
    g = (opl.groupby(["code", "provider_id"])
             .agg(cnt=("claim_id", "size"), pts=("member_id", "nunique")).reset_index())
    g = g[g["code"].isin(["97110", "99213", "36415", "85025", "93005", "71020", "99212"])]
    g = g[g["pts"].between(6, 15) & (g["cnt"] / g["pts"] < 2)]
    g = g.drop_duplicates("provider_id")
    base = claims.set_index("claim_id")
    for _, row in pick(g, n(6)).iterrows():
        report.setdefault("_used_providers", set()).add(row["provider_id"])
        src = opl[(opl["provider_id"] == row["provider_id"]) & (opl["code"] == row["code"])]
        need = int(6 * row["pts"] - row["cnt"])
        for i in range(need):
            s = src.iloc[i % len(src)]
            c = base.loc[s["claim_id"]].copy()
            c["claim_id"] = s["claim_id"]
            nid = b.new_id("SF")
            new_from = c["from_dt"] + pd.Timedelta(days=int(rng.integers(1, 365)))
            amt = float(c["pmt_amt_total"]) / max(1, ncodes.get(s["claim_id"], 1))
            b.clone(c, lines_of(s["claim_id"]), nid, new_from, keep_codes=[row["code"]],
                    amount=round(amt, 2))
            keys.append((nid, "facility_over_servicing", "FWA-02"))
    report["facility_over_servicing"] = int(sum(k[1] == "facility_over_servicing" for k in keys))

    # A5 unbundling: claim with 80053 gets 80048 added
    has53 = set(lines.loc[lines["code"] == "80053", "claim_id"])
    has48 = set(lines.loc[lines["code"] == "80048", "claim_id"])
    cand = op[op["claim_id"].isin(has53 - has48)]
    for _, c in pick(cand, n(20)).iterrows():
        nid = b.new_id("SU")
        b.clone(c, lines_of(c["claim_id"]), nid,
                c["from_dt"] + pd.Timedelta(days=int(rng.integers(7, 60))), extra_codes=["80048"])
        keys.append((nid, "unbundling", "EDIT-20"))
    report["unbundling"] = int(sum(k[1] == "unbundling" for k in keys))

    # A6 exact duplicates
    for _, c in pick(op, n(20)).iterrows():
        nid = b.new_id("SD")
        b.clone(c, lines_of(c["claim_id"]), nid, c["from_dt"])
        keys.append((nid, "exact_duplicate", "EDIT-03"))
    report["exact_duplicate"] = int(sum(k[1] == "exact_duplicate" for k in keys))


# pattern -> (difficulty, counts as FWA for the review queue)
PATTERNS = {
    "service_after_death": ("easy", True), "consecutive_days": ("easy", True),
    "excluded_provider": ("easy", True), "facility_over_servicing": ("easy", True),
    "unbundling": ("easy", False), "exact_duplicate": ("easy", True),
    # subtle: same idea, weaker signal (around or below the detector thresholds)
    "consecutive_3_days": ("subtle", True), "alternate_days": ("subtle", True),
    "facility_3x": ("subtle", True), "near_duplicate_amount": ("subtle", True),
    "near_duplicate_next_day": ("subtle", True), "unbundling_other_component": ("subtle", False),
    # novel: no detector was written for these
    "upcoding_drift": ("novel", True), "same_day_mass_billing": ("novel", True),
}


def seed_hard(t, b, rng, scale, keys, report, used_providers):
    """Subtle variants and novel patterns. Expected detectors may be 'A|B' (any of)
    or 'any_fwa' (caught if the claim reaches the FWA queue at all), or 'no_rule'
    (no detector exists for it, so it counts as missed)."""
    claims, lines, members = t["claims"], t["claim_lines"], t["members"]
    op = claims[(claims["claim_type"] == "outpatient") & claims["from_dt"].notna()]
    lines_by = lines.groupby("claim_id")
    death = members.dropna(subset=["death_dt"]).groupby("member_id")["death_dt"].min()
    alive = op[~op["member_id"].isin(set(death.index))]
    ncodes = lines.groupby("claim_id").size()

    def n(x):
        return max(1, int(round(x * scale)))

    def pick(df, k):
        return df.sample(min(k, len(df)), random_state=int(rng.integers(1e9)))

    def lines_of(cid):
        return lines_by.get_group(cid) if cid in lines_by.groups else lines.iloc[0:0]

    def count(p):
        return int(sum(k[1] == p for k in keys))

    small = alive[alive["claim_id"].map(ncodes).between(1, 3)].drop_duplicates("member_id")

    # S1 three consecutive days (detector needs 4)
    for _, c in pick(small, n(15)).iterrows():
        start = c["from_dt"] + pd.Timedelta(days=int(rng.integers(30, 200)))
        for k in range(3):
            nid = b.new_id("HC")
            b.clone(c, lines_of(c["claim_id"]), nid, start + pd.Timedelta(days=k))
            keys.append((nid, "consecutive_3_days", "FWA-01"))

    # S2 every other day, five times
    for _, c in pick(small, n(15)).iterrows():
        start = c["from_dt"] + pd.Timedelta(days=int(rng.integers(30, 200)))
        for k in range(5):
            nid = b.new_id("HA")
            b.clone(c, lines_of(c["claim_id"]), nid, start + pd.Timedelta(days=2 * k))
            keys.append((nid, "alternate_days", "FWA-01"))

    # S3 facility over-servicing at ~3x per patient (detector threshold is 4x)
    opl = lines[lines["claim_id"].isin(op["claim_id"])]
    g = (opl.groupby(["code", "provider_id"])
             .agg(cnt=("claim_id", "size"), pts=("member_id", "nunique")).reset_index())
    g = g[g["code"].isin(["97110", "99213", "36415", "85025", "93005", "71020", "99212"])]
    g = g[g["pts"].between(10, 20) & (g["cnt"] / g["pts"] < 1.5) &
          ~g["provider_id"].isin(used_providers)].drop_duplicates("provider_id")
    base = claims.set_index("claim_id")
    for _, row in pick(g, n(6)).iterrows():
        used_providers.add(row["provider_id"])
        src = opl[(opl["provider_id"] == row["provider_id"]) & (opl["code"] == row["code"])]
        for i in range(int(3 * row["pts"] - row["cnt"])):
            s_ = src.iloc[i % len(src)]
            c = base.loc[s_["claim_id"]].copy()
            c["claim_id"] = s_["claim_id"]
            nid = b.new_id("HF")
            amt = float(c["pmt_amt_total"]) / max(1, ncodes.get(s_["claim_id"], 1))
            b.clone(c, lines_of(s_["claim_id"]), nid,
                    c["from_dt"] + pd.Timedelta(days=int(rng.integers(1, 365))),
                    keep_codes=[row["code"]], amount=round(amt, 2))
            keys.append((nid, "facility_3x", "FWA-02"))

    # S4 near duplicates: amount changed by 1%, or the same claim the next day
    for _, c in pick(op, n(10)).iterrows():
        nid = b.new_id("HD")
        b.clone(c, lines_of(c["claim_id"]), nid, c["from_dt"],
                amount=round(float(c["pmt_amt_total"]) * 1.01 + 1, 2))
        keys.append((nid, "near_duplicate_amount", "EDIT-03|EDIT-04"))
    for _, c in pick(op, n(10)).iterrows():
        nid = b.new_id("HN")
        b.clone(c, lines_of(c["claim_id"]), nid, c["from_dt"] + pd.Timedelta(days=1))
        keys.append((nid, "near_duplicate_next_day", "EDIT-03|EDIT-04"))

    # S5 unbundling with a component we have no pair edit for (glucose 82947 with 80053)
    has53 = set(lines.loc[lines["code"] == "80053", "claim_id"])
    has47 = set(lines.loc[lines["code"] == "82947", "claim_id"])
    for _, c in pick(op[op["claim_id"].isin(has53 - has47)], n(20)).iterrows():
        nid = b.new_id("HU")
        b.clone(c, lines_of(c["claim_id"]), nid,
                c["from_dt"] + pd.Timedelta(days=int(rng.integers(7, 60))), extra_codes=["82947"])
        keys.append((nid, "unbundling_other_component", "no_rule"))

    # N1 upcoding drift: a provider's 99213 visits re-billed later as 99215
    v = opl[opl["code"] == "99213"]
    vp = v.groupby("provider_id").size()
    vp = vp[(vp >= 15) & ~vp.index.isin(used_providers)]
    for p in vp.sample(min(n(4), len(vp)), random_state=int(rng.integers(1e9))).index:
        used_providers.add(p)
        src = v[v["provider_id"] == p]
        for i in range(n(25)):
            s_ = src.iloc[i % len(src)]
            c = base.loc[s_["claim_id"]].copy()
            c["claim_id"] = s_["claim_id"]
            nid = b.new_id("NU")
            b.clone(c, lines_of(s_["claim_id"]).assign(code="99215"), nid,
                    c["from_dt"] + pd.Timedelta(days=int(rng.integers(180, 365))),
                    keep_codes=["99215"], amount=round(float(c["pmt_amt_total"]) * 1.6, 2))
            keys.append((nid, "upcoding_drift", "any_fwa"))

    # N2 same-day mass billing: one provider bills an ECG for 15 unrelated members on one day
    provs = op["provider_id"].value_counts()
    provs = provs[(provs >= 20) & ~provs.index.isin(used_providers)]
    pool = alive.drop_duplicates("member_id")
    for p in provs.sample(min(n(4), len(provs)), random_state=int(rng.integers(1e9))).index:
        used_providers.add(p)
        day = pd.Timestamp("2009-01-01") + pd.Timedelta(days=int(rng.integers(0, 600)))
        for _, m in pool.sample(15, random_state=int(rng.integers(1e9))).iterrows():
            nid = b.new_id("NM")
            b.make(nid, m["member_id"], day, {"93005": 1}, p, amount=40.0)
            keys.append((nid, "same_day_mass_billing", "any_fwa"))

    for p_, (lvl, _) in PATTERNS.items():
        if lvl != "easy":
            report[p_] = count(p_)


# ====================================================================== boundary cases
def seed_boundary(t, b, plans_rows, exclusions):
    """Synthetic members with claims sitting exactly on rule edges."""
    cases = []
    members = t["members"]
    tmpl = {c: np.nan for c in members.columns}
    PS = pd.Timestamp("2008-03-15")

    def member(case, plan="SILVER", start=PS, **flags):
        mid = f"BND{case}"
        for yr in (2008, 2009, 2010):
            r = dict(tmpl)
            r.update({"member_id": mid, "year": yr, "birth_dt": pd.Timestamp("1940-01-01"),
                      "death_dt": pd.NaT, "sex": "F", "race_cd": "1", "esrd": False,
                      "state_cd": "01", "county_cd": "000", "months_part_a": 12,
                      "months_part_b": 12, "months_hmo": 0, "months_part_d": 12,
                      "age_at_year_end": yr - 1940, "flag_negative_annual_amount": False})
            for c in members.columns:
                if c.startswith("cc_"):
                    r[c] = False
            for k, v in flags.items():
                if isinstance(v, dict):
                    r[k] = v.get(yr, r.get(k))
                else:
                    r[k] = v
            members_rows.append(r)
        plans_rows.append({"member_id": mid, "plan": plan, "policy_start": start})
        return mid

    seq = {"n": 0}

    def claim(case, mid, date, codes, provider=None, **kw):
        seq["n"] += 1
        cid = f"B{case}-{seq['n']:03d}"
        b.make(cid, mid, date, codes, provider or f"BNDP{case}", **kw)
        return cid

    def case(cid, desc, target, decision, rule=""):
        cases.append({"case_id": cid, "description": desc, "claim_id": target,
                      "expected_decision": decision, "expected_rule": rule})

    def weekly(start, k):
        return [pd.Timestamp(start) + pd.Timedelta(weeks=i) for i in range(k)]

    members_rows = []

    # --- physio frequency limits (COV-2.1), policy start 2008-03-15
    for cid, plan, k, dec, rule, desc in [
        ("01", "SILVER", 6, "paid", "", "Silver physio: 6th session in policy year (limit 6)"),
        ("02", "SILVER", 7, "denied", "COV-2.1", "Silver physio: 7th session in policy year"),
        ("05", "GOLD", 12, "paid", "", "Gold physio: 12th session (limit 12)"),
        ("06", "GOLD", 13, "denied", "COV-2.1", "Gold physio: 13th session"),
    ]:
        m = member(cid, plan)
        ids = [claim(cid, m, d, {"97110": 1}) for d in weekly("2008-04-01", k)]
        case(cid, desc, ids[-1], dec, rule)

    for cid, last, dec, rule, desc in [
        ("03", "2009-03-15", "paid", "", "Silver physio: 7th session on the policy anniversary (new year)"),
        ("04", "2009-03-14", "denied", "COV-2.1", "Silver physio: 7th session the day before the anniversary"),
    ]:
        m = member(cid, "SILVER")
        for d in weekly("2009-01-20", 6):
            claim(cid, m, d, {"97110": 1})
        case(cid, desc, claim(cid, m, last, {"97110": 1}), dec, rule)

    # --- waiting period (COV-3.1): Basic, 90 days from 2008-03-15
    m = member("07", "BASIC")
    case("07", "Basic physio on day 89 of the waiting period",
         claim("07", m, PS + pd.Timedelta(days=89), {"97110": 1}), "denied", "COV-3.1")
    m = member("08", "BASIC")
    case("08", "Basic physio on day 90 (waiting period over)",
         claim("08", m, PS + pd.Timedelta(days=90), {"97110": 1}), "paid")
    m = member("33", "BASIC")
    ids = [claim("33", m, d, {"97110": 1}) for d in weekly("2008-07-01", 5)]
    case("33", "Basic physio: 5th session after waiting period (limit 4)", ids[-1], "denied", "COV-2.1")

    # --- dialysis drugs (COV-2.2)
    m = member("09", esrd=True)
    case("09", "Dialysis drug, member has ESRD", claim("09", m, "2009-02-01", {"Q4081": 1}), "paid")
    m = member("10", cc_chronic_kidney=True)
    case("10", "Dialysis drug, no ESRD but chronic kidney disease",
         claim("10", m, "2009-02-01", {"Q4081": 1}), "pended", "COV-2.2")
    m = member("11")
    case("11", "Dialysis drug, no ESRD, no kidney disease",
         claim("11", m, "2009-02-01", {"Q4081": 1}), "denied", "COV-2.2")

    # --- metabolic panel (COV-2.3) and HbA1c (COV-2.4)
    months = [pd.Timestamp("2008-04-01") + pd.DateOffset(months=i) for i in range(8)]
    for cid, k, diab, dec, rule, desc in [
        ("12", 4, False, "paid", "", "80053: 4th in policy year (limit 4)"),
        ("13", 5, False, "denied", "COV-2.3", "80053: 5th in policy year"),
        ("14", 5, True, "paid", "", "80053: 5th for a diabetic member (limit 6)"),
        ("15", 7, True, "denied", "COV-2.3", "80053: 7th for a diabetic member"),
    ]:
        m = member(cid, cc_diabetes=diab)
        ids = [claim(cid, m, d, {"80053": 1}) for d in months[:k]]
        case(cid, desc, ids[-1], dec, rule)
    for cid, diab, dec, rule, desc in [
        ("34", False, "denied", "COV-2.4", "HbA1c: 2nd in policy year, not diabetic (limit 1)"),
        ("35", True, "paid", "", "HbA1c: 2nd in policy year, diabetic (limit 4)"),
    ]:
        m = member(cid, cc_diabetes=diab)
        claim(cid, m, "2008-05-01", {"83036": 1})
        case(cid, desc, claim(cid, m, "2008-09-01", {"83036": 1}), dec, rule)

    # --- eligibility (COV-1.1)
    m = member("16", months_part_b={2009: 0})
    case("16", "No Part B cover in the service year",
         claim("16", m, "2009-04-01", {"99213": 1}), "denied", "COV-1.1")
    m = member("17")
    case("17", "Control: eligible member, office visit",
         claim("17", m, "2009-04-01", {"99213": 1}), "paid")

    # --- outpatient during inpatient stay (EDIT-01)
    for cid, d, dec, rule, desc in [
        ("18", "2009-05-05", "denied", "EDIT-01", "Outpatient visit during an inpatient stay"),
        ("19", "2009-05-10", "paid", "", "Outpatient visit on the discharge date"),
    ]:
        m = member(cid)
        ip = claim(cid, m, "2009-05-01", {}, claim_type="inpatient", admit="2009-05-01",
                   discharge="2009-05-10", amount=8000.0)
        t["stays"] = pd.concat([t["stays"], pd.DataFrame([{
            "claim_id": ip, "member_id": m, "provider_id": f"BNDP{cid}",
            "admit_dt": pd.Timestamp("2009-05-01"), "discharge_dt": pd.Timestamp("2009-05-10"),
            "drg_cd": "OTH", "util_day_cnt": 9, "pmt_amt_total": 8000.0, "length_of_stay": 9}])],
            ignore_index=True)
        case(cid, desc, claim(cid, m, d, {"99213": 1}, provider=f"BNDQ{cid}"), dec, rule)

    # --- unit caps, pair edits, companions
    m = member("20")
    case("20", "36415 with 3 units (cap 3)", claim("20", m, "2009-03-01", {"36415": 3, "85025": 1}), "paid")
    m = member("21")
    case("21", "36415 with 4 units (cap 3), plus another code",
         claim("21", m, "2009-03-01", {"36415": 4, "85025": 1}), "partial", "EDIT-10")
    m = member("22")
    case("22", "80053 and 80048 on the same claim",
         claim("22", m, "2009-03-01", {"80053": 1, "80048": 1}), "partial", "EDIT-20")
    m = member("32")
    case("32", "85025 and 85027 on the same claim",
         claim("32", m, "2009-03-01", {"85025": 1, "85027": 1}), "partial", "EDIT-21")
    m = member("23")
    case("23", "Sedation drug without a procedure",
         claim("23", m, "2009-03-01", {"J2250": 1, "99213": 1}), "pended", "EDIT-30")
    m = member("24")
    case("24", "Sedation drug with a colonoscopy",
         claim("24", m, "2009-03-01", {"J2250": 1, "45378": 1}), "paid")
    m = member("25")
    case("25", "Contrast with a CT scan", claim("25", m, "2009-03-01", {"Q9967": 1, "74177": 1}), "paid")
    m = member("26")
    case("26", "Contrast without imaging",
         claim("26", m, "2009-03-01", {"Q9967": 1, "99213": 1}), "pended", "EDIT-31")

    # --- death (COV-1.2)
    for cid, d, dec, rule, desc in [
        ("27", "2009-06-15", "denied", "COV-1.2", "Service 14 days after death"),
        ("28", "2009-06-01", "paid", "", "Service on the date of death"),
    ]:
        m = member(cid, death_dt={2009: pd.Timestamp("2009-06-01"), 2010: pd.Timestamp("2009-06-01")})
        case(cid, desc, claim(cid, m, d, {"99213": 1}), dec, rule)

    # --- duplicates (EDIT-03, EDIT-04)
    m = member("29")
    claim("29", m, "2009-04-01", {"99213": 1}, provider="BNDP29")
    case("29", "Exact duplicate of an earlier claim",
         claim("29", m, "2009-04-01", {"99213": 1}, provider="BNDP29"), "denied", "EDIT-03")
    m = member("30")
    claim("30", m, "2009-04-01", {"99213": 1}, provider="BNDP30A")
    case("30", "Same code same day on a second claim (different provider)",
         claim("30", m, "2009-04-01", {"99213": 1, "85025": 1}, provider="BNDP30B"),
         "partial", "EDIT-04")

    # --- data quality (DQ-01)
    m = member("31")
    case("31", "Negative payment amount",
         claim("31", m, "2009-04-01", {"99213": 1}, amount=-50.0), "rejected", "DQ-01")

    # --- excluded provider (COV-1.3)
    exclusions.append({"provider_id": "BNDXPROV", "excl_dt": pd.Timestamp("2009-01-01"),
                       "excl_type": "1128a1", "specialty": "FACILITY", "reinstated_dt": pd.NaT})
    m = member("36")
    case("36", "Excluded provider, service after exclusion date",
         claim("36", m, "2009-02-01", {"99213": 1}, provider="BNDXPROV"), "denied", "COV-1.3")
    m = member("37")
    case("37", "Excluded provider, service the day before exclusion",
         claim("37", m, "2008-12-31", {"99213": 1}, provider="BNDXPROV"), "paid")

    t["members"] = pd.concat([members, pd.DataFrame(members_rows).reindex(columns=members.columns)],
                             ignore_index=True)
    return pd.DataFrame(cases).sort_values("case_id")


# ====================================================================== main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clean", required=True, help="clean sample folder, e.g. clean/sample2_full")
    ap.add_argument("--out", required=True, help="seeded output folder")
    ap.add_argument("--keys", required=True, help="answer-key folder (keep away from the app)")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--scale", type=float, default=1.0, help="multiplies anomaly counts")
    ap.add_argument("--hard", action="store_true",
                    help="also seed subtle variants and novel patterns (no detector)")
    a = ap.parse_args()

    rng = np.random.default_rng(a.seed)
    t = {n: pd.read_parquet(os.path.join(a.clean, f"{n}.parquet")) for n in TABLES}
    plans_cfg, _ = engine.load_config()
    plans = engine.assign_plans(t["members"]["member_id"], plans_cfg)

    b = Builder(list(t["claims"].columns), list(t["claim_lines"].columns))
    keys, report = [], {}
    seed_anomalies(t, b, rng, a.scale, keys, report)
    used = report.pop("_used_providers", set()) | set(t["exclusions"]["provider_id"])
    if a.hard:
        seed_hard(t, b, rng, a.scale, keys, report, used)

    plans_rows, excl_rows = [], t["exclusions"].to_dict("records")
    boundary = seed_boundary(t, b, plans_rows, excl_rows)
    t["exclusions"] = pd.DataFrame(excl_rows)
    plans = pd.concat([plans, pd.DataFrame(plans_rows)], ignore_index=True)

    new_c, new_l = b.frames()
    claims = pd.concat([t["claims"], new_c.astype(t["claims"].dtypes.to_dict(), errors="ignore")],
                       ignore_index=True)
    lines = pd.concat([t["claim_lines"], new_l], ignore_index=True)
    for col in ["from_dt", "thru_dt", "admit_dt", "discharge_dt"]:
        claims[col] = pd.to_datetime(claims[col])
    lines["service_dt"] = pd.to_datetime(lines["service_dt"])
    lines["repeat_count"] = lines["repeat_count"].astype(int)
    claims = claims.drop(columns=[c for c in claims.columns if c.startswith("flag_")])
    claims = add_flags(claims, t["stays"], t["members"])
    t["claims"], t["claim_lines"] = claims, lines

    # excluded-provider answer key: every final claim (any type, incl. clones)
    # at an excluded provider on/after the exclusion date; boundary cases excluded
    ex = t["exclusions"][~t["exclusions"]["provider_id"].str.startswith("BND")]
    hit = claims.merge(ex[["provider_id", "excl_dt"]], on="provider_id")
    hit = hit[hit["from_dt"] >= hit["excl_dt"]]
    keys.extend((cid, "excluded_provider", "COV-1.3") for cid in hit["claim_id"])
    report["excluded_provider_claims"] = int(len(hit))

    os.makedirs(a.out, exist_ok=True)
    for n in TABLES:
        t[n].to_parquet(os.path.join(a.out, f"{n}.parquet"), index=False)
    plans.to_parquet(os.path.join(a.out, "member_plans.parquet"), index=False)
    t["exclusions"].to_parquet(os.path.join(a.out, "exclusions.parquet"), index=False)

    os.makedirs(a.keys, exist_ok=True)
    kdf = pd.DataFrame(keys, columns=["claim_id", "pattern", "expected_detector"])
    kdf["difficulty"] = kdf["pattern"].map(lambda p: PATTERNS[p][0])
    kdf["fwa"] = kdf["pattern"].map(lambda p: PATTERNS[p][1])
    kdf.to_csv(os.path.join(a.keys, "anomalies.csv"), index=False)
    boundary.to_csv(os.path.join(a.keys, "boundary_expected.csv"), index=False)
    src = os.path.join(HERE, "answer_keys", "expected_conflicts.yaml")
    if os.path.exists(src):
        shutil.copy(src, a.keys)
    report.update({"seed": a.seed, "scale": a.scale, "hard": a.hard, "boundary_cases": int(len(boundary)),
                   "new_claims": int(len(new_c)), "new_lines": int(len(new_l)),
                   "claims_total": int(len(claims)), "members_with_plans": int(len(plans))})
    with open(os.path.join(a.keys, "seed_report.json"), "w") as fh:
        json.dump(report, fh, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
