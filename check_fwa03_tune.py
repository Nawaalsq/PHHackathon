"""Tune FWA-03 (near-duplicate detector) on sample1_dev_seeded.

Compares variants: off (baseline), current (days 3, pct .05, score .7),
tighter windows and lower scores. Reports reviewer workload and how many
seeded near-duplicate claims FWA-03 itself flags.
"""
import copy
import sys

sys.path.insert(0, "rd_engine")
import engine  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import evaluate  # noqa: E402

DATA = "clean/sample1_dev_seeded"
KEYS = "answer_keys/sample1_dev"

keys = pd.read_csv(f"{KEYS}/anomalies.csv", dtype=str)
seeded_nd = set(keys.loc[keys["pattern"].str.startswith("near_duplicate"), "claim_id"])
target = evaluate.workload_target(keys)


def variant(rules, days=None, pct=None, score=None, off=False):
    out = []
    for r in rules:
        r = copy.deepcopy(r)
        if r["id"] == "FWA-03":
            if off:
                continue
            if days is not None:
                r["params"]["days"] = days
            if pct is not None:
                r["params"]["amount_pct"] = pct
            if score is not None:
                r["score"] = score
        out.append(r)
    return out


base_rules = engine.load_rules()
scenarios = [
    ("off (no FWA-03)", variant(base_rules, off=True)),
    ("d3 p.05 s.70 (current)", variant(base_rules)),
    ("d1 p.05 s.70", variant(base_rules, days=1)),
    ("d3 p.05 s.50", variant(base_rules, score=0.5)),
    ("d1 p.05 s.50", variant(base_rules, days=1, score=0.5)),
    ("d3 p.02 s.50", variant(base_rules, pct=0.02, score=0.5)),
]

print(f"{'variant':<26} {'fwa03 flags':>11} {'on seeds':>9} {'false':>6} "
      f"{'queue':>7} {'opens/80%':>10}")
for name, rules in scenarios:
    res = engine.run(DATA, rules=rules, verbose=False)
    hits, d = res["hits"], res["decisions"]
    f03 = hits[hits["rule_id"] == "FWA-03"]
    flagged = set(f03["claim_id"])
    queue = d[d["fwa_score"] > 0].sort_values(["fwa_score", "allowed_amt"], ascending=False)
    found = np.cumsum(queue["claim_id"].isin(target).to_numpy())
    need80 = int(np.ceil(0.8 * len(target)))
    k80 = int(np.argmax(found >= need80)) + 1 if len(found) and found[-1] >= need80 else None
    print(f"{name:<26} {len(flagged):>11} {len(flagged & seeded_nd):>9} "
          f"{len(flagged - seeded_nd):>6} {len(queue):>7} {str(k80):>10}")
