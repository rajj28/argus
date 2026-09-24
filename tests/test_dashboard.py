"""Tests for argus/report/dashboard.py (reads the Memory folder layout; no network, no LLM)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from argus import models
from argus.models import RunReport
from argus.report.dashboard import create_app, load_runs

FIXTURES = Path(__file__).parent / "fixtures"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01test-image"


def _run_ids() -> list[str]:
    runs = [
        RunReport.model_validate_json(f.read_text(encoding="utf-8"))
        for f in sorted(FIXTURES.glob("sample_run*.json"))
    ]
    return [r.run_id for r in runs]


def _build_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "runs").mkdir(parents=True)
    for run_file in sorted(FIXTURES.glob("sample_run*.json")):
        report = RunReport.model_validate_json(run_file.read_text(encoding="utf-8"))
        run_dir = home / "runs" / report.run_id
        (run_dir / "screenshots").mkdir(parents=True)
        (run_dir / "report.json").write_text(report.model_dump_json(), encoding="utf-8")
        (run_dir / "screenshots" / "login_flow_s1.jpg").write_bytes(JPEG)

    tests_dir = home / "tests"
    tests_dir.mkdir()
    for spec_file in sorted((FIXTURES / "sample_tests").glob("*.json")):
        spec = models.TestSpec.model_validate_json(spec_file.read_text(encoding="utf-8"))
        (tests_dir / f"{spec.id}.json").write_text(spec.model_dump_json(), encoding="utf-8")
    (tests_dir / "_history").mkdir()
    (tests_dir / "_history" / "login_flow.v2.json").write_text(
        (tests_dir / "login_flow.json").read_text(encoding="utf-8"), encoding="utf-8"
    )

    (home / "knowledge.json").write_text(
        json.dumps(
            {
                "stability": {"testid": {"same": 18, "changed": 2}, "role": {"same": 32, "changed": 0}},
                "decisions": {
                    "login_flow::[assertion_failed]": {
                        "category": "BUG",
                        "confidence": 0.9,
                        "rationale": "Remembered regression: export button 500s.",
                        "evidence_refs": [],
                        "changelog_refs": [],
                        "decided_by": "llm",
                        "action": "bug_reported",
                    }
                },
                "allow_console": ["favicon"],
            }
        ),
        encoding="utf-8",
    )
    (home / "atlas.json").write_text(
        json.dumps(
            {
                "states": {
                    "st_login": {"url_pattern": "/login", "title": "Sign in", "summary": "Login page", "visits": 12},
                    "st_missions": {"url_pattern": "/missions", "title": "Missions", "summary": "Mission list", "visits": 9},
                },
                "transitions": [{"from": "st_login", "to": "st_missions", "action": "click", "intent": "Sign in"}],
            }
        ),
        encoding="utf-8",
    )
    (home / "gauntlet.json").write_text(
        json.dumps(
            {
                "trials": [
                    {"id": 1, "name": "login stuck", "verdict": "BUG", "found": True},
                    {"id": 2, "name": "cosmetic reorder", "verdict": "COSMETIC_DRIFT", "found": True},
                    {"id": 3, "name": "baseline", "verdict": "PASS", "found": False},
                ]
            }
        ),
        encoding="utf-8",
    )
    return home


@pytest.fixture
def home(tmp_path):
    return _build_home(tmp_path)


@pytest.fixture
def client(home):
    app = create_app(home)
    with TestClient(app) as c:
        yield c


RUN_A, RUN_B = _run_ids()


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/tests",
        "/memory",
        "/gauntlet",
        f"/runs/{RUN_A}",
        f"/runs/{RUN_B}",
        "/tests/login_flow",
        "/tests/mission_flow",
        "/tests/login_flow/export",
        "/tests/mission_flow/export",
    ],
)
def test_pages_return_200(path, client):
    assert client.get(path).status_code == 200


def test_empty_home_is_tolerant(tmp_path):
    app = create_app(tmp_path / "does_not_exist")
    with TestClient(app) as c:
        for path in ["/", "/tests", "/memory", "/gauntlet"]:
            assert c.get(path).status_code == 200


def test_index_kpis_and_charts(client):
    html = client.get("/").text
    assert "Tokens avoided vs LLM-per-action" in html
    assert "33.3%" in html
    assert "50000" in html
    assert "PASS" in html and "COSMETIC_DRIFT" in html and "BUG" in html
    assert "https://cdn.jsdelivr.net/npm/chart.js" in html
    assert 'content="5"' in html


def test_run_detail_renders_verdicts_bug_and_ledger(client):
    html = client.get(f"/runs/{RUN_A}").text
    assert "COSMETIC_DRIFT" in html
    assert '<span class="pill bug">BUG</span>' in html
    assert "T1 similarity" in html
    assert "locator_healed" in html
    assert "billing export fails with 500" in html
    assert "nvidia/nemotron-3-super-120b-a12b" in html
    assert f"/runs/{RUN_A}/shots/login_flow_s1.jpg" in html


def test_tests_pages(client):
    html = client.get("/tests").text
    assert "login_flow" in html and "mission_flow" in html

    detail = client.get("/tests/mission_flow").text
    assert "Mission name" in detail
    assert "View Playwright export" in detail
    assert "URL shows a mission id" in detail

    export = client.get("/tests/mission_flow/export").text
    assert 'page.getByRole(&#34;link&#34;, { name: &#34;New mission&#34; })' in export
    assert "base_url:" in export and "http://localhost:8000" in export


def test_memory_page(client):
    html = client.get("/memory").text
    assert "login_flow::[assertion_failed]" in html
    assert "testid" in html
    assert "st_login" in html
    assert "st_missions" in html
    assert "https://cdn.jsdelivr.net/npm/vis-network" in html


def test_gauntlet_page(client):
    html = client.get("/gauntlet").text
    assert "login stuck" in html
    assert "COSMETIC_DRIFT" in html


def test_api_runs_sorted_by_started_at(client, home):
    runs = load_runs(home)
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["runs"]) == 2
    assert [r["run_id"] for r in data["runs"]] == [r.run_id for r in runs]

    by_id = client.get(f"/api/runs/{RUN_A}").json()
    assert by_id["totals"]["tests"] == 3
    assert {t["test_id"] for t in by_id["results"]} == {"login_flow", "mission_flow", "billing_flow"}
    assert by_id["results"][2]["verdict"]["category"] == "BUG"
    assert client.get("/api/runs/does_not_exist").status_code == 404


def test_api_tests(client):
    resp = client.get("/api/tests")
    assert resp.status_code == 200
    tests = {t["id"] for t in resp.json()["tests"]}
    assert tests == {"login_flow", "mission_flow"}
    assert next(t for t in resp.json()["tests"] if t["id"] == "mission_flow")["steps"][0]["action"] == "goto"


def test_screenshot_route_traversal_safe(client):
    ok = client.get(f"/runs/{RUN_A}/shots/login_flow_s1.jpg")
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("image/")

    traversal = client.get(f"/runs/{RUN_A}/shots/..%2Freport.json")
    assert traversal.status_code == 404

    assert client.get(f"/runs/{RUN_A}/shots/nope.jpg").status_code == 404
    assert client.get("/runs/does_not_exist/shots/x.jpg").status_code == 404