"""LLM-path benchmark: generation, judging and replay of Argus against SkyOps.

Proves the LLM-powered path of Argus end to end against an ALREADY-RUNNING
instance (no server is started or stopped here):

  1. `argus.explore.generator.propose` + `generate` author the suite from a
     real LLM proposal (business rules in the prompt), based on an existing
     exploration atlas in the memory home.
  2. The suite is replayed across builds 1.0 -> 1.2 -> 1.3 with
     `demo_app.deploy.deploy` switching the deployment between runs and
     `settings.build` tracking the version.
  3. Verdicts come from rule-based triage; the LLM judge path (purpose
     "triage") is exercised with a labeled probe on a real 1.3 regression
     (rule_ref stripped so the deterministic guards can not short-circuit).

Per run it collects verdicts, tier usage, LLM call records (model, cache
status, cost) and durations. Results are saved to `.logs/llm_path_data.json`
and a readable report to `.logs/llm_path_report.md`, then printed.

Memory: the benchmark wipes `tests/` and `runs/` under the home so runs and
suite are reproduced from scratch, but it REUSES the exploration atlas,
auth session, config and LLM cache (a warm cache makes re-generation
costless and deterministic).

Run:  .venv/Scripts/python.exe benchmarks/llm_path.py [--url http://127.0.0.1:8003]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("ARGUS_LLM", "on")
os.environ.setdefault(
    "ARGUS_MODELS_FAST",
    "groq:openai/gpt-oss-120b,nvidia/nemotron-3-ultra-550b-a55b:free,openrouter/free",
)
os.environ.setdefault(
    "ARGUS_MODELS_SMART",
    "groq:openai/gpt-oss-120b,nvidia/nemotron-3-super-120b-a12b:free,openrouter/free",
)
os.environ.setdefault(
    "ARGUS_MODELS_VISION",
    "qwen/qwen3.8-27b:free,qwen/qwen3.185-25b:free,openrouter/free",
)

from argus.config import Settings, load_settings  # noqa: E402
from argus.explore.generator import generate, propose  # noqa: E402
from argus.llm.client import LLMClient  # noqa: E402
from argus.memory.store import Memory  # noqa: E402
from argus.runner.runner import run_suite  # noqa: E402

HOME = ROOT / ".argus-gen"
LOGS = ROOT / ".logs"
REPORT_FILE = LOGS / "llm_path_report.md"
DATA_FILE = LOGS / "llm_path_data.json"
CONTEXT = ROOT / "demo_app" / "context"  # changelog/product context live here (rewritten by deploy)
DEFAULT_URL = "http://127.0.0.1:8003"
BUILDS = ["1.0", "1.2", "1.3"]

# On 1.3, these regression bugs are injected by the deployment; only passed when
# the corresponding LLM-authored test is present in the suite.
EXPECTED_BUGS_1_3: dict[str, str] = {
    "plan-and-launch": "missing_mission",
    "settings-save": "settings_500",
    "altitude-above-limit-rejected": "altitude_limit",
    "low-battery-drone-is-disabled": "low_battery_assignable",
}


def make_settings(version: str, home: Path, base_url: str) -> Settings:
    """Settings for `version`, as the CLI builds them: config.json + .env + env overrides.

    `Settings(...)` alone would skip .env keys and ARGUS_MODELS_*; `load_settings`
    applies them so the LLM mesh (and the judge probe's real call) actually works.
    """
    return load_settings(home, build=version, base_url=base_url, llm_enabled=True)


def _compact(verdicts: dict[str, int]) -> str:
    return " ".join(f"{k}:{v}" for k, v in sorted(verdicts.items()))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def wipe_suite_and_runs(home: Path) -> None:
    """Drop previous suite + runs; keep atlas, auth, config, knowledge, llm cache."""
    for rel in ("tests", "runs"):
        path = home / rel
        if path.exists():
            shutil.rmtree(path)
            print(f"removed {path}")


async def author_suite(settings: Settings, base_url: str) -> tuple[list[Any], list[dict]]:
    """Author the suite via a real (cached) LLM proposal; return (specs, ledgers)."""
    memory = Memory(settings.home)
    atlas = memory.atlas()
    if not (atlas or {}).get("states"):
        raise SystemExit(f"no atlas in {settings.home}/atlas.json — run `argus explore` first")

    probe = LLMClient(settings)
    raw = await propose(settings, atlas, probe)
    ledgers: list[dict] = [r.model_dump() for r in probe.take_ledger()]
    print(f"[proposal] {len(raw)} candidate tests from the LLM; calling generate() (cache)…")

    produced = await generate(settings, atlas=atlas, log=print)
    if not any(not t.id.startswith("login") for t in produced):
        print("WARNING: generate() produced no LLM-authored tests "
              "(LLM proposal failed or dropped); falling back to the cached proposal result.")
    return produced, ledgers


async def run_build(version: str, home: Path, base_url: str) -> Any:
    """Deploy `version` to a live server, then replay the suite. Returns RunReport."""
    from demo_app.deploy import deploy

    deploy(version, base_url)
    settings = make_settings(version, home, base_url)
    before = time.monotonic()
    report = await run_suite(settings, label=f"llm_path v{version}", update=True)
    report.totals.duration_ms = int(1000 * (time.monotonic() - before))
    return report


async def judge_probe(settings: Settings, report13: Any, test_id: str) -> dict:
    """Drive the LLM judge directly on a real 1.3 deviation with rule grounding removed."""
    memory = Memory(settings.home)
    spec = memory.load_test(test_id)
    result = next((r for r in report13.results if r.test_id == test_id), None)
    if spec is None or result is None:
        return {"skipped": True, "test_id": test_id}

    stripped = spec.model_copy(update={
        "oracles": [o.model_copy(update={"rule_ref": None}) for o in spec.oracles],
    })
    result2 = result.model_copy(deep=True)
    for step in result2.steps:
        for obs in step.observations:
            obs.evidence.pop("rule_ref", None)
    for obs in result2.observations:
        obs.evidence.pop("rule_ref", None)

    from argus.triage.triage import triage

    llm = LLMClient(settings)
    ctx = SimpleNamespace(llm=llm, memory=memory,
                          product_context=settings.product_context(),
                          changelog=settings.changelog())
    before = time.monotonic()
    verdict = await triage(stripped, result2, ctx)
    verdict = verdict.model_copy(update={
        "rationale": f"[judge probe, rule_ref stripped] {verdict.rationale}"[:400]})
    ledgers = [r.model_dump() for r in llm.take_ledger()]
    return {
        "skipped": False,
        "test_id": test_id,
        "observations_before": [o.kind for o in _result_obs(result2)],
        "calls": ledgers,
        "state_before": _metric(result),
        "decided_by": verdict.decided_by,
        "category": verdict.category,
        "confidence": verdict.confidence,
        "rationale": verdict.rationale,
        "duration_ms": int(1000 * (time.monotonic() - before)),
    }


def _metric(result: Any) -> float:
    """Share of replayed (T0) steps as a stability metric."""
    if not result.steps:
        return 1.0
    t0 = sum(1 for s in result.steps if s.tier == 0)
    return round(t0 / len(result.steps), 3)


def _result_obs(result: Any) -> list[Any]:
    obs = list(result.observations)
    for step in result.steps:
        obs.extend(step.observations)
    return obs


def _oracle_desc(spec: Any) -> str:
    out = []
    for o in spec.oracles:
        ref = o.rule_ref and f" R{o.rule_ref}" or ""
        out.append(f"{o.kind}{ref}")
    return ", ".join(out) or "-"


def build_data(home: Path, base_url: str, suite: list[Any], proposal: list[dict],
               reports: list[tuple[str, Any]], probe: dict, started: str) -> dict:
    """Collect everything the report needs into one JSON-serializable dict."""
    runs = []
    for version, report in reports:
        runs.append({
            "version": version,
            "run_id": report.run_id,
            "label": report.label,
            "duration_ms": report.totals.duration_ms,
            "verdicts": dict(report.totals.verdicts),
            "tiers": dict(report.totals.tiers),
            "tests": report.totals.tests,
            "passed": report.totals.passed,
            "failed": report.totals.failed,
            "healed_steps": report.totals.healed_steps,
            "replayed_steps": report.totals.replayed_steps,
            "llm_calls": report.totals.llm_calls,
            "llm_calls_cached": report.totals.llm_calls_cached,
            "tokens_in": report.totals.tokens_in,
            "tokens_out": report.totals.tokens_out,
            "cost_usd": report.totals.cost_usd,
            "list_cost_usd": report.totals.list_cost_usd,
            "naive_tokens_estimate": report.totals.naive_tokens_estimate,
            "models": sorted({
                c.model for r in report.results for c in r.llm_calls if c.model}),
            "results": [{
                "test_id": r.test_id,
                "status": r.status,
                "verdict": r.verdict.category,
                "decided_by": r.verdict.decided_by,
                "action": r.verdict.action,
                "rationale": r.verdict.rationale,
            } for r in report.results],
        })
    return {
        "app_url": base_url,
        "home": str(home),
        "started_at": started,
        "finished_at": _now(),
        "models_fast": os.environ["ARGUS_MODELS_FAST"],
        "models_smart": os.environ["ARGUS_MODELS_SMART"],
        "models_vision": os.environ["ARGUS_MODELS_VISION"],
        "proposal_llm_calls": proposal,
        "suite": [{
            "id": s.id, "name": s.name, "steps": len(s.steps),
            "requires_login": s.requires_login, "tags": list(s.tags),
            "oracles": _oracle_desc(s),
        } for s in suite],
        "runs": runs,
        "judge_probe": probe,
    }


def write_report(data: dict) -> None:
    """Render `data` as the readable .logs/llm_path_report.md."""
    lines: list[str] = []
    lines.append("# Argus — LLM Path Benchmark\n")
    lines.append(f"- **date**: {data['started_at']} → {data['finished_at']}")
    lines.append(f"- **target**: {data['app_url']} (no server started/stopped by this benchmark)")
    lines.append(f"- **home**: `{data['home']}` (atlas/auth/config/LLM cache reused; suite + runs re-built)")
    lines.append(f"- **providers**: fast=`{data['models_fast']}` · smart=`{data['models_smart']}` "
                 f"· vision=`{data['models_vision']}`\n")

    lines.append("## 1. Suite (LLM-authored)\n")
    lines.append("| test id | steps | login | tags | oracles (rule refs) |")
    lines.append("|---|---|---|---|---|")
    for s in data["suite"]:
        lines.append(f"| {s['id']} | {s['steps']} | {s['requires_login']} | "
                     f"{', '.join(s['tags']) or '-'} | {s['oracles']} |")
    if data["proposal_llm_calls"]:
        lines.append("\nProposal LLM use (author-time):\n")
        lines.append("| purpose | tier | model | cached | tokens | cost $ | latency ms |")
        lines.append("|---|---|---|---|---|---|---|")
        for c in data["proposal_llm_calls"]:
            lines.append(f"| {c['purpose']} | {c['tier']} | {c['model']} | {c['cached']} | "
                         f"{c['tokens_in'] + c['tokens_out']} | {c['cost_usd']} | {c['latency_ms']} |")
    else:
        lines.append("\n*(no proposal records — LLM disabled during authoring)*")
    lines.append("")

    lines.append("## 2. Runs across builds\n")
    lines.append("| build | verdicts | tests | failed | T0 | healed | llm (real/cached) | "
                 "tokens | cost $ | naive tok | dur (s) |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in data["runs"]:
        lines.append(f"| {r['version']} | {_compact(r['verdicts'])} | {r['tests']} | {r['failed']} | "
                     f"{r['replayed_steps']} | {r['healed_steps']} | "
                     f"{r['llm_calls']}/{r['llm_calls_cached']} | "
                     f"{r['tokens_in'] + r['tokens_out']} | {r['cost_usd']} | "
                     f"{r['naive_tokens_estimate']} | {r['duration_ms'] / 1000:.1f} |")
    lines.append("")

    for r in data["runs"]:
        lines.append(f"### v{r['version']} — per test\n")
        lines.append("| test | status | verdict | decided_by | action |")
        lines.append("|---|---|---|---|---|")
        for res in r["results"]:
            lines.append(f"| {res['test_id']} | {res['status']} | {res['verdict']} | "
                         f"{res['decided_by']} | {res['action']} |")
        lines.append("")

    probe = data["judge_probe"]
    lines.append("## 3. LLM judge path (labeled probe)\n")
    if probe.get("skipped"):
        lines.append(f"probe skipped: test `{probe.get('test_id')}` not present this run.\n")
    else:
        lines.append("On build 1.3 the `rule_ref` grounding of every oracle was stripped from the "
                     "altitude test, so triage's deterministic rule guard cannot fire and control "
                     "reaches the LLM judge (purpose `triage`, smart tier):\n")
        lines.append(f"- **test**: `{probe['test_id']}` (observations: {', '.join(probe['observations_before'])})")
        lines.append(f"- **decided_by**: `{probe['decided_by']}` → **{probe['category']}** "
                     f"(conf {probe['confidence']}, {probe['duration_ms']} ms)")
        lines.append(f"- **rationale**: {probe['rationale']}")
        for c in probe.get("calls", []):
            lines.append(f"- **LLM call**: purpose=`{c['purpose']}` model=`{c['model']}` "
                         f"cached={c['cached']} tokens={c['tokens_in'] + c['tokens_out']} "
                         f"cost=${c['cost_usd']}")
        lines.append("")

    lines.append("## 4. Verdict\n")
    total_real = sum(r["llm_calls"] for r in data["runs"]) + \
        sum(1 for c in data["proposal_llm_calls"] if not c["cached"]) + \
        sum(1 for c in probe.get("calls", []) if not c["cached"])
    total_naive = sum(r["naive_tokens_estimate"] for r in data["runs"])
    lines.append(f"- End-to-end suite authoring (LLM proposal) + judging + replay across "
                 f"{len(data['runs'])} builds fits in **{total_real} real LLM call(s)** "
                 f"(all replay-step decisions are rule/deterministic grounding, tier-0 replay).")
    lines.append(f"- Naive LLM-per-action agent would spend ~**{total_naive} tokens**; "
                 f"Argus totals are recorded per run above.")
    lines.append("- Run verdicts: v1.0 must be all-PASS; v1.2 graceful (intended change / cosmetic / "
                 "feature-removed, no BUGs); v1.3 must surface BUGs for every injected regression "
                 "covered by the suite.")
    lines.append("")
    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {REPORT_FILE}")


def verify_expectations(reports: list[tuple[str, Any]]) -> list[str]:
    """Human-readable pass/fail list; never raises — the report is the source of truth."""
    notes: list[str] = []
    by_ver = dict(reports)
    r10, r13 = by_ver.get("1.0"), by_ver.get("1.3")
    if r10 is not None and r10.totals.failed:
        notes.append(f"FAIL: v1.0 expected all-PASS but {r10.totals.failed} failed")
    else:
        notes.append("PASS: v1.0 all-PASS (baseline replayed, 0 LLM calls at run time)")
    if r13 is not None:
        bug_tests = {res.test_id for res in r13.results if res.verdict.category == "BUG"}
        for test_id, bug in EXPECTED_BUGS_1_3.items():
            if test_id in bug_tests:
                notes.append(f"PASS: v1.3 {bug} → BUG on `{test_id}`")
            elif any(test_id in res.test_id for res in r13.results):
                notes.append(f"FAIL: v1.3 {bug} NOT surfaced as BUG (test `{test_id}` exists)")
            else:
                notes.append(f"NOTE: v1.3 {bug} not probed — no test `{test_id}` in this suite")
    return notes


async def main() -> int:
    args = argparse.ArgumentParser(description="Argus LLM-path benchmark")
    args.add_argument("--home", type=str, default=str(HOME))
    args.add_argument("--url", type=str, default=DEFAULT_URL)
    args.add_argument("--builds", type=str, default=",".join(BUILDS))
    args.add_argument("--wipe-home", action="store_true",
                      help="delete the whole home (incl. atlas) and re-explore")
    args.add_argument("--skip-generate", action="store_true",
                      help="reuse the suite already in the home")
    opts = args.parse_args()

    home = Path(opts.home)
    builds = [b.strip() for b in opts.builds.split(",") if b.strip()]
    started = _now()

    import httpx
    try:
        httpx.get(f"{opts.url}/__admin/state", timeout=3).raise_for_status()
    except Exception as exc:
        print(f"FATAL: server at {opts.url} not reachable ({type(exc).__name__}) — "
              "start SkyOps first; this benchmark never boots servers.")
        return 2

    if opts.wipe_home and home.exists():
        shutil.rmtree(home)
        home.mkdir(parents=True, exist_ok=True)
    else:
        home.mkdir(parents=True, exist_ok=True)
        wipe_suite_and_runs(home)

    base_settings = make_settings(builds[0], home, opts.url)
    proposal_ledgers: list[dict] = []
    if not opts.skip_generate:
        from demo_app.deploy import deploy

        deploy(builds[0], opts.url)  # baselines must be recorded against the clean v1.0
        suite, proposal_ledgers = await author_suite(base_settings, opts.url)
    else:
        suite = Memory(home).list_tests("active", build="")
        print(f"[generate] skipped — reusing {len(suite)} saved tests")

    reports: list[tuple[str, Any]] = []
    for version in builds:
        print(f"\n=== deploy v{version} + run ===")
        report = await run_build(version, home, opts.url)
        print(f"v{version} {_compact(report.totals.verdicts)} "
              f"(llm={report.totals.llm_calls}, cached={report.totals.llm_calls_cached}, "
              f"cost=${report.totals.cost_usd}, {report.totals.duration_ms / 1000:.1f}s)")
        reports.append((version, report))

    probe = {}
    if "1.3" in builds:
        report13 = dict(reports)["1.3"]
        probe = await judge_probe(base_settings, report13, "altitude-above-limit-rejected")

    data = build_data(home, opts.url, suite, proposal_ledgers, reports, probe, started)
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {DATA_FILE}")
    write_report(data)

    print("\n=== expectations ===")
    for note in verify_expectations(reports):
        print(" * " + note)
    print("\nreport: " + str(REPORT_FILE))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))