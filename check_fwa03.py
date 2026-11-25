"""Debug: did FWA-03 hit the seeded near-duplicate claims on sample1_dev_seeded?"""
import sys

sys.path.insert(0, "rd_engine")
import engine  # noqa: E402
import pandas as pd  # noqa: E402

res = engine.run("clean/sample1_dev_seeded", verbose=False)
hits = res["hits"]
print("FWA-03 hits total:", int((hits["rule_id"] == "FWA-03").sum()))

keys = pd.read_csv("answer_keys/sample1_dev_seeded/anomalies.csv", dtype=str)
nd = keys[keys["pattern"].isin(["near_duplicate_amount", "near_duplicate_next_day"])]
fwa03_ids = set(hits.loc[hits["rule_id"] == "FWA-03", "claim_id"])
nd = nd.assign(fwa03=nd["claim_id"].isin(fwa03_ids))
print(nd.groupby(["pattern", "fwa03"]).size())

# show a couple of next-day seed claims with their siblings
sample = nd[(nd["pattern"] == "near_duplicate_next_day") & ~nd["fwa03"]].head(2)
d = res["decisions"].set_index("claim_id")
for cid in sample["claim_id"]:
    row = d.loc[cid]
    sib = d[(d["member_id"] == row["member_id"]) & (d["provider_id"] == row["provider_id"])]
    print(cid, "amount", row["pmt_amt_total"], "date", row["from_dt"])
    print(sib[["from_dt", "pmt_amt_total", "claim_type"]].head(5))
