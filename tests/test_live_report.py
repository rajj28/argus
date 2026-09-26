"""Unit tests for argus.live.report — synthetic results only; no network, no browser, no scenarios."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from argus.live.report import build_report, key_samples, load_results, order_results

BUG = {
    "id": "S1-freshness",
    "title": "Stale data shown as live",
    "level": "L2",
    "category": "Telemetry / freshness",
    "description": "The cockpit should say when data is stale. Old data must not look live.",
    "approach": "Argus drops the telemetry feed and compares the screen with the simulator.",
    "verdict": "BUG",
    "findings": [{
        "title": "Stale drone data presented as live",
        "category": "Telemetry / data freshness",
        "level": "L2",
        "severity": "High",
        "detail": "No per-device freshness state; the socket badge lies by omission.",
        "symptoms": ["distance frozen at 75 m while the drone moved to 200 m",
                     "simulator offline but row still 'in_flight'"],
        "evidence": {"distinct_frames_seen": 2},
    }],
    "steps": ["[  0.6s] Start: simulator reset, Drone 1 takes off",
              "[ 18.4s] Condition 1/2 - feed lost: 100% dropped",
              "[ 52.1s] Condition 2/2 - simulator disconnected"],
    "samples": [{"condition": "drop", "ui_distance": 75.0, "truth_distance": 95, "t": 20.5},
                {"condition": "drop", "ui_distance": 75.0, "truth_distance": 115, "t": 22.6},
                {"condition": "drop", "ui_distance": 75.0, "truth_distance": 135, "t": 24.6},
                {"condition": "offline", "ui_distance": 350.0, "truth_distance": 430, "t": 54.2},
                {"condition": "offline", "ui_distance": 360.0, "truth_distance": 450, "t": 56.2}],
    "videos": ["video.mp4"],
    "shots": ["shots/1-socket-drop.png"],
    "started": "2026-09-26 12:51:13",
    "duration_s": 68.0,
}

PASS = {
    "id": "S7-two-operators",
    "title": "Two operators see the same take-off",
    "level": "L3",
    "category": "Real-time and multi-user",
    "description": "A change must reach every screen promptly.",
    "approach": "Argus opens two operators and times each screen against the simulator.",
    "verdict": "PASS",
    "findings": [],
    "steps": ["[  0.6s] Start: two operators have the cockpit open",
              "[ 13.4s] Drone 2 takes off from the control API"],
    "samples": [{"lag": {"A": 0.5, "B": 0.5}, "t": 23.9}],
    "videos": ["video.mp4"],
    "shots": [],
    "started": "2026-09-26 12:54:13",
    "duration_s": 28.0,
}


@pytest.fixture()
def runs(tmp_path: Path) -> Path:
    root = tmp_path / "runs" / "live"
    for r in (BUG, PASS):
        d = root / r["id"]
        d.mkdir(parents=True)
        (d / "result.json").write_text(json.dumps(r), encoding="utf-8")
    return root


def test_load_and_default_order(runs: Path) -> None:
    results = load_results(runs)
    assert [r["id"] for r in results] == ["S1-freshness", "S7-two-operators"]
    ordered = order_results(results)
    assert ordered[0]["verdict"] == "BUG" and ordered[1]["verdict"] == "PASS"


def test_include_order_and_prefix(runs: Path) -> None:
    results = load_results(runs)
    assert [r["id"] for r in order_results(results, ["S7", "S1"])] == \
           ["S7-two-operators", "S1-freshness"]
    assert [r["id"] for r in order_results(results, ["S1"])] == ["S1-freshness"]


def test_build_report_markdown(runs: Path, tmp_path: Path) -> None:
    out = tmp_path / "EVALUATION.md"
    path = build_report(runs, out)
    assert path == out and out.exists()
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Argus Live — Evaluation Document")
    # summary counts
    assert "2 scenario(s)" in text and "1 genuine issue(s)" in text
    assert "1 scenario(s) PASS" in text
    assert "L2, L3" in text and "Telemetry / freshness" in text
    # sections present
    for section in ("## System design", "## Scenarios", "## How to reproduce",
                    "docker compose up --build", "argus live run --only S1"):
        assert section in text
    # BUG numbered first, PASS second
    assert text.index("### 1. Stale data shown as live") < text.index("### 2. Two operators")
    # steps cleaned of timestamps, starting state split out
    assert "[  0.6s]" not in text and "[ 18.4s]" not in text
    assert "**Starting state.** simulator reset, Drone 1 takes off" in text
    assert "Condition 1/2 - feed lost: 100% dropped" in text
    # finding rendered with severity, detail, grouped symptoms
    assert "**Stale drone data presented as live**" in text
    assert "severity: **High**" in text
    assert "- distance frozen at 75 m while the drone moved to 200 m" in text
    # PASS says what was verified
    assert "**Result: PASS**" in text and "verified: A change must reach every screen promptly" in text
    # evidence: samples table + screenshot path relative to the document
    assert "| ui_distance | truth_distance |" in text
    assert "runs/live/S1-freshness/shots/1-socket-drop.png" in text
    # local video with TODO note
    assert "(runs/live/S1-freshness/video.mp4)" in text
    assert "TODO: upload" in text


def test_video_links_used(runs: Path, tmp_path: Path) -> None:
    out = tmp_path / "EVALUATION.md"
    build_report(runs, out, video_links={"S1-freshness": "https://cdn.example/s1.mp4"})
    text = out.read_text(encoding="utf-8")
    assert "https://cdn.example/s1.mp4" in text
    s1 = text.split("### 1.")[1].split("### 2.")[0]
    assert "TODO: upload" not in s1
    assert "TODO: upload" in text          # S7 still local


def test_include_limits_and_orders_document(runs: Path, tmp_path: Path) -> None:
    out = tmp_path / "EVALUATION.md"
    build_report(runs, out, include=["S7"])
    text = out.read_text(encoding="utf-8")
    assert "### 1. Two operators see the same take-off" in text
    assert "Stale data shown as live" not in text


def test_index_html(runs: Path, tmp_path: Path) -> None:
    build_report(runs, tmp_path / "EVALUATION.md")
    page = runs / "index.html"
    assert page.exists()
    text = page.read_text(encoding="utf-8")
    assert "<title>Argus Live — Evaluation Document</title>" in text
    assert '<video controls preload="metadata" src="S1-freshness/video.mp4"></video>' in text
    assert "Grouped symptoms (2, one root cause)" in text
    assert "[  0.6s]" not in text
    assert "<script" not in text and "background:#fff" in text


def test_html_escaping(runs: Path, tmp_path: Path) -> None:
    nasty = dict(PASS, id="S9-xss", title="<img src=x onerror=alert(1)>")
    d = runs / "S9-xss"
    d.mkdir()
    (d / "result.json").write_text(json.dumps(nasty), encoding="utf-8")
    build_report(runs, tmp_path / "EVALUATION.md")
    text = (runs / "index.html").read_text(encoding="utf-8")
    assert "<img src=x onerror" not in text
    assert "&lt;img src=x onerror=alert(1)&gt;" in text


def test_design_section_word_limit(runs: Path, tmp_path: Path) -> None:
    out = tmp_path / "EVALUATION.md"
    build_report(runs, out)
    text = out.read_text(encoding="utf-8")
    section = text.split("## System design")[1].split("## Scenarios")[0]
    prose = re.sub(r"```.*?```", "", section, flags=re.S)   # diagram is extra, prose <= 350 words
    assert len(prose.split()) <= 350
    assert "```" in section                                   # ASCII diagram included


def test_key_samples_picks_group_boundaries() -> None:
    samples = [{"condition": "a", "v": i} for i in range(10)] + \
              [{"condition": "b", "v": i} for i in range(10)]
    picked = key_samples(samples, limit=8)
    assert picked[0] == samples[0] and picked[-1] == samples[-1]
    assert {"condition": "b", "v": 0} in picked
    assert len(picked) <= 8


def test_error_verdict_reported_not_hidden(runs: Path, tmp_path: Path) -> None:
    err = dict(PASS, id="S10-load", title="Load and long run", verdict="ERROR",
               steps=PASS["steps"] + ["scenario error: TimeoutError: page.goto"])
    d = runs / "S10-load"
    d.mkdir()
    (d / "result.json").write_text(json.dumps(err), encoding="utf-8")
    out = tmp_path / "EVALUATION.md"
    build_report(runs, out)
    text = out.read_text(encoding="utf-8")
    assert "1 scenario(s) ended in ERROR" in text
    assert "**Result: ERROR.**" in text
    assert "scenario error: TimeoutError" in text
    s10 = text.split("### 2. Load and long run")[1].split("### 3.")[0]
    assert "**Result: ERROR.**" in s10 and "**Result: PASS**" not in s10


def test_empty_runs_folder(tmp_path: Path) -> None:
    runs = tmp_path / "runs" / "live"
    out = tmp_path / "EVALUATION.md"
    path = build_report(runs, out)
    assert path == out
    text = out.read_text(encoding="utf-8")
    assert "0 scenario(s)" in text and "No results found" in text
    assert (runs / "index.html").exists()
