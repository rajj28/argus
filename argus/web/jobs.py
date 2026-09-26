"""Live job runner behind the Argus web console (docs/WEB_SPEC.md).

One whitelisted scenario at a time. A job shells out to the real Argus CLI with ``--home`` pointed
at the live memory folder, streams its output as ``log`` events, tails the run's screenshot folder as
``shot`` events and finally turns ``report.json`` into ``step`` / ``verdict`` / ``meters`` events.
No user text ever reaches a command line: the scenario table below *is* the whitelist.

Guardrails implemented here: single-slot FIFO queue (max 5 waiting), 1 job per IP / 90 s, 6 min wall
timeout per job, hourly LLM caps (JEV 15, JEV2 15, Groq 60) pushed into the CLI through
``max_llm_calls_per_run``.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Sequence

from argus.llm.providers import PROVIDERS, api_key, split
from argus.models import RunReport
from argus.report.dashboard import load_runs

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HOME = ".argus-live"
DEFAULT_SKYOPS_URL = "http://127.0.0.1:8000"
DEFAULT_SKYOPS_B_URL = "http://127.0.0.1:8001"

JOB_TIMEOUT_S = 360.0            # hard wall clock per job
RATE_LIMIT_WINDOW_S = 90.0       # 1 job per IP per window
MAX_QUEUE = 5                    # waiting jobs before the runner says no
MAX_EVENTS = 5000                # per-job replay buffer
SHOT_POLL_S = 0.4
RUNS_CACHE_S = 2.0               # memo for the live report aggregation
HTTP_TIMEOUT_S = 5.0

MCP_INTENT = "Mission planner v2: parameters before drone selection; new required Pilot in command field"
CHAOS_MUTATIONS = ["ids", "classes", "testids", "wrappers", "order", "text", "tags", "layout"]
DEMO_USER = "pilot@skyops.io"
DEMO_PASSWORD = "flysafe123"

# (display name, provider key in argus.llm.providers, calls per hour)
LLM_CAPS: tuple[tuple[str, str, int], ...] = (("JEV", "openrouter", 15), ("JEV2", "openrouter2", 15),
                                              ("Groq", "groq", 60))
_PROVIDER_NAMES = {provider: name for name, provider, _ in LLM_CAPS}

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CLOSE = object()


class UnknownScenario(ValueError):
    """The requested scenario is not on the whitelist."""


class RateLimited(Exception):
    """Per-IP limit hit; `retry_after` is whole seconds."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"per-IP limit: 1 job / {int(RATE_LIMIT_WINDOW_S)} s")
        self.retry_after = max(1, int(retry_after))


class QueueFull(Exception):
    """The FIFO already holds MAX_QUEUE waiting jobs."""


# --------------------------------------------------------------------------------------
# time helpers
# --------------------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _parse_dt(value: str) -> Optional[datetime]:
    """Parse an ISO timestamp tolerantly; None when unusable."""
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _clean(line: str) -> str:
    """Strip ANSI colour codes and trailing whitespace from one CLI output line."""
    return _ANSI.sub("", line).rstrip()


# --------------------------------------------------------------------------------------
# scenarios (the whitelist)
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    description: str
    expected_cost: str
    uses_llm: bool
    run: Callable[["JobRunner", "Job"], Awaitable[None]]


def _scn_replay(runner: "JobRunner", job: "Job") -> Awaitable[None]:
    return runner.run_release(job, "1.0", "live: replay v1.0")


def _scn_refresh(runner: "JobRunner", job: "Job") -> Awaitable[None]:
    return runner.run_release(job, "1.1", "live: design refresh v1.1")


def _scn_redesign(runner: "JobRunner", job: "Job") -> Awaitable[None]:
    return runner.run_release(job, "1.2", "live: mission planner v2")


def _scn_hidden_bugs(runner: "JobRunner", job: "Job") -> Awaitable[None]:
    return runner.run_release(job, "1.3", "live: hidden bugs v1.3", no_update=True)


def _scn_chaos(runner: "JobRunner", job: "Job") -> Awaitable[None]:
    return runner.run_chaos(job)


def _scn_diff(runner: "JobRunner", job: "Job") -> Awaitable[None]:
    return runner.run_diff(job)


def _scn_generate(runner: "JobRunner", job: "Job") -> Awaitable[None]:
    return runner.run_generate(job)


def _scn_mcp(runner: "JobRunner", job: "Job") -> Awaitable[None]:
    return runner.run_mcp(job)


def _scn_cli(runner: "JobRunner", job: "Job") -> Awaitable[None]:
    return runner.run_cli(job)


SCENARIOS: dict[str, Scenario] = {s.id: s for s in (
    Scenario("replay", "Replay suite (v1.0)", "Deploy 1.0, replay the baselined suite deterministically.",
             "$0 · 0 LLM calls", False, _scn_replay),
    Scenario("refresh", "Deploy design refresh (v1.1) & run",
             "Ids renamed, classes hashed, nav to sidebar; Argus self-heals.", "$0 · 0 LLM calls", False, _scn_refresh),
    Scenario("redesign", "Deploy Mission planner v2 (v1.2) & run",
             "Wizard reordered, new required field, Flight logs retired.", "$0 · 0 LLM calls", False, _scn_redesign),
    Scenario("hidden-bugs", "Deploy hidden-bug release (v1.3) & run",
             "Five silent regressions behind \"performance improvements\".", "$0 · 0 LLM calls", False,
             _scn_hidden_bugs),
    Scenario("chaos", "Random chaos refactor & run",
             "Random-seed DOM mutations across the app, then replay.", "$0 · 0 LLM calls", False, _scn_chaos),
    Scenario("diff", "Differential diff v1.0 <-> v1.3",
             "Same intents on two live builds; no changelog, no LLM.", "$0 · 0 LLM calls", False, _scn_diff),
    Scenario("generate", "Generate a suite from zero",
             "Explore the app, author journeys + negative tests, baseline.", "~2k tokens · LLM", True, _scn_generate),
    Scenario("mcp", "MCP: verify_change", "Stream the JSON-RPC frames a coding agent sends Argus.",
             "1 LLM call", True, _scn_mcp),
    Scenario("cli", "CLI: doctor / models / status", "Run the three read-only CLI commands in sequence.",
             "$0 · 0 LLM calls", False, _scn_cli),
)}


def scenario_catalogue() -> list[dict[str, Any]]:
    """`GET /api/scenarios` payload: the whitelist with its prices, never a command."""
    return [{"id": s.id, "title": s.title, "description": s.description,
             "expected_cost": s.expected_cost, "uses_llm": s.uses_llm} for s in SCENARIOS.values()]


# --------------------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------------------

@dataclass
class Job:
    """One queued/running/finished scenario plus its replayable event buffer."""

    id: str
    scenario: str
    client: str
    state: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    exit_code: Optional[int] = None
    error: str = ""
    run_id: Optional[str] = None
    home: Optional[Path] = None
    known_runs: set[str] = field(default_factory=set)   # run dirs that existed when the job started
    events: list[dict[str, Any]] = field(default_factory=list)
    queues: set[asyncio.Queue] = field(default_factory=set)
    first_seq: int = 1
    next_seq: int = 1

    @property
    def elapsed_ms(self) -> int:
        end = self.finished_at if self.finished_at else time.time()
        start = self.started_at if self.started_at else self.created_at
        return int(max(0.0, end - start) * 1000)

    def buffer(self) -> list[tuple[int, dict[str, Any]]]:
        """Buffered events as (sequence, event) pairs."""
        return [(self.first_seq + i, e) for i, e in enumerate(self.events)]

    def push(self, event: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Append one event, trim the buffer and hand it to every live subscriber."""
        seq = self.next_seq
        self.next_seq += 1
        self.events.append(event)
        overflow = len(self.events) - MAX_EVENTS
        if overflow > 0:
            del self.events[:overflow]
            self.first_seq += overflow
        for queue in list(self.queues):
            try:
                queue.put_nowait((seq, event))
            except asyncio.QueueFull:      # a slow reader loses frames, never the job
                pass
        return seq, event


class JobRunner:
    """Single-slot FIFO queue that runs whitelisted scenarios against the live memory home."""

    def __init__(self, home: str | Path | None = None, skyops_url: str | None = None,
                 skyops_b_url: str | None = None, executor: Callable[["Job"], Awaitable[None]] | None = None,
                 job_timeout_s: float = JOB_TIMEOUT_S) -> None:
        self.home = Path(home or os.environ.get("ARGUS_LIVE_HOME") or DEFAULT_HOME)
        self.skyops_url = (skyops_url or os.environ.get("SKYOPS_URL") or DEFAULT_SKYOPS_URL).rstrip("/")
        self.skyops_b_url = (skyops_b_url or os.environ.get("SKYOPS_B_URL") or DEFAULT_SKYOPS_B_URL).rstrip("/")
        self.job_timeout_s = job_timeout_s
        self.executor = executor          # set by tests to fake a scenario; None -> the real one
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._active: Optional[str] = None
        self._pump: Optional[asyncio.Task] = None
        self._wake = asyncio.Event()
        self._rate: dict[str, float] = {}        # client -> last accepted submit (monotonic)
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._runs_cache: Optional[tuple[float, list[RunReport]]] = None
        self._closing = False

    # ------------------------------------------------------------------ public API
    def scenarios(self) -> list[dict[str, Any]]:
        return scenario_catalogue()

    async def submit(self, scenario: str, client: str) -> dict[str, Any]:
        """Queue a whitelisted scenario. Raises RateLimited / QueueFull / UnknownScenario."""
        if scenario not in SCENARIOS:
            raise UnknownScenario(scenario)
        now = time.monotonic()
        self._prune_rate(now)
        last = self._rate.get(client)
        if last is not None and now - last < RATE_LIMIT_WINDOW_S:
            raise RateLimited(math.ceil(RATE_LIMIT_WINDOW_S - (now - last)))
        if self._waiting() >= MAX_QUEUE:
            raise QueueFull()
        self._rate[client] = now
        job = Job(id=f"job-{_stamp()}-{secrets.token_hex(2)}", scenario=scenario, client=client, home=self.home)
        self._jobs[job.id] = job
        self._order.append(job.id)
        await self.emit(job, {"type": "state", "state": "queued"})
        self._ensure_pump()
        self._wake.set()
        return {"job_id": job.id, "queue_position": self.queue_position(job.id)}

    def status(self, job_id: str) -> Optional[dict[str, Any]]:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        out: dict[str, Any] = {
            "job_id": job.id,
            "scenario": job.scenario,
            "state": job.state,
            "queue_position": self.queue_position(job.id),
            "started_at": _iso(job.started_at) if job.started_at else None,
            "elapsed_ms": job.elapsed_ms,
        }
        if job.run_id:
            out["run_id"] = job.run_id
        if job.finished_at:
            out["finished_at"] = _iso(job.finished_at)
        if job.exit_code is not None:
            out["exit_code"] = job.exit_code
        if job.error:
            out["error"] = job.error[:500]
        return out

    def queue_position(self, job_id: str) -> int:
        """0 for the running job, otherwise the number of jobs ahead of it."""
        pending = [jid for jid in self._order if self._jobs[jid].state == "queued"]
        return pending.index(job_id) if job_id in pending else 0

    async def events(self, job_id: str) -> AsyncIterator[dict[str, Any]]:
        """Replay the buffered events, then follow the live stream until the job ends."""
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
        job.queues.add(queue)
        seen = 0
        try:
            while True:
                for seq, event in job.buffer():
                    if seq > seen:
                        seen = seq
                        yield event
                if job.state in ("done", "failed") and seen >= job.next_seq - 1:
                    return
                item = await queue.get()
                if item is _CLOSE:
                    return
                seq, event = item
                if seq > seen:
                    seen = seq
                    yield event
        finally:
            job.queues.discard(queue)

    def runs(self) -> list[RunReport]:
        """Live run reports, newest first (tolerant loader shared with the dashboard).

        Parsing every report is not free, so results are memoised for a couple of seconds; the cache
        is dropped as soon as a job finishes.
        """
        now = time.monotonic()
        cached = self._runs_cache
        if cached is not None and now - cached[0] < RUNS_CACHE_S:
            return cached[1]
        reports = load_runs(self.home)
        self._runs_cache = (now, reports)
        return reports

    def invalidate_runs(self) -> None:
        self._runs_cache = None

    def latest_run(self) -> Optional[RunReport]:
        reports = self.runs()
        return reports[0] if reports else None

    def live_stats(self) -> dict[str, Any]:
        """`live` block of `/api/overview`: runs, last run, LLM calls today, tier histogram."""
        reports = self.runs()
        tiers: dict[str, int] = {}
        calls_today = 0
        today = _utc_now().date()
        for report in reports:
            for tier, count in report.totals.tiers.items():
                key = str(tier)
                tiers[key] = tiers.get(key, 0) + int(count)
            started = _parse_dt(report.started_at)
            if started and started.date() == today:
                calls_today += int(report.totals.llm_calls)
        return {
            "runs_total": len(reports),
            "last_run_at": reports[0].started_at if reports else None,
            "llm_calls_today": calls_today,
            "tiers": {k: tiers[k] for k in sorted(tiers, key=lambda t: int(t) if t.isdigit() else 99)},
        }

    def llm_usage(self) -> dict[str, int]:
        """Non-cached LLM calls per capped provider over the last hour, from live run reports."""
        cutoff = _utc_now() - timedelta(hours=1)
        counts = {name: 0 for name, _, _ in LLM_CAPS}
        for report in self.runs():
            started = _parse_dt(report.started_at)
            if started is None or started < cutoff:
                continue
            for result in report.results:
                for call in result.llm_calls:
                    if call.cached or not call.model:
                        continue
                    name = _PROVIDER_NAMES.get(split(call.model)[0])
                    if name:
                        counts[name] += 1
        return counts

    def llm_status(self) -> list[dict[str, Any]]:
        """`GET /api/llm` payload. Availability is a bool; keys are never read out."""
        usage = self.llm_usage()
        out = []
        for name, provider, cap in LLM_CAPS:
            cfg = PROVIDERS.get(provider)
            available = bool(cfg and api_key(cfg))
            out.append({"name": name, "available": available, "calls_this_hour": usage[name],
                        "cap_per_hour": cap, "exhausted": usage[name] >= cap})
        return out

    def remaining_llm_calls(self) -> int:
        """Hourly budget left across the capped providers (0 when nothing is available)."""
        usage = self.llm_usage()
        return sum(max(0, cap - usage[name]) for name, _, cap in LLM_CAPS)

    async def close(self) -> None:
        """Stop the pump and any child process; safe to call twice."""
        self._closing = True
        self._wake.set()
        pump, self._pump = self._pump, None
        if pump is not None:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(pump, timeout=10)
        self._close_all()

    # ------------------------------------------------------------------ events
    async def emit(self, job: Job, event: dict[str, Any]) -> None:
        job.push(event)

    async def log(self, job: Job, line: str, stream: str = "stdout") -> None:
        text = _clean(line)
        if text:
            await self.emit(job, {"type": "log", "stream": stream, "line": text})

    # ------------------------------------------------------------------ scenarios
    async def run_release(self, job: Job, version: str, label: str, no_update: bool = False) -> None:
        """Deploy `version` to SkyOps, then replay the suite against it."""
        await self.deploy(job, version)
        args = ["run", "--build", version, "--label", label]
        if no_update:
            args.append("--no-update")
        await self.apply_llm_cap(job)
        await self.cli(job, args)

    async def run_chaos(self, job: Job) -> None:
        """Deploy 1.0, flip every mutation on with a random seed, replay, flip chaos off."""
        await self.deploy(job, "1.0")
        seed = 100 + secrets.randbelow(9000)
        await self.chaos(job, seed, CHAOS_MUTATIONS)
        try:
            await self.apply_llm_cap(job)
            await self.cli(job, ["run", "--build", "1.0", "--no-update", "--label", f"live: chaos seed={seed}"])
        finally:
            await self.chaos(job, None, [])
            await self.log(job, "chaos disabled")

    async def run_diff(self, job: Job) -> None:
        """Differential execution between the two SkyOps builds (skyops 1.0 vs skyops-b 1.3)."""
        await self.deploy(job, "1.0")
        await self.log(job, f"baseline={self.skyops_url} (v1.0)  candidate={self.skyops_b_url} (v1.3)")
        await self.apply_llm_cap(job)
        await self.cli(job, ["diff", "--baseline", self.skyops_url, "--candidate", self.skyops_b_url])
        data = self._read_json(self.home_for(job) / "diff.json")
        if isinstance(data, dict) and isinstance(data.get("summary"), dict):
            for cls, count in data["summary"].items():
                await self.log(job, f"  {cls}: {count}")

    async def run_generate(self, job: Job) -> None:
        """Fresh temporary home: explore -> generate (LLM) -> one run."""
        home = self.home / "_generate"
        shutil.rmtree(home, ignore_errors=True)
        home.mkdir(parents=True, exist_ok=True)
        job.home = home
        job.known_runs = self._known_runs(job)
        user, password = self._credentials()
        await self.apply_llm_cap(job)
        await self.cli(job, ["init", "--url", self.skyops_url, "--context", "demo_app/context",
                             "--user", user, "--password", password], home=home)
        await self.cli(job, ["explore", "--max-states", "12", "--max-actions", "40"], home=home)
        await self.cli(job, ["generate", "--max-tests", "4"], home=home)
        await self.cli(job, ["run", "--label", "live: generated suite"], home=home)

    async def run_mcp(self, job: Job) -> None:
        """Drive `argus mcp` over stdio with the official client, streaming every JSON-RPC frame."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        home = self.home_for(job)
        await self.apply_llm_cap(job)
        await self.log(job, f"$ argus mcp --home {home}   (stdio, official mcp client)")
        params = StdioServerParameters(command=sys.executable, args=["-m", "argus", "mcp", "--home", str(home)],
                                       env=self.child_env())
        async with stdio_client(params) as (read, write):
            async with ClientSession(_FrameReader(read, self, job), _FrameWriter(write, self, job)) as session:
                await session.initialize()
                for tool in (await session.list_tools()).tools:
                    await self.log(job, f"tool  {tool.name}: {(tool.description or '').splitlines()[0]}")
                last = await session.call_tool("last_report", {})
                await self.log(job, f"last_report -> {_tool_text(last)[:400]}")
                digest = await session.call_tool("verify_change", {"description": MCP_INTENT})
                await self.log(job, f"verify_change -> {_tool_text(digest)[:1200]}")

    async def run_cli(self, job: Job) -> None:
        """The three read-only CLI commands, in sequence."""
        for args in (["doctor"], ["models"], ["status"]):
            code = await self.cli(job, args, check=False)
            if code:
                await self.log(job, f"argus {args[0]} exited with {code}", stream="stderr")

    # ------------------------------------------------------------------ SkyOps
    async def deploy(self, job: Job, version: str) -> None:
        """Switch the live SkyOps build (and rewrite the changelog the triage reads)."""
        await self.log(job, f"$ python -m demo_app.deploy {version} {self.skyops_url}")
        await asyncio.to_thread(self._deploy, version)
        await self.log(job, f"SkyOps is now v{version}")

    def _deploy(self, version: str) -> None:
        from demo_app.deploy import deploy as deploy_build

        deploy_build(version, self.skyops_url)

    async def chaos(self, job: Job, seed: Optional[int], mutations: Sequence[str]) -> None:
        """POST `/__admin/chaos` (seed None switches chaos off)."""
        await self.log(job, f"POST {self.skyops_url}/__admin/chaos  seed={seed} mutations={list(mutations)}")
        await asyncio.to_thread(post_json, f"{self.skyops_url}/__admin/chaos",
                                {"seed": seed, "mutations": list(mutations)})

    def skyops_state(self) -> dict[str, Any]:
        """Proxy of SkyOps `/__admin/state`; raises OSError/ValueError when unreachable."""
        with urllib.request.urlopen(f"{self.skyops_url}/__admin/state", timeout=HTTP_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("SkyOps state is not an object")
        return data

    # ------------------------------------------------------------------ subprocesses
    def child_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    async def cli(self, job: Job, args: Sequence[str], home: Path | None = None, check: bool = True) -> int:
        """Run the real CLI as a subprocess and stream its output as `log` events."""
        target = Path(home or self.home_for(job))
        cmd = [sys.executable, "-m", "argus", *args, "--home", str(target)]
        await self.log(job, "$ " + " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(REPO_ROOT), env=self.child_env(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt")   # own process group, so kill_tree never hits the server
        self._proc = proc
        try:
            await asyncio.gather(self._pump_stream(job, proc.stdout, "stdout"),
                                 self._pump_stream(job, proc.stderr, "stderr"))
            code = await proc.wait()
        except BaseException:
            await asyncio.to_thread(kill_tree, proc)
            raise
        finally:
            self._proc = None
        job.exit_code = code
        if check and code != 0:
            raise RuntimeError(f"`argus {' '.join(args)}` exited with {code}")
        return code

    async def _pump_stream(self, job: Job, stream: Optional[asyncio.StreamReader], name: str) -> None:
        if stream is None:
            return
        while True:
            raw = await stream.readline()
            if not raw:
                return
            await self.log(job, raw.decode("utf-8", "replace"), stream=name)

    # ------------------------------------------------------------------ report + shots
    def home_for(self, job: Job) -> Path:
        return Path(job.home) if job.home else self.home

    async def emit_report(self, job: Job) -> None:
        """Turn the newest run report of this job into step / verdict / meters events."""
        run_id = self._job_run_id(job)
        if not run_id:
            return
        report = self._read_report(run_id, self.home_for(job))
        if report is None:
            return
        job.run_id = run_id
        for result in report.results:
            for step in result.steps:
                await self.emit(job, {"type": "step", "test": result.test_id, "step_id": step.step_id,
                                      "intent": step.intent, "status": step.status, "tier": step.tier,
                                      "score": step.score})
            await self.emit(job, {"type": "verdict", "test": result.test_id, "category": result.verdict.category,
                                  "rationale": result.verdict.rationale, "changelog_refs": result.verdict.changelog_refs,
                                  "bug_report": result.bug_report})
        await self.emit(job, {"type": "meters", **report.totals.model_dump(mode="json")})
        await self.log(job, f"run {run_id}: {report.totals.passed}/{report.totals.tests} passed, "
                            f"{report.totals.llm_calls} LLM calls, ${report.totals.cost_usd:.4f}")

    async def watch_shots(self, job: Job, stop: asyncio.Event) -> None:
        """Tail `runs/<newest>/screenshots` and emit a `shot` event per new jpeg."""
        seen: set[str] = set()
        run_dir: Optional[Path] = None
        while not stop.is_set():
            try:
                newest = self._job_run_dir(job)
                if newest is not None and newest != run_dir:
                    run_dir = newest
                    seen = set()                    # a fresh run dir: every jpeg in it is this job's
                if run_dir is not None:
                    for shot in sorted((run_dir / "screenshots").glob("*.jpg")):
                        if shot.name in seen:
                            continue
                        seen.add(shot.name)
                        await self.emit(job, {"type": "shot", "name": shot.name, "test": _test_of(shot.name),
                                              "url": f"/api/runs/{run_dir.name}/shots/{shot.name}"})
            except Exception:                     # a watcher must never kill the job
                pass
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=SHOT_POLL_S)

    # ------------------------------------------------------------------ internals
    async def apply_llm_cap(self, job: Job) -> int:
        """Push the remaining hourly LLM budget into the CLI's per-run cap."""
        from argus.config import load_settings

        home = self.home_for(job)
        remaining = self.remaining_llm_calls()
        try:
            settings = load_settings(home)
        except Exception as exc:                    # unreadable home: leave the cap alone
            await self.log(job, f"LLM cap not applied ({exc})", stream="stderr")
            return remaining
        settings.max_llm_calls_per_run = max(0, min(int(settings.max_llm_calls_per_run), remaining))
        try:
            settings.save()
        except OSError as exc:
            await self.log(job, f"LLM cap not persisted ({exc})", stream="stderr")
        await self.log(job, f"LLM cap for this run: {settings.max_llm_calls_per_run} call(s)")
        return settings.max_llm_calls_per_run

    def _credentials(self) -> tuple[str, str]:
        from argus.config import load_settings

        try:
            creds = load_settings(self.home).credentials
        except Exception:
            creds = {}
        return creds.get("user") or DEMO_USER, creds.get("password") or DEMO_PASSWORD

    def _known_runs(self, job: Job) -> set[str]:
        runs_dir = self.home_for(job) / "runs"
        if not runs_dir.is_dir():
            return set()
        return {d.name for d in runs_dir.iterdir() if d.is_dir()}

    def _job_run_dir(self, job: Job) -> Optional[Path]:
        """Newest run directory created after this job started (None before it appears)."""
        runs_dir = self.home_for(job) / "runs"
        if not runs_dir.is_dir():
            return None
        candidates = [d for d in runs_dir.iterdir() if d.is_dir() and d.name not in job.known_runs]
        if not candidates:
            return None
        return max(candidates, key=lambda d: d.stat().st_mtime)

    def _job_run_id(self, job: Job) -> Optional[str]:
        run_dir = self._job_run_dir(job)
        return run_dir.name if run_dir else None

    def _read_report(self, run_id: str, home: Path) -> Optional[RunReport]:
        path = home / "runs" / run_id / "report.json"
        if not path.is_file():
            return None
        try:
            return RunReport.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _read_json(self, path: Path) -> Any:
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _prune_rate(self, now: float) -> None:
        """Forget every client outside the rate-limit window: nothing is kept beyond it."""
        for client, stamp in list(self._rate.items()):
            if now - stamp >= RATE_LIMIT_WINDOW_S:
                del self._rate[client]

    def _waiting(self) -> int:
        return sum(1 for job in self._jobs.values() if job.state == "queued")

    def _ensure_pump(self) -> None:
        if self._pump is None or self._pump.done():
            self._pump = asyncio.create_task(self._pump_loop())

    def _close_all(self) -> None:
        for job in list(self._jobs.values()):
            for queue in list(job.queues):
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(_CLOSE)
        if self._proc is not None:
            with contextlib.suppress(Exception):
                kill_tree(self._proc)

    async def _pump_loop(self) -> None:
        while not self._closing:
            job = self._next_job()
            if job is None:
                self._wake.clear()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=5.0)
                continue
            self._active = job.id
            try:
                await self._execute(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:                # scenario blew up: the job fails, the pump lives
                await self._fail(job, f"{type(exc).__name__}: {exc}")
            finally:
                self._active = None

    def _next_job(self) -> Optional[Job]:
        for jid in self._order:
            job = self._jobs[jid]
            if job.state == "queued":
                return job
        return None

    async def _execute(self, job: Job) -> None:
        job.state = "running"
        job.started_at = time.time()
        job.known_runs = self._known_runs(job)
        scenario = SCENARIOS.get(job.scenario)
        await self.emit(job, {"type": "state", "state": "running"})
        await self.log(job, f"scenario {job.scenario}"
                             f"{'' if scenario is None else ' - ' + scenario.title}", stream="stderr")
        stop = asyncio.Event()
        watcher = asyncio.create_task(self.watch_shots(job, stop))
        try:
            work = self.executor(job) if self.executor is not None else (
                scenario.run(self, job) if scenario is not None else self._reject(job.scenario))
            await asyncio.wait_for(work, timeout=self.job_timeout_s)
            job.state = "done"
        except asyncio.TimeoutError:
            await self._fail(job, f"wall timeout after {int(self.job_timeout_s)}s")
        except asyncio.CancelledError:
            await self._fail(job, "cancelled")
            raise
        except Exception as exc:
            await self._fail(job, f"{type(exc).__name__}: {exc}")
        finally:
            stop.set()
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await watcher
            if job.finished_at is None:
                job.finished_at = time.time()
            self.invalidate_runs()
            await self.emit_report(job)
            await self.emit(job, {"type": "state", "state": job.state})
            for queue in list(job.queues):
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(_CLOSE)

    async def _reject(self, scenario: str) -> None:
        raise UnknownScenario(scenario)

    async def _fail(self, job: Job, message: str) -> None:
        job.error = message or "job failed"
        job.state = "failed"
        await self.log(job, job.error, stream="stderr")


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------

def _test_of(name: str) -> str:
    """`create-mission_03.jpg` -> `create-mission`; `login_final.jpg` -> `login`."""
    return name.rsplit("_", 1)[0] if "_" in name else Path(name).stem


def _tool_text(result: Any) -> str:
    parts = getattr(result, "content", None) or []
    return "".join(getattr(part, "text", "") or "" for part in parts)


def _frame_dict(message: Any) -> dict[str, Any]:
    """A stdio message -> the JSON-RPC frame that travelled on the wire.

    Handles both wrappings the mcp SDK has shipped: SessionMessage(message=...) and the
    JSONRPCMessage(root=...) union; older/newer versions differ.
    """
    inner = getattr(message, "message", message)
    root = getattr(inner, "root", inner)
    dump = getattr(root, "model_dump", None)
    if dump is None:
        return {"raw": str(root)}
    return dump(mode="json", by_alias=True, exclude_none=True)


class _FrameReader:
    """Read stream wrapper that emits every inbound JSON-RPC frame as an `mcp` event."""

    def __init__(self, stream: Any, runner: JobRunner, job: Job) -> None:
        self._stream = stream
        self._runner = runner
        self._job = job

    def __aiter__(self) -> "_FrameReader":
        return self

    async def __anext__(self) -> Any:
        message = await self._stream.__anext__()
        await self._runner.emit(self._job, {"type": "mcp", "dir": "<-", "frame": _frame_dict(message)})
        return message

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._stream.aclose()

    async def __aenter__(self) -> "_FrameReader":
        enter = getattr(self._stream, "__aenter__", None)
        if enter is not None:
            await enter()
        return self

    async def __aexit__(self, *exc: Any) -> bool | None:
        exit_ = getattr(self._stream, "__aexit__", None)
        if exit_ is not None:
            return await exit_(*exc)
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


class _FrameWriter:
    """Write stream wrapper that emits every outbound JSON-RPC frame as an `mcp` event."""

    def __init__(self, stream: Any, runner: JobRunner, job: Job) -> None:
        self._stream = stream
        self._runner = runner
        self._job = job

    async def send(self, message: Any) -> None:
        await self._runner.emit(self._job, {"type": "mcp", "dir": "->", "frame": _frame_dict(message)})
        await self._stream.send(message)

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._stream.aclose()

    async def __aenter__(self) -> "_FrameWriter":
        enter = getattr(self._stream, "__aenter__", None)
        if enter is not None:
            await enter()
        return self

    async def __aexit__(self, *exc: Any) -> bool | None:
        exit_ = getattr(self._stream, "__aexit__", None)
        if exit_ is not None:
            return await exit_(*exc)
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def post_json(url: str, payload: dict[str, Any], timeout: float = HTTP_TIMEOUT_S) -> dict[str, Any]:
    """POST a JSON payload with urllib and return the decoded body ({} on failure)."""
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError, ValueError):
        return {}
    try:
        data = json.loads(body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def kill_tree(proc: Any) -> None:
    """Terminate a child process and everything it spawned (chromium included)."""
    if proc is None or proc.returncode is not None:
        return
    if os.name == "nt":
        with contextlib.suppress(Exception):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=20)
        return
    with contextlib.suppress(Exception):
        os.killpg(os.getpgid(proc.pid), 15)
    with contextlib.suppress(Exception):
        proc.terminate()
