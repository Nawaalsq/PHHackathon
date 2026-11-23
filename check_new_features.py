"""Headless replay of the new app features: appeal guidance, saved-scenario compare,
review-workflow counts (data_editor itself can't run headless)."""
import sys

sys.path.insert(0, "rd_engine")
import pandas as pd  # noqa: E402

# appeal_guidance / APPEAL_HINTS live in app.py; importing app.py runs Streamlit,
# so re-declare the same logic here from the YAML-driven data instead.

DATA = "clean/sample1_dev_seeded"
d = pd.read_parquet("results/sample1_dev_seeded/decisions.parquet")

# --- scenario compare logic (mirrors the What-if save/compare block)
base = d[["claim_id", "member_id", "decision", "plan_paid_amt"]]
scen = base.copy()
flip = scen.sample(frac=0.05, random_state=1).index
scen.loc[flip, "decision"] = "denied"
m2 = base.merge(scen[["claim_id", "decision", "plan_paid_amt"]], on="claim_id",
                suffixes=("_base", "_scenario"))
mv2 = m2[m2["decision_base"] != m2["decision_scenario"]]
dl2 = m2["plan_paid_amt_scenario"].sum() - m2["plan_paid_amt_base"].sum()
print("scenario compare: claims that change", len(mv2),
      "| cost delta", round(dl2, 2),
      "| members affected", m2.loc[m2["claim_id"].isin(mv2["claim_id"]), "member_id"].nunique())

# --- review workflow counts (mirrors the FWA queue block)
qd = d[d["fwa_score"] > 0].sort_values(["fwa_score", "allowed_amt"], ascending=False).copy()
reviewed = {}
qd["review"] = qd["claim_id"].map(reviewed).fillna("pending")
print("review workflow: queue", len(qd), "| pending", int((qd["review"] == "pending").sum()))
done = qd[qd["review"] != "pending"]
print("worked rows:", len(done))

# --- appeal guidance coverage: which primary rules appear on denied claims
denied = d[d["decision"].isin(["denied", "rejected"])]
print("\ndenied/rejected claims:", len(denied))
print("primary rules on them:")
print(denied["primary_rule"].value_counts().head(12).to_string())
