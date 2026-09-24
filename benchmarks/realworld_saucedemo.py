"""Real-world benchmark: Swag Labs user matrix on SauceDemo.

Authors + baselines the semantic suite (examples/saucedemo/suite.json) as the
`standard_user` build, then replays the frozen plans against every store account —
each of which is a *build variant* shipped by the same deployment (the invisible
buggy ones: problem_user, error_user, performance_glitch_user, visual_user).

The LLM is disabled unless `--llm` is passed, so every verdict comes from the
self-healing replay engine + rule-based triage. Per-user results are written to
`.argus-saucedemo/realworld.json` and a user x test verdict table is printed.

Run:  .venv/Scripts/python.exe benchmarks/realworld_saucedemo.py [--llm]
"""
from __future__ import annotations

import argparse
import asyncio
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

from argus.config import Settings, load_settings  # noqa: E402
from argus.runner.author import author  # noqa: E402
from argus.runner.runner import run_suite  # noqa: E402

HOME = ROOT / ".argus-saucedemo"
CONTEXT = ROOT / "examples" / "saucedemo" / "context"
SUITE_FILE = ROOT / "examples" / "saucedemo" / "suite.json"
REPORT_FILE = HOME / "realworld.json"

BASE_URL = "https://www.saucedemo.com"
PASSWORD = "secret_sauce"

# The deployment's account matrix. `standard_user` is the reference build; the rest
# are variant builds with latent defects the suite should surface.
VARIANTS = ["standard_user", "problem_user", "error_user", "performance_glitch_user", "visual_user"]

TEST_IDS = [
    "login", "sort-by-price", "add-to-cart", "cart-contents",
    "checkout-validation", "checkout-happy", "item-detail", "logout",
]


def creds_for(user: str) -> dict[str, str]:
    return {"user": user, "password": PASSWORD, "login_path": "/"}


def make_settings(user: str, llm: bool) -> Settings:
    settings = Settings(
        home=HOME,
        base_url=BASE_URL,
        context_dir=CONTEXT,
        credentials=creds_for(user),
        build=user,
        headless=True,
        llm_enabled=llm,
    )
    if llm:
        settings.api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("JEV_API", "")
    return settings


def report_payload() -> dict[str, Any]:
    if REPORT_FILE.exists():
        try:
            return json.loads(REPORT_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"schema": "argus-saucedemo-realworld-v1", "base_url": BASE_URL}


async def run_all(llm: bool) -> int:
    suite = json.loads(SUITE_FILE.read_text(encoding="utf-8"))

    # Wipe the memory home so stale tests/plans from earlier runs never replay.
    if HOME.exists():
        shutil.rmtree(HOME)
    HOME.mkdir(parents=True)

    author_settings = make_settings("standard_user", llm)
    saved = await author(author_settings, suite, print)
    if not saved:
        print("FATAL: the suite could not be authored against the reference build")
        return 2
    print(f"Authored {len(saved)} tests under build='standard_user'\n")

    payload = report_payload()
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    users: dict[str, dict[str, str]] = {}
    metrics: dict[str, dict[str, Any]] = {}

    for user in VARIANTS:
        settings = make_settings(user, llm)
        t0 = time.perf_counter()
        report = await run_suite(settings, label=f"saucedemo:{user}", update=False)
        elapsed = time.perf_counter() - t0
        verds = {r.test_id: r.verdict.category for r in report.results}
        users[user] = verds
        metrics[user] = {
            "counts": dict(sorted(Counter(verds.values()).items())),
            "llm_calls": report.totals.llm_calls,
            "llm_calls_cached": report.totals.llm_calls_cached,
            "naive_tokens_estimate": report.totals.naive_tokens_estimate,
            "duration_ms": report.totals.duration_ms,
            "wall_s": round(elapsed, 1),
        }
        print(f"[{user}] {metrics[user]['counts']}  llm={metrics[user]['llm_calls']}  "
              f"{metrics[user]['wall_s']}s")

    payload["users"] = {"verdicts": users, "metrics": metrics}
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print_table(users, metrics)
    return 0


def print_table(users: dict[str, dict[str, str]], metrics: dict[str, dict[str, Any]]) -> None:
    width = max([len(t) for t in TEST_IDS] + [len(u) for u in VARIANTS])
    header = "user".ljust(width + 2) + "".join(t.center(16) for t in TEST_IDS) + "  ok " + "bug "
    print("\n" + "=" * len(header))
    print(header)
    print("-" * len(header))
    for user in VARIANTS:
        verds = users.get(user, {})
        cells = "".join(verds.get(t, "-").center(16) for t in TEST_IDS)
        counts = metrics.get(user, {}).get("counts", {})
        ok = counts.get("PASS", 0) + counts.get("COSMETIC_DRIFT", 0)
        bug = counts.get("BUG", 0)
        print(user.ljust(width + 2) + cells + f"  {ok:>2}  {bug:>2}")
    print("=" * len(header))
    print("ok = PASS+COSMETIC_DRIFT, bug = BUG. Epic = NEEDS_REVIEW/INTENDED_CHANGE/FEATURE_REMOVED/PRECONDITION_FAILURE/INFRA.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--llm", action="store_true", help="enable the LLM heal/replan/triage layer")
    args = ap.parse_args()
    if args.llm:
        probe = load_settings(HOME, base_url=BASE_URL, context_dir=CONTEXT,
                              credentials=creds_for("standard_user"), build="standard_user")
        assert probe.api_key, "LLM requested but no OPENROUTER_API_KEY / JEV_API found in the environment"
    return asyncio.run(run_all(args.llm))


if __name__ == "__main__":
    raise SystemExit(main())