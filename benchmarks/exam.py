"""Blind exam harness for the hold-out app ``exam_app/`` (MediQueue).

Usage
-----
    .venv/Scripts/python.exe benchmarks/exam.py          # mode A, LLM off
    .venv/Scripts/python.exe benchmarks/exam.py --llm    # mode A with LLM + mode B

What it does
------------
Mode A ("human-intent", one test per ``exam/intents.json`` id)
    fresh ``.argus-exam`` -> release r1 -> author ``exam/argus_suite.json`` -> run r1 ->
    for r in r2..r6: release r -> run --build r (update=True) -> r2 memory probe: release r2
    again and run twice more, recording heals.

Mode B ("zero-knowledge", ``--llm`` only)
    fresh ``.argus-exam-gen`` -> release r1 -> ``explore`` -> ``generate`` -> run r1..r6,
    mapping generated test names onto intent ids with rapidfuzz for scoring.

Blindness
---------
Argus only ever receives ``base_url``, the front-desk credentials and
``context_dir=exam/context`` (PRODUCT.md + CHANGELOG.md). ``exam/answer_key.json`` is read
once, by :func:`score_all`, after every run has finished. No file under ``argus/``,
``exam_app/`` or ``exam/context/`` is written by this harness; the release switch goes
through the app's own control endpoint, which rewrites ``exam/context/CHANGELOG.md``.

Disclosed harness configuration
-------------------------------
* ``thresholds.margin`` 0.12 -> 0.04. The schedule renders three structurally identical
  Cancel/Reschedule buttons, so even a perfect 1.00 replay match (runner-up 0.957) is
  refused with stock thresholds and those two intents cannot be baselined at all.
* ``jury_enabled`` off. A 3-member jury costs 3 calls per verdict, which the 60-call task
  budget cannot afford; the single smart-tier judge is used instead.
* ``max_llm_calls_per_run`` is clamped per phase so the whole session stays within
  ``LLM_BUDGET`` non-cached calls.
* The app data is reset before every run so each release starts from the same 60
  appointments, which keeps cross-release verdicts comparable.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── paths ─────────────────────────────────────────────────────────────────────────
SUITE_FILE = ROOT / "exam" / "argus_suite.json"
ANSWER_KEY = ROOT / "exam" / "answer_key.json"
CONTEXT_DIR = ROOT / "exam" / "context"
RESULTS_DIR = ROOT / "exam" / "results"
HOME_A = ROOT / ".argus-exam"
HOME_B = ROOT / ".argus-exam-gen"

# ── exam facts (from exam/README.md, not from the answer key) ────────────────────
BASE_URL = "http://127.0.0.1:8010"
CREDS = {"user": "reception@mediqueue.io", "password": "triage42"}
ALL_RELEASES = ["r1", "r2", "r3", "r4", "r5", "r6"]
SCORED_RELEASES = ["r2", "r3", "r4", "r5", "r6"]  # r1 is the authoring build, not scored

# ── harness configuration ─────────────────────────────────────────────────────────
LLM_BUDGET = 60            # task-wide cap on non-cached LLM calls
# Mode A worst case 8 runs x 5 = 40 calls, mode B up to 16; budget_left() clamps the rest.
PHASE_LLM_CAPS = {"run": 5, "probe": 4, "explore": 6, "generate": 10}
HEAL_MARGIN = 0.04         # see module docstring
BUDGET_FILE = RESULTS_DIR / "llm_budget.json"
RAW_FILES = {"a_nollm": "raw_a_nollm.json", "a_llm": "raw_a_llm.json", "b_llm": "raw_b_llm.json"}

_LLM = {"calls": 0, "cached": 0, "tokens_in": 0, "tokens_out": 0, "by_purpose": Counter()}


def _log(msg: str) -> None:
    print(msg, flush=True)


# ── app control (the server rewrites its own changelog) ──────────────────────────

def _post(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode() if payload is not None else b""
    req = urllib.request.Request(f"{BASE_URL}{path}", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _get(path: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=15) as resp:
        return json.loads(resp.read())


def switch_release(release: str) -> dict[str, Any]:
    """Switch the live app to `release`; the endpoint also rewrites exam/context/CHANGELOG.md."""
    state = _post("/__exam/release", {"release": release})
    _log(f"  -> release {release} (appointments={state.get('appointment_count')})")
    return state


def reset_app() -> None:
    """Reset in-memory data (60 appointments, SMS reminders on) without changing release."""
    _post("/__exam/reset")


def app_state() -> dict[str, Any]:
    return _get("/__exam/state")


def server_alive() -> bool:
    try:
        app_state()
        return True
    except Exception:
        return False


# ── LLM accounting / budget ──────────────────────────────────────────────────────

def install_llm_counter() -> None:
    """Count every LLM call made in this process (runs, explore, generate, heals)."""
    from argus.llm import client as client_mod

    original = client_mod.LLMClient.json
    original_from = client_mod.LLMClient.json_from

    async def counted_json(self: Any, **kwargs: Any) -> Any:
        before = len(self.ledger)
        try:
            return await original(self, **kwargs)
        finally:
            _absorb(self, before, kwargs.get("purpose", "?"))

    async def counted_json_from(self: Any, model_spec: str, **kwargs: Any) -> Any:
        before = len(self.ledger)
        try:
            return await original_from(self, model_spec, **kwargs)
        finally:
            _absorb(self, before, kwargs.get("purpose", "?"))

    client_mod.LLMClient.json = counted_json
    client_mod.LLMClient.json_from = counted_json_from


def _absorb(client: Any, before: int, purpose: str) -> None:
    for record in client.ledger[before:]:
        if record.cached:
            _LLM["cached"] += 1
        else:
            _LLM["calls"] += 1
        _LLM["tokens_in"] += record.tokens_in
        _LLM["tokens_out"] += record.tokens_out
        _LLM["by_purpose"][f"{purpose}{'*' if record.cached else ''}"] += 1


def load_budget() -> None:
    if BUDGET_FILE.exists():
        try:
            data = json.loads(BUDGET_FILE.read_text(encoding="utf-8"))
            for key in ("calls", "cached", "tokens_in", "tokens_out"):
                _LLM[key] = int(data.get(key, 0))
            _LLM["by_purpose"] = Counter(data.get("by_purpose", {}))
        except Exception:
            pass


def save_budget() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {key: _LLM[key] for key in ("calls", "cached", "tokens_in", "tokens_out")}
    payload["by_purpose"] = dict(_LLM["by_purpose"])
    payload["budget"] = LLM_BUDGET
    BUDGET_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def budget_left() -> int:
    return max(0, LLM_BUDGET - _LLM["calls"])


# ── settings ──────────────────────────────────────────────────────────────────────

def make_settings(home: Path, build: str, llm_on: bool, phase: str) -> Any:
    """Settings for the exam app. `load_settings` picks up keys from .env (never read here)."""
    from argus.config import load_settings

    settings = load_settings(
        home,
        base_url=BASE_URL,
        context_dir=CONTEXT_DIR,
        credentials=dict(CREDS),
        headless=True,
    )
    settings.build = build
    settings.llm_enabled = bool(llm_on)
    settings.jury_enabled = False                      # 60-call budget: single judge only
    settings.thresholds.margin = HEAL_MARGIN
    settings.max_llm_calls_per_run = min(PHASE_LLM_CAPS.get(phase, 4), budget_left())
    return settings


# ── authoring (per spec, pristine state, failures isolated) ───────────────────────

async def author_spec(ctx: Any, browser: Any, auth: Any, spec: dict[str, Any],
                      log: Callable[[str], None]) -> Any:
    """Resolve a semantic spec on the live app, then freeze a pristine baseline.

    Mirrors ``argus.runner.author.author`` but resets the app before the resolve pass and
    again before ``record_baseline``: the resolve pass mutates data (it cancels the first
    appointment, books a patient), and a baseline recorded against mutated data produces
    fingerprints that no longer match the rows they came from.
    """
    from argus.browser.effects import EffectRecorder, wait_for_settle
    from argus.browser.snapshot import take_snapshot
    from argus.models import Fingerprint, Step, TestChange
    from argus.runner.author import _act, find_element, spec_from_dict
    from argus.runner.runner import record_baseline

    reset_app()
    context = await browser.new_context(viewport=ctx.settings.viewport, storage_state=None)
    page = await context.new_page()
    recorder = EffectRecorder(page)
    await page.goto(ctx.url(spec["start_url"]), wait_until="domcontentloaded")
    await wait_for_settle(page, recorder)

    ok = True
    for step in spec["steps"]:
        if step["action"] == "goto":
            await page.goto(ctx.url(step["value"]), wait_until="domcontentloaded")
            await wait_for_settle(page, recorder)
            continue
        if step["action"] == "wait":
            await page.wait_for_timeout(int(step.get("value") or 500))
            continue
        if step["action"] == "press" and not step.get("find"):
            await page.keyboard.press(step.get("value") or "Enter")
            continue
        snapshot = await take_snapshot(page)
        element = find_element(snapshot, step["find"])
        if element is None:
            log(f"  ! {spec['id']}: cannot resolve {step['find']} on {snapshot.url}")
            ok = False
            break
        step["fp"] = Fingerprint.from_element(element).model_dump()
        value = ctx.value_for(Step(id="x", intent="", action=step["action"], value=step.get("value")))
        handle = await page.evaluate_handle(f"() => window.__argus.refs[{int(element.ref[1:])}]")
        await recorder.begin()
        await _act(page, handle.as_element(), step["action"], value)
        await recorder.end()

    if ok:
        snapshot = await take_snapshot(page)
        for oracle in spec.get("oracles", []):
            find = oracle.get("params", {}).pop("find", None)
            if not find:
                continue
            element = find_element(snapshot, find)
            if element is None:
                log(f"  ! {spec['id']}: oracle target {find} not found")
                ok = False
            else:
                oracle["params"]["fingerprint"] = Fingerprint.from_element(element).model_dump()
    await context.close()
    if not ok:
        return None

    frozen_spec = spec_from_dict(spec)
    reset_app()
    frozen = await record_baseline(frozen_spec, ctx, browser, auth)
    if frozen is None:
        log(f"  ! {spec['id']}: baseline run failed")
        return None
    ctx.memory.save_test(
        frozen,
        TestChange(version=1, kind="created", summary=f"Authored ({frozen.origin}); baseline recorded"),
        build=getattr(ctx.settings, "build", ""),
    )
    dropped = [o.description or o.kind for o in frozen_spec.oracles if o not in frozen.oracles]
    if dropped:
        log(f"    ! oracle(s) did not hold on the baseline and were dropped: {dropped}")
    log(f"  + {frozen.id}: {len(frozen.steps)} steps, {len(frozen.oracles)} oracles")
    return frozen


async def author_suite(settings: Any, specs: list[dict[str, Any]]) -> tuple[list[Any], list[dict[str, str]]]:
    """Author every spec, isolating per-spec crashes (one broken spec must not stop the rest)."""
    from argus.memory.store import Memory
    from argus.runner.runner import RunContext, ensure_auth
    from playwright.async_api import async_playwright

    memory = Memory(settings.home)
    run_id, run_dir = memory.new_run_dir()
    ctx = RunContext(settings, memory, None, run_id, run_dir)
    saved: list[Any] = []
    failures: list[dict[str, str]] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=settings.headless)
        try:
            auth = await ensure_auth(browser, ctx)
            for spec in specs:
                try:
                    frozen = await author_spec(ctx, browser, auth, spec, _log)
                except Exception as exc:  # noqa: BLE001 - one spec must not abort the exam
                    _log(f"  ! {spec['id']}: authoring crashed: {type(exc).__name__}: {str(exc)[:160]}")
                    failures.append({"id": spec["id"], "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
                    continue
                if frozen is None:
                    failures.append({"id": spec["id"], "error": "steps/oracles could not be resolved or baselined"})
                else:
                    saved.append(frozen)
        finally:
            await browser.close()
    return saved, failures


# ── running + collecting ─────────────────────────────────────────────────────────

def collect_run(report: Any, label: str, release: str, update: bool, wall_s: float,
                started: str) -> dict[str, Any]:
    """Flatten a RunReport into a JSON-serialisable payload (no scoring here)."""
    totals = report.totals
    tests: list[dict[str, Any]] = []
    for result in report.results:
        tiers = Counter(str(sr.tier) for sr in result.steps if sr.tier is not None)
        tests.append({
            "test_id": result.test_id,
            "name": result.test_name,
            "status": result.status,
            "verdict": result.verdict.category,
            "decided_by": result.verdict.decided_by,
            "action": result.verdict.action,
            "confidence": result.verdict.confidence,
            "rationale": result.verdict.rationale,
            "changelog_refs": result.verdict.changelog_refs,
            "observations": [
                {"kind": obs.kind, "step_id": obs.step_id, "detail": obs.detail,
                 "tier": obs.tier, "evidence": _slim(obs.evidence)}
                for obs in result.observations
            ],
            "step_observations": [
                {"kind": obs.kind, "step_id": obs.step_id, "detail": obs.detail, "tier": obs.tier}
                for sr in result.steps for obs in sr.observations
            ],
            "tiers": dict(sorted(tiers.items())),
            "healed_steps": sum(1 for sr in result.steps if sr.tier),
            "failed_steps": [sr.intent for sr in result.steps if sr.status != "passed"],
            "llm_calls": len([c for c in result.llm_calls if not c.cached]),
            "llm_calls_cached": len([c for c in result.llm_calls if c.cached]),
            "cost_usd": round(sum(c.cost_usd for c in result.llm_calls), 6),
            "duration_ms": result.duration_ms,
        })
    return {
        "label": label,
        "release": release,
        "update": update,
        "started_at": started,
        "run_id": report.run_id,
        "wall_s": round(wall_s, 1),
        "tests_run": totals.tests,
        "healed_steps": totals.healed_steps,
        "replayed_steps": totals.replayed_steps,
        "tiers": totals.tiers,
        "llm_calls": totals.llm_calls,
        "llm_calls_cached": totals.llm_calls_cached,
        "tokens_in": totals.tokens_in,
        "tokens_out": totals.tokens_out,
        "cost_usd": round(totals.cost_usd, 6),
        "verdicts": totals.verdicts,
        "tests": tests,
    }


def _slim(evidence: dict[str, Any]) -> dict[str, Any]:
    """Keep small scalar evidence only; candidate lists are huge and already summarised."""
    out: dict[str, Any] = {}
    for key, value in (evidence or {}).items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value if not isinstance(value, str) else value[:180]
    return out


async def run_release(settings: Any, label: str, release: str, update: bool = True) -> dict[str, Any]:
    """Reset the app, run the whole suite for one release, return the collected payload."""
    from argus.runner.runner import run_suite

    reset_app()
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _log(f"\n[{label}] build={release} update={update} llm={settings.llm_enabled} "
         f"llm_cap={settings.max_llm_calls_per_run}")
    t0 = time.perf_counter()

    def progress(result: Any) -> None:
        _log(f"    {result.test_id:26s} {result.status:6s} {result.verdict.category:16s} "
             f"by={result.verdict.decided_by:6s} {result.duration_ms / 1000:5.1f}s")

    report = await run_suite(settings, label=label, update=update, on_result=progress)
    wall = time.perf_counter() - t0
    payload = collect_run(report, label, release, update, wall, started)
    _log(f"  verdicts={payload['verdicts']} healed={payload['healed_steps']} "
         f"llm={payload['llm_calls']} wall={payload['wall_s']}s")
    save_budget()
    return payload


def fresh_home(home: Path) -> Path:
    """Wipe and recreate an Argus home. Called once per mode, never between releases."""
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True, exist_ok=True)
    return home


# ── mode A: human-intent ──────────────────────────────────────────────────────────

async def run_mode_a(llm_on: bool) -> dict[str, Any]:
    """Author the human-intent suite on r1 and run it across r1..r6 plus the r2 probe."""
    key = "a_llm" if llm_on else "a_nollm"
    _log("\n" + "=" * 74)
    _log(f"MODE A - human-intent suite (LLM {'on' if llm_on else 'off'}), fresh home {HOME_A.name}")
    _log("=" * 74)

    specs = json.loads(SUITE_FILE.read_text(encoding="utf-8"))
    switch_release("r1")
    fresh_home(HOME_A)
    settings = make_settings(HOME_A, "r1", llm_on, "run")
    reset_app()
    _log("\n[r1] authoring exam/argus_suite.json")
    authored, author_failures = await author_suite(settings, specs)
    _log(f"  authored {len(authored)}/{len(specs)} specs; {len(author_failures)} could not be baselined")

    runs: list[dict[str, Any]] = [await run_release(make_settings(HOME_A, "r1", llm_on, "run"),
                                                     "r1-baseline", "r1", update=True)]
    for release in SCORED_RELEASES:
        switch_release(release)
        runs.append(await run_release(make_settings(HOME_A, release, llm_on, "run"),
                                      f"{release}-run", release, update=True))
    switch_release("r2")
    for probe in (1, 2):
        runs.append(await run_release(make_settings(HOME_A, "r2", llm_on, "probe"),
                                      f"r2-memory-probe-{probe}", "r2", update=True))

    payload = {
        "mode": key,
        "title": f"Mode A - human-intent suite (LLM {'on' if llm_on else 'off'})",
        "llm_on": llm_on,
        "home": str(HOME_A.relative_to(ROOT)),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "intents": [s["id"] for s in specs],
        "authored": [{"id": t.id, "steps": len(t.steps), "oracles": len(t.oracles),
                      "goals": t.goal} for t in authored],
        "author_failures": author_failures,
        "runs": runs,
        "config": {
            "heal_margin": HEAL_MARGIN,
            "stock_heal_margin": 0.12,
            "jury_enabled": False,
            "reset_before_each_run": True,
        },
    }
    write_raw(key, payload)
    return payload


# ── mode B: zero-knowledge ────────────────────────────────────────────────────────

def map_to_intents(generated: list[Any], intent_ids: list[str]) -> tuple[dict[str, str], float]:
    """Map generated test names onto intent ids by fuzzy name similarity.

    rapidfuzz returns (value, score, key) for dict choices, so the intent id is the key.
    """
    from rapidfuzz import fuzz, process

    choices = {iid.replace("-", " "): iid for iid in intent_ids}
    mapping: dict[str, str] = {}
    for test in generated:
        label = (test.name or test.id).lower()
        hit = process.extractOne(label, choices, scorer=fuzz.token_set_ratio, score_cutoff=40)
        if hit is None:
            continue
        intent_id = hit[2] if len(hit) > 2 else hit[0]
        mapping[test.id] = str(intent_id)
    coverage = len(set(mapping.values())) / len(intent_ids) if intent_ids else 0.0
    return mapping, coverage


async def run_mode_b() -> dict[str, Any]:
    """Explore + generate a suite with no human input, then run it across r1..r6."""
    from argus.explore.explorer import explore
    from argus.explore.generator import generate

    _log("\n" + "=" * 74)
    _log(f"MODE B - zero-knowledge (explore + generate), fresh home {HOME_B.name}")
    _log("=" * 74)

    intent_ids = [spec["id"] for spec in json.loads(SUITE_FILE.read_text(encoding="utf-8"))]
    switch_release("r1")
    fresh_home(HOME_B)
    reset_app()

    _log("\n[r1] explore")
    t0 = time.perf_counter()
    atlas = await explore(make_settings(HOME_B, "r1", True, "explore"),
                          max_states=20, max_actions=70, log=_log)
    explore_s = time.perf_counter() - t0
    save_budget()
    counts = (atlas.get("meta") or {}).get("counts", {})
    _log(f"  atlas: {counts} in {explore_s:.0f}s")
    # The app keeps its session in sessionStorage ("mq-authenticated"), which Playwright's
    # storage_state cannot carry, so every fresh context explore() opens is logged out and the
    # crawl is pinned to the login wall.
    login_wall = len(atlas.get("states", {})) <= 2 and not (atlas.get("login") or {}).get("elements")

    _log("\n[r1] generate")
    gen_log: list[str] = []

    def gen_log_line(msg: str) -> None:
        gen_log.append(msg)
        _log(msg)

    t0 = time.perf_counter()
    # generate() forwards its own log to most steps, but propose() prints its retries to stdout;
    # capture them so the artifact explains a generation failure without re-running.
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        generated = await generate(make_settings(HOME_B, "r1", True, "generate"), atlas,
                                   max_tests=8, log=gen_log_line)
    for line in captured.getvalue().splitlines():
        stripped = line.strip()
        if stripped:
            gen_log.append(stripped)
            _log(stripped)
    generate_s = time.perf_counter() - t0
    save_budget()
    dropped = [m for m in gen_log if "drop" in m]
    _log(f"  generated {len(generated)} tests in {generate_s:.0f}s "
         f"({len(dropped)} proposals could not be baselined)")

    mapping, coverage = map_to_intents(generated, intent_ids)
    for test in generated:
        _log(f"    {test.id:28s} {test.name[:44]:44s} -> {mapping.get(test.id, '(unmapped)')}")

    runs: list[dict[str, Any]] = [await run_release(make_settings(HOME_B, "r1", True, "run"),
                                                     "r1-gen-baseline", "r1", update=True)]
    for release in SCORED_RELEASES:
        switch_release(release)
        runs.append(await run_release(make_settings(HOME_B, release, True, "run"),
                                      f"{release}-gen-run", release, update=True))

    payload = {
        "mode": "b_llm",
        "title": "Mode B - zero-knowledge (explore + generate, LLM on)",
        "llm_on": True,
        "home": str(HOME_B.relative_to(ROOT)),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "intents": intent_ids,
        "generated": [{"id": t.id, "name": t.name, "steps": len(t.steps), "oracles": len(t.oracles)}
                      for t in generated],
        "intent_map": mapping,
        "coverage": round(coverage, 3),
        "atlas_counts": counts,
        "login_wall": login_wall,
        "generate_log": [m.strip()[:200] for m in gen_log[-25:]],
        "baseline_success": {
            "authored": len(generated),
            "requested": len(generated) + len(dropped),
            "dropped": len(dropped),
            "dropped_reasons": [m.strip()[:140] for m in dropped],
        },
        "explore_wall_s": round(explore_s, 1),
        "generate_wall_s": round(generate_s, 1),
        "runs": runs,
        "config": {"heal_margin": HEAL_MARGIN, "jury_enabled": False, "reset_before_each_run": True},
    }
    write_raw("b_llm", payload)
    return payload


# ── scoring (the only place the answer key is read) ───────────────────────────────

# Ordered by causal priority, not by how often the words appear: a step that cannot resolve is the
# cause, a missing expected-text oracle is usually the consequence, so step rules outrank text drift.
_WEAKNESS_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("overlay / modal blocks navigation",
     ("a smoother booking journey", "what's new")),
    ("shadow DOM (custom element not snapshot-able)", ("shadowroot", "shadow dom", "mq-toggle", "getcsspath")),
    ("virtualised list (no scroll primitive)", ("virtual", "not in the viewport", "rahul iyer")),
    ("custom dropdown (mq-select listbox)", ("mq-select", "listbox", "dropdown", "combobox")),
    ("confirmation dialog flow", ("cancel this appointment?",)),
    ("async loading / client-side delay", ("aria-busy", "skeleton", "loading doctors", "loading directory")),
    ("similarity margin gate (best match below threshold)", ("below thresholds", "below margin")),
    ("step target not resolvable on the page",
     ("cannot find", "no compatible candidates", "no plan", "candidates:")),
    ("expected UI text missing (label or text drift)", ("expected text",)),
    ("UI lies (backend failure masked)",
     ("http 500", "status 500", "internal server error", "unable to save", "booking failed")),
    ("paraphrased release notes", ("changelog", "release note")),
    ("iframe / embedded frame", ("iframe",)),
    ("precondition / session lost", ("session missing", "precondition")),
)

_INTENT_FALLBACK = {
    "toggle-sms-reminders": "shadow DOM (custom element not snapshot-able)",
    "cancel-appointment": "virtualised list (no scroll primitive)",
    "reschedule-appointment": "virtualised list (no scroll primitive)",
    "doctor-list-loads": "async loading / client-side delay",
    "book-consultation": "custom dropdown (mq-select listbox)",
    "age-validation": "custom dropdown (mq-select listbox)",
    "emergency-fee-zero": "custom dropdown (mq-select listbox)",
    "invoice-total-correct": "async loading / client-side delay",
    "login": "precondition / session lost",
}


def classify_weakness(intent: str, verdict: str, text: str) -> str:
    """Map a wrong verdict to the most likely Argus weakness, evidence first."""
    haystack = text.lower()
    for label, needles in _WEAKNESS_RULES:
        if any(needle in haystack for needle in needles):
            return label
    if verdict == "INFRA":
        return "environment / infra"
    return _INTENT_FALLBACK.get(intent, "unclassified")


def load_answer_key() -> dict[str, Any]:
    """Read the answer key - only ever called after every run has finished."""
    return json.loads(ANSWER_KEY.read_text(encoding="utf-8"))


def _acceptable_of(entry: Any) -> list[str]:
    """Verdicts that count as correct for one intent/release, tolerating key shape changes."""
    if isinstance(entry, list):
        return [str(v) for v in entry]
    if isinstance(entry, dict):
        values = entry.get("acceptable") or entry.get("expected") or []
        if isinstance(values, str):
            values = [values]
        return [str(v) for v in values]
    return []


def score_payload(payload: dict[str, Any], key: dict[str, Any]) -> dict[str, Any]:
    """Score one mode's runs against the answer key."""
    intent_map = payload.get("intent_map", {})
    key_releases = key.get("releases", {}) if isinstance(key, dict) else {}
    releases: dict[str, Any] = {}
    for run in payload["runs"]:
        release = run["release"]
        if release not in SCORED_RELEASES or run["label"].startswith("r2-memory-probe"):
            continue
        expected_all = key_releases.get(release, {}) if isinstance(key_releases, dict) else {}
        for test in run["tests"]:
            intent = intent_map.get(test["test_id"], test["test_id"])
            entry = expected_all.get(intent, {}) if isinstance(expected_all, dict) else {}
            acceptable = _acceptable_of(entry)
            details = [o["detail"] for o in test["observations"]]
            details += [o["detail"] for o in test.get("step_observations", [])]
            # Classify on Argus's own evidence only; the judge's prose is quoted but never used to
            # name the weakness, otherwise a well-written rationale would bias the roll-up.
            text = " ".join(details).lower()
            correct = test["verdict"] in acceptable
            releases.setdefault(release, {})[intent] = {
                "test_id": test["test_id"],
                "verdict": test["verdict"],
                "decided_by": test["decided_by"],
                "acceptable": acceptable,
                "correct": correct,
                "why": entry.get("why", "") if isinstance(entry, dict) else "",
                "rationale": test["rationale"],
                "observation": next((d for d in details if d), ""),
                "weakness": "" if correct else classify_weakness(intent, test["verdict"], text),
                "healed_steps": test["healed_steps"],
            }
    return releases


def write_raw(key_name: str, payload: dict[str, Any]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / RAW_FILES[key_name]).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def read_raw(key_name: str) -> dict[str, Any] | None:
    path = RESULTS_DIR / RAW_FILES[key_name]
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ── scorecard ────────────────────────────────────────────────────────────────────

def _accuracy(correct: int, total: int) -> float:
    return round(correct / total, 3) if total else 0.0


def _pct(value: float, total: int) -> str:
    return f"{value:.0%}" if total else "n/a"


def score_mode(payload: dict[str, Any], scored: dict[str, Any]) -> dict[str, Any]:
    """Accuracy, per-criterion scores and the failure list for one mode."""
    correct = sum(1 for per_intent in scored.values() for v in per_intent.values() if v["correct"])
    total = sum(len(per_intent) for per_intent in scored.values())
    per_release = {
        release: {
            "correct": sum(1 for v in per_intent.values() if v["correct"]),
            "total": len(per_intent),
            "accuracy": _accuracy(sum(1 for v in per_intent.values() if v["correct"]), len(per_intent)),
        }
        for release, per_intent in scored.items()
    }
    failures = [
        {"release": release, "intent": intent, "got": v["verdict"],
         "acceptable": v["acceptable"], "observation": v["observation"],
         "rationale": v["rationale"], "why": v["why"], "weakness": v["weakness"],
         "decided_by": v["decided_by"]}
        for release, per_intent in scored.items()
        for intent, v in per_intent.items() if not v["correct"]
    ]
    r2 = scored.get("r2", {})
    r2_correct = sum(1 for v in r2.values() if v["correct"])
    context_releases = ["r3", "r4", "r5", "r6"]
    ctx_correct = sum(1 for r in context_releases for v in scored.get(r, {}).values() if v["correct"])
    ctx_total = sum(len(scored.get(r, {})) for r in context_releases)
    probe_runs = [r for r in payload["runs"] if r["label"].startswith("r2-memory-probe")]
    main_r2 = next((r for r in payload["runs"] if r["label"] == "r2-run"), None)
    total_calls = sum(r["llm_calls"] for r in payload["runs"])
    total_tokens = sum(r["tokens_in"] + r["tokens_out"] for r in payload["runs"])
    authored = len(payload.get("authored", []))
    return {
        "mode": payload["mode"],
        "title": payload["title"],
        "llm_on": payload["llm_on"],
        "generated_at": payload["generated_at"],
        "home": payload["home"],
        "intents": payload["intents"],
        "authored": payload.get("authored", []),
        "author_failures": payload.get("author_failures", []),
        "overall": {"correct": correct, "total": total, "accuracy": _accuracy(correct, total)},
        "per_release": per_release,
        "scored": scored,
        "failures": failures,
        "criteria": {
            "Reliability": {
                "detail": "r2 is a design-only refresh: PASS or COSMETIC_DRIFT everywhere",
                "correct": r2_correct, "total": len(r2),
                "accuracy": _accuracy(r2_correct, len(r2)),
                "memory_probe_heals": sum(r["healed_steps"] for r in probe_runs),
            },
            "Context": {
                "detail": "r3-r6: intended change, banner reflow, masked 500 bug, feature removal",
                "correct": ctx_correct, "total": ctx_total, "accuracy": _accuracy(ctx_correct, ctx_total),
                "per_release": {r: per_release.get(r, {}).get("accuracy", 0.0) for r in context_releases},
            },
            "Cost": {
                "detail": "steady-state LLM cost of a full suite run",
                "llm_calls_total": total_calls,
                "tokens_total": total_tokens,
                "per_run": [{"label": r["label"], "llm_calls": r["llm_calls"],
                             "cached": r["llm_calls_cached"],
                             "tokens": r["tokens_in"] + r["tokens_out"],
                             "cost_usd": r["cost_usd"]} for r in payload["runs"]],
            },
            "Memory": {
                "detail": "heals when the same release is run again (r2 x3)",
                "runs": [{"label": r["label"], "healed_steps": r["healed_steps"],
                          "replayed_steps": r["replayed_steps"]}
                         for r in [main_r2] + probe_runs if r is not None],
                "heals_first_run": main_r2["healed_steps"] if main_r2 else 0,
                "heals_repeat_runs": sum(r["healed_steps"] for r in probe_runs),
            },
            "Generation": {
                "detail": "zero-knowledge coverage (mode B) and how much of it baselined",
                "coverage": payload.get("coverage"),
                "baseline_success": payload.get("baseline_success"),
                "atlas": payload.get("atlas_counts"),
                "login_wall": bool(payload.get("login_wall")),
                "generate_log": payload.get("generate_log", []),
                "authored": authored,
                "intents": len(payload["intents"]),
            },
        },
        "runs": [{k: v for k, v in r.items() if k != "tests"} for r in payload["runs"]],
        "config": payload.get("config", {}),
        "intent_map": payload.get("intent_map", {}),
        "generated": payload.get("generated", []),
    }


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(row) + " |" for row in rows]
    return out


def _esc(text: str, limit: int = 220) -> str:
    text = (text or "").replace("|", "/").replace("\n", " ").strip()
    return text[:limit]


def build_scorecard(modes: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """Render scorecard.md + scorecard.json from the scored modes."""
    data = {
        "schema": "argus-exam-scorecard-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app": "MediQueue (exam_app) - hold-out, blind",
        "suite": "exam/argus_suite.json (one test per exam/intents.json id)",
        "server": BASE_URL,
        "llm_budget": {"limit": LLM_BUDGET, "used": _LLM["calls"], "cached": _LLM["cached"],
                       "tokens_in": _LLM["tokens_in"], "tokens_out": _LLM["tokens_out"],
                       "by_purpose": dict(_LLM["by_purpose"])},
        "modes": {m["mode"]: m for m in modes},
    }
    lines: list[str] = [
        "# Argus blind exam scorecard - MediQueue (hold-out app)",
        "",
        f"Generated {data['generated_at']} | suite `exam/argus_suite.json` | "
        f"app `{BASE_URL}` | LLM budget {_LLM['calls']}/{LLM_BUDGET} non-cached calls",
        "",
        "Argus saw only `base_url`, the front-desk credentials and `exam/context` "
        "(PRODUCT.md + CHANGELOG.md). `exam/answer_key.json` was read once, by the scorer, "
        "after every run had finished.",
        "",
        "## Overall accuracy",
        "",
    ]
    lines += _md_table(
        ["Mode", "LLM", "Correct", "Scored cases", "Accuracy"],
        [[m["title"], "on" if m["llm_on"] else "off",
          str(m["overall"]["correct"]), str(m["overall"]["total"]),
          _pct(m["overall"]["accuracy"], m["overall"]["total"])] for m in modes],
    )
    lines += ["", "### Per release", ""]
    release_rows = []
    for mode in modes:
        for release in SCORED_RELEASES:
            pr = mode["per_release"].get(release)
            if pr:
                release_rows.append([mode["mode"], release, str(pr["correct"]), str(pr["total"]),
                                     _pct(pr["accuracy"], pr["total"])])
    lines += _md_table(["Mode", "Release", "Correct", "Intents scored", "Accuracy"], release_rows)

    off = next((m for m in modes if m["mode"] == "a_nollm"), None)
    on = next((m for m in modes if m["mode"] == "a_llm"), None)
    if off and on:
        rows = []
        for release in SCORED_RELEASES:
            a, b = off["per_release"].get(release), on["per_release"].get(release)
            if not a or not b:
                continue
            delta = b["correct"] - a["correct"]
            rows.append([release, f"{a['correct']}/{a['total']}", f"{b['correct']}/{b['total']}",
                         f"{delta:+d}"])
        rows.append(["**all scored**",
                     f"{off['overall']['correct']}/{off['overall']['total']}",
                     f"{on['overall']['correct']}/{on['overall']['total']}",
                     f"{on['overall']['correct'] - off['overall']['correct']:+d}"])
        lines += ["", "### LLM off vs LLM on (same suite, same releases, same resets)", ""]
        lines += _md_table(["Release", "LLM off", "LLM on", "Delta"], rows)
        fixed = {(f["release"], f["intent"]) for f in off["failures"]}
        broken = {(f["release"], f["intent"]) for f in on["failures"]}
        lines += ["", f"Fixed by the LLM: {', '.join(f'{r}/{i}' for r, i in sorted(fixed - broken)) or 'none'}"
                      f" | newly wrong: {', '.join(f'{r}/{i}' for r, i in sorted(broken - fixed)) or 'none'}", ""]

    # ── weakness roll-up: what actually costs Argus points ──
    buckets: dict[str, list[dict[str, Any]]] = {}
    for mode in modes:
        for failure in mode["failures"]:
            buckets.setdefault(failure["weakness"], []).append({**failure, "mode": mode["mode"]})
    blockers = []
    for mode in modes:
        blockers += [{"mode": mode["mode"], "id": f["id"], "why": f["error"]}
                     for f in mode["author_failures"]]
        if mode["criteria"]["Generation"].get("login_wall"):
            blockers.append({"mode": mode["mode"], "id": "zero-knowledge generation",
                             "why": "explore() cannot authenticate: the session lives in "
                                    "sessionStorage, which storage_state does not carry"})
    lines += ["", "## Weakness roll-up", ""]
    if buckets:
        lines += _md_table(
            ["Likely Argus weakness", "Wrong verdicts", "Where", "Example observation"],
            [[label, str(len(items)),
              " ".join(sorted({f"{i['release']}/{i['intent']}" for i in items}))[:150],
              _esc(items[0]["observation"], 130)]
             for label, items in sorted(buckets.items(), key=lambda kv: -len(kv[1]))],
        )
    else:
        lines += ["No wrong verdicts.", ""]
    if blockers:
        lines += ["", "### Blocked before scoring", ""]
        lines += _md_table(["Mode", "Intent / stage", "Why"],
                           [[b["mode"], b["id"], _esc(b["why"], 200)] for b in blockers])

    lines += ["", "## Criteria", ""]
    for mode in modes:
        lines += [f"### {mode['title']}", ""]
        crit = mode["criteria"]
        rows = [
            ["Reliability (r2 + memory probe)",
             f"{crit['Reliability']['correct']}/{crit['Reliability']['total']}"
             f" ({_pct(crit['Reliability']['accuracy'], crit['Reliability']['total'])})",
             f"probe heals {crit['Reliability']['memory_probe_heals']}"],
            ["Context (r3-r6)",
             f"{crit['Context']['correct']}/{crit['Context']['total']} "
             f"({_pct(crit['Context']['accuracy'], crit['Context']['total'])})",
             " ".join(f"{k}={_pct(v, mode['per_release'].get(k, {}).get('total', 0))}"
                      for k, v in crit["Context"]["per_release"].items())],
            ["Cost",
             f"{crit['Cost']['llm_calls_total']} calls / {crit['Cost']['tokens_total']:,} tokens",
             f"{len(crit['Cost']['per_run'])} runs"],
            ["Memory (repeated r2 runs)",
             f"first {crit['Memory']['heals_first_run']} -> repeats "
             f"{crit['Memory']['heals_repeat_runs']} heals",
             ", ".join(f"{r['label'].split('-')[-1]}:{r['healed_steps']}" for r in crit["Memory"]["runs"])],
            ["Generation",
             (f"coverage {crit['Generation']['coverage']:.0%}" if crit["Generation"]["coverage"] is not None
              else f"authored {crit['Generation']['authored']}/{crit['Generation']['intents']}"),
             (f"baselined {crit['Generation']['baseline_success']['authored']}"
              f"/{crit['Generation']['baseline_success']['requested']}"
              if crit["Generation"]["baseline_success"] else "n/a (mode A)")],
        ]
        lines += _md_table(["Criterion", "Score", "Notes"], rows)
        lines.append("")

    for mode in modes:
        if mode["author_failures"]:
            lines += [f"### Not baselined in {mode['mode']}", ""]
            lines += _md_table(["Intent", "Why"],
                               [[f["id"], _esc(f["error"], 160)] for f in mode["author_failures"]])
            lines.append("")

    for mode in modes:
        if not mode.get("generated") and mode["mode"] != "b_llm":
            continue
        gen = mode["criteria"]["Generation"]
        bs = gen["baseline_success"] or {}
        atlas = gen.get("atlas") or {}
        lines += [f"### Zero-knowledge coverage ({mode['mode']})", "",
                  f"{bs.get('authored', 0)}/{bs.get('requested', 0)} generated tests baselined, "
                  f"{bs.get('dropped', 0)} dropped, intent coverage "
                  f"{(gen['coverage'] or 0):.0%}. Explore reached "
                  f"{atlas.get('states', 0)} states / {atlas.get('actions', 0)} actions.", ""]
        if gen.get("login_wall"):
            lines += [
                "**Generation is blocked at the login wall.** MediQueue keeps its session in "
                "`sessionStorage` (`mq-authenticated`), which Playwright's `storage_state` does not "
                "carry, so every context `explore()` opens is logged out: the crawl can only reach "
                "`/login`, the atlas has no authenticated page to write tests from, and the "
                "zero-knowledge suite is empty. This is the single most damaging weakness found - "
                "mode A's 8/9 authored suite proves the app is testable, mode B cannot discover any "
                "of it on its own.", ""]
        if gen.get("generate_log"):
            lines += ["Generator log:", "", "```"] + gen["generate_log"] + ["```", ""]
        if mode["generated"]:
            lines += _md_table(["Generated test", "Name", "Steps", "Mapped intent"],
                               [[g["id"], _esc(g["name"], 52), str(g["steps"]),
                                 mode["intent_map"].get(g["id"], "(unmapped)")]
                                for g in mode["generated"]])
        if bs.get("dropped_reasons"):
            lines += ["", "Dropped during baselining:", ""]
            lines += [f"- {_esc(reason, 160)}" for reason in bs["dropped_reasons"]]
        lines.append("")

    lines += ["## Failure list", ""]
    any_failure = False
    for mode in modes:
        if not mode["failures"]:
            continue
        any_failure = True
        lines += [f"### {mode['title']}", ""]
        lines += _md_table(
            ["Release", "Intent", "Got", "Acceptable", "Key observation", "Likely Argus weakness"],
            [[f["release"], f["intent"], f["got"], "/".join(f["acceptable"]) or "-",
              _esc(f["observation"], 150), f["weakness"]] for f in mode["failures"]],
        )
        lines.append("")
    if not any_failure:
        lines += ["No wrong verdicts.", ""]

    lines += ["## Run statistics", ""]
    rows = []
    for mode in modes:
        for run in mode["runs"]:
            rows.append([mode["mode"], run["label"], run["release"],
                         "yes" if run["update"] else "no",
                         str(run["tests_run"]), str(run["healed_steps"]), str(run["replayed_steps"]),
                         str(run["llm_calls"]), f"{run['tokens_in'] + run['tokens_out']:,}",
                         f"{run['wall_s']:.0f}s",
                         " ".join(f"{k}:{v}" for k, v in sorted(run["verdicts"].items()))])
    lines += _md_table(["Mode", "Run", "Build", "Update", "Tests", "Heals", "Replayed",
                        "LLM", "Tokens", "Wall", "Verdicts"], rows)

    lines += ["## Method notes and harness configuration", "",
              f"- `thresholds.margin` {HEAL_MARGIN} (stock 0.12): the schedule renders three "
              "identical Cancel/Reschedule buttons, so a perfect 1.00 replay match with a 0.957 "
              "runner-up is refused by the stock margin gate and those intents cannot be baselined.",
              "- `jury_enabled=false`: a 3-member jury costs 3 calls per verdict, which the "
              f"{LLM_BUDGET}-call budget cannot afford; the single smart-tier judge is used.",
              f"- `max_llm_calls_per_run` clamped per phase to stay within {LLM_BUDGET} calls; "
              "phases that exhaust it degrade to rule-based verdicts.",
              "- App data is reset before every run, so each release starts from the same 60 "
              "appointments and verdicts are comparable across releases.",
              "- Baselines are recorded after a data reset: the authoring pass itself books and "
              "cancels appointments, which would otherwise invalidate the fingerprints it just "
              "recorded.",
              "- One spec crash is isolated: `toggle-sms-reminders` dies in "
              "`argus/browser/snapshot.js:getCSSPath` (`ShadowRoot` -> undefined `toLowerCase`), "
              "so it never reaches memory in any mode.",
              ""]
    return "\n".join(lines) + "\n", data


def score_all() -> tuple[str, dict[str, Any]]:
    """Read the answer key once and build the combined scorecard from every stored run."""
    key = load_answer_key()
    modes = []
    for name in RAW_FILES:
        payload = read_raw(name)
        if payload is None:
            continue
        modes.append(score_mode(payload, score_payload(payload, key)))
    if not modes:
        raise RuntimeError(f"no run artifacts in {RESULTS_DIR}")
    return build_scorecard(modes)


# ── server lifecycle ─────────────────────────────────────────────────────────────

def start_server() -> "subprocess.Popen[bytes]":
    """Start the only permitted server: exam_app.server on port 8010."""
    proc = subprocess.Popen(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "exam_app.server", "--port", "8010"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        time.sleep(0.5)
        if server_alive():
            return proc
    proc.kill()
    raise RuntimeError("exam_app server did not come up on port 8010")


def listening_pids() -> list[int]:
    """PIDs listening on port 8010 (used to stop a server this harness did not start)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue)"
             ".OwningProcess | Sort-Object -Unique"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        return []
    pids = []
    for line in (out.stdout or "").split():
        if line.strip().isdigit():
            pids.append(int(line.strip()))
    return pids


def stop_server(proc: "subprocess.Popen[bytes] | None") -> None:
    """Stop the exam server, including one that was already running when the harness started."""
    if proc is not None:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
    for pid in listening_pids():
        if pid == os.getpid():
            continue
        subprocess.run(["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {pid} -Force"],
                       capture_output=True, timeout=20)
    for _ in range(20):
        if not server_alive():
            break
        time.sleep(0.5)
    _log(f"server stopped (alive={server_alive()})")


# ── entry point ───────────────────────────────────────────────────────────────────

async def main() -> int:
    parser = argparse.ArgumentParser(description="MediQueue blind exam harness for Argus")
    parser.add_argument("--llm", action="store_true",
                        help="mode A with the LLM on, plus zero-knowledge mode B")
    parser.add_argument("--mode-b", action="store_true",
                        help="only run the zero-knowledge mode (used to regenerate its artifact)")
    parser.add_argument("--score-only", action="store_true",
                        help="re-render scorecard.md/.json from the stored raw_*.json artifacts")
    parser.add_argument("--keep-server", action="store_true",
                        help="leave the exam server running when the harness exits")
    args = parser.parse_args()

    install_llm_counter()
    load_budget()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    _log(f"LLM budget: {_LLM['calls']}/{LLM_BUDGET} non-cached calls used so far")

    if args.score_only:
        md, data = score_all()
        (RESULTS_DIR / "scorecard.md").write_text(md, encoding="utf-8")
        (RESULTS_DIR / "scorecard.json").write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                                    encoding="utf-8")
        for mode in data["modes"].values():
            _log(f"{mode['mode']:8s} {mode['overall']['correct']:3d}/{mode['overall']['total']:<3d} "
                 f"failures={len(mode['failures'])}")
        return 0

    proc: "subprocess.Popen[bytes] | None" = None
    if not server_alive():
        _log("starting exam_app.server on port 8010")
        proc = start_server()
    try:
        if args.mode_b:
            await run_mode_b()
        elif args.llm:
            await run_mode_a(llm_on=True)
            if budget_left() > 0:
                await run_mode_b()
            else:
                _log("\nMODE B skipped: LLM budget exhausted")
        else:
            await run_mode_a(llm_on=False)
    finally:
        save_budget()
        try:
            switch_release("r1")
            _log("restored release r1 (exam/context/CHANGELOG.md rewritten by the app)")
        except Exception as exc:  # noqa: BLE001
            _log(f"could not restore r1: {exc}")
        if not args.keep_server:
            stop_server(proc)

    md, data = score_all()
    (RESULTS_DIR / "scorecard.md").write_text(md, encoding="utf-8")
    (RESULTS_DIR / "scorecard.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    _log("\n" + "=" * 74)
    for mode in data["modes"].values():
        _log(f"{mode['mode']:8s} {mode['overall']['correct']:3d}/{mode['overall']['total']:<3d} "
             f"({mode['overall']['accuracy']:.0%})  failures={len(mode['failures'])}")
    _log(f"wrote {(RESULTS_DIR / 'scorecard.md')}")
    _log("=" * 74)
    return 0


def run() -> int:
    return asyncio.run(main())


if __name__ == "__main__":
    raise SystemExit(run())
