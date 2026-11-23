"""
precompute.py - Run the engine over one or more data folders and save the
results to results/<folder-name>/ so the app can load them instead of
re-running every rule on start-up.

    python precompute.py --data clean/sample2_seeded clean/sample1_dev_seeded

Writes (via engine.save): decisions.parquet, hits.parquet, conflicts.csv,
summary.json, plus rule_ids.json (the live rule set used, so the app can
check the precomputed results still match the rule book).

The app only uses precomputed results for the *base* run (live rules, no
proposals or AI drafts). What-if scenarios still run live.
"""

import argparse
import json
import os

import engine


def precompute(data, out_root="results"):
    """Run the engine on one data folder and save results + rule ids."""
    res = engine.run(data, verbose=False)
    out = os.path.join(out_root, os.path.basename(os.path.normpath(data)))
    engine.save(res, out)
    live = [r["id"] for r in res["rules"] if r["source_file"] != engine.PROPOSAL_FILE]
    with open(os.path.join(out, "rule_ids.json"), "w") as fh:
        json.dump(sorted(live), fh, indent=2)
    return out, res["summary"]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", nargs="+", required=True,
                    help="one or more clean or seeded data folders")
    ap.add_argument("--out", default="results", help="results root (default: results/)")
    a = ap.parse_args()
    for d in a.data:
        out, s = precompute(d, a.out)
        print(f"{out}: {s['claims']:,} claims in {s['seconds']}s "
              f"({s['fwa_flagged_claims']:,} flagged)")


if __name__ == "__main__":
    main()
