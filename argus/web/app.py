"""Argus web console: JSON API, SSE job stream and the static console (docs/WEB_SPEC.md).

    .venv/Scripts/python.exe -m uvicorn argus.web.app:app --port 8080

Everything under `/api` is JSON except `/api/jobs/{id}/events`, which is a `text/event-stream`.
`recorded` numbers come from the committed `site_data/*.json` (exported by
`scripts/export_site_data.py` from real local artefacts), `live` numbers from the live memory home
(`ARGUS_LIVE_HOME`, default `.argus-live`).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from argus.models import now_iso
from argus.report.dashboard import load_diff, load_gauntlet, load_ten_runs
from argus.web.jobs import (MAX_QUEUE, JobRunner, QueueFull, RateLimited, UnknownScenario,
                            scenario_catalogue)

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
SITE_DATA = Path(os.environ.get("ARGUS_SITE_DATA") or (REPO_ROOT / "site_data"))
RECORDED = ("gauntlet", "ten_runs", "saucedemo", "exam_before", "exam_after", "diff")
DEFAULT_REPO_URL = "https://huggingface.co/spaces/rajj28/argus"
HEARTBEAT_S = 15.0
_END = object()

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SAFE_SHOT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Google Fonts + cdnjs only: the console loads nothing else from the network.
CSP = "; ".join((
    "default-src 'self'",
    "script-src 'self' https://cdnjs.cloudflare.com",
    "style-src 'self' https://fonts.googleapis.com https://cdnjs.cloudflare.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
))

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

PLACEHOLDER = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Argus</title></head>
<body style="font:16px/1.6 system-ui;max-width:44rem;margin:4rem auto;padding:0 1rem">
<h1>Argus</h1>
<p>The console front-end has not been built into <code>argus/web/static/</code> yet.</p>
<p>The API is live: <a href="/api/overview">/api/overview</a>, <a href="/api/scenarios">/api/scenarios</a>,
<a href="/api/runs/latest">/api/runs/latest</a>, <a href="/api/llm">/api/llm</a>.</p>
</body></html>
"""


class JobRequest(BaseModel):
    """Body of `POST /api/jobs`: the scenario id only, nothing free-form."""

    scenario: str = Field(..., min_length=1, max_length=64)


def get_runner(request: Request) -> JobRunner:
    """The process-wide job runner, created on first use (keeps tests hermetic)."""
    runner = getattr(request.app.state, "runner", None)
    if runner is None:
        runner = JobRunner()
        request.app.state.runner = runner
    return runner


def client_ip(request: Request) -> str:
    """First hop of X-Forwarded-For (Caddy) or the socket peer. Never logged."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_recorded(name: str) -> Any:
    """One exported artefact from `site_data/` (None when not exported yet)."""
    if name == "gauntlet":
        return load_gauntlet(SITE_DATA)
    if name == "ten_runs":
        return load_ten_runs(SITE_DATA)
    if name == "diff":
        return load_diff(SITE_DATA)
    return _read_json(SITE_DATA / f"{name}.json")


def _page(filename: str, title: str) -> Response:
    """Serve a static page, wiring the deployment meta tags the frontend reads.

    `argus-repo` and `argus-skyops-url` stay empty in the committed HTML; the values are
    deployment-specific, so they are injected here from the environment (empty -> the frontend
    leaves the links disabled). Falls back to a short placeholder before the build lands.
    """
    target = STATIC_DIR / filename
    if target.is_file():
        page = target.read_text(encoding="utf-8")
        for name, env_var, default in (("argus-repo", "ARGUS_REPO_URL", DEFAULT_REPO_URL),
                                       ("argus-skyops-url", "ARGUS_SKYOPS_PUBLIC_URL", "")):
            value = escape(os.environ.get(env_var) or default, quote=True)
            page = page.replace(f'<meta name="{name}" content="">', f'<meta name="{name}" content="{value}">')
        return HTMLResponse(page)
    return HTMLResponse(PLACEHOLDER.replace("Argus", f"Argus - {title}", 1), status_code=200)


def create_app() -> FastAPI:
    """Build the console app. `app.state.runner` is injected by tests when needed."""

    @asynccontextmanager
    async def lifespan(instance: FastAPI) -> AsyncIterator[None]:
        yield
        runner = getattr(instance.state, "runner", None)
        if runner is not None:
            await runner.close()

    app = FastAPI(title="Argus", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.runner = None

    if (STATIC_DIR / "assets").is_dir():
        assets = StaticFiles(directory=STATIC_DIR / "assets", check_dir=False)   # no directory listing
        app.mount("/assets", assets, name="assets")
        app.mount("/static", assets, name="static")
    if SITE_DATA.is_dir():
        app.mount("/site_data", StaticFiles(directory=SITE_DATA, check_dir=False), name="site_data")

    @app.middleware("http")
    async def security_and_log(request: Request, call_next) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        # No client address is written to the log: the only place one lives is the in-memory
        # rate-limit map, which is pruned after the window (see JobRunner._prune_rate).
        print(f"{request.method} {request.url.path} -> {response.status_code} "
              f"{int((time.perf_counter() - started) * 1000)}ms", flush=True)
        return response

    # ------------------------------------------------------------------ pages
    @app.get("/", response_class=HTMLResponse)
    def index() -> Response:
        return _page("index.html", "landing")

    @app.get("/live", response_class=HTMLResponse)
    def live_page() -> Response:
        return _page("live.html", "live console")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True, "generated_at": now_iso()}

    # ------------------------------------------------------------------ api
    @app.get("/api/overview")
    def api_overview(request: Request) -> dict[str, Any]:
        return {"generated_at": now_iso(),
                "recorded": {name: load_recorded(name) for name in RECORDED},
                "live": get_runner(request).live_stats()}

    @app.get("/api/scenarios")
    def api_scenarios() -> list[dict[str, Any]]:
        return scenario_catalogue()

    @app.get("/api/llm")
    def api_llm(request: Request) -> list[dict[str, Any]]:
        return get_runner(request).llm_status()

    @app.get("/api/skyops/state")
    async def api_skyops_state(request: Request) -> dict[str, Any]:
        runner = get_runner(request)
        try:
            return await asyncio.to_thread(runner.skyops_state)
        except Exception as exc:                     # SkyOps down / slow: 503, never a 500
            raise HTTPException(status_code=503, detail=f"SkyOps is not reachable ({type(exc).__name__})")

    @app.get("/api/runs/latest")
    def api_latest_run(request: Request) -> Optional[dict[str, Any]]:
        report = get_runner(request).latest_run()
        return report.model_dump(mode="json") if report is not None else None

    @app.get("/api/runs/{run_id}")
    def api_run(request: Request, run_id: str) -> dict[str, Any]:
        report = _find_run(get_runner(request), run_id)
        if report is None:
            raise HTTPException(status_code=404, detail="run not found")
        return report.model_dump(mode="json")

    @app.get("/api/runs/{run_id}/shots/{name}")
    def api_shot(request: Request, run_id: str, name: str) -> Response:
        run_dir = _run_dir(get_runner(request), run_id)
        if not SAFE_SHOT.match(name) or Path(name).name != name:
            raise HTTPException(status_code=404, detail="screenshot not found")
        shot = run_dir / "screenshots" / name
        if shot.is_file() and shot.resolve().is_relative_to(run_dir.resolve()):
            return FileResponse(shot, media_type="image/jpeg")
        raise HTTPException(status_code=404, detail="screenshot not found")

    @app.post("/api/jobs")
    async def api_create_job(payload: JobRequest, request: Request) -> Response:
        runner = get_runner(request)
        try:
            ticket = await runner.submit(payload.scenario, client_ip(request))
        except UnknownScenario:
            raise HTTPException(status_code=400, detail="unknown scenario")
        except RateLimited as exc:
            return JSONResponse({"error": "rate limited", "detail": str(exc), "retry_after": exc.retry_after},
                                status_code=429, headers={"Retry-After": str(exc.retry_after)})
        except QueueFull:
            return JSONResponse({"error": "queue full", "queue_limit": MAX_QUEUE, "retry_after": 30},
                                status_code=503, headers={"Retry-After": "30"})
        return JSONResponse(ticket)

    @app.get("/api/jobs/{job_id}")
    def api_job_status(job_id: str, request: Request) -> dict[str, Any]:
        status = get_runner(request).status(job_id)
        if status is None:
            raise HTTPException(status_code=404, detail="job not found")
        return status

    @app.get("/api/jobs/{job_id}/events")
    async def api_job_events(job_id: str, request: Request) -> StreamingResponse:
        runner = get_runner(request)
        if runner.status(job_id) is None:
            raise HTTPException(status_code=404, detail="job not found")
        return StreamingResponse(_sse(runner, job_id), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache, no-store, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        })

    return app


def _find_run(runner: JobRunner, run_id: str) -> Any:
    if not SAFE_ID.match(run_id) or Path(run_id).name != run_id:
        return None
    return next((r for r in runner.runs() if r.run_id == run_id), None)


def _run_dir(runner: JobRunner, run_id: str) -> Path:
    """`<home>/runs/<run_id>` after rejecting anything that is not a plain run id."""
    if not SAFE_ID.match(run_id) or Path(run_id).name != run_id:
        raise HTTPException(status_code=404, detail="run not found")
    run_dir = runner.home / "runs" / run_id
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="run not found")
    return run_dir


async def _sse(runner: JobRunner, job_id: str) -> AsyncIterator[str]:
    """Replay + live events as `data: {...}\\n\\n`, with a comment heartbeat when idle."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)

    async def pump() -> None:
        try:
            async for event in runner.events(job_id):
                await queue.put(event)
        finally:
            await queue.put(_END)          # the client stops even if the last event was a log line

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if item is _END:
                return
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            if item.get("type") == "state" and item.get("state") in ("done", "failed"):
                return
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


app = create_app()
