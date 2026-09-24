"""Differential execution: same immutable intents, two live builds, compare canonical behaviour traces.

This is the answer to "bug or intended change?" when there are no release notes. Instead of asking a
model what the product intends, we measure how behaviour moved between a known-good build and a
candidate build, then apply evidence rules:

  REGRESSION        business outcome held on the baseline and fails on the candidate
                    (oracle failure, 5xx, uncaught error, lost side effect)
  UI_DRIFT          same outcome, same side effects; only how elements were found changed
  BEHAVIOR_CHANGE   same business outcome, but the path or side effects changed
  LIKELY_INTENDED   a BEHAVIOR_CHANGE that recurs consistently across independent tests
                    (a coherent redesign rarely breaks the same way twice by accident)
  JOURNEY_GONE      the journey's page is gone (4xx) with no crash: removed feature OR broken route;
                    without release notes Argus refuses to guess which
  BASELINE_BROKEN   the baseline itself failed, so nothing can be concluded
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Optional

from argus.config import Settings
from argus.models import RunReport, TestResult
from argus.runner.runner import MUTATING, run_suite
from argus.browser.snapshot import normalize_path

HARD = {"page_error", "network_error", "console_error"}


def canonical_trace(r: TestResult) -> dict[str, Any]:
    """Implementation-independent description of what a test run *did*."""
    calls, routes = [], []
    for s in r.steps:
        e = s.effects
        if e is None:
            continue
        for c in e.network:
            if c.method.upper() in MUTATING:
                calls.append(f"{c.method} {c.path} {c.status // 100}xx")
        if e.url_changed:
            route = normalize_path(e.url_after)
            if not routes or routes[-1] != route:
                routes.append(route)
    obs = [o for s in r.steps for o in s.observations] + list(r.observations)
    return {
        "outcome_ok": not any(o.kind in HARD | {"assertion_failed", "goal_unreachable"} for o in obs),
        "failed_oracles": [o.detail[:120] for o in obs if o.kind == "assertion_failed"],
        "hard_signals": [o.detail[:120] for o in obs if o.kind in HARD],
        "calls": calls,
        "routes": routes,
        "healed": sum(1 for s in r.steps if s.status == "healed"),
        "gone": any("start page" in o.detail and "returned HTTP 4" in o.detail for o in obs),
        "structural": sorted({o.kind for o in obs if o.kind in ("step_reordered", "step_added", "step_missing")}),
    }


def _delta_key(b: dict, c: dict) -> str:
    """A signature of *how* behaviour moved, used to spot the same change across independent tests."""
    gone = sorted(set(b["routes"]) - set(c["routes"]))
    new = sorted(set(c["routes"]) - set(b["routes"]))
    calls_gone = sorted(set(b["calls"]) - set(c["calls"]))
    calls_new = sorted(set(c["calls"]) - set(b["calls"]))
    return json.dumps([gone, new, calls_gone, calls_new, c["structural"]])


def classify(b: dict, c: dict) -> tuple[str, str]:
    if not b["outcome_ok"]:
        return "BASELINE_BROKEN", "the known-good build failed this journey; fix the baseline first"
    if not c["outcome_ok"] and c.get("gone") and not c["hard_signals"]:
        return "JOURNEY_GONE", ("the journey's page no longer exists (HTTP 4xx): a removed feature or a broken route - "
                                "release notes or a human must say which")
    if not c["outcome_ok"]:
        why = (c["hard_signals"] or c["failed_oracles"] or ["journey could not complete"])[0]
        lost = sorted(set(b["calls"]) - set(c["calls"]))
        return "REGRESSION", why + (f" | side effects lost: {lost}" if lost else "")
    if b["calls"] == c["calls"] and b["routes"] == c["routes"] and not c["structural"]:
        return ("UI_DRIFT", f"{c['healed']} element(s) re-identified; identical outcome and side effects") \
            if c["healed"] else ("NO_CHANGE", "identical behaviour")
    return "BEHAVIOR_CHANGE", (f"routes {b['routes']} -> {c['routes']}; calls {b['calls']} -> {c['calls']}; "
                               f"{', '.join(c['structural']) or 'same steps'}; business outcome held")


async def run_diff(settings: Settings, baseline_url: str, candidate_url: str, *,
                   test_ids: Optional[list[str]] = None, log: Callable[..., None] = print) -> dict:
    base_s = settings.model_copy(update={"base_url": baseline_url.rstrip("/")})
    cand_s = settings.model_copy(update={"base_url": candidate_url.rstrip("/")})
    base_s.build, cand_s.build = settings.build or "baseline", settings.build or "baseline"
    base_s.api_key = cand_s.api_key = settings.api_key
    log(f"[baseline]  {baseline_url}")
    base: RunReport = await run_suite(base_s, test_ids=test_ids, label="diff: baseline", update=False)
    log(f"[candidate] {candidate_url}")
    cand: RunReport = await run_suite(cand_s, test_ids=test_ids, label="diff: candidate", update=False)

    by_id = {r.test_id: r for r in cand.results}
    rows = []
    for rb in base.results:
        rc = by_id.get(rb.test_id)
        if rc is None:
            continue
        tb, tc = canonical_trace(rb), canonical_trace(rc)
        cls, why = classify(tb, tc)
        rows.append({"test": rb.test_id, "name": rb.test_name, "class": cls, "why": why,
                     "delta": _delta_key(tb, tc), "baseline": tb, "candidate": tc})
    # consistency across independent tests: the same behaviour delta seen >= 2 times reads as a redesign
    counts = Counter(r["delta"] for r in rows if r["class"] == "BEHAVIOR_CHANGE")
    for r in rows:
        if r["class"] == "BEHAVIOR_CHANGE" and counts[r["delta"]] >= 2:
            r["class"] = "LIKELY_INTENDED"
            r["why"] += f" | same change observed consistently in {counts[r['delta']]} independent tests"
    out = {"baseline": baseline_url, "candidate": candidate_url,
           "summary": dict(Counter(r["class"] for r in rows)), "tests": rows,
           "runs": {"baseline": base.run_id, "candidate": cand.run_id}}
    Path(settings.home).mkdir(parents=True, exist_ok=True)
    (Path(settings.home) / "diff.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out
