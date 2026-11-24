# Daman Claim Decision Engine — project context for Claude Code

Hackathon project (PureHealth, Rules & Decision Intelligence track). One system covering:
- **RD02 Policy Rules Simulator** — what-if on benefit rules before they go live
- **RD03 Fraud Investigation Triage Board** — ranked FWA queue with reasons
- **RD04 Claim Edit Rule Conflict Detector** — finds clashing rules, explains the winner

Judged (per the hackathon Task Guide) on traceable decisions, rule coverage and recall of
*seeded* cases. Not on fraud accuracy. Never claim real-world fraud detection or revenue impact.

## Data (synthetic, safe)
CMS DE-SynPUF Samples 1–3 (US synthetic Medicare claims 2008–2010). Raw CSVs sit in this folder.
**Never open or re-save them in Excel**: it strips leading zeros from codes and breaks dates.
Only beneficiary, inpatient and outpatient files are used. Prescription and carrier files are ignored.

- `clean/sampleN_full/`, `clean/sampleN_dev/` (20k members): Parquet tables from the loader
- `clean/sampleN_seeded/`, `clean/sampleN_hard/`: the same plus fictional plans, exclusion list,
  seeded anomalies and 37 boundary-case members (ids `BND..`)
- `answer_keys/<name>/`: answer keys for evaluation. **The engine and app must never read these
  for decisions.** Only evaluate.py and the app's Evaluation/FWA-curve displays use them.

Sample 1 = build/tune, Sample 2 = test (unseen), Sample 3 = spare. Members don't overlap across samples.

## Code (in `rd_engine/`)
| File | Role |
|---|---|
| `load_desynpuf.py` | raw CSV → clean tables: members (member×year), claims (IP+OP, 34 cols incl. flag_*), claim_lines (OP claim×HCPCS code + repeat_count), stays, diagnoses, procedures |
| `rules/*.yaml` | the rule book. coverage.yaml (COV-*), edits.yaml (DQ-*, EDIT-*), anomalies.yaml (FWA-*), conflict_seeds.yaml (PROP-*, off by default), plans.yaml, precedence.yaml |
| `engine.py` | `run(folder, include_proposals=False, rules=None)` → decisions, hits, conflicts, summary. `explain()`, `compare()`, `detect_conflicts()`, `apply_precedence()` |
| `precompute.py` | runs the engine over data folders and saves results to `results/<name>/` (decisions/hits parquet + rule_ids.json) for fast app start-up |
| `seed.py` | adds plans, exclusions, seeded anomalies (`--hard` adds subtle + novel), boundary cases, answer keys |
| `evaluate.py` | scores against answer keys: boundary accuracy, anomaly recall by difficulty, reviewer workload, conflicts |
| `ai.py` | AI helpers: clause→rule drafting (validated), grounded explanations (EN/AR), FWA case notes, provider briefs (section 7). Backends: Claude Code CLI (`claude -p --output-format json`, prompt via stdin) / Anthropic API / offline. Cache in `.ai_cache/` |
| `api.py` | FastAPI backend (port 8000) serving `results/<name>/` as JSON + the built React frontend from `web/dist`. Must insert `rd_engine` into sys.path and chdir to the project root BEFORE importing ai/engine/evaluate (flat imports). AI/eval routes wrap ai.py/evaluate.py. Member profiles: `GET /api/members` (search + condition filter), `GET /api/member/{id}` (condition timeline, diagnoses, claims), `POST /api/ai/member-brief` (English AI brief), `POST /api/ai/provider-brief` (AI provider triage brief, grounded in the provider's stats + fired rule ids). AI shadow review: `POST /api/ai/shadow-review` — independent LLM reading of the rule book vs engine decisions; disagreements = rule ambiguity candidates (cached in SHADOW). In-memory demo state: reviews, scenarios, drafts |
| `guidance.py` | ported from app.py: plain_explanation (returns {"text"}), APPEAL_HINTS, appeal_guidance — no Streamlit |
| `app.py` | old Streamlit board (kept, no longer primary). 9 tabs, appeal guidance, review workflow, scenario save/compare |

### How the engine works
- Each rule has a `kind` implemented as `k_<kind>()` in engine.py and registered automatically in
  `RULE_KINDS`. To add a new kind: write `k_newkind(rule, claims, lines, t)` returning hits via
  `_hits()`, then use `kind: newkind` in YAML.
- Hit outcomes: reject > deny > refer > deny_line > allow, plus `flag` for FWA. Claim decision:
  rejected > denied > pended > partial > paid. A claim with all lines denied counts as denied.
- Joins (build_context): plan on member_id; member facts on member_id + year of thru_dt; death =
  earliest death_dt; lines inherit claim context; policy_year counted from each member's policy start.
- Grants (`outcome: allow`) cancel lower-priority **coverage** denials on the same line, never edits.
- Precedence (precedence.yaml) decides conflict winners **and** is applied at run time for clashing
  frequency limits / unit caps (the loser stops applying for overlapping plans/codes/dates).
- No units, modifiers or line dates exist in DE-SynPUF. Units = repeat_count of a code on a claim.

## Run commands (Windows)
Python: `C:\Users\sayyed.imran\AppData\Local\Python\pythoncore-3.14-64\python.exe` (here: PY).
`streamlit` and `python` may not be on PATH, so use `PY -m ...`. **npm/vite shims are blocked by
group policy on this machine — run vite via node** (`node node_modules\vite\bin\vite.js build` in
web/). The app runs from `dev.bat` or `PY -m uvicorn rd_engine.api:app --port 8000`.

```
PY rd_engine/load_desynpuf.py --data-dir . --sample 1 --out-dir ./clean
PY rd_engine/seed.py --clean clean/sample1_dev --out clean/sample1_dev_seeded --keys answer_keys/sample1_dev --hard
PY rd_engine/evaluate.py --data clean/sample1_dev_seeded --keys answer_keys/sample1_dev
PY -m streamlit run rd_engine/app.py
```

## Baseline results (keep these from regressing)
On Sample 2 hard: boundary cases 37/37 (decision and rule); easy anomalies 100%; subtle 100%
(FWA-01 min_run 3 + alternate-day runs min_run_alt 4; FWA-02 ratio 2.0 — catches facility_3x,
which seeds at ~3x rate but ~2.75x vs the peer median; near_dup_next_day 10/10 via FWA-03 after
the key's expectation gained FWA-03, unbundling_other 20/20 via EDIT-22 82947+80053); novel 100%
(upcoding_drift 100/100 via the widened FWA-02; same_day_mass_billing 60/60 via FWA-04
same-day mass billing, min_members 8 — zero benign groups ≥6 on Sample 1); reviewer opens ~1,290
claims to catch 80% of easy seeded FWA (queue ~21,352; was ~1,181 / ~21,300 — the +60 mass-billing
flags rank high, each open catches a real seeded claim); subtle/novel FWA claims reaching the
queue 457/460; conflicts 9/9 with the right winner; 0 conflicts in the live rule set. Full sample
runs in ~50 s, dev subset in ~7 s; precomputed results load in ~0.1 s.
**After any engine or rule change, rerun evaluate.py on a dev seeded set and check these.**
FWA-01/FWA-02 were tuned on Sample 1 (short_score 0.7 for 3-day/alternate runs so they rank below
easy flags; FWA-02 ratio 2.0 adds only ~3 benign pairs on Sample 1).

## Working rules for changes
- Keep decisions deterministic and rule-based. AI drafts and explains only; it never decides.
- Every new rule needs `id`, `clause` (plain text), `reason`, `priority`, `effective`.
- Don't tune detectors on Sample 2. Tune on Sample 1, report on Sample 2 (use a new `--seed` if needed).
- Codes in the data are ~80% CPT (AMA terms): fine internally; use placeholder codes in anything shared.
- Keep the app fast: default to dev subsets; cache with st.cache_data; after rule changes re-run
  precompute.py so the app loads fresh results.

## Open ideas / next steps
1. New detectors for current misses: repeats within 7 days; upcoding (provider share of 99214/99215
   vs peers and over time); same-day mass billing (provider billing many members it has never seen
   on one day); extra pair edits (e.g. 82947 with 80053). ~~near-duplicates~~ — done (FWA-03).
2. ~~App speed: precompute results to Parquet~~ — done (precompute.py + app auto-loads results/).
3. ~~App insight: provider triage view, money waterfall, actionable FWA queue~~ — done (tab +
   Overview chart + editable review column with save/export). ~~FWA cases grouped by
   (provider, member, pattern)~~ — done (FwaQueue grouped view + /api/fwa/case + bulk review).
   Remaining: honest peer comparisons with case-mix context (use case P029).
4. ~~More AI use cases~~ — done: conflict explainer (/api/ai/explain-conflict[s], Conflicts page),
   scenario narrator (/api/ai/narrate-scenario, WhatIf page), both with offline fallbacks and
   grounded rule-ID checking. All AI functions live in ai.py section 4.
5. Demo prep: script, results slide (honest easy/subtle/novel split), limits slide.
