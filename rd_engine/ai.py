"""
ai.py - AI helpers for the Daman Claim Decision Engine.

The rules engine makes every decision. AI only DRAFTS rules (a person approves
them) and WRITES explanations from the engine's own trace. Every rule ID the AI
mentions is checked against the trace.

Backends (picked automatically, or set AI_BACKEND=claude_code|api|offline):
  claude_code  Claude Code CLI in headless mode: `claude -p ... --output-format json`.
               Uses your existing Claude Code login; no API key needed.
  api          Anthropic API (pip install anthropic; set ANTHROPIC_API_KEY).
  offline      No AI: simple parsing and templates, so a demo never breaks.

Responses are cached in .ai_cache/ so a repeated demo click is instant and
works even if the network drops.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile

import yaml

import engine

CACHE_DIR = os.path.join(os.getcwd(), ".ai_cache")
API_MODEL = os.environ.get("AI_MODEL", "claude-sonnet-5-5")
CLI_TIMEOUT = int(os.environ.get("AI_TIMEOUT", "180"))
PLANS = ["GOLD", "SILVER", "BASIC"]
CONDITIONS = ["esrd", "cc_alzheimers", "cc_heart_failure", "cc_chronic_kidney", "cc_cancer",
              "cc_copd", "cc_depression", "cc_diabetes", "cc_ischemic_heart",
              "cc_osteoporosis", "cc_ra_oa", "cc_stroke_tia"]
RULE_ID_RE = re.compile(r"\b(?:COV|EDIT|DQ|FWA|PROP|DRAFT)-\d+(?:\.\d+)?\b")


# ====================================================================== backends
def backend():
    want = os.environ.get("AI_BACKEND", "").strip().lower()
    if want in ("claude_code", "api", "offline"):
        return want
    if shutil.which("claude"):
        return "claude_code"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    return "offline"


class AIError(RuntimeError):
    pass


def _cache_get(key):
    p = os.path.join(CACHE_DIR, key + ".json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)["text"]
    return None


def _cache_put(key, text):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(os.path.join(CACHE_DIR, key + ".json"), "w", encoding="utf-8") as fh:
        json.dump({"text": text}, fh, ensure_ascii=False)


def _call_claude_code(prompt):
    exe = shutil.which("claude")
    if not exe:
        raise AIError("Claude Code CLI ('claude') not found on PATH.")
    # The full prompt goes in on stdin, so quotes and new lines are safe on Windows too.
    # Run in an empty temp folder so Claude Code doesn't read project files.
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = subprocess.run(
                [exe, "-p", "Follow the instructions in the input exactly. Do not use tools.",
                 "--output-format", "json"],
                input=prompt, capture_output=True, text=True, encoding="utf-8",
                timeout=CLI_TIMEOUT, cwd=tmp)
        except subprocess.TimeoutExpired:
            raise AIError(f"Claude Code did not answer within {CLI_TIMEOUT} s.")
    raw = (out.stdout or "").strip()
    try:
        data = json.loads(raw.splitlines()[-1] if raw else "{}")
    except json.JSONDecodeError:
        raise AIError(f"Unexpected Claude Code output: {raw[:300] or out.stderr[:300]}")
    if data.get("is_error") or "result" not in data:
        raise AIError(f"Claude Code error: {data.get('result') or out.stderr[:300]}")
    return data["result"]


def _call_api(prompt):
    try:
        import anthropic
    except ImportError:
        raise AIError("pip install anthropic to use the API backend.")
    client = anthropic.Anthropic()
    msg = client.messages.create(model=API_MODEL, max_tokens=1500,
                                 messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def ask(prompt, use_cache=True):
    """Send a prompt to the active backend. Returns (text, source)."""
    b = backend()
    if b == "offline":
        raise AIError("offline")
    key = hashlib.sha256((b + "\n" + prompt).encode("utf-8")).hexdigest()[:24]
    if use_cache:
        hit = _cache_get(key)
        if hit is not None:
            return hit, f"{b} (cached)"
    text = _call_claude_code(prompt) if b == "claude_code" else _call_api(prompt)
    _cache_put(key, text)
    return text, b


def check_backend():
    """Quick round trip for the sidebar 'Test AI' button."""
    try:
        text, src = ask("Reply with exactly the word READY.", use_cache=False)
        return True, f"{src}: {text.strip()[:60]}"
    except AIError as e:
        return False, str(e)


# ====================================================================== 1. clause -> rule
RULE_SCHEMA = """
A rule is ONE YAML mapping with these fields:
  id: DRAFT-<n>            (leave as DRAFT-1; the app renumbers it)
  clause: "<the policy sentence, cleaned up>"
  layer: coverage | edit
  kind: one of the kinds below
  scope: {codes: [...], code_prefix: [...], plans: [...]}   (only the keys you need)
  params: {...}            (depends on kind)
  outcome: deny_line | refer | allow
  priority: integer (coverage rules 50-60, edit rules 65-70; higher wins)
  effective: ["YYYY-MM-DD", "YYYY-MM-DD"]   (default ["2007-01-01", "2099-12-31"])
  reason: short text shown to reviewers; may use {rank} {limit} {units} {max_units} {code}

Kinds:
  frequency_limit (coverage): services per policy year.
      params: {count: distinct_service_dates, period: policy_year,
               limits: {PLAN: n, ...}   # list only the plans the clause covers
               exceptions: [{condition: <condition>, limit: n}]}   # optional
      outcome: deny_line
  exclusion (coverage): not covered at all. scope.codes or scope.code_prefix, scope.plans. outcome: deny_line
  waiting_period (coverage): not covered for N days after policy start. scope.codes, scope.plans, params: {days: n}. outcome: deny_line
  requires_condition (coverage): covered only if the member has a condition.
      params: {conditions_any: [...], refer_if_any: [...]}  outcome: deny_line
  grant (coverage): explicit exception that allows lines. params: {conditions_any: [...]}  outcome: allow
  unit_cap (edit): max units of a code per claim. scope.codes, params: {max_units: n}. outcome: deny_line
  pair_edit (edit): column2 not paid with column1 on the same claim. params: {column1: "<code>", column2: "<code>"}. outcome: deny_line
  requires_companion (edit): code needs a companion code in a numeric range on the claim.
      scope.codes, params: {companion_ranges: [[lo, hi]]}. outcome: refer

Plans: GOLD, SILVER, BASIC.
Conditions: """ + ", ".join(CONDITIONS) + """
Codes are HCPCS/CPT codes as 5-character strings, quoted (e.g. "97110").

If the clause cannot be expressed with these kinds, return a mapping with a single
field `unsupported: "<why, in one sentence>"`.
Return ONLY the YAML, inside one ```yaml code block. No other text.
"""


def _examples():
    ex = [r for r in engine.load_rules() if r["id"] in ("COV-2.3", "EDIT-10")]
    out = []
    for r in ex:
        r = {k: v for k, v in r.items() if k not in ("source_file", "fwa", "family")}
        out.append(yaml.safe_dump(r, sort_keys=False, allow_unicode=True))
    return "\n".join(out)


def _extract_yaml(text):
    m = re.search(r"```(?:yaml|yml)?\s*\n(.*?)```", text, re.S)
    body = m.group(1) if m else text
    data = yaml.safe_load(body)
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        raise ValueError("The AI did not return a YAML mapping.")
    return data


def validate_rule(r, existing_ids=()):
    """Return a list of problems (empty = valid). Also fills safe defaults."""
    problems = []
    if "unsupported" in r:
        return [f"AI says this clause is not supported: {r['unsupported']}"]
    r.setdefault("scope", {})
    r.setdefault("params", {})
    r.setdefault("effective", ["2007-01-01", "2099-12-31"])
    r.setdefault("priority", 50 if r.get("layer") == "coverage" else 65)
    for f in ["id", "clause", "layer", "kind", "outcome", "reason"]:
        if not r.get(f):
            problems.append(f"Missing field '{f}'.")
    if r.get("kind") not in engine.RULE_KINDS:
        problems.append(f"Unknown kind '{r.get('kind')}'.")
    if r.get("layer") not in ("coverage", "edit"):
        problems.append("layer must be coverage or edit.")
    if r.get("outcome") not in ("deny_line", "refer", "allow", "deny"):
        problems.append(f"Unknown outcome '{r.get('outcome')}'.")
    if r.get("id") in existing_ids:
        problems.append(f"Rule id {r.get('id')} already exists.")
    s, p, k = r["scope"], r["params"], r.get("kind")
    for key in ("codes", "code_prefix", "plans"):
        if key in s and not isinstance(s[key], list):
            s[key] = [s[key]]
    if "codes" in s:
        s["codes"] = [str(c) for c in s["codes"]]
    bad_plans = [x for x in s.get("plans", []) if x not in PLANS]
    if bad_plans:
        problems.append(f"Unknown plans {bad_plans}.")
    if k in ("frequency_limit", "exclusion", "waiting_period", "requires_condition", "grant",
             "unit_cap", "requires_companion") and not (s.get("codes") or s.get("code_prefix")):
        problems.append("scope needs codes or code_prefix.")
    if k == "frequency_limit":
        lim = p.get("limits")
        if not isinstance(lim, dict) or not lim or any(x not in PLANS for x in lim):
            problems.append("frequency_limit needs params.limits like {SILVER: 8}.")
        p.setdefault("count", "distinct_service_dates")
        p.setdefault("period", "policy_year")
        for ex in p.get("exceptions", []):
            if ex.get("condition") not in CONDITIONS:
                problems.append(f"Unknown condition '{ex.get('condition')}'.")
    if k in ("requires_condition", "grant"):
        for c in p.get("conditions_any", []) + p.get("refer_if_any", []):
            if c not in CONDITIONS:
                problems.append(f"Unknown condition '{c}'.")
        if not p.get("conditions_any"):
            problems.append(f"{k} needs params.conditions_any.")
    if k == "unit_cap" and not isinstance(p.get("max_units"), int):
        problems.append("unit_cap needs an integer params.max_units.")
    if k == "waiting_period" and not isinstance(p.get("days"), int):
        problems.append("waiting_period needs an integer params.days.")
    if k == "pair_edit":
        if not (p.get("column1") and p.get("column2")):
            problems.append("pair_edit needs params.column1 and column2.")
        p["column1"], p["column2"] = str(p.get("column1")), str(p.get("column2"))
    try:
        e0, e1 = r["effective"]
        r["effective"] = [str(e0), str(e1)]
    except (TypeError, ValueError):
        problems.append("effective must be [from, to].")
    return problems


def _offline_draft(clause):
    """Very small parser for the demo when no AI is available."""
    t = clause.lower()
    codes = re.findall(r"\b([A-Z]?\d{4,5})\b", clause.upper())
    codes = [c for c in codes if len(c) == 5]
    plans = [p for p in PLANS if p.lower() in t]
    nums = [int(x) for x in re.findall(r"\b(\d{1,3})\b", t) if int(x) < 1000]
    known = {"physio": "97110", "physiotherapy": "97110", "metabolic panel": "80053",
             "hba1c": "83036", "blood draw": "36415", "venipuncture": "36415",
             "blood count": "85025", "dialysis": "Q4081"}
    if not codes:
        codes = [v for k, v in known.items() if k in t][:1]
    nums = [n for n in nums if str(n) not in "".join(codes)]
    if not codes:
        return {"unsupported": "No service code or known service name found."}
    base = {"id": "DRAFT-1", "clause": clause.strip(), "layer": "coverage",
            "effective": ["2007-01-01", "2099-12-31"]}
    if any(w in t for w in ["exclude", "not covered", "no cover"]):
        return {**base, "kind": "exclusion", "scope": {"codes": codes, **({"plans": plans} if plans else {})},
                "params": {}, "outcome": "deny_line", "priority": 58,
                "reason": "Excluded under the plan"}
    if "unit" in t and nums:
        return {**base, "layer": "edit", "kind": "unit_cap", "scope": {"codes": codes},
                "params": {"max_units": nums[0]}, "outcome": "deny_line", "priority": 65,
                "reason": "{units} units billed; cap {max_units}"}
    if "wait" in t and nums:
        return {**base, "kind": "waiting_period", "scope": {"codes": codes, "plans": plans or PLANS},
                "params": {"days": nums[0]}, "outcome": "deny_line", "priority": 55,
                "reason": "Within waiting period"}
    if nums:
        return {**base, "kind": "frequency_limit", "scope": {"codes": codes, **({"plans": plans} if plans else {})},
                "params": {"count": "distinct_service_dates", "period": "policy_year",
                           "limits": {p: nums[0] for p in (plans or PLANS)}},
                "outcome": "deny_line", "priority": 50,
                "reason": "Service {rank} in policy year; limit {limit}"}
    return {"unsupported": "Could not find a limit, unit cap, waiting period or exclusion."}


def draft_rule(clause, existing_ids=()):
    """Clause text -> (rule dict, yaml text, problems, source)."""
    try:
        prompt = ("You convert insurance policy clauses into machine-readable rules for a claims "
                  "engine.\n" + RULE_SCHEMA + "\nExamples of existing rules:\n```yaml\n" + _examples() +
                  "```\n\nClause to convert:\n" + clause.strip() + "\n")
        text, src = ask(prompt)
        rule = _extract_yaml(text)
    except AIError as e:
        rule, src = _offline_draft(clause), ("offline" if str(e) == "offline" else f"offline (AI failed: {e})")
    except (ValueError, yaml.YAMLError) as e:
        return {}, "", [f"Could not read the AI's answer as a rule: {e}"], "ai"
    rule = {k: v for k, v in rule.items() if k in ("id", "clause", "layer", "kind", "scope", "params",
                                                     "outcome", "priority", "effective", "reason", "unsupported")}
    problems = validate_rule(rule, existing_ids)
    return rule, yaml.safe_dump(rule, sort_keys=False, allow_unicode=True), problems, src


# ====================================================================== 2. explanations
def _facts(row, trace, rules_by_id):
    lines = [f"Claim {row['claim_id']} ({row['claim_type']}, service date {str(row['from_dt'])[:10]}), "
             f"member plan {row['plan']}, member coinsurance {row['coinsurance']:.0%}.",
             f"DECISION: {row['decision']}. Billed {row['allowed_amt']:.2f}; payable {row['payable_amt']:.2f}; "
             f"plan pays {row['plan_paid_amt']:.2f}; held for review {row['held_amt']:.2f}."]
    if len(trace) == 0:
        lines.append("No rules fired.")
    for _, h in trace.iterrows():
        code = f" on service {h['code']}" if isinstance(h["code"], str) else ""
        sup = f" (overridden by exception {h['suppressed_by']})" if isinstance(h["suppressed_by"], str) else ""
        clause = rules_by_id.get(h["rule_id"], {}).get("clause", "")
        lines.append(f"- Rule {h['rule_id']} [{h['outcome']}]{code}: {h['reason']}{sup}. Clause: {clause}")
    return "\n".join(lines)


def grounding_check(text, trace):
    """trace may be a DataFrame with a rule_id column or an iterable of allowed ids."""
    if hasattr(trace, "columns"):                      # DataFrame
        allowed = set(trace["rule_id"]) if len(trace) else set()
    else:
        allowed = set(trace)
    cited = set(RULE_ID_RE.findall(text))
    return sorted(cited - allowed), sorted(cited & allowed)


def explain_claim(row, trace, rules_by_id, audience="member", language="English", fallback=""):
    """Plain-language explanation grounded in the trace. Returns (text, source, warnings)."""
    who = {"member": "the insured member (no jargon, warm and clear)",
           "provider": "the billing team at the provider (precise; say what to fix or resubmit)"}[audience]
    prompt = (f"Write a short explanation of an insurance claim decision for {who}.\n"
              f"Write it in {language}.\n"
              "Write as structured paragraphs: each paragraph opens with a bolded lead-in phrase "
              "ending in a period, e.g. **The decision.**, followed by 1-3 sentences. Three "
              "paragraphs: **The decision.** (paid / denied / held, simply), **Why.** (the rules "
              "that drove it, with rule IDs in brackets), **What happens next.** (payment, "
              "resubmission, or review in general terms). Paragraphs separated by blank lines. No "
              "headings, no numbered lists, no bullet lists, no all-caps.\n"
              "Rules:\n- Use ONLY the facts below. Do not invent reasons, amounts, dates or rules.\n"
              "- Name the rule ID in brackets after each reason, e.g. [COV-2.1].\n"
              "- At most 120 words.\n\nFACTS:\n" + _facts(row, trace, rules_by_id))
    try:
        text, src = ask(prompt)
    except AIError as e:
        note = "" if language == "English" else " (offline mode is English only)"
        return fallback, ("offline" if str(e) == "offline" else f"offline (AI failed: {e})") + note, []
    bad, _ = grounding_check(text, trace)
    warnings = [f"Mentions rule(s) not in the trace: {', '.join(bad)}"] if bad else []
    return text.strip(), src, warnings


# ====================================================================== 3. FWA case notes
def fwa_case_note(row, trace, rules_by_id, provider_stats):
    facts = _facts(row, trace, rules_by_id) + (
        f"\nFWA score: {row['fwa_score']:.2f}. FWA signals: {row['fwa_reasons']}.\n"
        f"Provider {row['provider_id']}: {provider_stats}")
    prompt = ("You are helping a fraud, waste and abuse reviewer at a health insurer.\n"
              "Write a short case note as structured paragraphs. Format: each paragraph opens with "
              "a bolded lead-in phrase ending in a period, e.g. **What stands out.**, followed by "
              "1-3 sentences. Three paragraphs: **What stands out.** (the signal and why the "
              "detector caught it), **What to check.** (concrete checks), **Keep in mind.** "
              "(possible innocent explanations). Paragraphs separated by blank lines. No headings, "
              "no numbered lists, no bullet lists, no all-caps.\n"
              "Rules: use ONLY the facts below; name rule IDs in brackets where relevant; do not "
              "accuse anyone; a flag is a prompt for review, not a finding. At most 120 words.\n\n"
              "FACTS:\n" + facts)
    try:
        text, src = ask(prompt)
    except AIError as e:
        sig = row["fwa_reasons"] or "no signal text"
        text = (f"**What stands out.** This claim was flagged ({str(sig).replace('|', ', ')}) — "
                "the pattern is what caught the detector's eye.\n\n"
                "**What to check.** Whether the medical records support each billed service and "
                "date, and whether the same pattern shows up across this provider's other "
                f"patients ({provider_stats}).\n\n"
                "**Keep in mind.** A legitimate treatment plan or a data entry error can look "
                "exactly like this.")
        return text, ("offline" if str(e) == "offline" else f"offline (AI failed: {e})"), []
    bad, _ = grounding_check(text, trace)
    return text.strip(), src, ([f"Mentions rule(s) not in the trace: {', '.join(bad)}"] if bad else [])


# ====================================================================== 4. conflict + scenario narration
def _rule_yaml(r):
    if r is None:
        return ""
    r = {k: v for k, v in r.items() if k not in ("source_file", "fwa", "family")}
    return yaml.safe_dump(r, sort_keys=False, allow_unicode=True)


# What to change in the losing rule, by conflict type (used by the offline fallback).
_FIX_BY_TYPE = {
    "redundant": "Retire the duplicate rule (keep the live one, retire the proposal).",
    "limit_clash": "Align the losing rule's limit with the winner's, or narrow its plans/codes so they no longer overlap.",
    "unit_cap_clash": "Align the losing rule's unit cap with the winner's, or narrow its scope so they no longer overlap.",
    "version_overlap": "Retire the older version, or give it a non-overlapping effective window.",
    "exclusion_shadows_rule": "Narrow the losing rule's scope, dates or plans so the winner's terms still apply.",
    "orphan_exception": "Retire the exception, or add the base edit it refers to.",
}


def explain_conflict(conflict, rule_a, rule_b, precedence):
    """conflict dict + both rule dicts (rule_b may be None) + precedence list
    -> (text, source, warnings)."""
    ids = {conflict.get("rule_a")} | ({conflict.get("rule_b")} if conflict.get("rule_b") else set())
    prec = "\n".join(f"{p['id']}: {p['rule']}" for p in precedence)
    facts = (f"Conflict type: {conflict.get('type')}\nDetail: {conflict.get('detail')}\n"
             f"Detector winner: {conflict.get('winner') or 'none'}\n"
             f"Winning criterion: {conflict.get('criterion')}\nWhy: {conflict.get('why')}\n\n"
             f"Rule {conflict.get('rule_a')}:\n```yaml\n{_rule_yaml(rule_a)}```\n")
    if rule_b is not None:
        facts += f"\nRule {conflict.get('rule_b')}:\n```yaml\n{_rule_yaml(rule_b)}```\n"
    else:
        facts += ("\nThere is no second rule: this is an orphan exception with no base rule to "
                  "conflict with.\n")
    prompt = ("You explain rule conflicts in a claims policy engine to a policy analyst.\n"
              "Write as structured paragraphs. Format: each paragraph opens with a bolded lead-in "
              "phrase ending in a period, e.g. **The clash.**, followed by 1-3 sentences. Three "
              "paragraphs: **The clash.** (why the two rules conflict), **The winner.** (which "
              "rule takes precedence and per which criterion, quoting the criterion id, e.g. P3), "
              "**The fix.** (one concrete change to the losing rule). Paragraphs separated by "
              "blank lines. No headings, no numbered lists, no bullets, no all-caps.\n"
              "Rules: use ONLY the facts below; the only rule IDs that exist are the ones in the "
              "conflict — name them in brackets, e.g. [COV-2.1]; at most 120 words.\n\n"
              "PRECEDENCE POLICY:\n" + prec + "\n\nFACTS:\n" + facts)
    try:
        text, src = ask(prompt)
    except AIError as e:
        w = conflict.get("winner") or "no automatic winner — a person must decide"
        fix = _FIX_BY_TYPE.get(conflict.get("type"),
                               "Narrow the losing rule's scope, dates or plans so the clash goes away.")
        text = (f"**The clash.** {conflict.get('type')} — {conflict.get('detail')}\n\n"
                f"**The winner.** {w} ({conflict.get('criterion')}: {conflict.get('why')})\n\n"
                f"**The fix.** {fix}")
        return text, ("offline" if str(e) == "offline" else f"offline (AI failed: {e})"), []
    bad, _ = grounding_check(text, ids - {None})
    return text.strip(), src, ([f"Mentions rule(s) not in the conflict: {', '.join(bad)}"] if bad else [])


def explain_conflicts_summary(conflicts, precedence):
    """All conflicts at once -> (text, source, warnings)."""
    ids = {c.get("rule_a") for c in conflicts} | {c.get("rule_b") for c in conflicts}
    ids.discard(None)
    prec = "\n".join(f"{p['id']}: {p['rule']}" for p in precedence)
    lines = [f"- {c.get('rule_a')} vs {c.get('rule_b') or '—'} ({c.get('type')}): {c.get('detail')} — "
             f"winner {c.get('winner') or 'none'} ({c.get('criterion')})"
             for c in conflicts]
    prompt = ("You summarise the rule conflicts of a claims policy engine for a policy analyst.\n"
              "Group them by what should be done (retire a rule, align a limit, narrow a scope), "
              "and say which precedence criteria settled them. Use ONLY the facts below; name rule "
              "IDs in brackets; at most 180 words. Plain text.\n\n"
              "PRECEDENCE POLICY:\n" + prec + "\n\nCONFLICTS:\n" + "\n".join(lines) + "\n")
    try:
        text, src = ask(prompt)
    except AIError as e:
        by_type = {}
        for c in conflicts:
            by_type.setdefault(c.get("type"), []).append(c)
        parts = [f"{t}: {len(cs)} ({', '.join(str(c.get('rule_a')) for c in cs)})"
                 for t, cs in by_type.items()]
        text = ("Conflict types: " + "; ".join(parts) + ".\n"
                "Winners were decided by the precedence policy; see the table for each clash and "
                "the suggested fix per conflict.")
        return text, ("offline" if str(e) == "offline" else f"offline (AI failed: {e})"), []
    bad, _ = grounding_check(text, ids)
    return text.strip(), src, ([f"Mentions rule(s) not in the conflicts: {', '.join(bad)}"] if bad else [])


def narrate_scenario(diff, changed_rule_ids):
    """What-if diff dict + changed rule ids -> stakeholder briefing (text, source, warnings)."""
    allowed = set(changed_rule_ids or [])
    for s in (diff.get("superseded") or []):
        allowed.add(s.get("winner")); allowed.add(s.get("superseded"))
    allowed.discard(None)
    before = " / ".join(f"{k}: {v}" for k, v in (diff.get("before") or {}).items())
    after = " / ".join(f"{k}: {v}" for k, v in (diff.get("after") or {}).items())
    moves = "; ".join(f"{m.get('decision_before')}->{m.get('decision_after')}: {m.get('claims')}"
                      for m in (diff.get("moves") or [])[:6])
    sup = "; ".join(f"{s.get('winner')} replaces {s.get('superseded')} for {s.get('for')}"
                    for s in (diff.get("superseded") or []))
    examples = "\n".join(f"- {c.get('claim_id')}: {c.get('decision_before')} -> {c.get('decision_after')}, "
                         f"plan pays {c.get('plan_paid_amt_before')} -> {c.get('plan_paid_amt_after')}"
                         for c in (diff.get("changed_claims") or [])[:10])
    facts = (f"Rules changed: {', '.join(sorted(allowed)) if allowed else '(selected rules)'}\n"
             f"Claims that change: {diff.get('claims_changed')}\n"
             f"Members affected: {diff.get('members_affected')}\n"
             f"Plan cost change: {diff.get('plan_cost_delta')}\n"
             f"Decisions before: {before}\nDecisions after: {after}\n"
             f"Moves: {moves or 'none'}\n"
             f"Conflicts in the new rule set: {diff.get('conflicts')}\n"
             f"Superseded at run time: {sup or 'none'}\n"
             f"Ran on: {diff.get('ran_on')} in {diff.get('seconds')}s\n"
             f"Example claims:\n{examples or '- none'}")
    prompt = ("Write a short stakeholder briefing about a what-if simulation of a claim rule "
              "change, as structured paragraphs. Format: each paragraph opens with a bolded "
              "lead-in phrase ending in a period, e.g. **What changed.**, followed by 1-3 "
              "sentences. Four paragraphs: **What changed.** (name the rules in brackets), "
              "**Who is affected.** (which claims move between which decisions), **Cost impact.** "
              "(increases or decreases, with the number), **Before go-live.** (conflicts, "
              "supersessions, what to check). Paragraphs separated by blank lines. No headings, "
              "no numbered lists, no bullet lists, no all-caps.\n"
              "Rules: use ONLY the numbers below; never invent a rule ID — the only rule ids in "
              "this scenario are the listed ones; at most 150 words.\n\nFACTS:\n" + facts)
    try:
        text, src = ask(prompt)
    except AIError as e:
        delta = diff.get("plan_cost_delta") or 0
        text = (f"**What changed.** We simulated switching "
                f"{', '.join(sorted(allowed)) if allowed else 'the selected rules'} and re-ran "
                f"every claim.\n\n"
                f"**Who is affected.** {diff.get('members_affected')} members across "
                f"{diff.get('claims_changed')} claims would see a different outcome — the biggest "
                f"moves: {moves or 'no claims change decision'}.\n\n"
                f"**Cost impact.** Plan cost {'decreases' if delta < 0 else 'increases'} by "
                f"{abs(delta):,.2f} as a result.\n\n"
                f"**Before go-live.** The new rule set shows {diff.get('conflicts')} conflicts"
                + (f" and these supersessions: {sup}" if sup else " and no rules were superseded")
                + " — worth re-checking against production before go-live.")
        return text, ("offline" if str(e) == "offline" else f"offline (AI failed: {e})"), []
    bad, _ = grounding_check(text, allowed)
    return text.strip(), src, ([f"Mentions rule(s) not in the scenario: {', '.join(bad)}"] if bad else [])


# ====================================================================== 5. shadow review
def _rulebook_text(rules):
    """Compact YAML of the rule book for the shadow-review prompt."""
    out = []
    for r in rules:
        out.append(yaml.safe_dump({
            "id": r.get("id"), "clause": r.get("clause"), "kind": r.get("kind"),
            "outcome": r.get("outcome", "flag"), "priority": r.get("priority"),
            "params": r.get("params", {}),
        }, sort_keys=False, allow_unicode=True))
    return "\n".join(out)


def shadow_decide(claim_facts, rules_text, allowed_ids):
    """Independent AI reading: decide one claim from the rule book alone.
    Returns (decision, reason, cited_rules, source, warnings)."""
    prompt = ("You are an independent claims adjudicator at a health insurer, reading the same "
              "rule book the claims engine uses. Given the claim facts below, state what you would "
              "decide: pay, deny, or pend.\n"
              "Return ONLY JSON, no other text:\n"
              '{"decision": "pay" | "deny" | "pend", "reason": "one sentence citing the rule IDs '
              'you applied", "rules": ["COV-2.1", ...]}\n'
              "Use ONLY the facts and the rules below. If the facts are insufficient to decide, "
              "pend. Do not invent rule IDs.\n"
              "Semantics: an edit rule with outcome 'deny_line' makes only that service line "
              "unpaid — the rest of the claim pays, so the claim decision is pay. But if ALL "
              "billed service lines are denied by deny_line edits, the claim decision is deny. "
              "Only 'deny' or 'reject' outcomes deny the whole claim directly.\n\nRULE BOOK:\n```yaml\n"
              + rules_text
              + "```\n\nCLAIM FACTS:\n" + claim_facts)
    text, src = ask(prompt)
    # robust parse: strip code fences, try the outermost {...}, fall back to regex
    body = re.sub(r"```(?:json)?", "", text)
    data = None
    m = re.search(r"\{.*\}", body, re.S)
    if m:
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            data = None
    if data is None:
        dm = re.search(r'"decision"\s*:\s*"([a-z]+)"', body, re.I)
        rm = re.search(r'"reason"\s*:\s*"([^"]*)"', body, re.S)
        if dm:
            data = {"decision": dm.group(1), "reason": rm.group(1) if rm else "",
                    "rules": re.findall(r'"([A-Z]{3,4}-[\d.]+)"', body)}
    if data is None:
        raise AIError(f"Could not read the AI's answer: {text[:200]}")
    raw = str(data.get("decision", "")).strip().lower()
    decision = {"pay": "pay", "approve": "pay", "paid": "pay", "allow": "pay",
                "deny": "deny", "denied": "deny", "reject": "deny", "rejected": "deny",
                "pend": "pend", "pended": "pend", "review": "pend"}.get(raw)
    if decision is None:
        raise AIError(f"Unreadable decision '{raw}'")
    reason = str(data.get("reason", "")).strip()
    cited = [str(x) for x in (data.get("rules") or [])]
    bad, _ = grounding_check(reason, set(allowed_ids))
    warnings = [f"AI cited rule(s) not in the rule book: {', '.join(bad)}"] if bad else []
    return decision, reason, cited, src, warnings


# ====================================================================== 6. member brief
def member_brief(profile):
    """Member profile dict -> grounded plain-English brief (text, source, warnings).
    Describes the member's conditions and claim history; never diagnoses."""
    conditions = ", ".join(profile.get("conditions") or []) or "no flagged conditions"
    tl = "; ".join(f"{t['year']}: {', '.join(t['conditions']) if t['conditions'] else 'none'}"
                   for t in (profile.get("timeline") or []))
    dx = "; ".join(f"{d['dx_code']} ({d['count']} claims)" for d in (profile.get("top_dx") or [])[:6])
    dec = {}
    for c in (profile.get("claims") or []):
        dec[c.get("decision")] = dec.get(c.get("decision"), 0) + 1
    decisions = ", ".join(f"{k}: {v}" for k, v in sorted(dec.items(), key=lambda x: -x[1])) or "none"
    rules = ", ".join(sorted(set(profile.get("rule_ids") or []))) or "none"
    facts = (
        f"Member {profile.get('member_id')}, plan {profile.get('plan')}, "
        f"{profile.get('sex') or 'sex unknown'}, age {profile.get('age') or 'unknown'}"
        + (f", deceased {profile.get('died')}" if profile.get("died") else "") + "\n"
        + f"Conditions (latest year): {conditions}\n"
        + f"Condition timeline: {tl or 'n/a'}\n"
        + f"Total claims: {profile.get('total_claims')}, billed {profile.get('billed')}, "
        f"plan paid {profile.get('plan_paid')}, providers seen {profile.get('providers')}\n"
        + f"Decisions: {decisions}\n"
        + f"Claims flagged for FWA review: {profile.get('fwa_flags')}\n"
        + f"Rules that fired: {rules}\n"
        + f"Top diagnoses: {dx or 'none recorded'}")
    prompt = ("Write a short member profile for a claims/care reviewer, as structured "
              "paragraphs. Format: each paragraph opens with a bolded lead-in phrase ending in a "
              "period, e.g. **Conditions.**, followed by 1-3 sentences. Three or four paragraphs: "
              "**Conditions.**, **Utilization.** (claims, spend, providers), **Decisions.** (how "
              "their claims were decided), **Worth noting.** (flags, anything a reviewer should "
              "be aware of). Paragraphs separated by blank lines. No headings, no numbered lists, "
              "no bullet lists, no all-caps.\n"
              "Rules: use ONLY the facts below; describe, do not diagnose or give medical advice; "
              "do not accuse anyone of fraud — a flag is a prompt for review; name rule IDs in "
              "brackets where relevant; at most 120 words.\n\n"
              "FACTS:\n" + facts)
    try:
        text, src = ask(prompt)
    except AIError as e:
        flags = profile.get("fwa_flags") or 0
        text = (f"**Conditions.** {conditions}.\n\n"
                f"**Utilization.** {profile.get('total_claims')} claims billed at "
                f"{profile.get('billed')}, {profile.get('plan_paid')} paid by the plan, across "
                f"{profile.get('providers')} providers. Top diagnoses: {dx or 'none recorded'}.\n\n"
                f"**Decisions.** {decisions}.\n\n"
                f"**Worth noting.** {flags} claim(s) flagged for review — worth a look before "
                "deciding anything new.")
        return text, ("offline" if str(e) == "offline" else f"offline (AI failed: {e})"), []
    bad, _ = grounding_check(text, set(profile.get("rule_ids") or []))
    return text.strip(), src, ([f"Mentions rule(s) not in this member's claims: {', '.join(bad)}"] if bad else [])
