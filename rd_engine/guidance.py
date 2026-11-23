"""
guidance.py - Plain-language decision text for the app: the explanation builder,
appeal/next-steps hints and small formatting helpers. Ported from app.py when the
UI moved from Streamlit to FastAPI + React; no Streamlit imports.

Everything here is deterministic and built only from the decision trace - the
rules decide, this restates what the trace already says.
"""

# Appeal / next-steps for a denied claim (use case P026): the evidence or fix
# that would change the decision.
APPEAL_HINTS = {
    "COV-1.1": "Provide a valid eligibility (Part B) record for the service year and resubmit.",
    "COV-1.2": "The service date is after the recorded death date. Appeal only with corrected service dates.",
    "COV-1.3": "The provider was excluded on the service date. Appeal only if the exclusion date or the provider on the claim is wrong.",
    "COV-2.1": "The policy-year limit was reached by earlier services. Check the earlier claims; if one is wrong, correct it first - or reschedule in the next policy year.",
    "COV-2.2": "Provide clinical evidence of ESRD (or chronic kidney disease) on record for re-adjudication.",
    "COV-2.3": "Provide evidence of diabetes on record for the higher limit - or reschedule in the next policy year.",
    "COV-2.4": "Provide evidence of diabetes on record for the higher limit - or reschedule in the next policy year.",
    "COV-3.1": "The service fell inside the waiting period from the policy start date. Check the policy start date and the service date.",
    "EDIT-01": "The service was inside an inpatient stay. If the stay dates are wrong, correct the stay first and resubmit.",
    "EDIT-03": "If this is not a true duplicate, correct the claim dates, provider or amount and resubmit.",
    "EDIT-04": "If the earlier same-day claim is not the same service, correct one of the two claims.",
}

DECISIONS = ["paid", "partial", "pended", "denied", "rejected"]


def money(x):
    return f"{x:,.0f}"


def rule_label(r):
    return f"{r['id']} · {r['clause'][:80]}"


def plain_explanation(row, h, rules_by_id):
    """Plain-language explanation built only from the decision trace.
    Returns {"text": markdown} for react-markdown on the frontend."""
    act = h[h["suppressed_by"].isna() & (h["layer"] != "anomaly") & (h["outcome"] != "allow")]
    dec = row["decision"]
    parts = []
    if dec == "paid":
        parts.append("**Paid.** No coverage or edit rule stopped this claim.")
    elif dec == "rejected":
        r = act[act["outcome"] == "reject"].iloc[0]
        parts.append(f"**Rejected for correction.** {r['reason']} (rule {r['rule_id']}). "
                     "The provider needs to fix and resubmit the claim.")
    elif dec == "denied":
        claim_level = act[act["outcome"] == "deny"]
        if len(claim_level):
            r = claim_level.sort_values("priority", ascending=False).iloc[0]
            parts.append(f"**Denied.** {r['reason']} (rule {r['rule_id']}).")
        else:
            parts.append("**Denied.** Every service on the claim was refused:")
    elif dec == "pended":
        refs = act[act["outcome"] == "refer"].drop_duplicates("rule_id")
        parts.append("**Held for review.** " + " ".join(
            f"{r['reason']} (rule {r['rule_id']})." for _, r in refs.iterrows()))
    elif dec == "partial":
        parts.append(f"**Partly paid.** {row['denied_line_share']:.0%} of the services were refused:")

    lines = act[act["outcome"] == "deny_line"].drop_duplicates(["code", "rule_id"])
    if dec in ("partial", "denied") and len(lines):
        for _, r in lines.iterrows():
            parts.append(f"- **{r['code']}**: {r['reason']} (rule {r['rule_id']})")

    sup = h[h["suppressed_by"].notna()]
    for _, r in sup.drop_duplicates("rule_id").iterrows():
        parts.append(f"- Rule {r['rule_id']} would have refused {r['code']}, but exception "
                     f"{r['suppressed_by']} overrides it.")

    if row["fwa_score"] > 0:
        parts.append(f"**Fraud review:** score {row['fwa_score']:.2f}. {row['fwa_reasons']}.")
    if dec in ("paid", "partial"):
        parts.append(f"Plan pays **{money(row['plan_paid_amt'])}** of {money(row['allowed_amt'])} "
                     f"({row['plan']} plan, member coinsurance {row['coinsurance']:.0%}).")
    return {"text": "\n\n".join(parts)}


def appeal_guidance(row, rules_by_id):
    """Next-steps for a denied/rejected claim, or None."""
    if row["decision"] not in ("denied", "rejected"):
        return None
    if row["decision"] == "rejected":
        return {"title": "Rejected for correction",
                "body": "A data-quality rejection, not a coverage decision. "
                        "Fix the reported issue and resubmit; no appeal is needed."}
    rid = row["primary_rule"]
    hint = APPEAL_HINTS.get(rid) if isinstance(rid, str) else None
    if not hint:
        hint = "Appeal with documents showing this rule does not apply to the claim as submitted."
    reason = row.get("primary_reason")
    return {"title": "Denial reason",
            "body": f"{reason} (rule {rid}).\n\n**To appeal or fix:** {hint}"}
