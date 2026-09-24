"""FastAPI "Mission Control" dashboard.

Reads directly from the Memory folder layout (docs/SPEC.md §5), tolerant of
missing or unparseable files. No build step: Chart.js and vis-network load from
jsdelivr; the home page auto-refreshes every 5 s.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from argus.export.playwright_export import default_base_url, to_playwright_ts
from argus.models import RunReport, Step, TestSpec, Verdict

TIER_LABELS: dict[int, str] = {
    0: "T0 replay",
    1: "T1 similarity",
    2: "T2 out-of-order",
    3: "T3 LLM heal",
    4: "T4 replan",
    5: "T5 vision",
}
TIER_COLORS: dict[int, str] = {
    0: "#38bdf8",
    1: "#34d399",
    2: "#fbbf24",
    3: "#a78bfa",
    4: "#fb923c",
    5: "#f472b6",
}
VERDICT_CSS: dict[str, str] = {
    "PASS": "pass",
    "COSMETIC_DRIFT": "drift",
    "INTENDED_CHANGE": "change",
    "FEATURE_REMOVED": "removed",
    "BUG": "bug",
    "NEEDS_REVIEW": "review",
    "INFRA": "infra",
}


def load_runs(home: Path) -> list[RunReport]:
    """All run reports under <home>/runs/*/report.json, newest started_at first."""
    base = Path(home) / "runs"
    if not base.is_dir():
        return []
    runs: list[RunReport] = []
    for run_dir in sorted(base.iterdir()):
        if not run_dir.is_dir():
            continue
        report = run_dir / "report.json"
        if not report.is_file():
            continue
        try:
            runs.append(RunReport.model_validate_json(report.read_text(encoding="utf-8")))
        except ValueError:
            continue
    runs.sort(key=lambda r: r.started_at, reverse=True)
    return runs


def load_tests(home: Path) -> list[TestSpec]:
    """Current TestSpecs from <home>/tests/*.json (the _history/ dir is skipped)."""
    base = Path(home) / "tests"
    if not base.is_dir():
        return []
    tests: list[TestSpec] = []
    for f in sorted(base.glob("*.json")):
        try:
            tests.append(TestSpec.model_validate_json(f.read_text(encoding="utf-8")))
        except ValueError:
            continue
    tests.sort(key=lambda t: t.id)
    return tests


def load_test(home: Path, test_id: str) -> TestSpec | None:
    """Load one TestSpec by id, or None."""
    report = Path(home) / "tests" / f"{test_id}.json"
    if not report.is_file():
        return None
    try:
        return TestSpec.model_validate_json(report.read_text(encoding="utf-8"))
    except ValueError:
        return None


def load_test_history(home: Path, test_id: str) -> list[TestSpec]:
    """Archived versions <home>/tests/_history/<id>.v<N>.json, newest first."""
    base = Path(home) / "tests" / "_history"
    if not base.is_dir():
        return []
    history: list[TestSpec] = []
    for f in sorted(base.glob(f"{test_id}.v*.json")):
        try:
            history.append(TestSpec.model_validate_json(f.read_text(encoding="utf-8")))
        except ValueError:
            continue
    history.sort(key=lambda t: t.version, reverse=True)
    return history


def load_knowledge(home: Path) -> dict[str, Any]:
    """Parsed <home>/knowledge.json, tolerant of a missing/invalid file."""
    return _json_objects(Path(home) / "knowledge.json") or {}


def load_atlas(home: Path) -> dict[str, Any]:
    """Parsed <home>/atlas.json, tolerant of a missing/invalid file."""
    return _json_objects(Path(home) / "atlas.json") or {}


def load_gauntlet(home: Path) -> Any:
    """Raw <home>/gauntlet.json content (None when absent)."""
    return _json_objects(Path(home) / "gauntlet.json")


def create_app(home: Path, base_url: str | None = None) -> FastAPI:
    """Build the dashboard app. `home` points at a Memory folder (SPEC §5)."""
    home = Path(home)
    base = base_url or default_base_url(home)
    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
    templates.env.globals["pill_class"] = _pill_class

    app = FastAPI(title="Argus Mission Control")
    static_dir = (Path(__file__).parent / "static").resolve()
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        runs = load_runs(home)
        tests = load_tests(home)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "kpis": _kpis(tests, runs),
                "charts": _run_charts(runs),
                "rows": _run_rows(runs),
            },
        )

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(request: Request, run_id: str):
        run = next((r for r in load_runs(home) if r.run_id == run_id), None)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return templates.TemplateResponse(request, "run.html", _run_context(run))

    @app.get("/runs/{run_id}/shots/{name}")
    def run_shot(run_id: str, name: str):
        """Serve a screenshot from the run dir, path-traversal safe."""
        run_dir = Path(home) / "runs" / run_id
        if not run_dir.is_dir():
            raise HTTPException(status_code=404, detail="run not found")
        safe = Path(name).name
        if not safe:
            raise HTTPException(status_code=404, detail="invalid screenshot name")
        shot = run_dir / "screenshots" / safe
        if shot.is_file() and shot.resolve().is_relative_to(run_dir.resolve()):
            return FileResponse(shot)
        raise HTTPException(status_code=404, detail="screenshot not found")

    @app.get("/tests", response_class=HTMLResponse)
    def tests_page(request: Request):
        tests = load_tests(home)
        rows = [
            {
                "id": t.id,
                "name": t.name,
                "goal": t.goal,
                "version": t.version,
                "status": t.status,
                "tags": t.tags,
                "steps": len(t.steps),
                "start_url": t.start_url,
            }
            for t in tests
        ]
        return templates.TemplateResponse(request, "tests.html", {"rows": rows, "count": len(tests)})

    @app.get("/tests/{test_id}", response_class=HTMLResponse)
    def test_detail(request: Request, test_id: str):
        test = load_test(home, test_id)
        if test is None:
            raise HTTPException(status_code=404, detail="test not found")
        steps = [
            {
                "action": s.action,
                "intent": s.intent,
                "target_desc": s.target.describe() if s.target else "-",
                "value": s.value,
                "optional": s.optional,
                "expect": _expect_summary(s),
            }
            for s in test.steps
        ]
        history = [c.model_dump(mode="json") for c in test.history]
        archived = [
            {"version": v.version, "name": v.name, "steps": len(v.steps), "status": v.status}
            for v in load_test_history(home, test_id)
        ]
        oracles = [{"kind": a.kind, "description": a.description} for a in test.oracles]
        return templates.TemplateResponse(
            request,
            "test.html",
            {
                "test": test,
                "steps": steps,
                "history": history,
                "archived": archived,
                "oracles": oracles,
                "export_url": f"/tests/{test_id}/export",
            },
        )

    @app.get("/tests/{test_id}/export", response_class=HTMLResponse)
    def test_export(request: Request, test_id: str):
        test = load_test(home, test_id)
        if test is None:
            raise HTTPException(status_code=404, detail="test not found")
        return templates.TemplateResponse(
            request,
            "export.html",
            {"test": test, "ts": to_playwright_ts(test, base), "base_url": base},
        )

    @app.get("/memory", response_class=HTMLResponse)
    def memory_page(request: Request):
        knowledge = load_knowledge(home)
        atlas = load_atlas(home)
        stability: dict[str, float] = {}

        for attr, counts in knowledge.get("stability", {}).items():
            if not isinstance(counts, dict):
                continue
            same = int(counts.get("same", 0))
            changed = int(counts.get("changed", 0))
            stability[attr] = round((same + 1) / (same + changed + 2), 3)
        stability_totals = {
            attr: {
                "same": int(c.get("same", 0)),
                "changed": int(c.get("changed", 0)),
            }
            for attr, c in knowledge.get("stability", {}).items()
            if isinstance(c, dict)
        }
        decisions: list[dict[str, Any]] = []
        for sig, raw in (knowledge.get("decisions") or {}).items():
            if isinstance(raw, dict):
                try:
                    verdict = Verdict.model_validate(raw)
                except ValueError:
                    continue
                decisions.append({"sig": sig, "verdict": verdict})
        graph = _atlas_graph(atlas)
        return templates.TemplateResponse(
            request,
            "memory.html",
            {
                "stability": stability,
                "stability_totals": stability_totals,
                "decisions": decisions,
                "graph": graph,
                "atlas_counts": {"states": len(graph["nodes"]), "transitions": len(graph["edges"])},
            },
        )

    @app.get("/gauntlet", response_class=HTMLResponse)
    def gauntlet_page(request: Request):
        return templates.TemplateResponse(
            request, "gauntlet.html", _gauntlet_context(load_gauntlet(home))
        )

    @app.get("/api/runs")
    def api_runs():
        return {"runs": [r.model_dump(mode="json") for r in load_runs(home)]}

    @app.get("/api/runs/{run_id}")
    def api_run(run_id: str):
        for r in load_runs(home):
            if r.run_id == run_id:
                return r.model_dump(mode="json")
        raise HTTPException(status_code=404, detail="run not found")

    @app.get("/api/tests")
    def api_tests():
        return {"tests": [t.model_dump(mode="json") for t in load_tests(home)]}

    return app


def _json_objects(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _pill_class(category: str) -> str:
    return VERDICT_CSS.get(category, "neutral")


def _kpis(tests: list[TestSpec], runs: list[RunReport]) -> dict[str, Any]:
    latest = runs[0].totals if runs else None
    pass_rate = (latest.passed / latest.tests) if (latest and latest.tests) else 0.0
    return {
        "tests": len(tests),
        "pass_rate": round(pass_rate * 100, 1),
        "healed_steps": sum(r.totals.healed_steps for r in runs),
        "bugs": sum(r.totals.verdicts.get("BUG", 0) for r in runs),
        "llm_calls": sum(r.totals.llm_calls for r in runs),
        "cost_total": round(sum(r.totals.cost_usd for r in runs), 4),
        "tokens_avoided": sum(r.totals.naive_tokens_estimate for r in runs),
    }


def _run_charts(runs: list[RunReport]) -> dict[str, Any]:
    labels = [r.run_id for r in runs]
    tiers = [
        {
            "label": TIER_LABELS[tier],
            "backgroundColor": TIER_COLORS[tier],
            "data": [r.totals.tiers.get(str(tier), 0) for r in runs],
        }
        for tier in sorted(TIER_LABELS)
    ]
    return {
        "labels": labels,
        "cost": [r.totals.cost_usd for r in runs],
        "calls": [r.totals.llm_calls for r in runs],
        "tiers": tiers,
    }


def _run_rows(runs: list[RunReport]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in runs:
        rows.append(
            {
                "run_id": r.run_id,
                "label": r.label,
                "started_at": r.started_at,
                "tests": r.totals.tests,
                "passed": r.totals.passed,
                "failed": r.totals.failed,
                "healed": r.totals.healed_steps,
                "llm_calls": r.totals.llm_calls,
                "cost": r.totals.cost_usd,
                "verdicts": [res.verdict.category for res in r.results],
            }
        )
    return rows


def _run_context(run: RunReport) -> dict[str, Any]:
    tests = []
    for res in run.results:
        steps = [
            {
                "step_id": s.step_id,
                "intent": s.intent or s.step_id,
                "status": s.status,
                "tier": s.tier,
                "tier_label": TIER_LABELS.get(s.tier or 0, "T0 replay") if s.tier is not None else "-",
                "score": s.score,
                "duration_ms": s.duration_ms,
                "screenshot": s.screenshot,
                "shot_url": f"/runs/{run.run_id}/shots/{Path(s.screenshot).name}"
                if s.screenshot
                else None,
                "observations": s.observations,
                "effects": s.effects,
            }
            for s in res.steps
        ]
        tests.append(
            {
                "test_id": res.test_id,
                "test_name": res.test_name or res.test_id,
                "test_version": res.test_version,
                "status": res.status,
                "verdict": res.verdict,
                "verdict_class": _pill_class(res.verdict.category),
                "updated_to_version": res.updated_to_version,
                "duration_ms": res.duration_ms,
                "bug_report": res.bug_report,
                "steps": steps,
                "observations": res.observations,
                "llm_calls": res.llm_calls,
            }
        )
    return {"run": run, "tests": tests}


def _expect_summary(step: Step) -> dict[str, Any]:
    return {
        "url_pattern": step.expect.url_pattern,
        "network": [f"{n.method} {n.path} ({n.status})" for n in step.expect.network],
        "assertions": [
            {"kind": a.kind, "description": a.description} for a in step.expect.assertions
        ],
    }


def _atlas_graph(atlas: dict[str, Any]) -> dict[str, Any]:
    states = atlas.get("states") or {}
    nodes = [
        {
            "id": sid,
            "label": str(info.get("url_pattern") or sid),
            "title": str(info.get("summary") or ""),
        }
        for sid, info in states.items()
    ]
    edges = []
    for t in atlas.get("transitions") or []:
        edges.append(
            {
                "from": t.get("from"),
                "to": t.get("to"),
                "label": f'{t.get("action", "")} {t.get("intent", "")}'.strip(),
            }
        )
    return {"nodes": nodes, "edges": edges}


def _gauntlet_context(data: Any) -> dict[str, Any]:
    """Tolerant rendering context for <home>/gauntlet.json of unknown schema."""
    if isinstance(data, dict):
        records = data.get("trials") or data.get("runs") or []
    elif isinstance(data, list):
        records = data
    else:
        records = []
    rows = [dict(r) for r in records if isinstance(r, dict)]
    if not rows:
        return {"present": False}
    headers = list(rows[0].keys())
    dist: dict[str, int] = {}
    for key in ("verdict", "status", "outcome", "passed"):
        if key not in headers:
            continue
        for r in rows:
            value = r.get(key)
            if value is not None:
                dist[str(value)] = dist.get(str(value), 0) + 1
        if dist:
            break
    return {"present": True, "headers": headers, "rows": rows, "dist": dist}