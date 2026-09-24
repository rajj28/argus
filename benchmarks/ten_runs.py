"""Ten-run cost curve: the SkyOps suite replayed 10 times with the LLM off.

Uses a fresh memory home (`.argus-10`): deploys version 1.0 and authors the
semantic suite (`examples/skyops_suite.json`), then replays it 10 times against
the already-running instance at `http://127.0.0.1:8002`, switching the deployment
to 1.1 before run 3 and 1.2 before run 6 (`settings.build` tracks the version).
LLM is off (`ARGUS_LLM=off`), so every verdict comes from deterministic replay,
healing, out-of-order execution and rule-based triage.

Per run it records: steps, T0-replayed steps, healed steps, LLM calls, the
naive-agent token *estimate* (`totals.naive_tokens_estimate`) and duration.
Results are saved to `.argus-10/ten_runs.json` and a table is printed.
Servers are never started or stopped here; port 8002 is assumed to be up.

Run:  .venv/Scripts/python.exe benchmarks/ten_runs.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("ARGUS_LLM", "off")

from argus.config import Settings  # noqa: E402
from argus.runner.author import author  # noqa: E402
from argus.runner.runner import run_suite  # noqa: E402

HOME = ROOT / ".argus-10"
CONTEXT = ROOT / "demo_app" / "context"
SUITE_FILE = ROOT / "examples" / "skyops_suite.json"
REPORT_FILE = HOME / "ten_runs.json"
BASE_URL = "http://127.0.0.1:8002"
CREDS = {"user": "pilot@skyops.io", "password": "flysafe123", "login_path": "/login"}

TOTAL_RUNS = 10
DEPLOY_AT: dict[int, str] = {3: "1.1", 6: "1.2"}   # switch deployment before these runs
FIRST_VERSION = "1.0"


def make_settings(version: str) -> Settings:
    """Fresh Settings for the given build version, LLM hard-disabled."""
    return Settings(home=HOME, base_url=BASE_URL, context_dir=CONTEXT,
                    credentials=CREDS, build=version, headless=True, llm_enabled=False)


def _compact(verdicts: dict[str, int]) -> str:
    return " ".join(f"{k}:{v}" for k, v in sorted(verdicts.items()))


def print_table(rows: list[dict[str, Any]]) -> None:
    header = (f"{'run':>3}  {'ver':<5} {'steps':>5} {'T0':>4} {'T0%':>6} {'heal':>4} "
              f"{'llm':>4} {'naive tok (est.)':>16} {'dur(s)':>8}  verdicts")
    print("\n" + "=" * 96)
    print(header)
    print("-" * 96)
    for r in rows:
        t0_pct = (100.0 * r["t0_replayed"] / r["steps"]) if r["steps"] else 0.0
        print(f"{r['run']:>3}  {r['version']:<5} {r['steps']:>5} {r['t0_replayed']:>4} "
              f"{t0_pct:>5.1f}% {r['healed']:>4} {r['llm_calls']:>4} "
              f"{r['naive_tokens_estimate']:>16} {r['wall_s']:>8.1f}  {_compact(r['verdicts'])}")
    print("=" * 96)


async def run_all() -> int:
    from demo_app.deploy import deploy

    changelog = CONTEXT / "CHANGELOG.md"
    backup = changelog.read_text(encoding="utf-8")
    try:
        if HOME.exists():
            shutil.rmtree(HOME)
        HOME.mkdir(parents=True, exist_ok=True)

        version = FIRST_VERSION
        deploy(version, BASE_URL)
        suite = json.loads(SUITE_FILE.read_text(encoding="utf-8"))
        saved = await author(make_settings(version), suite, print)
        if not saved:
            print("FATAL: the suite could not be authored against v1.0")
            return 2
        print(f"Authored {len(saved)} tests on v{version} (LLM off)\n")

        rows: list[dict[str, Any]] = []
        for n in range(1, TOTAL_RUNS + 1):
            if n in DEPLOY_AT:
                version = DEPLOY_AT[n]
                deploy(version, BASE_URL)
                print(f"--- deployed v{version} before run {n} ---")
            settings = make_settings(version)
            t0 = time.perf_counter()
            report = await run_suite(settings, label=f"10run#{n} v{version}", update=True)
            wall_s = time.perf_counter() - t0
            totals = report.totals
            rows.append({
                "run": n,
                "version": version,
                "run_id": report.run_id,
                "label": report.label,
                "steps": sum(len(r.steps) for r in report.results),
                "t0_replayed": totals.replayed_steps,
                "healed": totals.healed_steps,
                "llm_calls": totals.llm_calls,
                "naive_tokens_estimate": totals.naive_tokens_estimate,
                "duration_ms": totals.duration_ms,
                "wall_s": round(wall_s, 1),
                "verdicts": dict(sorted(Counter(r.verdict.category for r in report.results).items())),
            })
            last = rows[-1]
            t0_pct = (100.0 * last["t0_replayed"] / last["steps"]) if last["steps"] else 0.0
            print(f"[run {n:2d}] v{version}  steps={last['steps']:3d}  t0={last['t0_replayed']:3d} "
                  f"({t0_pct:4.1f}%)  healed={last['healed']:2d}  llm={last['llm_calls']}  "
                  f"naive~{last['naive_tokens_estimate']}  {last['wall_s']:5.1f}s")

        payload = {
            "schema": "argus-ten-runs-v1",
            "base_url": BASE_URL,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "runs": rows,
        }
        REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        REPORT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print_table(rows)
        print(f"\nSaved {REPORT_FILE}")
        return 0
    finally:
        changelog.write_text(backup, encoding="utf-8")


def main() -> int:
    import asyncio
    return asyncio.run(run_all())


if __name__ == "__main__":
    raise SystemExit(main())