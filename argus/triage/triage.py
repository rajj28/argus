"""Verdict engine: deterministic rules first, a citation-verified LLM judge only for ambiguous runs.

Asymmetric risk: calling an intended change a bug costs one human review; calling a bug an intended
change ships a regression. So INTENDED_CHANGE / FEATURE_REMOVED must be justified by a verbatim quote
from the changelog / release notes, otherwise we downgrade to NEEDS_REVIEW.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

from argus.llm import prompts as P
from argus.models import Observation, TestResult, TestSpec, Verdict

HARD_KINDS = {"page_error", "network_error", "console_error"}
STRUCTURAL_KINDS = {"step_reordered", "step_added", "step_missing"}
SEVERITY = ["PASS", "COSMETIC_DRIFT", "INTENDED_CHANGE", "FEATURE_REMOVED", "NEEDS_REVIEW", "INFRA", "BUG"]


def _norm(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").lower())
    s = re.sub(r"[\"'`“”‘’]", "", s)
    return s.strip()


def _stable(s: str) -> str:
    """Remove run-specific noise (ids, numbers, unique suffixes) so signatures repeat across runs."""
    return re.sub(r"[0-9a-f]{6,}|\d+", "#", _norm(s))


def all_observations(result: TestResult) -> list[Observation]:
    obs = [o for s in result.steps for o in s.observations]
    return obs + list(result.observations)


def deviation_signature(test_id: str, observations: list[Observation]) -> str:
    parts = sorted(f"{o.kind}|{o.step_id or ''}|{_stable(o.detail)[:80]}"
                   for o in observations if o.kind != "locator_healed")
    return hashlib.sha1((test_id + "\n" + "\n".join(parts)).encode("utf-8")).hexdigest()[:16]


def _plain(s: str) -> str:
    """Normalise away markdown/typography so a quote matches regardless of **bold**, `code`, dashes."""
    s = re.sub(r"[*_`#>]+", "", s or "")
    s = s.replace("→", "->").replace("—", "-").replace("–", "-")
    return _norm(s)


def verify_quotes(quotes: list[str], changelog: str) -> list[str]:
    """Keep only quotes that (near-)verbatim occur in the changelog (anti-hallucination guard)."""
    from rapidfuzz import fuzz
    hay = _plain(changelog)
    ok = []
    for q in quotes or []:
        nq = _plain(q).strip(" .")
        if len(nq) >= 12 and (nq in hay or fuzz.partial_ratio(nq, hay) >= 92):
            ok.append(q)
    return ok


def _rule_mentioned(rule_ref: str, changelog: str) -> bool:
    return bool(rule_ref) and re.search(rf"\b{re.escape(rule_ref)}\b", changelog or "") is not None


def _fmt_steps(result: TestResult) -> str:
    lines = []
    for i, s in enumerate(result.steps, 1):
        tier = f" T{s.tier}" if s.tier is not None else ""
        lines.append(f"{i}. [{s.status}{tier}] {s.intent}")
    return "\n".join(lines) or "(no steps executed)"


def _fmt_obs(obs: list[Observation]) -> str:
    out = []
    for i, o in enumerate(obs, 1):
        conf = f" conf={o.confidence:.2f}" if o.confidence is not None else ""
        out.append(f"obs{i} {o.kind} (step {o.step_id or '-'}){conf}: {o.detail[:220]}")
    return "\n".join(out) or "(none)"


def _fmt_oracles(test: TestSpec, obs: list[Observation]) -> str:
    failed = {o.evidence.get("oracle_index") for o in obs if o.kind == "assertion_failed"}
    lines = []
    for i, a in enumerate(test.oracles):
        mark = "FAIL" if i in failed else "pass"
        rule = f" [{a.rule_ref}]" if a.rule_ref else ""
        lines.append(f"[{mark}]{rule} {a.description or a.kind} {a.params}")
    return "\n".join(lines) or "(none)"


def _verdict(category: str, confidence: float, rationale: str, decided_by: str = "rules",
             evidence: Optional[list[str]] = None, quotes: Optional[list[str]] = None) -> Verdict:
    action = {
        "PASS": "none", "COSMETIC_DRIFT": "test_healed", "INTENDED_CHANGE": "test_updated",
        "FEATURE_REMOVED": "test_retired", "BUG": "bug_reported", "NEEDS_REVIEW": "review_requested",
        "INFRA": "none",
    }[category]
    return Verdict(category=category, confidence=round(confidence, 2), rationale=rationale,
                   decided_by=decided_by, action=action, evidence_refs=evidence or [],
                   changelog_refs=quotes or [])


async def triage(test: TestSpec, result: TestResult, ctx: Any) -> Verdict:
    """Classify one test run. `ctx` provides .llm, .memory, .product_context, .changelog."""
    obs = all_observations(result)
    if not obs:
        return _verdict("PASS", 1.0, "Replayed exactly; every step and oracle held.")

    kinds = {o.kind for o in obs}
    hard = [o for o in obs if o.kind in HARD_KINDS]
    failed_asserts = [o for o in obs if o.kind == "assertion_failed"]
    rule_failures = [o for o in failed_asserts if o.evidence.get("rule_ref")]
    structural = [o for o in obs if o.kind in STRUCTURAL_KINDS]
    unreachable = "goal_unreachable" in kinds or "timeout" in kinds
    changelog = getattr(ctx, "changelog", "") or ""

    # 1) Pure locator drift: the business outcome held, only the way we found elements changed.
    if kinds <= {"locator_healed"}:
        conf = min((o.confidence or 0.9) for o in obs)
        tiers = sorted({o.tier for o in obs if o.tier is not None})
        return _verdict("COSMETIC_DRIFT", conf,
                        f"{len(obs)} element(s) moved/renamed and were re-identified (tiers {tiers}); "
                        "all expectations and business oracles still hold.")

    # 2) Hard failure signals on an unchanged flow: a regression, no model needed.
    if hard and not structural and not unreachable:
        kinds_s = ", ".join(sorted({o.kind for o in hard}))
        return _verdict("BUG", 0.92, f"Hard failure signals ({kinds_s}): {hard[0].detail[:160]}",
                        evidence=[o.kind for o in hard])

    # 3) Business-rule oracle violated on an unchanged flow, and the changelog does not touch that rule.
    if rule_failures and not structural and not unreachable:
        rules = {o.evidence.get("rule_ref") for o in rule_failures}
        if not any(_rule_mentioned(r, changelog) for r in rules):
            return _verdict("BUG", 0.9,
                            f"Business rule(s) {', '.join(sorted(rules))} violated: {rule_failures[0].detail[:160]}",
                            evidence=[f"rule {r}" for r in sorted(rules)])

    # 4) Memory: a human (or earlier verified judgement) already decided this exact deviation.
    sig = deviation_signature(test.id, obs)
    memory = getattr(ctx, "memory", None)
    if memory is not None:
        remembered = memory.get_decision(sig)
        if remembered is not None:
            v = remembered.model_copy()
            v.decided_by = "memory"
            v.rationale = f"(remembered decision) {v.rationale}"
            return v

    # 5) Ambiguous: ask the judge, with the product rules and changelog as the intent oracle.
    verdict = await _llm_judge(test, result, obs, ctx, changelog)
    if hard and verdict.category != "BUG":
        # Structural change may be intended, but crashes / 5xx are never acceptable.
        verdict = _verdict("BUG", 0.85, f"Flow changed ({verdict.category.lower()}), but hard failure signals "
                           f"remain: {hard[0].detail[:140]}", decided_by=verdict.decided_by,
                           quotes=verdict.changelog_refs)
    return verdict


async def _llm_judge(test: TestSpec, result: TestResult, obs: list[Observation], ctx: Any,
                     changelog: str) -> Verdict:
    llm = getattr(ctx, "llm", None)
    has_hard = any(o.kind in HARD_KINDS for o in obs)
    fallback_cat = "BUG" if has_hard else "NEEDS_REVIEW"
    if llm is None:
        return _verdict(fallback_cat, 0.5, "No LLM available to judge a behavioural change; needs a human.")
    user = P.TRIAGE_USER.format(
        name=test.name, goal=test.goal,
        rules=(getattr(ctx, "product_context", "") or "(none)")[:3500],
        changelog=(changelog or "(no changelog provided)")[:3500],
        steps=_fmt_steps(result), observations=_fmt_obs(obs), oracles=_fmt_oracles(test, obs))
    try:
        data = await llm.json(tier="smart", purpose="triage", system=P.TRIAGE_SYSTEM, user=user, max_tokens=500)
    except Exception as exc:  # LLMUnavailable, BudgetExceeded, provider errors
        return _verdict(fallback_cat, 0.5, f"Judge unavailable ({type(exc).__name__}); needs a human.")

    cat = str(data.get("category", "NEEDS_REVIEW")).upper().strip()
    if cat not in {"BUG", "INTENDED_CHANGE", "FEATURE_REMOVED", "NEEDS_REVIEW"}:
        cat = "NEEDS_REVIEW"
    try:
        conf = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    rationale = str(data.get("rationale", ""))[:500]
    quotes = verify_quotes([str(q) for q in data.get("changelog_refs", []) or []], changelog)
    evidence = [str(e) for e in data.get("evidence_refs", []) or []][:8]

    if cat == "INTENDED_CHANGE" and any(o.kind == "goal_unreachable" for o in obs):
        cat = "FEATURE_REMOVED"  # intended, but the journey no longer exists: retire rather than update
    if cat in {"INTENDED_CHANGE", "FEATURE_REMOVED"} and (conf < 0.7 or not quotes):
        why = "no verbatim changelog citation" if not quotes else f"confidence {conf:.2f} < 0.70"
        return _verdict("NEEDS_REVIEW", conf, f"Judge suggested {cat} but {why}. {rationale}",
                        decided_by="llm", evidence=evidence, quotes=quotes)
    return _verdict(cat, conf, rationale, decided_by="llm", evidence=evidence, quotes=quotes)
