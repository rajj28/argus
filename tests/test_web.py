"""Contract tests for argus/web (API shape, guardrails, SSE framing).

No network, no browser, no LLM: every job is faked through `JobRunner(executor=...)` and the live
memory home points at a tmp dir.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from argus.web.app import RECORDED, create_app
from argus.web.jobs import MAX_QUEUE, SCENARIOS, JobRunner

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01test-image"
RUN_ID = "20260924-175006-9945"
SAMPLE_REPORT = {
    "run_id": RUN_ID,
    "started_at": "2026-09-24T17:50:06+00:00",
    "app_url": "http://skyops:8000",
    "label": "live: hidden bugs v1.3",
    "results": [
        {
            "test_id": "login",
            "test_name": "Pilot can sign in",
            "status": "passed",
            "verdict": {"category": "PASS", "rationale": "Replayed exactly."},
            "steps": [{"step_id": "s1", "intent": "Enter the pilot email", "status": "passed", "tier": 0,
                       "score": 1.0, "screenshot": "screenshots/login_01.jpg"}],
        },
        {
            "test_id": "save-settings",
            "test_name": "Settings persist",
            "status": "failed",
            "verdict": {"category": "BUG", "rationale": "PUT /api/settings returned 500.",
                        "changelog_refs": []},
            "steps": [{"step_id": "s1", "intent": "Save preferences", "status": "failed", "tier": 0,
                       "score": 0.2, "screenshot": "screenshots/save-settings_01.jpg"}],
            "bug_report": "## BUG: settings 500\nsteps to reproduce",
        },
    ],
    "totals": {"tests": 2, "passed": 1, "failed": 1, "healed_steps": 0, "replayed_steps": 2, "llm_calls": 0,
               "llm_calls_cached": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "list_cost_usd": 0.0,
               "naive_tokens_estimate": 22118, "verdicts": {"PASS": 1, "BUG": 1}, "tiers": {"0": 2},
               "duration_ms": 37524},
}


def _home(tmp_path: Path) -> Path:
    """A live memory home with one recorded run and its screenshots."""
    home = tmp_path / ".argus-live"
    run_dir = home / "runs" / RUN_ID
    (run_dir / "screenshots").mkdir(parents=True)
    (run_dir / "report.json").write_text(json.dumps(SAMPLE_REPORT), encoding="utf-8")
    (run_dir / "screenshots" / "login_01.jpg").write_bytes(JPEG)
    (run_dir / "screenshots" / "save-settings_01.jpg").write_bytes(JPEG)
    (home / "config.json").write_text(json.dumps({"base_url": "http://127.0.0.1:8000"}), encoding="utf-8")
    return home


def _fake_job(runner: JobRunner) -> Callable[[Any], Awaitable[None]]:
    """A scenario stand-in that emits one of every event type and finishes instantly."""

    async def _run(job) -> None:
        await runner.log(job, "fake scenario running")
        await runner.emit(job, {"type": "shot", "name": "login_01.jpg", "test": "login",
                                "url": f"/api/runs/{RUN_ID}/shots/login_01.jpg"})
        await runner.emit(job, {"type": "step", "test": "login", "step_id": "s1", "intent": "Sign in",
                                "status": "passed", "tier": 0, "score": 1.0})
        await runner.emit(job, {"type": "verdict", "test": "save-settings", "category": "BUG",
                                "rationale": "PUT /api/settings returned 500.", "changelog_refs": [],
                                "bug_report": "## BUG: settings 500"})
        await runner.emit(job, {"type": "mcp", "dir": "->", "frame": {"jsonrpc": "2.0", "id": 0,
                                                                     "method": "tools/list", "params": {}}})
        await runner.emit(job, {"type": "meters", "tests": 2, "passed": 1, "failed": 1, "healed_steps": 0,
                                "replayed_steps": 2, "llm_calls": 0, "llm_calls_cached": 0, "tokens_in": 0,
                                "tokens_out": 0, "cost_usd": 0.0, "list_cost_usd": 0.0,
                                "naive_tokens_estimate": 22118, "verdicts": {"PASS": 1, "BUG": 1},
                                "tiers": {"0": 2}, "duration_ms": 37524})

    return _run


async def _block_forever(job) -> None:
    """A scenario stand-in that never finishes, so the queue stays busy."""
    await asyncio.sleep(120)


def _sse_events(text: str) -> list[dict[str, Any]]:
    """Parse an SSE body; only `data:` lines carry JSON."""
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def _wait_for(predicate: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert predicate(), "timed out waiting for the job runner"


def _read_stream(client: TestClient, job_id: str) -> tuple[str, Any]:
    with client.stream("GET", f"/api/jobs/{job_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        return "".join(response.iter_text())


@pytest.fixture()
def runner(tmp_path: Path) -> JobRunner:
    return JobRunner(home=_home(tmp_path), skyops_url="http://127.0.0.1:9", job_timeout_s=30)


@pytest.fixture()
def client(runner: JobRunner) -> Iterator[TestClient]:
    app = create_app()
    app.state.runner = runner
    with TestClient(app) as test_client:
        yield test_client


# -------------------------------------------------------------------------------------- recorded + live

def test_overview_shape(client: TestClient) -> None:
    body = client.get("/api/overview").json()
    assert set(body) == {"generated_at", "recorded", "live"}
    assert set(body["recorded"]) == set(RECORDED)
    assert set(body["live"]) == {"runs_total", "last_run_at", "llm_calls_today", "tiers"}
    assert body["live"]["runs_total"] == 1
    assert body["live"]["last_run_at"] == SAMPLE_REPORT["started_at"]
    assert body["live"]["tiers"] == {"0": 2}
    assert isinstance(body["live"]["llm_calls_today"], int)
    assert "T" in body["generated_at"]


def test_overview_serves_the_exported_site_data(client: TestClient) -> None:
    """Whatever scripts/export_site_data.py committed is served verbatim, with its provenance."""
    site_data = Path(__file__).resolve().parents[1] / "site_data"
    overview = client.get("/api/overview").json()
    for name in RECORDED:
        path = site_data / f"{name}.json"
        if not path.is_file():
            continue
        data = overview["recorded"][name]
        assert data is not None, f"{name} is exported but /api/overview does not serve it"
        assert data["measured_at"] and data["source"]
        assert data == json.loads(path.read_text(encoding="utf-8"))


def test_runs_latest_and_detail(client: TestClient) -> None:
    latest = client.get("/api/runs/latest")
    assert latest.status_code == 200
    assert latest.json()["run_id"] == RUN_ID
    detail = client.get(f"/api/runs/{RUN_ID}")
    assert detail.status_code == 200
    assert detail.json()["totals"]["verdicts"] == {"PASS": 1, "BUG": 1}
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/runs/..%2F..%2Fconfig").status_code == 404


def test_runs_latest_is_null_without_runs(tmp_path: Path) -> None:
    app = create_app()
    app.state.runner = JobRunner(home=tmp_path / "empty")
    with TestClient(app) as empty_client:
        assert empty_client.get("/api/runs/latest").json() is None
        assert empty_client.get("/api/overview").json()["live"]["runs_total"] == 0


# -------------------------------------------------------------------------------------- shots

def test_shot_served(client: TestClient) -> None:
    response = client.get(f"/api/runs/{RUN_ID}/shots/login_01.jpg")
    assert response.status_code == 200
    assert response.content == JPEG
    assert response.headers["content-type"].startswith("image/jpeg")


@pytest.mark.parametrize("name", [
    "../../config.json",
    "..%2f..%2fconfig.json",
    "%2e%2e%2f%2e%2e%2fconfig.json",
    "%2e%2e",
    "sub/login_01.jpg",
    "/etc/passwd",
    "",
])
def test_shot_path_traversal_is_blocked(client: TestClient, name: str) -> None:
    assert client.get(f"/api/runs/{RUN_ID}/shots/{name}").status_code in (404, 405, 307)


def test_shot_unknown_run(client: TestClient) -> None:
    assert client.get("/api/runs/20990101-000000-abcd/shots/login_01.jpg").status_code == 404


# -------------------------------------------------------------------------------------- scenarios + llm

def test_scenarios_are_a_whitelist(client: TestClient) -> None:
    body = client.get("/api/scenarios").json()
    assert [s["id"] for s in body] == list(SCENARIOS)
    for entry in body:
        assert set(entry) == {"id", "title", "description", "expected_cost", "uses_llm"}
        assert isinstance(entry["uses_llm"], bool)
        assert entry["expected_cost"] and entry["title"]
    assert {s["id"] for s in body if s["uses_llm"]} == {"generate", "mcp"}


def test_llm_caps_never_leak_keys(client: TestClient) -> None:
    body = client.get("/api/llm").json()
    assert [p["name"] for p in body] == ["JEV", "JEV2", "Groq"]
    assert [p["cap_per_hour"] for p in body] == [15, 15, 60]
    for provider in body:
        assert set(provider) == {"name", "available", "calls_this_hour", "cap_per_hour", "exhausted"}
        assert isinstance(provider["available"], bool)
        assert provider["exhausted"] is False
    assert "sk-" not in json.dumps(body)


# -------------------------------------------------------------------------------------- jobs + guardrails

def test_post_job_returns_ticket(client: TestClient) -> None:
    response = client.post("/api/jobs", json={"scenario": "cli"})
    assert response.status_code == 200
    ticket = response.json()
    assert set(ticket) == {"job_id", "queue_position"}
    assert ticket["queue_position"] == 0
    status = client.get(f"/api/jobs/{ticket['job_id']}").json()
    assert status["scenario"] == "cli"
    assert status["state"] in ("queued", "running", "done")
    assert client.get("/api/jobs/does-not-exist").status_code == 404


def test_post_job_rejects_unknown_scenario(client: TestClient) -> None:
    assert client.post("/api/jobs", json={"scenario": "rm -rf /"}).status_code == 400
    assert client.post("/api/jobs", json={}).status_code == 422


def test_per_ip_rate_limit(client: TestClient) -> None:
    assert client.post("/api/jobs", json={"scenario": "cli"}).status_code == 200
    other = client.post("/api/jobs", json={"scenario": "replay"},
                        headers={"x-forwarded-for": "203.0.113.7"})
    assert other.status_code == 200                    # a different IP has its own budget
    limited = client.post("/api/jobs", json={"scenario": "replay"})
    assert limited.status_code == 429
    body = limited.json()
    assert 0 < body["retry_after"] <= 90
    assert limited.headers["Retry-After"] == str(body["retry_after"])


def test_queue_is_bounded(client: TestClient, runner: JobRunner) -> None:
    runner.executor = _block_forever                  # one running + at most MAX_QUEUE waiting
    first = client.post("/api/jobs", json={"scenario": "cli"}).json()
    _wait_for(lambda: runner.status(first["job_id"])["state"] == "running")
    waiting = 0
    refused = 0
    for i in range(MAX_QUEUE + 2):
        response = client.post("/api/jobs", json={"scenario": "cli"},
                               headers={"x-forwarded-for": f"203.0.113.{i}"})
        if response.status_code == 200:
            assert response.json()["queue_position"] == waiting
            waiting += 1
        else:
            assert response.status_code == 503
            assert response.json()["queue_limit"] == MAX_QUEUE
            refused += 1
    assert waiting == MAX_QUEUE
    assert refused == 2


# -------------------------------------------------------------------------------------- SSE

def test_sse_framing_on_a_fake_job(client: TestClient, runner: JobRunner) -> None:
    runner.executor = _fake_job(runner)
    ticket = client.post("/api/jobs", json={"scenario": "cli"}).json()
    body = _read_stream(client, ticket["job_id"])
    assert body.endswith("\n\n")
    for block in body.split("\n\n"):
        for line in block.splitlines():
            if line:
                assert line.startswith("data: ") or line.startswith(":"), line
    events = _sse_events(body)
    assert [e["type"] for e in events][:2] == ["state", "state"]
    assert events[0]["state"] == "queued" and events[1]["state"] == "running"
    assert events[-1] == {"type": "state", "state": "done"}
    assert {e["type"] for e in events} >= {"state", "log", "shot", "step", "verdict", "mcp", "meters"}
    shot = next(e for e in events if e["type"] == "shot")
    assert shot == {"type": "shot", "name": "login_01.jpg", "test": "login",
                    "url": f"/api/runs/{RUN_ID}/shots/login_01.jpg"}
    log = next(e for e in events if e["type"] == "log" and e["line"] == "fake scenario running")
    assert log["stream"] == "stdout"
    step = next(e for e in events if e["type"] == "step")
    assert set(step) == {"type", "test", "step_id", "intent", "status", "tier", "score"}
    verdict = next(e for e in events if e["type"] == "verdict")
    assert set(verdict) == {"type", "test", "category", "rationale", "changelog_refs", "bug_report"}
    mcp = next(e for e in events if e["type"] == "mcp")
    assert mcp["dir"] == "->" and mcp["frame"]["method"] == "tools/list"
    meters = next(e for e in events if e["type"] == "meters")
    assert meters["naive_tokens_estimate"] == 22118 and meters["tiers"] == {"0": 2}
    assert client.get(f"/api/jobs/{ticket['job_id']}").json()["state"] == "done"


def test_sse_replays_a_finished_job(client: TestClient, runner: JobRunner) -> None:
    runner.executor = _fake_job(runner)
    ticket = client.post("/api/jobs", json={"scenario": "cli"}).json()
    first = _sse_events(_read_stream(client, ticket["job_id"]))
    assert first[-1] == {"type": "state", "state": "done"}
    second = _sse_events(_read_stream(client, ticket["job_id"]))       # replayed from the buffer
    assert [e["type"] for e in second] == [e["type"] for e in first]
    assert client.get("/api/jobs/nope/events").status_code == 404


def test_sse_streams_a_finished_run_report(client: TestClient, runner: JobRunner) -> None:
    """A run that appeared during the job is parsed into step/verdict/meters events."""
    runner.home = runner.home.parent / "elsewhere"

    async def _run(job) -> None:
        run_dir = runner.home / "runs" / RUN_ID
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "report.json").write_text(json.dumps(SAMPLE_REPORT), encoding="utf-8")

    runner.executor = _run
    ticket = client.post("/api/jobs", json={"scenario": "cli"}).json()
    events = _sse_events(_read_stream(client, ticket["job_id"]))
    assert [e["type"] for e in events] == ["state", "state", "log", "step", "verdict", "step", "verdict",
                                           "meters", "log", "state"]
    assert events[-1] == {"type": "state", "state": "done"}
    assert events[3] == {"type": "step", "test": "login", "step_id": "s1", "intent": "Enter the pilot email",
                         "status": "passed", "tier": 0, "score": 1.0}
    assert events[6]["category"] == "BUG" and events[6]["test"] == "save-settings"
    assert events[6]["bug_report"].startswith("## BUG")
    assert events[7]["naive_tokens_estimate"] == 22118
    assert client.get(f"/api/jobs/{ticket['job_id']}").json()["run_id"] == RUN_ID


def test_failed_job_reports_the_reason(client: TestClient, runner: JobRunner) -> None:
    async def boom(job) -> None:
        raise RuntimeError("no browser here")

    runner.executor = boom
    ticket = client.post("/api/jobs", json={"scenario": "cli"}).json()
    body = _read_stream(client, ticket["job_id"])
    status = client.get(f"/api/jobs/{ticket['job_id']}").json()
    assert status["state"] == "failed"
    assert "no browser here" in status["error"]
    assert _sse_events(body)[-1] == {"type": "state", "state": "failed"}


# -------------------------------------------------------------------------------------- pages + headers

def test_pages_render(client: TestClient) -> None:
    for path in ("/", "/live"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")


def test_security_headers_and_no_listing(client: TestClient) -> None:
    headers = client.get("/api/overview").headers
    csp = headers["Content-Security-Policy"]
    assert "https://fonts.googleapis.com" in csp and "https://cdnjs.cloudflare.com" in csp
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert client.get("/assets/").status_code in (404, 307)          # no directory listing


def test_skyops_state_is_503_when_unreachable(client: TestClient) -> None:
    response = client.get("/api/skyops/state")
    assert response.status_code == 503
    assert "SkyOps" in response.json()["detail"]
