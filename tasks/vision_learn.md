# Task: canvas heals must be learned (the "10th run" must be pure replay)

Read `AGENTS.md`, `tasks/validate_vision.md`, `benchmarks/ten_runs.py`, `.argus-10/ten_runs.json`.
You may edit ONLY: `argus/vision/**`, `argus/runner/runner.py` (only `_execute_visual`, and the
COSMETIC_DRIFT / INTENDED_CHANGE branches of `_apply_verdict` where step targets are rewritten),
`tests/test_vision.py`.

Bug: in the ten-run benchmark every run after the v1.1 deploy re-heals 1 step (the canvas map step):
DOM heals are written back to the compiled plan after a verified run, but a visual heal is not, so the
VisualMark keeps its v1.0 position and must be re-found by template matching on every run.

Fix: when `_execute_visual` re-finds a mark with tier > 0, re-capture the mark at the new location
(`capture_mark` on the same canvas at the hit point, keep the hint) and carry it on the executed step in
the trace (`step.visual = new_mark`), so `_apply_verdict` persists it with the healed plan (only after a
verified run: COSMETIC_DRIFT / INTENDED_CHANGE - never on BUG). Next run must hit V0 (tier 0).
Verify with SkyOps on http://127.0.0.1:8001 (already running; never start/stop servers or Docker;
ARGUS_LLM=off; PYTHONIOENCODING=utf-8): home `.argus-v` flow from tasks/validate_vision.md, deploy 1.1,
run build 1.1 twice -> first COSMETIC_DRIFT (tier 5), second PASS with tier 0. Then run
`.venv/Scripts/python.exe benchmarks/ten_runs.py` (uses port 8002) and confirm runs 4-5 and 7-10 show
healed = 0. Run tests/test_vision.py. Never open .env; no git. Report results and changes.
