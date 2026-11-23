# Claim Decision Engine — rules, seeding, engine, evaluation

Fictional rules running on real (synthetic CMS DE-SynPUF) claims. Covers RD02 (policy rules),
RD03 (FWA triage) and RD04 (claim edits and rule conflicts).

## Files

| File | What it is |
|---|---|
| `load_desynpuf.py` | Loads raw CSVs into clean tables (you've already run this) |
| `rules/plans.yaml` | 3 fictional plans (Gold, Silver, Basic): share, coinsurance, policy start window |
| `rules/coverage.yaml` | 8 coverage clauses (RD02): eligibility, death, exclusions, frequency limits, dialysis drugs, waiting period |
| `rules/edits.yaml` | 3 data-quality rules + 13 claim edits (RD04): stay overlaps, duplicates, unit caps, pair edits, companion codes |
| `rules/anomalies.yaml` | 3 FWA detectors (RD03): consecutive days, facility over-servicing (services per patient), near-duplicate claims |
| `rules/conflict_seeds.yaml` | 8 proposed rules, some clashing with the live set on purpose |
| `rules/precedence.yaml` | The precedence policy the conflict detector uses to pick a winner |
| `engine.py` | Runs the rules → decision, primary rule, trace, payable amount, FWA score per claim; conflict detector; what-if compare |
| `precompute.py` | Runs the engine over one or more data folders and saves results to `results/` for fast app start-up |
| `seed.py` | Adds plans, an exclusion list, seeded anomalies and 37 boundary cases; writes answer keys |
| `evaluate.py` | Scores the engine against the answer keys |
| `app.py` | Streamlit reviewer board (see below) |
| `ai.py` | AI helpers: clause → rule drafting, grounded explanations (English/Arabic), FWA case notes |
| `answer_keys/expected_conflicts.yaml` | Answer key for the planted rule conflicts |

## Setup

Put these files and the `rules/` and `answer_keys/` folders in the same folder as your notebook
and the `clean/` folder. Needs `pandas`, `pyarrow` and `pyyaml` (`%pip install pyyaml`).

## Reviewer app

The UI is a FastAPI backend + React dashboard (dark theme). The backend serves the precomputed
results and the built frontend from one origin:

```
dev.bat                       (Windows: starts API + opens http://127.0.0.1:8000)
PY -m uvicorn rd_engine.api:app --port 8000        (manual)
```

To rebuild the frontend after editing `web/src` (npm is blocked by policy on some machines — run
vite via node):

```
cd web && npm install
node node_modules\vite\bin\vite.js build
```

For live-frontend development: `node node_modules\vite\bin\vite.js dev` (proxies /api to :8000).

The old Streamlit board is still there (`rd_engine/app.py`) but is no longer the primary UI.

| Tab | Shows |
|---|---|
| Overview | Decision counts, money (billed, payable, plan pays, held), which rules fired, where the billed money goes |
| Claim review | Filter by decision, rule or ID; plain-language explanation, appeal/next-steps guidance, claim, member, diagnoses and procedures, full trace, services |
| FWA queue | Claims ranked by fraud score with reasons; editable review column (confirm/dismiss), save + CSV export; reviewer-workload curve if answer keys exist |
| Provider triage | Claims, members and FWA flags per provider; services per patient vs the peer median per code; office-visit mix by year; flagged claims |
| What-if | Add proposed rules or switch off live ones in the sidebar: claims that change, cost change, members affected; save named scenarios and compare them against the baseline |
| AI rule drafting | Type a policy change in plain words → AI drafts the rule → app validates it and shows clashes → you approve → What-if shows the impact |
| Rule conflicts | Clashes in the current rule set, the winner and why, plus the precedence policy |
| Rules | Every rule with its clause text, on/off status and source |
| Evaluation | Boundary cases pass/fail, seeded-anomaly recall, conflict detection |

The base run (live rules) loads precomputed results from `results/<folder-name>/` when they exist
and match the rule book — create them with:

```
python precompute.py --data clean/sample2_seeded
```

That takes ~45 s on a full sample once; after that the app starts in about a second. Without
precomputed results the first load runs the engine (about 5 s on a dev subset, 25–30 s on a full
sample). Each new what-if combination runs once. After editing a YAML rule file, re-run
`precompute.py` (or clear the cache, menu ⋮ → Clear cache) to rerun.

## AI features

The rules engine makes every decision. AI only drafts rules for a person to approve and writes
explanations from the engine's trace. Any rule ID the AI mentions that isn't in the trace is flagged.

| Feature | Where | What it does |
|---|---|---|
| Clause → rule | AI rule drafting tab | Plain-language policy change → YAML rule → validation + conflict check → approve → What-if |
| Explanation | Claim review → "AI explanation" | Member or provider version, English or Arabic, written only from the decision trace |
| Case note | FWA queue → "AI case note" | Why flagged, what to check, possible innocent explanations |

**Backend** (shown in the sidebar, with a "Test AI connection" button):

| Backend | Used when | Notes |
|---|---|---|
| `claude_code` | The `claude` command is on your PATH | Calls Claude Code headless (`claude -p … --output-format json`) with your existing login. No API key. Start Streamlit from a terminal where `claude` works |
| `api` | `ANTHROPIC_API_KEY` is set and `claude` is not found | `pip install anthropic`; model via `AI_MODEL` (default `claude-sonnet-5-5`) |
| `offline` | Neither is available, or `AI_BACKEND=offline` | Simple parser for clauses + template explanations, so the demo never breaks |

Force one with `set AI_BACKEND=offline` (Windows) before `streamlit run`. Answers are cached in
`.ai_cache/`, so repeated demo clicks are instant. Delete the folder to get fresh answers.

**Precedence at run time:** when two limit rules or two unit caps clash, the winner under
`rules/precedence.yaml` replaces the loser for the overlapping plans/codes and dates. So raising a
limit through a draft or proposal actually changes decisions. The What-if tab says which rule replaced which.

## Notebook quick start

```python
import engine

# 1. Run the live rules on the dev subset
res = engine.run("clean/sample1_dev")
res["decisions"].decision.value_counts()

# 2. Explain one claim
d = res["decisions"]
engine.explain(res, d[d.decision == "partial"].claim_id.iloc[0])

# 3. What-if: add the proposed rules and see what changes
res_p = engine.run("clean/sample1_dev", include_proposals=True)
engine.compare(res, res_p)          # decisions that move + plan cost change
res_p["conflicts"]                   # clashes and which rule wins, and why

# 4. FWA review queue
d.sort_values("fwa_score", ascending=False).query("fwa_score > 0")[
    ["claim_id", "provider_id", "fwa_score", "fwa_reasons"]].head(20)
```

## Seed and evaluate (build on Sample 1, test on Sample 2)

```
python seed.py --clean clean/sample1_dev  --out clean/sample1_dev_seeded --keys answer_keys/sample1_dev
python seed.py --clean clean/sample2_full --out clean/sample2_seeded     --keys answer_keys/sample2
python evaluate.py --data clean/sample2_seeded --keys answer_keys/sample2
```

In a notebook, prefix with `!` or use `%run`. Keep `answer_keys/` out of the app: it's what you score against.

### Hard mode (`--hard`)

Adds two more groups of seeded cases next to the easy ones:

| Difficulty | Patterns | Purpose |
|---|---|---|
| easy | after death, 5 consecutive days, excluded provider, facility at 6x peers, unbundling 80048, exact duplicate | Proves each detector works end to end |
| subtle | 3 consecutive days, every other day, facility at 3x, duplicate with amount +1%, duplicate next day, unbundling with 82947 | Same ideas, weaker signal: shows where the thresholds sit |
| novel | upcoding drift (99213 rebilled as 99215), same-day mass billing (one provider, 15 unrelated members, one day) | No detector exists: shows what the engine can't see |

```
python seed.py --clean clean/sample2_full --out clean/sample2_hard --keys answer_keys/sample2_hard --hard
python evaluate.py --data clean/sample2_hard --keys answer_keys/sample2_hard
```

Results on Sample 1 (full): easy 100%, subtle 3% (9/325), novel 32% (51/160; half the upcoding cases
are caught by the facility-outlier check). That is the honest picture to present: the engine catches
what it was built for, misses weaker versions, and only sometimes catches patterns it wasn't designed for.

## How decisions work

- Each rule produces hits: `reject`, `deny` (whole claim), `refer` (pend), `deny_line`, `allow` (exception) or `flag` (FWA).
- Claim decision: **rejected** > **denied** > **pended** > **partial** > **paid**. A claim where every line is denied counts as denied.
- The primary rule is the highest-priority hit at the deciding level. `explain()` shows the full trace.
- An `allow` (grant) cancels lower-priority **coverage** denials on the same line. It never cancels edits.
- Payable = claim payment if paid; × share of lines not denied if partial; 0 if denied, rejected or pended (pended amounts go to `held_amt`). Plan pays payable × (1 − coinsurance).
- FWA score = 1 − ∏(1 − score) over FWA hits. Facility outliers score higher the more extreme they are.

## Results on Sample 1 (tested before delivery)

| Check | Result |
|---|---|
| Full sample run time | ~25 s for 847k claims |
| Decisions (full, seeded) | 91% paid, 3.5% partial, 2.9% denied, 2.3% pended, 0.3% rejected |
| Boundary cases | 37/37 correct decision and correct rule cited |
| Seeded anomalies | 100% recall across all 6 patterns |
| Rule conflicts | 9/9 found, correct winner 9/9, 0 false conflicts on the live set |
| Reviewer workload | 929 claims opened to catch 80% of 645 seeded FWA claims (queue of 1,690) |

100% recall is expected, because the rules and the seeds were written together. The useful
numbers are reviewer workload and how the engine does on Sample 2.

## Known limits

- All rules, plans and clauses are fictional. Codes are used as found in the CMS data (about 80% CPT, under AMA terms): fine internally, but use placeholder codes in anything you share.
- DE-SynPUF has no units, modifiers or line dates. Units = how often a code repeats on a claim. The modifier exception (PROP-06) never fires at run time.
- Coverage months are counts per year, not specific months, so Part B eligibility is checked per year.
- Frequency limits count every earlier service, including ones on claims later denied for other reasons.
- Payable amounts for partial claims are prorated by number of codes. There are no line-level amounts.
- Natural FWA flags are unlabelled, so they count against queue precision even when they look suspicious.
