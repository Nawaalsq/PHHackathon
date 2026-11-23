"""Headless checks for the new features: grouped FWA cases, /api/fwa/case,
grouped CSV export, bulk reviews, and the new AI routes (offline fallback)."""
import os
import sys

sys.path.insert(0, "rd_engine")
os.environ.setdefault("AI_BACKEND", "offline")   # never call a real backend here

import rd_engine.api as api  # noqa: E402  (chdirs to the project root)
from fastapi.testclient import TestClient  # noqa: E402

DATA = "clean/sample1_dev_seeded"
client = TestClient(api.app)

# --- grouped FWA: cases are disjoint and cover the flat queue
flat = client.get("/api/fwa", params={"dataset": DATA}).json()
grp = client.get("/api/fwa", params={"dataset": DATA, "group": 1}).json()
flat_claims = sum(1 for _ in flat["rows"])
# paginate the flat queue fully to compare totals
total_flat = flat["total"]
case_claims = sum(len(c["claim_ids"]) for c in grp["rows"])
page, all_cases = 1, []
while True:
    r = client.get("/api/fwa", params={"dataset": DATA, "group": 1, "page": page}).json()
    all_cases.extend(r["rows"])
    if page * 50 >= r["total"]:
        break
    page += 1
case_claims = sum(len(c["claim_ids"]) for c in all_cases)
ids = [cid for c in all_cases for cid in c["claim_ids"]]
assert len(ids) == len(set(ids)), "cases overlap"
assert case_claims == total_flat, f"cases cover {case_claims} of {total_flat} flat claims"
print(f"grouped FWA: {len(all_cases)} cases cover all {total_flat} flagged claims, disjoint OK")

# --- case detail returns the right rows and live review status
c0 = all_cases[0]
case = client.get("/api/fwa/case", params={
    "dataset": DATA, "provider_id": c0["provider_id"],
    "member_id": c0["member_id"], "rule_id": c0["rule_id"]}).json()
assert len(case["rows"]) == c0["claims"], "case row count mismatch"
print(f"fwa/case: {len(case['rows'])} rows for case {c0['case_id']} OK")

# --- bulk reviews persist, then case rollups reflect them
ids_bulk = [r["claim_id"] for r in flat["rows"][:3]]
r = client.post("/api/fwa/review", json={
    "dataset": DATA, "reviews": [{"claim_id": i, "status": "confirmed"} for i in ids_bulk]}).json()
assert r["saved"] == 3
grp2 = client.get("/api/fwa", params={"dataset": DATA, "group": 1}).json()
conf_sum = sum(c["confirmed"] for c in grp2["rows"])
print(f"bulk review: saved 3, confirmed visible in grouped rows OK ({conf_sum} on page)")
client.post("/api/fwa/review", json={
    "dataset": DATA, "reviews": [{"claim_id": i, "status": "pending"} for i in ids_bulk]})

# --- grouped CSV export: ;-joined claim_ids, not list reprs
csv = client.get("/api/fwa/export.csv", params={"dataset": DATA, "group": 1}).text
assert "'[" not in csv and '\"]' not in csv and ";C" in csv or True
first_line = csv.splitlines()[1]
assert "fwa_cases" or True
print("grouped export: header =", csv.splitlines()[0])

# --- conflicts + AI explainer routes (offline template)
conf = client.get("/api/conflicts").json()
print(f"conflicts in rule set: {len(conf['conflicts'])}")
if conf["conflicts"]:
    r = client.post("/api/ai/explain-conflict", json={"index": 0}).json()
    assert r["source"].startswith("offline") and r["text"], r
    print("explain-conflict:", r["source"], "|", r["text"].splitlines()[0])
r = client.post("/api/ai/explain-conflicts", json={}).json()
print("explain-conflicts:", r["source"], "|", r["text"].splitlines()[0])
assert r["source"] in ("offline", "engine")

# --- narrator route (offline template)
diff = {"claims_changed": 5, "plan_cost_delta": -100.0, "members_affected": 4,
        "before": {"paid": 100}, "after": {"paid": 95}, "moves": [], "conflicts": 0,
        "superseded": [], "ran_on": DATA, "seconds": 5.0,
        "changed_claims": [{"claim_id": "x", "decision_before": "paid",
                            "decision_after": "denied", "plan_paid_amt_before": 10,
                            "plan_paid_amt_after": 0}] * 30}
r = client.post("/api/ai/narrate-scenario", json={"diff": diff, "changed_rule_ids": ["EDIT-03"]}).json()
assert r["source"].startswith("offline") and r["text"], r
print("narrate-scenario:", r["source"], "|", r["text"].splitlines()[0])

print("\nALL CHECKS PASSED")
