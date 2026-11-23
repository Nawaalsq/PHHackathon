"""
evaluate.py - Score the engine against the answer keys written by seed.py.

    python evaluate.py --data clean/sample2_seeded --keys answer_keys/sample2

Reports the Task Guide metrics:
  RD02  boundary decision accuracy and clause-trace accuracy (right rule cited)
  RD04  conflict detection recall and correct winner under the precedence policy
  RD03  recall of seeded anomalies by pattern and difficulty (easy / subtle / novel),
        and reviewer workload
        (claims a reviewer opens, highest FWA score first, to catch 80%)
"""

import argparse
import json
import os

import numpy as np
import pandas as pd
import yaml

import engine

FWA_PATTERNS = ["service_after_death", "consecutive_days", "excluded_provider",
                "facility_over_servicing", "exact_duplicate"]


def boundary_metrics(res, keys_dir):
    exp = pd.read_csv(os.path.join(keys_dir, "boundary_expected.csv"), dtype=str).fillna("")
    d = res["decisions"][["claim_id", "decision", "primary_rule"]].copy()
    d["primary_rule"] = d["primary_rule"].fillna("")
    m = exp.merge(d, on="claim_id", how="left")
    m["decision_ok"] = m["decision"] == m["expected_decision"]
    m["rule_ok"] = m["primary_rule"] == m["expected_rule"]
    fails = m[~(m["decision_ok"] & m["rule_ok"])]
    cols = ["case_id", "claim_id", "description", "expected_decision", "decision",
            "expected_rule", "primary_rule", "decision_ok", "rule_ok"]
    return {
        "cases": int(len(m)),
        "decision_accuracy": round(float(m["decision_ok"].mean()), 3),
        "rule_trace_accuracy": round(float(m["rule_ok"].mean()), 3),
        "failures": fails[cols].to_dict("records"),
        "all_cases": m[cols].to_dict("records"),
    }, m


def _normalise_keys(keys):
    """Older key files have no difficulty / fwa columns."""
    keys = keys.copy()
    if "difficulty" not in keys:
        keys["difficulty"] = "easy"
    if "fwa" not in keys:
        keys["fwa"] = keys["pattern"].isin(FWA_PATTERNS)
    keys["fwa"] = keys["fwa"].astype(str).str.lower().isin(["true", "1"])
    return keys


def workload_target(keys):
    """Seeded claims the FWA queue should surface: easy FWA patterns (comparable across runs)."""
    keys = _normalise_keys(keys)
    return set(keys.loc[keys["fwa"] & (keys["difficulty"] == "easy"), "claim_id"])


def anomaly_metrics(res, keys_dir):
    keys = _normalise_keys(pd.read_csv(os.path.join(keys_dir, "anomalies.csv"), dtype=str))
    h = res["hits"]
    h = h[h["suppressed_by"].isna()][["claim_id", "rule_id"]].drop_duplicates()
    hit_pairs = set(zip(h["claim_id"], h["rule_id"]))
    d = res["decisions"]
    in_queue = set(d.loc[d["fwa_score"] > 0, "claim_id"])

    def found(cid, det):
        dets = det.split("|")
        if "any_fwa" in dets:
            return cid in in_queue
        return any((cid, x) in hit_pairs for x in dets)

    keys["detected"] = [found(c, x) for c, x in zip(keys["claim_id"], keys["expected_detector"])]
    keys["in_fwa_queue"] = keys["claim_id"].isin(in_queue)
    order = {"easy": 0, "subtle": 1, "novel": 2}
    by = (keys.groupby(["difficulty", "pattern"])
              .agg(seeded=("claim_id", "size"), detected=("detected", "sum"),
                   detector=("expected_detector", "first"))
              .reset_index())
    by["recall"] = (by["detected"] / by["seeded"]).round(3)
    by = by.sort_values(["difficulty", "pattern"], key=lambda s: s.map(order) if s.name == "difficulty" else s)
    lvl = keys.groupby("difficulty")["detected"].agg(["size", "sum"])

    # reviewer workload on the FWA queue (easy FWA patterns, comparable across runs)
    queue = d[d["fwa_score"] > 0].sort_values(["fwa_score", "allowed_amt"], ascending=False)
    target = workload_target(keys)
    hit = queue["claim_id"].isin(target).to_numpy()
    cum = np.cumsum(hit)
    need80 = int(np.ceil(0.8 * len(target)))
    k80 = int(np.argmax(cum >= need80)) + 1 if len(target) and len(cum) and cum[-1] >= need80 else None
    hard_fwa = keys[keys["fwa"] & (keys["difficulty"] != "easy")]
    return {
        "by_pattern": {r["pattern"]: {"difficulty": r["difficulty"], "detector": r["detector"],
                                      "seeded": int(r["seeded"]), "detected": int(r["detected"]),
                                      "recall": float(r["recall"])} for _, r in by.iterrows()},
        "by_difficulty": {k: {"seeded": int(v["size"]), "detected": int(v["sum"]),
                              "recall": round(float(v["sum"] / v["size"]), 3)}
                          for k, v in lvl.sort_index(key=lambda s: s.map(order)).iterrows()},
        "overall_recall": round(float(keys["detected"].mean()), 3),
        "fwa_queue_size": int(len(queue)),
        "fwa_seeded_claims": int(len(target)),
        "fwa_seeded_in_queue": int(hit.sum()),
        "claims_to_open_for_80pct": k80,
        "queue_precision_at_that_point": round(float(need80 / k80), 3) if k80 else None,
        "subtle_novel_fwa_claims_reaching_queue": (
            f"{int(hard_fwa['in_fwa_queue'].sum())}/{len(hard_fwa)}" if len(hard_fwa) else None),
    }


def conflict_metrics(keys_dir, rules_dir=engine.RULES_DIR):
    path = os.path.join(keys_dir, "expected_conflicts.yaml")
    if not os.path.exists(path):
        path = os.path.join(os.path.dirname(engine.__file__), "answer_keys", "expected_conflicts.yaml")
    with open(path) as fh:
        exp = yaml.safe_load(fh)["conflicts"]
    _, prec = engine.load_config(rules_dir)
    found = engine.detect_conflicts(engine.load_rules(rules_dir, include_proposals=True), prec)
    got = {frozenset(x for x in (r.rule_a, r.rule_b) if isinstance(x, str)): r.winner
           for r in found.itertuples()}
    rows = []
    for e in exp:
        key = frozenset(e["pair"])
        rows.append({"pair": " + ".join(e["pair"]), "type": e["type"],
                     "detected": key in got,
                     "winner_expected": e["winner"],
                     "winner_got": got.get(key) if isinstance(got.get(key), str) else None})
    df = pd.DataFrame(rows)
    df["winner_ok"] = df["winner_expected"].fillna("-") == df["winner_got"].fillna("-")
    extra = [sorted(k) for k in got if k not in {frozenset(e["pair"]) for e in exp}]
    return {
        "expected": int(len(df)),
        "detected": int(df["detected"].sum()),
        "recall": round(float(df["detected"].mean()), 3),
        "winner_accuracy": round(float(df.loc[df["detected"], "winner_ok"].mean()), 3),
        "unexpected_conflicts": extra,
        "detail": df.to_dict("records"),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--keys", required=True)
    ap.add_argument("--out", default=None, help="where to write eval_report.json (default: --keys)")
    a = ap.parse_args()

    res = engine.run(a.data, verbose=False)
    b, _ = boundary_metrics(res, a.keys)
    an = anomaly_metrics(res, a.keys)
    co = conflict_metrics(a.keys)
    report = {"data": a.data, "engine_summary": res["summary"],
              "boundary": b, "anomalies": an, "conflicts": co}

    print("\n=== RD02/RD04 boundary cases ===")
    print(f"{b['cases']} cases | decision accuracy {b['decision_accuracy']:.0%} | "
          f"rule-trace accuracy {b['rule_trace_accuracy']:.0%}")
    for f in b["failures"]:
        print(f"  FAIL {f['case_id']} {f['description']}: expected {f['expected_decision']}/"
              f"{f['expected_rule'] or '-'}, got {f['decision']}/{f['primary_rule'] or '-'}")
    print("\n=== RD03 seeded anomalies ===")
    last = None
    for p, r in an["by_pattern"].items():
        if r["difficulty"] != last:
            lv = an["by_difficulty"][r["difficulty"]]
            print(f"  [{r['difficulty'].upper()}] {lv['detected']}/{lv['seeded']} found, recall {lv['recall']:.0%}")
            last = r["difficulty"]
        print(f"    {p:<28} {r['detected']:>4}/{r['seeded']:<4} recall {r['recall']:>4.0%}   ({r['detector']})")
    print(f"  FWA queue {an['fwa_queue_size']} claims; reviewer opens "
          f"{an['claims_to_open_for_80pct']} to catch 80% of {an['fwa_seeded_claims']} easy seeded FWA claims")
    if an["subtle_novel_fwa_claims_reaching_queue"]:
        print(f"  Subtle/novel FWA claims that reach the queue at all: "
              f"{an['subtle_novel_fwa_claims_reaching_queue']}")
    print("\n=== RD04 rule conflicts ===")
    print(f"  {co['detected']}/{co['expected']} planted conflicts found | "
          f"correct winner {co['winner_accuracy']:.0%} | unexpected {len(co['unexpected_conflicts'])}")

    out = a.out or a.keys
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "eval_report.json"), "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(f"\nSaved {os.path.join(out, 'eval_report.json')}")


if __name__ == "__main__":
    main()
