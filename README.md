---
title: Claim Decision Engine
emoji: 🏥
colorFrom: indigo
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# Claim Decision Engine

One rule-based decision system covering three linked products for a health insurer:

| Product | What it does |
|---|---|
| **RD02 — Policy Rules Simulator** | What-if analysis on benefit rules before they go live: claims that would change, cost impact, members affected |
| **RD03 — Fraud/Abuse Triage Board** | A ranked FWA (fraud, waste, abuse) review queue with per-claim reasons, provider triage and reviewer-workload tracking |
| **RD04 — Claim Edit Rule Conflict Detector** | Finds clashing claim-edit rules, explains which one wins and why, using an explicit precedence policy |

Every decision is deterministic and rule-based, with a full trace: which rule fired, which clause,
what was paid, held or denied, and why — in plain language (English/Arabic). AI is used only to
draft and explain rules, never to decide claims.

> **Scope note:** this is a hackathon project running on **synthetic data** (CMS DE-SynPUF
> public synthetic Medicare claims, 2008–2010, with fictional insurer-style plans layered on top).
> FWA scores measure recall of *seeded* test cases, not real-world fraud detection. All codes in
> the UI are placeholders, not real CPT terms.

## Architecture

```
raw CSVs ──load_desynpuf.py──▶ clean parquet tables
                                   │
                        seed.py (fictional plans, exclusions,
                        seeded anomalies, boundary cases, answer keys)
                                   │
                 engine.py ◀── rules/*.yaml (the rule book)
                    │
        precompute.py ──▶ results/ parquet (fast app start-up)
                    │
        FastAPI (rd_engine/api.py) ── serves JSON API + built React frontend
                    │
        React + Vite + Tailwind (web/) ── reviewer dashboard
```

- **Rule book** (`rd_engine/rules/*.yaml`): coverage clauses, data-quality rules and claim edits,
  FWA detectors, precedence policy. Each rule has an id, plain-text clause, reason, priority and
  effective dates — the UI shows the exact clause behind every decision.
- **Engine** (`rd_engine/engine.py`): maps each rule `kind` to a detector; resolves outcomes
  (reject > deny > refer > deny_line > allow) with grants, precedence and what-if comparison.
- **Evaluation** (`rd_engine/evaluate.py`): scores boundary-case accuracy, anomaly recall by
  difficulty, and reviewer workload against answer keys. The engine and app never read the
  answer keys for decisions — only `evaluate.py` does.
- **AI helpers** (`rd_engine/ai.py`): clause → rule drafting (validated against the live rule
  set), grounded explanations in English/Arabic, FWA case notes. Uses the Claude API or CLI when
  available and falls back to a deterministic offline mode.

## Run locally (Windows)

Requires Python 3.10+ with `pandas`, `pyarrow`, `pyyaml`, `fastapi`, `uvicorn`, plus Node 18+
for the frontend.

```bat
REM 1. Load raw DE-SynPUF CSVs into clean tables (download them from the CMS site first)
python rd_engine\load_desynpuf.py --data-dir . --sample 1 --out-dir .\clean

REM 2. Seed: fictional plans, exclusions, seeded anomalies, boundary cases, answer keys
python rd_engine\seed.py --clean clean\sample1_dev --out clean\sample1_dev_seeded --keys answer_keys\sample1_dev --hard

REM 3. Precompute engine results for fast app start-up
python rd_engine\precompute.py

REM 4. Build the frontend
cd web && npm install && npm run build && cd ..

REM 5. Run the app (API serves the built frontend on one origin)
python -m uvicorn rd_engine.api:app --port 8000
```

Open http://127.0.0.1:8000. `dev.bat` does steps 5 + browser-open in one go.

To evaluate the engine against the answer keys:

```bat
python rd_engine\evaluate.py --data clean\sample1_dev_seeded --keys answer_keys\sample1_dev
```

## Honest results (on an unseen sample)

| Measure | Result |
|---|---|
| Boundary cases (37 planted edge cases) | 37/37 correct decision and rule |
| Easy seeded FWA anomalies | 100% recalled |
| Subtle seeded anomalies | ~91% recalled |
| Novel anomalies (no matching rule) | ~62% — the rest honestly surface as gaps |
| Claim-edit rule conflicts | 9/9 found with the correct winner; 0 conflicts in the live rule set |
| Reviewer workload | ~80% of easy seeded FWA found within ~1,200 reviewed claims from a ~21k queue |

## Limits

- Synthetic data with no units, modifiers or service-line dates; quantities are inferred from
  code repeats on a claim.
- FWA detectors are tuned to catch the *seeded* patterns (plus general near-duplicates and
  provider over-servicing); novel fraud patterns are out of scope.
- AI features need API/CLI credentials; without them the app runs in offline fallback mode.
