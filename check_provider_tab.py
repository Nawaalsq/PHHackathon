"""Replay the Provider triage tab's data logic (sample2_seeded) outside Streamlit."""
import sys

sys.path.insert(0, "rd_engine")
import engine  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

DATA = "clean/sample2_seeded"

# base run = precomputed results (same as the app loads)
d = pd.read_parquet("results/sample2_seeded/decisions.parquet")
t = engine.load_tables(DATA)
lines_t = t["claim_lines"]

# --- provider groupby + defaults (mirrors the tab)
prov = (d.groupby("provider_id")
          .agg(claims=("claim_id", "size"), members=("member_id", "nunique"),
               flagged=("fwa_score", lambda s: int((s > 0).sum())),
               billed=("allowed_amt", "sum"))
          .sort_values(["flagged", "billed"], ascending=False))
top_provs = prov.head(300).reset_index()
top_provs["label"] = [f"{r['provider_id']} · {r['flagged']} flagged · {r['claims']:,} claims"
                      for _, r in top_provs.iterrows()]
pick_p = top_provs["provider_id"].iloc[0]
prow = d[d["provider_id"] == pick_p]
pcount = prov.loc[pick_p]
print("default provider:", pick_p,
      "| claims", int(pcount["claims"]), "| members", int(pcount["members"]),
      "| flagged", int(pcount["flagged"]), "| billed", round(float(pcount["billed"])))
print("label:", top_provs["label"].iloc[0])
print("plan pays:", round(float(prow["plan_paid_amt"].sum()), 2))

# --- peer rates (mirrors peer_rates())
g = (lines_t.groupby(["code", "provider_id"])
            .agg(count=("claim_id", "size"), patients=("member_id", "nunique")).reset_index())
g["rate"] = g["count"] / g["patients"]
med = g[g["patients"] >= 5].groupby("code")["rate"].median()
g["peer_median"] = g["code"].map(med)
g["ratio"] = g["rate"] / g["peer_median"]
pcodes = g[g["provider_id"] == pick_p].sort_values("count", ascending=False).head(10).copy()
pcodes["flag"] = np.where(pcodes["ratio"] >= 4, "⚠️", "")
print("\ntop codes vs peers:")
print(pcodes[["code", "count", "patients", "rate", "peer_median", "ratio", "flag"]].to_string(index=False))

# --- office-visit mix + flags by month
ov = lines_t[(lines_t["provider_id"] == pick_p) &
             lines_t["code"].isin(["99212", "99213", "99214", "99215"])]
if len(ov):
    mix = ov.groupby([ov["service_dt"].dt.year, "code"]).size().unstack(fill_value=0)
    mix.index.name = "year"
    print("\noffice-visit mix by year:")
    print(mix)
else:
    print("\nno office-visit codes for this provider")

fl = prow[prow["fwa_score"] > 0].copy()
fl["month"] = pd.to_datetime(fl["from_dt"]).dt.to_period("M").astype(str)
print("\nflags by month:")
print(fl.groupby("month").size().to_string())

fc = prow[prow["fwa_score"] > 0].sort_values("fwa_score", ascending=False)
print("\nflagged claims rows:", len(fc))
print("OK - provider tab logic runs clean")
