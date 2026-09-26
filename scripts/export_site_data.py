"""Export REAL recorded Argus artefacts into `site_data/` with provenance.

Every file it writes carries `measured_at` (when the measurement actually happened) and `source`
(the repo-relative path it came from), so the console can show where a number came from instead of
a hard-coded mock.

    .venv/Scripts/python.exe scripts/export_site_data.py                 # everything
    .venv/Scripts/python.py scripts/export_site_data.py --no-transcripts  # artefacts only (no browser)
    .venv/Scripts/python.exe scripts/export_site_data.py --transcripts-only

Transcripts need SkyOps on 127.0.0.1:8000. If it is not up the script starts it in the background
and stops it again in a `finally`; every subprocess and every HTTP call has a timeout, so the script
always terminates. Nothing here reads `.env` - the CLI subprocesses inherit it the way a normal
`argus run` does.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import closing, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:                       # run as a plain script
    sys.path.insert(0, str(REPO_ROOT))

from argus.web.jobs import Job, JobRunner                # noqa: E402  (needs sys.path above)

SKYOPS_URL = os.environ.get("SKYOPS_URL", "http://127.0.0.1:8000")
RUN_TRANSCRIPT_BUILD = "1.1"
MCP_TRANSCRIPT_HOME_LABEL = "export"

# name -> (source path, extractor). `exam_after` is extracted from the scorecard's Mode B block
# because the exam never produced a separate `scorecard_after.json`; the file says so explicitly.
RECORDED: tuple[tuple[str, str], ...] = (
    ("gauntlet", ".argus/gauntlet.json"),
    ("ten_runs", ".argus-10/ten_runs.json"),
    ("saucedemo", ".argus-saucedemo/realworld.json"),
    ("diff", ".argus-d/diff.json"),
    ("exam_before", "exam/results/scorecard.json"),
)
EXAM_AFTER_CANDIDATES = ("exam/results/scorecard_after.json", "exam/results/after/scorecard.json")
EXAM_AFTER_MODE = "b_llm"
_RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4,}$")
_TIMESTAMP_KEYS = ("measured_at", "generated_at", "finished_at", "recorded_at")


# -------------------------------------------------------------------------------------- helpers

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
                    encoding="utf-8")
    return path


def _measured_at(payload: Any, path: Path) -> str:
    """When the measurement happened: the artefact's own stamp, else its newest run id, else mtime."""
    if isinstance(payload, dict):
        for key in _TIMESTAMP_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        stamps = []
        for key in ("runs", "trials", "modes", "tests"):
            entries = payload.get(key)
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict):
                        for candidate in (entry.get("run_id"), entry.get("generated_at")):
                            if isinstance(candidate, str) and _RUN_ID.match(candidate):
                                stamps.append(candidate)
                            elif isinstance(candidate, str) and candidate:
                                return candidate
        if stamps:
            newest = max(stamps)
            try:
                return datetime.strptime(newest[:15], "%Y%m%d-%H%M%S").replace(
                    tzinfo=timezone.utc).isoformat(timespec="seconds")
            except ValueError:
                pass
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _slug(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text]
    return "-".join(part for part in "".join(keep).split("-") if part)[:60]


# -------------------------------------------------------------------------------------- recorded artefacts

def export_recorded(out: Path) -> dict[str, dict[str, Any]]:
    """Copy the six recorded artefacts, each stamped with `measured_at` + `source`."""
    written: dict[str, dict[str, Any]] = {}
    for name, source in RECORDED:
        path = REPO_ROOT / source
        if not path.is_file():
            print(f"  ! missing {source}")
            written[name] = {"skipped": True, "source": source}
            continue
        payload = _read_json(path)
        if not isinstance(payload, dict):
            payload = {"records": payload}
        payload["measured_at"] = _measured_at(_read_json(path), path)
        payload["source"] = source
        _write_json(out / f"{name}.json", payload)
        written[name] = {"source": source, "measured_at": payload["measured_at"]}
        print(f"  + {name}.json            <- {source}")

    for candidate in EXAM_AFTER_CANDIDATES:
        if (REPO_ROOT / candidate).is_file():
            payload = _read_json(REPO_ROOT / candidate)
            payload["measured_at"] = _measured_at(payload, REPO_ROOT / candidate)
            payload["source"] = candidate
            _write_json(out / "exam_after.json", payload)
            written["exam_after"] = {"source": candidate, "measured_at": payload["measured_at"]}
            print(f"  + exam_after.json        <- {candidate}")
            return written

    scorecard = REPO_ROOT / "exam/results/scorecard.json"
    if scorecard.is_file():                               # derive Mode B from the one real scorecard
        data = _read_json(scorecard)
        mode = (data.get("modes") or {}).get(EXAM_AFTER_MODE)
        if isinstance(mode, dict):
            payload = {"schema": data.get("schema", "argus-exam-scorecard-v2"), **mode,
                       "measured_at": mode.get("generated_at") or data.get("generated_at") or _now(),
                       "source": f"exam/results/scorecard.json#modes.{EXAM_AFTER_MODE}",
                       "derived": True,
                       "derived_note": (f"The exam recorded no separate after-scorecard; this is the "
                                        f"'{EXAM_AFTER_MODE}' (zero-knowledge, LLM on) block of the same "
                                        f"real scorecard, with llm_budget from exam/results/llm_budget.json."),
                       "llm_budget": _read_json(REPO_ROOT / "exam/results/llm_budget.json")
                       if (REPO_ROOT / "exam/results/llm_budget.json").is_file() else None}
            _write_json(out / "exam_after.json", payload)
            written["exam_after"] = {"source": payload["source"], "measured_at": payload["measured_at"]}
            print(f"  + exam_after.json        <- {payload['source']} (derived, see derived_note)")
    return written


# -------------------------------------------------------------------------------------- verdicts + shots

def _results(root: Path) -> list[tuple[Path, dict[str, Any], dict[str, Any]]]:
    """(report path, report, result) for every recorded run under `root`."""
    rows: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    for report_path in sorted(root.glob("*/report.json")):
        try:
            report = _read_json(report_path)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  ! unreadable {report_path}: {exc}")
            continue
        for result in report.get("results", []):
            if isinstance(result, dict) and isinstance(result.get("verdict"), dict):
                rows.append((report_path, report, result))
    return rows


def _shot_names(result: dict[str, Any]) -> list[str]:
    names = []
    for step in result.get("steps", []):
        shot = step.get("screenshot") if isinstance(step, dict) else None
        if isinstance(shot, str) and shot.lower().endswith((".jpg", ".jpeg", ".png")):
            names.append(shot)
    return names


def _bug_rank(row: tuple[Path, dict[str, Any], dict[str, Any]]) -> tuple[int, int, int, str]:
    """Prefer a deliberate, documented bug over an incidental one, and a small focused shot set."""
    report, result = row[1], row[2]
    deliberate = 0 if str(report.get("label") or "").startswith("gauntlet bug=") else 1
    shots = len(_shot_names(result))
    return (deliberate, 0 if 1 <= shots <= 3 else 1, -shots, str(report.get("run_id") or ""))


def export_verdicts(out: Path) -> dict[str, Any]:
    """One real INTENDED_CHANGE (with its changelog citation) and one real BUG (report + screenshot)."""
    runs = REPO_ROOT / ".argus" / "runs"
    rows = _results(runs)
    summary: dict[str, Any] = {}

    intended = next((row for row in rows
                     if row[2]["verdict"].get("category") == "INTENDED_CHANGE"
                     and row[2]["verdict"].get("changelog_refs")), None)
    if intended is None:
        print("  ! no INTENDED_CHANGE with a citation found")
    else:
        report_path, report, result = intended
        citations = list(result["verdict"]["changelog_refs"])
        entry = {"category": "INTENDED_CHANGE", "run_id": report.get("run_id"),
                 "label": report.get("label"), "app_url": report.get("app_url"),
                 "build": report.get("build"), "started_at": report.get("started_at"),
                 "test_id": result.get("test_id"), "test_name": result.get("test_name"),
                 "test_version": result.get("test_version"), "status": result.get("status"),
                 "updated_to_version": result.get("updated_to_version"),
                 "duration_ms": result.get("duration_ms"),
                 "verdict": result["verdict"], "citation": citations[0], "citations": citations,
                 "steps": result.get("steps", []),
                 "measured_at": _measured_at(report, report_path),
                 "source": f"{_rel(report_path)}#{result.get('test_id')}"}
        _write_json(out / "verdicts" / "intended_change.json", entry)
        summary["intended_change"] = {"test_id": entry["test_id"], "run_id": entry["run_id"],
                                      "source": entry["source"]}
        print(f"  + verdicts/intended_change.json  <- {entry['test_id']} @ {entry['run_id']}")

    bugs = [row for row in rows if row[2]["verdict"].get("category") == "BUG" and row[2].get("bug_report")]
    bug = min(bugs, key=_bug_rank) if bugs else None
    if bug is None:
        print("  ! no BUG verdict with a bug report and screenshot found")
        return summary
    report_path, report, result = bug
    shots_dir = report_path.parent / "screenshots"
    shots_out = out / "shots"
    shots_out.mkdir(parents=True, exist_ok=True)
    for stale in shots_out.glob("*"):
        with suppress(OSError):                            # no orphan screenshots between exports
            stale.unlink()
    copied: list[dict[str, str]] = []
    for name in _shot_names(result)[-1:]:                  # the last shot is where it broke
        source = shots_dir / Path(name).name
        if not source.is_file():
            continue
        target_name = f"{report.get('run_id')}-{_slug(result.get('test_id', 'test'))}-{source.name}"
        shutil.copy2(source, shots_out / target_name)
        copied.append({"name": source.name, "file": f"shots/{target_name}",
                       "source": _rel(source), "bytes": source.stat().st_size})
    verdict = result["verdict"]
    entry = {"category": "BUG", "run_id": report.get("run_id"), "label": report.get("label"),
             "app_url": report.get("app_url"), "build": report.get("build"),
             "started_at": report.get("started_at"), "test_id": result.get("test_id"),
             "test_name": result.get("test_name"), "test_version": result.get("test_version"),
             "status": result.get("status"), "duration_ms": result.get("duration_ms"),
             "verdict": verdict, "bug_report": result.get("bug_report"),
             "citation": (verdict.get("changelog_refs") or [None])[0],
             "shots": copied, "steps": result.get("steps", []),
             "measured_at": _measured_at(report, report_path),
             "source": f"{_rel(report_path)}#{result.get('test_id')}"}
    _write_json(out / "verdicts" / "bug.json", entry)
    summary["bug"] = {"test_id": entry["test_id"], "run_id": entry["run_id"], "shots": len(copied),
                      "source": entry["source"]}
    print(f"  + verdicts/bug.json                <- {entry['test_id']} @ {entry['run_id']} "
          f"({len(copied)} shot(s))")
    return summary


# -------------------------------------------------------------------------------------- SkyOps lifecycle

class SkyOps:
    """Use an already-running SkyOps, or start one in the background and stop it on exit."""

    def __init__(self, url: str, boot_timeout_s: float = 45.0) -> None:
        self.url = url.rstrip("/")
        self.boot_timeout_s = boot_timeout_s
        self.proc: Optional[subprocess.Popen] = None
        self.started_here = False

    def up(self) -> bool:
        try:
            with closing(urllib.request.urlopen(f"{self.url}/__admin/state", timeout=3)) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def start(self) -> None:
        if self.up():
            print(f"  = SkyOps already up at {self.url}")
            return
        cmd = [sys.executable, "-m", "demo_app.server", "--host", "127.0.0.1", "--port",
               self.url.rsplit(":", 1)[-1]]
        print(f"  > starting SkyOps: {' '.join(cmd)}")
        self.proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT), stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        self.started_here = True
        deadline = time.monotonic() + self.boot_timeout_s
        while time.monotonic() < deadline:
            if self.up():
                print(f"  = SkyOps ready at {self.url}")
                return
            if self.proc.poll() is not None:
                raise RuntimeError(f"SkyOps exited with {self.proc.returncode} before becoming ready")
            time.sleep(0.5)
        raise TimeoutError(f"SkyOps did not answer on {self.url} within {self.boot_timeout_s:.0f}s")

    def stop(self) -> None:
        if self.proc is None:
            return
        print("  < stopping SkyOps")
        self.proc.terminate()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            with suppress(subprocess.TimeoutExpired):
                self.proc.wait(timeout=10)
        self.proc = None

    def __enter__(self) -> "SkyOps":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()


# -------------------------------------------------------------------------------------- transcripts

def _seed_home(home: Path, url: str) -> None:
    """A fresh live home that already knows the SkyOps plans (so `run --build` can replay them)."""
    home.mkdir(parents=True, exist_ok=True)
    plans = REPO_ROOT / ".argus" / "tests"
    if plans.is_dir():
        shutil.copytree(plans, home / "tests", dirs_exist_ok=True)
    context = REPO_ROOT / "demo_app" / "context"
    if context.is_dir():
        shutil.copytree(context, home / "context", dirs_exist_ok=True)
    _write_json(home / "config.json", {
        "home": home.as_posix(), "base_url": url,
        "context_dir": (REPO_ROOT / "demo_app" / "context").as_posix(),
        "credentials": {"user": "pilot@skyops.io", "password": "flysafe123"},
        "headless": True, "max_llm_calls_per_run": 6,
    })


def _transcript_text(events: list[dict[str, Any]]) -> str:
    lines = []
    for event in events:
        if event["type"] == "log":
            prefix = "" if event.get("stream") == "stdout" else "! "
            lines.append(prefix + str(event.get("line", "")).rstrip())
        elif event["type"] == "shot":
            lines.append(f"[screenshot] {event.get('test')} {event.get('url')}")
        elif event["type"] == "step":
            lines.append(f"[step] {event.get('test')} {event.get('step_id')} {event.get('status')} "
                         f"tier={event.get('tier')} :: {event.get('intent')}")
        elif event["type"] == "verdict":
            lines.append(f"[verdict] {event.get('test')} {event.get('category')} :: "
                         f"{str(event.get('rationale', ''))[:200]}")
        elif event["type"] == "mcp":
            lines.append(f"[mcp] {event.get('dir')} {json.dumps(event.get('frame', {}), ensure_ascii=False)}")
        elif event["type"] == "meters":
            lines.append("[meters] " + json.dumps({k: v for k, v in event.items() if k != "type"},
                                                  ensure_ascii=False))
    return "\n".join(lines) + "\n"


async def _export_transcripts(out: Path, url: str, timeout_s: float) -> dict[str, Any]:
    home = Path(tempfile.mkdtemp(prefix="argus-export-"))
    summary: dict[str, Any] = {}
    try:
        _seed_home(home, url)
        runner = JobRunner(home=home, skyops_url=url, job_timeout_s=timeout_s)
        # 1. a real `argus run` against local SkyOps, captured exactly as the console streams it
        run_job = Job(id="export-run", scenario="refresh", client="export")
        run_job.known_runs = {p.name for p in (home / "runs").glob("*")} if (home / "runs").is_dir() else set()
        try:
            await asyncio.wait_for(runner.run_release(run_job, RUN_TRANSCRIPT_BUILD,
                                                      "export: recorded transcript"), timeout=timeout_s)
            await runner.emit_report(run_job)
        except Exception as exc:                           # a partial transcript is still a transcript
            await runner.log(run_job, f"[export] transcript run ended: {type(exc).__name__}: {exc}")
        await runner.close()
        run_path = out / "transcripts" / "argus_run.txt"
        run_path.parent.mkdir(parents=True, exist_ok=True)
        header = (f"# argus run --build {RUN_TRANSCRIPT_BUILD} against {url}\n"
                  f"# recorded {_now()} by scripts/export_site_data.py\n"
                  f"# home {home} (seeded from .argus/tests + demo_app/context)\n"
                  f"# exit code {run_job.exit_code}, run id {run_job.run_id}\n\n")
        run_path.write_text(header + _transcript_text(run_job.events), encoding="utf-8")
        summary["argus_run"] = {"file": "transcripts/argus_run.txt", "run_id": run_job.run_id,
                                "exit_code": run_job.exit_code, "build": RUN_TRANSCRIPT_BUILD,
                                "events": len(run_job.events), "measured_at": _now()}
        print(f"  + transcripts/argus_run.txt       ({len(run_job.events)} events, "
              f"exit {run_job.exit_code})")

        # 2. the MCP session: initialize, tools/list, last_report, verify_change - every frame kept
        mcp_runner = JobRunner(home=home, skyops_url=url, job_timeout_s=timeout_s)
        mcp_job = Job(id="export-mcp", scenario="mcp", client="export")
        try:
            await asyncio.wait_for(mcp_runner.run_mcp(mcp_job), timeout=timeout_s)
        except Exception as exc:
            await mcp_runner.log(mcp_job, f"[export] mcp session ended: {type(exc).__name__}: {exc}")
        await mcp_runner.close()
        frames = [{"dir": e["dir"], "frame": e["frame"]} for e in mcp_job.events if e["type"] == "mcp"]
        methods = [f.get("frame", {}).get("method") for f in frames if f.get("frame", {}).get("method")]
        payload = {"measured_at": _now(), "source": "argus mcp over stdio (official mcp client)",
                   "server": f"{sys.executable} -m argus mcp --home {home}",
                   "app_url": url, "frames": frames, "methods": methods,
                   "events": mcp_job.events, "home": home.as_posix()}
        _write_json(out / "transcripts" / "mcp_session.json", payload)
        (out / "transcripts" / "mcp_session.txt").write_text(_transcript_text(mcp_job.events),
                                                             encoding="utf-8")
        summary["mcp_session"] = {"file": "transcripts/mcp_session.json", "frames": len(frames),
                                  "methods": methods, "measured_at": payload["measured_at"]}
        print(f"  + transcripts/mcp_session.json    ({len(frames)} JSON-RPC frames: "
              f"{', '.join(m for m in methods if m) or 'none'})")
    finally:
        shutil.rmtree(home, ignore_errors=True)
    return summary


# -------------------------------------------------------------------------------------- main

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="site_data", help="output folder (default: site_data)")
    parser.add_argument("--skyops-url", default=SKYOPS_URL)
    parser.add_argument("--timeout", type=float, default=300.0, help="per-transcript timeout in seconds")
    parser.add_argument("--no-transcripts", action="store_true", help="skip the run + MCP transcripts")
    parser.add_argument("--transcripts-only", action="store_true", help="skip the recorded artefacts")
    args = parser.parse_args(argv)

    out = (REPO_ROOT / args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    index: dict[str, Any] = {"schema": "argus-site-data-v1", "generated_at": _now(),
                             "exported_by": "scripts/export_site_data.py", "entries": {}}

    if not args.transcripts_only:
        print("recorded artefacts:")
        index["entries"]["recorded"] = export_recorded(out)
        print("verdicts + screenshots:")
        index["entries"]["verdicts"] = export_verdicts(out)

    if not args.no_transcripts:
        print("transcripts (real runs against local SkyOps):")
        skyops = SkyOps(args.skyops_url)
        skyops.start()
        try:
            index["entries"]["transcripts"] = asyncio.run(
                _export_transcripts(out, args.skyops_url.rstrip("/"), args.timeout))
        finally:
            skyops.stop()

    index["files"] = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()
                            and p.name != "index.json")
    _write_json(out / "index.json", index)
    print(f"\nwrote {len(index['files'])} files to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
