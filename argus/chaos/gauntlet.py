"""The Gauntlet: a reproducible robustness benchmark for self-healing and bug-vs-change triage.

* Mutation trials: SkyOps' chaos engine applies seeded, behaviour-preserving refactors (ids, classes,
  test-ids, wrappers, order, synonyms, tags, layout). Every test must still pass; any BUG/NEEDS_REVIEW
  verdict is a false alarm.
* Bug trials: one real regression injected at a time (on top of a random mutation). It must be caught.
Tests are never updated during the gauntlet (update=False) so every trial starts from the same baseline.
"""
from __future__ import annotations

import json
import random
import urllib.request
from pathlib import Path
from typing import Any, Callable

from argus.config import Settings
from argus.runner.runner import run_suite

MUTATIONS = ["ids", "classes", "testids", "wrappers", "order", "text", "tags", "layout"]
BUGS = ["altitude_limit", "low_battery_assignable", "missing_mission", "settings_500", "logout_crash"]
OK = {"PASS", "COSMETIC_DRIFT"}


def _admin(base: str, path: str, body: dict) -> Any:
    req = urllib.request.Request(base.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"{}")


def _prepare(s: Settings, seed: int | None, bugs: list[str]) -> None:
    _admin(s.base_url, "/__admin/version", {"version": "1.0"})
    _admin(s.base_url, "/__admin/chaos", {"seed": seed, "mutations": MUTATIONS} if seed is not None else {"seed": None})
    _admin(s.base_url, "/__admin/bugs", {"bugs": bugs})
    _admin(s.base_url, "/__admin/reset", {})


def _trial_row(kind: str, seed: int, bug: str, report) -> dict:
    t = report.totals
    verdicts = {r.test_id: r.verdict.category for r in report.results}
    return {"kind": kind, "seed": seed, "bug": bug, "verdicts": verdicts,
            "passed": sum(v in OK for v in verdicts.values()), "tests": len(verdicts),
            "false_alarms": [k for k, v in verdicts.items() if v not in OK] if kind == "mutation" else [],
            "caught": any(v == "BUG" for v in verdicts.values()) if kind == "bug" else None,
            "bug_tests": [k for k, v in verdicts.items() if v == "BUG"],
            "healed": t.healed_steps, "replayed": t.replayed_steps, "tiers": t.tiers,
            "llm_calls": t.llm_calls, "duration_s": round(t.duration_ms / 1000, 1), "run_id": report.run_id}


async def run_gauntlet(s: Settings, trials: int = 6, bugs: bool = True, seed0: int = 101,
                       log: Callable[..., None] = print) -> dict:
    rows = []
    rng = random.Random(seed0)
    for i in range(trials):
        seed = seed0 + i
        _prepare(s, seed, [])
        rep = await run_suite(s, label=f"gauntlet mutation seed={seed}", update=False)
        row = _trial_row("mutation", seed, "", rep)
        rows.append(row)
        log(f"mutation seed {seed}: {row['passed']}/{row['tests']} pass, healed {row['healed']} steps, "
            f"false alarms {row['false_alarms'] or 0}, llm {row['llm_calls']}")
    if bugs:
        for bug in BUGS:
            seed = rng.randint(1, 10_000)
            _prepare(s, seed, [bug])
            rep = await run_suite(s, label=f"gauntlet bug={bug}", update=False)
            row = _trial_row("bug", seed, bug, rep)
            rows.append(row)
            log(f"bug {bug:<24} {'CAUGHT' if row['caught'] else 'MISSED'} by {row['bug_tests']} "
                f"(chaos seed {seed}), llm {row['llm_calls']}")
    _prepare(s, None, [])

    mut = [r for r in rows if r["kind"] == "mutation"]
    bug_rows = [r for r in rows if r["kind"] == "bug"]
    total_tests = sum(r["tests"] for r in mut) or 1
    summary = {
        "mutation_trials": len(mut),
        "heal_success_rate": round(sum(r["passed"] for r in mut) / total_tests, 3),
        "false_alarm_rate": round(sum(len(r["false_alarms"]) for r in mut) / total_tests, 3),
        "bug_trials": len(bug_rows),
        "bug_recall": round(sum(bool(r["caught"]) for r in bug_rows) / (len(bug_rows) or 1), 3),
        "avg_llm_calls_per_run": round(sum(r["llm_calls"] for r in rows) / (len(rows) or 1), 2),
        "steps_healed_total": sum(r["healed"] for r in rows),
    }
    out = {"summary": summary, "trials": rows}
    Path(s.home).mkdir(parents=True, exist_ok=True)
    (Path(s.home) / "gauntlet.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    log(f"\nGAUNTLET  heal success {summary['heal_success_rate']:.0%} | false alarms "
        f"{summary['false_alarm_rate']:.0%} | bug recall {summary['bug_recall']:.0%} | "
        f"avg LLM calls/run {summary['avg_llm_calls_per_run']}")
    return out
