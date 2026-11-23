"""One-off check: precomputed results in results/sample2_hard match a live engine run."""
import sys
import time

sys.path.insert(0, "rd_engine")
import engine  # noqa: E402
import pandas as pd  # noqa: E402

t0 = time.time()
pre = pd.read_parquet("results/sample2_hard/decisions.parquet")
hits = pd.read_parquet("results/sample2_hard/hits.parquet")
t_load = time.time() - t0

res = engine.run("clean/sample2_hard", verbose=False)
live = res["decisions"]
m = live.merge(pre, on="claim_id", suffixes=("_live", "_pre"))
print("rows live/pre:", len(live), len(pre))
print("decision equal:", bool((m["decision_live"] == m["decision_pre"]).all()))
print("fwa_score max diff:", float((m["fwa_score_live"] - m["fwa_score_pre"]).abs().max()))
print("payable max diff:", float((m["payable_amt_live"] - m["payable_amt_pre"]).abs().max()))
print("rules_fired equal:", bool((m["rules_fired_live"] == m["rules_fired_pre"]).all()))
print("hits rows live/pre:", len(res["hits"]), len(hits))
print(f"precomputed load time: {t_load:.2f}s vs live run {res['summary']['seconds']}s")
