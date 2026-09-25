"""Rule-boundary attack tests: one LLM call -> deterministic edge-case tests forever.

The model only ever supplies the *numbers* (what sits exactly at a limit and one step outside it, plus which
control they belong to). Everything else is deterministic: `case_to_spec` clones the base test's authored
steps, replaces the value of the single step whose target matches `field_find`, and re-derives the oracles, so
the cases stay reproducible (and free) on every later run.

`generate_boundary_tests` orchestrates: one `smart` call -> `record_baseline` on the live build for every
candidate -> keep only the ones whose own oracle actually holds -> Memory. A case that cannot be mapped to a
step of the base test, that does not execute end to end, or whose oracles are all false on the current build is
dropped rather than saved.

Public API:
    def step_field(step) -> dict[str, str]
    def test_digest(test) -> str
    def parse_cases(data) -> list[dict]
    def find_field_index(steps, find) -> int | None
    def positive_oracles(base) -> list[Assertion]
    def case_oracles(case, base) -> list[Assertion]
    def case_to_spec(case, base) -> TestSpec | None
    async def propose(settings, base, llm) -> list[dict]
    async def generate_boundary_tests(settings, base_test_id, log=print) -> list[TestSpec]
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from playwright.async_api import async_playwright

from argus.config import Settings
from argus.llm import prompts as P
from argus.memory.store import Memory
from argus.models import Assertion, Step, TestChange, TestSpec
from argus.runner.author import slug
from argus.runner.runner import RunContext, ensure_auth, record_baseline

MAX_CASES = 8
_FALLBACK_POST = {"method": "POST", "path": "/api/missions", "status_class": "2xx"}
_RULE_LINE = re.compile(r"^\s*[-*>|\s]*(R\d+\b.*?)\s*\|?\s*$")
_REJECTION_RE = re.compile(
    r"\b(must (?:not|be|at)|at (?:most|least)|required|invalid|error|cannot|can't|"
    r"exceed|exceeds|forbidden|unauthorized|duplicate|below|above|too (?:high|low)|"
    r"out of range|not allowed|rejected|failed|no longer)\b",
    re.I,
)
_FENCE_RE = re.compile(r"```(?:json)?|```", re.I)


# --------------------------------------------------------------------------------------
# semantic description of a test (what the model reads)
# --------------------------------------------------------------------------------------

def _norm(text: str) -> str:
    """Lower-case, collapse whitespace - the comparison form of any human label."""
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def step_field(step: Step) -> dict[str, str]:
    """Name a step's target the way a human would: {role, name, context} + `alias` (id/name attr).

    This is both what the model is shown and what an incoming `field_find` is matched against, so the two
    can never drift apart.
    """
    fp = step.target
    if fp is None:
        return {}
    out = {"role": _norm(fp.role), "name": _norm(fp.label or fp.name or fp.text)}
    if typ := _norm(fp.attrs.get("type", "")):
        out["type"] = typ
    for key in ("name", "id", "data-testid", "aria-label", "placeholder"):
        if alias := _norm(fp.attrs.get(key, "")):
            out["alias"] = alias
            break
    if fp.context:
        out["context"] = _norm(fp.context)
    return out


def test_digest(test: TestSpec, max_chars: int = 4000) -> str:
    """Compact JSON view of a test: semantic steps and business oracles, never selectors."""
    data = {
        "id": test.id,
        "name": test.name,
        "goal": test.goal,
        "start_url": test.start_url,
        "requires_login": test.requires_login,
        "steps": [{"n": i, "intent": s.intent, "action": s.action,
                   "field": step_field(s) or None, "value": s.value}
                  for i, s in enumerate(test.steps, 1)],
        "oracles": [{"kind": a.kind, "params": a.params, "rule_ref": a.rule_ref} for a in test.oracles],
    }
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if len(text) > max_chars:
        text = text[: max(0, max_chars - 20)].rstrip() + "\n... (truncated)"
    return text


def _rules_excerpt(product: str, limit: int = 1600) -> str:
    """Only the `R<n>` lines of PRODUCT.md, so the model cannot drift into UI trivia.

    Handles both a markdown rules table (`| R1 | ... |`) and a bullet list (`- R1: ...`).
    """
    out: list[str] = []
    for line in (product or "").splitlines():
        m = _RULE_LINE.match(line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|") if c.strip()]
        if cells:
            out.append(" - ".join(cells))
    text = "\n".join(out) if out else (product or "")
    return text[:limit].strip() or "(no business rules found)"


# --------------------------------------------------------------------------------------
# the answer, normalised
# --------------------------------------------------------------------------------------

def _as_dict(data: Any) -> dict:
    """Accept a dict, or a (fenced / double-encoded) JSON string that holds one."""
    if isinstance(data, str):
        try:
            data = json.loads(_FENCE_RE.sub("", data).strip())
        except ValueError:
            return {}
    return data if isinstance(data, dict) else {}


def _clean_case(case: Any) -> Optional[dict]:
    """One validated case dict, or None when a field is missing/unusable."""
    if not isinstance(case, dict):
        return None
    expect = str(case.get("expect") or "").strip().lower()
    if expect not in ("accept", "reject"):
        return None
    value = case.get("value")
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    find = case.get("field_find")
    if not isinstance(find, dict):
        return None
    clean = {str(k).strip().lower(): str(v).strip()
             for k, v in find.items()
             if isinstance(v, (str, int, float)) and str(v).strip()}
    if not clean:
        return None
    return {"rule_ref": str(case.get("rule_ref") or "").strip() or None,
            "field_find": clean,
            "value": str(value).strip(),
            "expect": expect,
            "why": _norm(case.get("why") or "")[:140]}


def parse_cases(data: Any) -> list[dict]:
    """Normalise one model answer into at most `MAX_CASES` usable, de-duplicated cases."""
    out: list[dict] = []
    seen: set[tuple] = set()
    for raw in _as_dict(data).get("cases") or []:
        case = _clean_case(raw)
        if case is None:
            continue
        key = (case["expect"], case["value"], tuple(sorted(case["field_find"].items())))
        if key in seen:
            continue
        seen.add(key)
        out.append(case)
        if len(out) >= MAX_CASES:
            break
    return out


# --------------------------------------------------------------------------------------
# case -> spec (pure)
# --------------------------------------------------------------------------------------

def _misses(key: str, find: dict, field: dict) -> bool:
    """True when `find[key]` does not describe `field` (same semantics as the author suite)."""
    want = _norm(str(find.get(key) or ""))
    if not want:
        return False
    if key in ("role", "type"):
        return want != field.get(key, "")
    if key == "name":
        return want not in " ".join([field.get("name", ""), field.get("alias", "")])
    return want not in field.get("context", "")


def find_field_index(steps: list[Step], find: dict) -> Optional[int]:
    """Index of the step whose target matches every key of `find`; None when nothing matches.

    Matching is conjunctive and case-insensitive; the most specific (longest) label wins so that
    `{"name": "altitude"}` never lands on "Altitude limit" or on the mission name.
    """
    keys = [k for k in ("role", "type", "name", "context") if str((find or {}).get(k) or "").strip()]
    if not keys:
        return None
    hits = [(i, len(step_field(s).get("name", "")))
            for i, s in enumerate(steps)
            if step_field(s) and not any(_misses(k, find, step_field(s)) for k in keys)]
    return max(hits, key=lambda h: (h[1], -h[0]))[0] if hits else None


def positive_oracles(base: TestSpec) -> list[Assertion]:
    """The base test's oracles that describe a SUCCESSFUL outcome."""
    out: list[Assertion] = []
    for a in base.oracles:
        if a.kind == "network_absent":
            continue
        if a.kind == "text_visible" and _REJECTION_RE.search(str(a.params.get("text", ""))):
            continue
        out.append(a)
    return out


def _error_text(base: TestSpec, rule_ref: str | None) -> str:
    """The rule's error message, when the base test already asserts one."""
    pool = [a for a in base.oracles if a.kind == "text_visible"
            and _REJECTION_RE.search(str(a.params.get("text", "")))]
    if rule_ref:
        hit = next((a for a in pool if a.rule_ref == rule_ref), None)
        if hit is not None:
            return str(hit.params["text"])
    return str(pool[0].params["text"]) if pool else ""


def case_oracles(case: dict, base: TestSpec) -> list[Assertion]:
    """Oracles of one case: the rule's rejection message (else the absent POST) vs the base's positive ones."""
    rule = case.get("rule_ref")
    value = case.get("value", "")
    if case.get("expect") == "accept":
        return positive_oracles(base)
    if text := _error_text(base, rule):
        return [Assertion(kind="text_visible", params={"text": text},
                          description=f"{rule or 'Rule'}: {value} must be rejected with this message",
                          rule_ref=rule)]
    params = next((dict(a.params) for a in base.oracles
                   if a.kind == "network_absent" and a.params.get("path")), dict(_FALLBACK_POST))
    return [Assertion(kind="network_absent", params=params,
                      description=f"{rule or 'Rule'}: {value} must never reach the API", rule_ref=rule)]


def _tags(case: dict, base: TestSpec) -> list[str]:
    """`boundary` + the rule id; `negative` survives only on the cases that must be rejected."""
    out: list[str] = []
    for tag in list(base.tags) + ["boundary", case.get("rule_ref") or ""]:
        tag = str(tag).strip()
        if not tag or (tag == "negative" and case.get("expect") == "accept") or tag in out:
            continue
        out.append(tag)
    return out


def case_to_spec(case: dict, base: TestSpec) -> Optional[TestSpec]:
    """Clone the base test with one value swapped in and the oracles re-derived; None if unmappable."""
    idx = find_field_index(base.steps, case.get("field_find") or {})
    if idx is None:
        return None
    value = str(case.get("value") or "")
    steps = [s.model_copy(deep=True) for s in base.steps]
    old = steps[idx]
    intent = old.intent
    if old.value and old.value in intent:
        intent = intent.replace(old.value, value)
    steps[idx] = old.model_copy(update={"value": value,
                                        "intent": f"{intent} [boundary {case['expect']} {value}]".strip()})
    return TestSpec(
        id=f"{slug(base.id)}-{case['expect']}-{slug(value)}",
        name=f"{base.name} [{case['expect']} {value}]",
        goal=f"{base.goal} (boundary: {value} must be "
             f"{'accepted' if case['expect'] == 'accept' else 'rejected'})",
        tags=_tags(case, base), start_url=base.start_url, steps=steps,
        oracles=case_oracles(case, base), requires_login=base.requires_login, origin="generated",
    )


# --------------------------------------------------------------------------------------
# orchestrator
# --------------------------------------------------------------------------------------

async def propose(settings: Settings, base: TestSpec, llm) -> list[dict]:
    """The single `smart` call: business rules + one passing test -> at most 8 boundary cases."""
    data = await llm.json(
        tier="smart", purpose="boundary", system=P.BOUNDARY_SYSTEM, max_tokens=1600,
        user=P.BOUNDARY_USER.format(rules=_rules_excerpt(settings.product_context()),
                                    test=test_digest(base)))
    return parse_cases(data)


async def generate_boundary_tests(settings: Settings, base_test_id: str, log=print) -> list[TestSpec]:
    """Derive, baseline and persist the boundary cases of one existing test; returns the ones that hold."""
    memory = Memory(settings.home)
    try:
        base = memory.load_test(base_test_id)
    except (OSError, ValueError) as exc:
        raise LookupError(f"no test '{base_test_id}' in {settings.home}") from exc

    llm = None
    if settings.llm_enabled:
        from argus.llm.client import LLMClient
        try:
            llm = LLMClient(settings)
        except Exception as exc:
            log(f"[boundary] LLM unavailable: {type(exc).__name__}")
    if llm is None:
        log("[boundary] no LLM available: cannot invent the boundary values")
        return []

    try:
        cases = await propose(settings, base, llm)
    except Exception as exc:
        log(f"[boundary] LLM call failed ({type(exc).__name__}); no cases generated")
        return []
    if not cases:
        log("[boundary] the model proposed no usable case")
        return []

    candidates: list[tuple[dict, TestSpec]] = []
    for case in cases:
        spec = case_to_spec(case, base)
        if spec is None:
            log(f"[boundary] drop {case['expect']} {case['value']}: "
                f"no step of '{base.id}' matches {case['field_find']}")
        elif any(s.id == spec.id for _, s in candidates):
            log(f"[boundary] drop {case['expect']} {case['value']}: already covered as '{spec.id}'")
        else:
            candidates.append((case, spec))
    log(f"[boundary] {len(cases)} case(s) proposed, {len(candidates)} mapped to a step of '{base.id}'")

    kept: list[TestSpec] = []
    run_id, run_dir = memory.new_run_dir()
    ctx = RunContext(settings, memory, llm, run_id, run_dir)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=settings.headless)
        try:
            try:
                auth = await ensure_auth(browser, ctx) if any(s.requires_login for _, s in candidates) else None
            except Exception as exc:
                log(f"[boundary] could not sign in ({type(exc).__name__}); baselining unauthenticated")
                auth = None
            for case, spec in candidates:
                wanted = list(spec.oracles)
                try:
                    frozen = await record_baseline(spec, ctx, browser, auth)
                except Exception as exc:
                    log(f"[boundary] drop '{spec.id}': {type(exc).__name__}")
                    continue
                if frozen is None:
                    log(f"[boundary] drop '{spec.id}': does not execute end to end")
                    continue
                if wanted and not any(a in frozen.oracles for a in wanted):
                    log(f"[boundary] drop '{spec.id}': none of its oracles hold on this build")
                    continue
                saved = memory.save_test(
                    frozen,
                    TestChange(version=1, kind="created",
                               summary=f"Rule-boundary case from {base.id}: {case.get('rule_ref') or '?'} "
                                       f"{case['value']} must be "
                                       f"{'accepted' if case['expect'] == 'accept' else 'rejected'}"
                                       + (f" - {case['why']}" if case.get("why") else "")),
                    build=settings.build)
                kept.append(saved)
                log(f"[boundary] keep '{saved.id}': {len(saved.steps)} steps, "
                    f"oracles {[o.kind for o in saved.oracles]}")
                if not wanted:
                    log(f"[boundary]   note: '{base.id}' exposes no positive oracle, so the frozen "
                        "step contract is this case's regression net")
        finally:
            await browser.close()
    return kept
