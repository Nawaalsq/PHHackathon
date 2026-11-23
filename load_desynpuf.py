"""
load_desynpuf.py - Load one CMS DE-SynPUF sample into clean tables for the
Claim Decision Engine (RD02 / RD03 / RD04).

Usage
-----
    python load_desynpuf.py --data-dir ./raw --sample 1 --out-dir ./clean
    python load_desynpuf.py --data-dir ./raw --sample 2 --out-dir ./clean --dev-members 0

Input files (looked up in --data-dir by name; a prefix before "DE1_0" is fine):
    DE1_0_2008_Beneficiary_Summary_File_Sample_<n>.csv   (required)
    DE1_0_2009_Beneficiary_Summary_File_Sample_<n>.csv   (optional)
    DE1_0_2010_Beneficiary_Summary_File_Sample_<n>.csv   (optional)
    DE1_0_2008_to_2010_Inpatient_Claims_Sample_<n>.csv   (required)
    DE1_0_2008_to_2010_Outpatient_Claims_Sample_<n>.csv  (required)

Output (Parquet) in <out-dir>/sample<n>_full/ and, unless --dev-members 0,
a member subset in <out-dir>/sample<n>_dev/:
    members      one row per member per year
    claims       one row per claim (inpatient + outpatient, claim_type column)
    claim_lines  one row per outpatient claim x HCPCS code, with repeat_count
    stays        one row per inpatient stay
    diagnoses    one row per claim x ICD-9 diagnosis
    procedures   one row per inpatient claim x ICD-9 procedure
    load_report.json  row counts, links, date ranges, data-quality flags

What it does NOT do: assign plans, write rules, or inject anomalies. That is
the seeding step, which runs on these tables.

Assumptions (documented so the team can change them):
  * SEGMENT = 2 rows are continuation rows of the claim with the same CLM_ID.
    Their codes and diagnoses are added to the claim; their payment amounts
    are added to pmt_amt_total (pmt_amt_seg1 keeps the first segment alone).
    Continuation rows whose first segment is missing are kept as claims and
    flagged orphan_segment = True.
  * Outpatient "units" are not in the data. repeat_count = how many times the
    same code appears on one claim, used as a stand-in for units.
  * Rows are never deleted for data-quality reasons; they are flagged.
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

CHRONIC = {
    "SP_ALZHDMTA": "cc_alzheimers", "SP_CHF": "cc_heart_failure",
    "SP_CHRNKIDN": "cc_chronic_kidney", "SP_CNCR": "cc_cancer",
    "SP_COPD": "cc_copd", "SP_DEPRESSN": "cc_depression",
    "SP_DIABETES": "cc_diabetes", "SP_ISCHMCHT": "cc_ischemic_heart",
    "SP_OSTEOPRS": "cc_osteoporosis", "SP_RA_OA": "cc_ra_oa",
    "SP_STRKETIA": "cc_stroke_tia",
}
MONEY_BEN = ["MEDREIMB_IP", "BENRES_IP", "PPPYMT_IP", "MEDREIMB_OP", "BENRES_OP",
             "PPPYMT_OP", "MEDREIMB_CAR", "BENRES_CAR", "PPPYMT_CAR"]


# ---------------------------------------------------------------- helpers
def find_file(data_dir, pattern, required=True):
    hits = sorted(glob.glob(os.path.join(data_dir, f"*{pattern}*.csv")))
    if not hits:
        if required:
            sys.exit(f"Missing required file matching '{pattern}' in {data_dir}")
        return None
    return hits[0]


def to_date(s):
    return pd.to_datetime(s, format="%Y%m%d", errors="coerce")


def to_num(s):
    return pd.to_numeric(s, errors="coerce")


def code_type(codes):
    """Rough HCPCS split: 5 digits (or 4 digits + letter) = CPT-style (AMA terms),
    letter + 4 digits = HCPCS Level II (public)."""
    out = np.where(codes.str.match(r"^[A-V]\d{4}$"), "hcpcs_level2",
          np.where(codes.str.match(r"^\d{4}[0-9A-Z]$"), "cpt_style", "other"))
    return pd.Series(out, index=codes.index)


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- members
def load_members(data_dir, n):
    frames, years_found = [], []
    for yr in (2008, 2009, 2010):
        f = find_file(data_dir, f"DE1_0_{yr}_Beneficiary_Summary_File_Sample_{n}",
                      required=(yr == 2008))
        if f is None:
            continue
        log(f"members {yr}: {os.path.basename(f)}")
        d = pd.read_csv(f, dtype=str)
        d["year"] = yr
        frames.append(d)
        years_found.append(yr)
    b = pd.concat(frames, ignore_index=True)
    m = pd.DataFrame({
        "member_id": b["DESYNPUF_ID"],
        "year": b["year"].astype("int16"),
        "birth_dt": to_date(b["BENE_BIRTH_DT"]),
        "death_dt": to_date(b["BENE_DEATH_DT"]),
        "sex": b["BENE_SEX_IDENT_CD"].map({"1": "M", "2": "F"}),
        "race_cd": b["BENE_RACE_CD"],
        "esrd": b["BENE_ESRD_IND"].eq("Y"),
        "state_cd": b["SP_STATE_CODE"],
        "county_cd": b["BENE_COUNTY_CD"],
        "months_part_a": to_num(b["BENE_HI_CVRAGE_TOT_MONS"]).astype("Int16"),
        "months_part_b": to_num(b["BENE_SMI_CVRAGE_TOT_MONS"]).astype("Int16"),
        "months_hmo": to_num(b["BENE_HMO_CVRAGE_TOT_MONS"]).astype("Int16"),
        "months_part_d": to_num(b["PLAN_CVRG_MOS_NUM"]).astype("Int16"),
    })
    for src, dst in CHRONIC.items():
        m[dst] = b[src].eq("1")          # 1 = yes, 2 = no
    for c in MONEY_BEN:
        m[c.lower()] = to_num(b[c])
    m["age_at_year_end"] = (m["year"] - m["birth_dt"].dt.year).astype("Int16")
    m["flag_negative_annual_amount"] = (m[[c.lower() for c in MONEY_BEN]] < 0).any(axis=1)
    return m, years_found


# ---------------------------------------------------------------- claims
def load_claims(path, claim_type):
    log(f"{claim_type} claims: {os.path.basename(path)}")
    d = pd.read_csv(path, dtype=str)
    d["SEGMENT"] = d["SEGMENT"].fillna("1")
    seg1 = d[d["SEGMENT"] == "1"]
    cont = d[d["SEGMENT"] != "1"]

    # header from segment 1; orphans (continuation row without segment 1) kept
    orphan_ids = set(cont["CLM_ID"]) - set(seg1["CLM_ID"])
    head = pd.concat([seg1, cont[cont["CLM_ID"].isin(orphan_ids)]
                      .drop_duplicates("CLM_ID")], ignore_index=True)

    pay_total = to_num(d["CLM_PMT_AMT"]).groupby(d["CLM_ID"]).sum()
    n_seg = d.groupby("CLM_ID").size()

    c = pd.DataFrame({
        "claim_id": head["CLM_ID"],
        "member_id": head["DESYNPUF_ID"],
        "claim_type": claim_type,
        "from_dt": to_date(head["CLM_FROM_DT"]),
        "thru_dt": to_date(head["CLM_THRU_DT"]),
        "provider_id": head["PRVDR_NUM"],
        "at_physn_npi": head["AT_PHYSN_NPI"],
        "op_physn_npi": head["OP_PHYSN_NPI"],
        "ot_physn_npi": head["OT_PHYSN_NPI"],
        "pmt_amt_seg1": to_num(head["CLM_PMT_AMT"]),
        "primary_payer_paid_amt": to_num(head["NCH_PRMRY_PYR_CLM_PD_AMT"]),
        "admitting_dx": head["ADMTNG_ICD9_DGNS_CD"],
    })
    c["pmt_amt_total"] = c["claim_id"].map(pay_total)
    c["n_segments"] = c["claim_id"].map(n_seg).astype("int16")
    c["orphan_segment"] = c["claim_id"].isin(orphan_ids)

    if claim_type == "inpatient":
        c["admit_dt"] = to_date(head["CLM_ADMSN_DT"])
        c["discharge_dt"] = to_date(head["NCH_BENE_DSCHRG_DT"])
        c["drg_cd"] = head["CLM_DRG_CD"]
        c["util_day_cnt"] = to_num(head["CLM_UTLZTN_DAY_CNT"])
        c["ip_deductible_amt"] = to_num(head["NCH_BENE_IP_DDCTBL_AMT"])
        c["pta_coinsurance_amt"] = to_num(head["NCH_BENE_PTA_COINSRNC_LBLTY_AM"])
        # orphans have no CLM_FROM_DT; fall back to admission / discharge dates
        c["from_dt"] = c["from_dt"].fillna(c["admit_dt"])
        c["thru_dt"] = c["thru_dt"].fillna(c["discharge_dt"])
    else:
        c["ptb_deductible_amt"] = to_num(head["NCH_BENE_PTB_DDCTBL_AMT"])
        c["ptb_coinsurance_amt"] = to_num(head["NCH_BENE_PTB_COINSRNC_AMT"])

    c["service_year"] = c["thru_dt"].dt.year.astype("Int16")

    # long tables use all segments
    dx_cols = [x for x in d.columns if x.startswith("ICD9_DGNS_CD_")]
    dx = d.melt(id_vars=["CLM_ID", "DESYNPUF_ID"], value_vars=dx_cols,
                var_name="pos", value_name="dx_code").dropna(subset=["dx_code"])
    dx["position"] = dx["pos"].str.extract(r"(\d+)$")[0].astype("int16")
    dx = (dx.rename(columns={"CLM_ID": "claim_id", "DESYNPUF_ID": "member_id"})
            .drop(columns="pos").drop_duplicates(["claim_id", "dx_code"]))
    dx["claim_type"] = claim_type

    px_cols = [x for x in d.columns if x.startswith("ICD9_PRCDR_CD_")]
    px = d.melt(id_vars=["CLM_ID", "DESYNPUF_ID"], value_vars=px_cols,
                value_name="proc_code").dropna(subset=["proc_code"])
    px = (px.rename(columns={"CLM_ID": "claim_id", "DESYNPUF_ID": "member_id"})
            [["claim_id", "member_id", "proc_code"]].drop_duplicates())
    px["claim_type"] = claim_type

    lines = None
    hc_cols = [x for x in d.columns if x.startswith("HCPCS_CD_")]
    if hc_cols:
        h = d.melt(id_vars=["CLM_ID", "DESYNPUF_ID"], value_vars=hc_cols,
                   var_name="pos", value_name="code").dropna(subset=["code"])
        if len(h):
            h["position"] = h["pos"].str.extract(r"(\d+)$")[0].astype("int16")
            lines = (h.groupby(["CLM_ID", "DESYNPUF_ID", "code"], as_index=False)
                       .agg(repeat_count=("position", "size"),
                            first_position=("position", "min")))
            lines = lines.rename(columns={"CLM_ID": "claim_id",
                                          "DESYNPUF_ID": "member_id"})
            lines["code_type"] = code_type(lines["code"])
            lines["is_drug_code"] = lines["code"].str.match(r"^[JQ]\d{4}$")
            dates = c.set_index("claim_id")[["from_dt", "provider_id"]]
            lines = lines.join(dates, on="claim_id").rename(
                columns={"from_dt": "service_dt"})
            lines["claim_type"] = claim_type

    return c, dx, px, lines, {"rows_in": len(d), "continuation_rows": len(cont),
                              "orphan_continuation_claims": len(orphan_ids)}


# ---------------------------------------------------------------- flags
def add_flags(claims, stays, members):
    c = claims
    c["flag_negative_payment"] = c["pmt_amt_total"] < 0
    c["flag_missing_dates"] = c["from_dt"].isna() | c["thru_dt"].isna()
    c["flag_thru_before_from"] = c["thru_dt"] < c["from_dt"]
    c["flag_no_member_link"] = ~c["member_id"].isin(members["member_id"])

    death = (members.dropna(subset=["death_dt"])
                    .groupby("member_id")["death_dt"].min())
    c["flag_after_death"] = c["from_dt"] > c["member_id"].map(death)

    ip = c["claim_type"] == "inpatient"
    los = (c.loc[ip, "discharge_dt"] - c.loc[ip, "admit_dt"]).dt.days
    c.loc[ip, "length_of_stay"] = los
    c.loc[ip, "flag_util_days_mismatch"] = (c.loc[ip, "util_day_cnt"] - los).abs() > 1
    c["flag_util_days_mismatch"] = c["flag_util_days_mismatch"].fillna(False).astype(bool)

    # exact duplicates: same member, dates, provider, amount, type
    key = ["member_id", "claim_type", "from_dt", "thru_dt", "provider_id", "pmt_amt_total"]
    c["flag_exact_duplicate"] = c.duplicated(key, keep="first")

    # overlapping inpatient stays (stay starts before the previous one ends)
    s = stays.sort_values(["member_id", "admit_dt"])
    prev_end = s.groupby("member_id")["discharge_dt"].shift()
    overlap_ids = set(s.loc[s["admit_dt"] < prev_end, "claim_id"])
    c["flag_overlapping_stay"] = c["claim_id"].isin(overlap_ids)

    # outpatient claim dated strictly inside an inpatient stay
    op = c.loc[c["claim_type"] == "outpatient", ["claim_id", "member_id", "from_dt"]]
    j = op.merge(stays[["member_id", "admit_dt", "discharge_dt"]], on="member_id")
    inside = set(j.loc[(j["from_dt"] > j["admit_dt"]) &
                       (j["from_dt"] < j["discharge_dt"]), "claim_id"])
    c["flag_op_during_ip_stay"] = c["claim_id"].isin(inside)
    return c


# ---------------------------------------------------------------- main
def write_tables(tables, folder):
    os.makedirs(folder, exist_ok=True)
    for name, df in tables.items():
        df.to_parquet(os.path.join(folder, f"{name}.parquet"), index=False)
    log(f"wrote {len(tables)} tables to {folder}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--sample", type=int, required=True)
    ap.add_argument("--out-dir", default="./clean")
    ap.add_argument("--dev-members", type=int, default=20000,
                    help="members in the dev subset (0 = skip)")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    n = a.sample

    members, years = load_members(a.data_dir, n)
    ip_c, ip_dx, ip_px, _, ip_info = load_claims(
        find_file(a.data_dir, f"DE1_0_2008_to_2010_Inpatient_Claims_Sample_{n}"), "inpatient")
    op_c, op_dx, op_px, op_lines, op_info = load_claims(
        find_file(a.data_dir, f"DE1_0_2008_to_2010_Outpatient_Claims_Sample_{n}"), "outpatient")

    claims = pd.concat([ip_c, op_c], ignore_index=True)
    stays = (ip_c[["claim_id", "member_id", "provider_id", "admit_dt", "discharge_dt",
                   "drg_cd", "util_day_cnt", "pmt_amt_total"]]
             .dropna(subset=["admit_dt", "discharge_dt"]).copy())
    stays["length_of_stay"] = (stays["discharge_dt"] - stays["admit_dt"]).dt.days
    claims = add_flags(claims, stays, members)

    tables = {
        "members": members,
        "claims": claims,
        "claim_lines": op_lines,
        "stays": stays,
        "diagnoses": pd.concat([ip_dx, op_dx], ignore_index=True),
        "procedures": pd.concat([ip_px, op_px], ignore_index=True),
    }

    flag_cols = [x for x in claims.columns if x.startswith("flag_")]
    report = {
        "sample": n,
        "member_years_loaded": years,
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "rows": {k: int(len(v)) for k, v in tables.items()},
        "members_distinct": int(members["member_id"].nunique()),
        "claims_by_type": claims["claim_type"].value_counts().to_dict(),
        "inpatient_raw": ip_info, "outpatient_raw": op_info,
        "claims_linked_to_member_pct": round(
            100 * (1 - claims["flag_no_member_link"].mean()), 2),
        "service_date_range": [str(claims["from_dt"].min().date()),
                               str(claims["thru_dt"].max().date())],
        "claims_by_service_year": {str(k): int(v) for k, v in
                                   claims["service_year"].value_counts().sort_index().items()},
        "flags": {f: int(claims[f].sum()) for f in flag_cols},
        "claim_lines": {
            "distinct_codes": int(op_lines["code"].nunique()),
            "code_type": op_lines["code_type"].value_counts().to_dict(),
            "drug_code_lines": int(op_lines["is_drug_code"].sum()),
            "lines_with_repeat_count_gt1": int((op_lines["repeat_count"] > 1).sum()),
        },
        "members_flags": {
            "deaths": int(members["death_dt"].notna().sum()),
            "esrd_member_years": int(members["esrd"].sum()),
            "negative_annual_amount": int(members["flag_negative_annual_amount"].sum()),
        },
    }
    if len(years) < 3:
        report["warning"] = (f"Only member years {years} loaded: eligibility checks for "
                             "other years will have no member record.")

    out_full = os.path.join(a.out_dir, f"sample{n}_full")
    write_tables(tables, out_full)
    with open(os.path.join(out_full, "load_report.json"), "w") as fh:
        json.dump(report, fh, indent=2, default=str)

    if a.dev_members:
        ids = members["member_id"].drop_duplicates()
        keep = set(ids.sample(min(a.dev_members, len(ids)), random_state=a.seed))
        dev = {k: v[v["member_id"].isin(keep)] for k, v in tables.items()}
        write_tables(dev, os.path.join(a.out_dir, f"sample{n}_dev"))
        report["dev_subset"] = {k: int(len(v)) for k, v in dev.items()}
        with open(os.path.join(out_full, "load_report.json"), "w") as fh:
            json.dump(report, fh, indent=2, default=str)

    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
