# Task: evaluation document generator for Argus Live (`argus/live/report.py`)

Read `AGENTS.md`, `docs/LIVE_SPEC.md`, `argus/live/engine.py` (the `Result`/`Finding` dataclasses), and a few real results:
`runs/live/*/result.json`. You own ONLY `argus/live/report.py`, `tests/test_live_report.py`, and you may append ONE
command `report` to the `live_app` typer group at the end of `argus/cli.py` (do not change anything else in that file).
Never open .env; no git; do not run scenarios, servers or browsers.

`def build_report(runs: Path = Path("runs/live"), out: Path = Path("EVALUATION.md"), include: list[str] | None = None,
video_links: dict[str, str] | None = None) -> Path` writes the hackathon submission document:
1. Title "Argus Live — Evaluation Document", a one-paragraph summary with counts (scenarios, issues found, passes, levels
   covered, categories covered) computed from the results.
2. "System design": a concise section (<= 350 words) + an ASCII architecture diagram describing: Operators (real Chrome,
   desktop/tablet/phone, independent users) → Scenario engine (starting state, steps, changing conditions via the control
   API: socket-drop/delay, sim-offline, video faults, socket-refuse/kick; device emulation; multi-user) → Probes (UI read by
   meaning + test ids; video frame counters and frame hashes; Cesium map entities via React fiber; layout/occlusion
   geometry) → Ground truth (simulator state, backend health, active faults) → Oracles (UI vs truth, freshness honesty,
   reachability, occlusion, overlap, propagation lag, recovery) → Triage (deterministic verdicts, root-cause grouping,
   precision guards such as scroll-into-view before calling something unreachable) → Evidence (real-time recording with a
   HUD narrating step / UI claim / ground truth / verdict, screenshots, samples, result.json). Mention it is built on
   Playwright with Chrome DevTools Protocol access, and Argus's self-healing semantic engine.
3. "Scenarios": numbered, in `include` order if given (default: all BUG results first, then PASS results that
   demonstrate precision). For each: Title, Level + Category, Description, Approach, Starting state + Steps (from
   `steps`, cleaned of timestamps), Result (BUG with finding title, severity, detail, grouped symptoms as bullets; or PASS
   with what was verified), Evidence (key samples as a small markdown table, screenshot paths), Video (link from
   `video_links[id]` or the local path `runs/live/<id>/video.mp4` with a TODO note to upload).
4. "How to reproduce": commands (`docker compose up --build` in the starter kit, `argus live run --only S1`, etc.).
Also write `runs/live/index.html` (clean white page, same content, embedded <video> tags for local viewing).
Unit test with a synthetic results folder in tmp_path. Run `.venv/Scripts/python.exe -m pytest -q tests/test_live_report.py`.
Then run `.venv/Scripts/python.exe -c "from argus.live.report import build_report; print(build_report())"` on the real
runs and print the first 60 lines of EVALUATION.md. Report briefly.
