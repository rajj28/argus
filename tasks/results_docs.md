# Task: publish verified results in README + dashboard, add the 10-run cost curve

Read `AGENTS.md`. You may edit ONLY: `README.md`, `docs/DEMO_SCRIPT.md`, `argus/report/**`,
`benchmarks/ten_runs.py` (new), `tests/test_dashboard.py`.

Verified numbers (use exactly these; do not invent others):
- Offline Gauntlet (0 LLM calls): 3 random refactor trials + 5 injected-bug trials -> heal success 100%,
  false alarms 0%, bug recall 100% (5/5), 106 steps self-healed. Source: `.argus/gauntlet.json`.
- Four-act demo, offline (0 LLM calls): v1.0 8 PASS; v1.1 7 COSMETIC_DRIFT + 1 PASS; v1.2 3 INTENDED_CHANGE
  (release-note quotes verified) + 1 FEATURE_REMOVED + 4 PASS; v1.3 5 BUG + 2 PASS (+1 retired).
- `argus diff` v1.0 vs v1.3 on two live instances, no changelog, no LLM: all 5 hidden bugs flagged
  REGRESSION with evidence (lost side effects, crash, 500, rule violations); refactor-only flows UI_DRIFT.

1. README.md: replace the "Results on SkyOps" section with these numbers (a table per result), add a short
   "Architecture" section explaining: immutable intent vs per-build compiled plans; the cascade
   T0 replay / T1 similarity / T2 out-of-order (never across a commit point) / T4a deterministic required-field
   completion / T3-T4 LLM / T5 vision (VisualMark: cached -> template -> multiscale -> VLM picks a mark id);
   rules-first triage with changelog-grounded citations; `argus diff` for no-changelog situations;
   PRECONDITION_FAILURE. Mention free libs used (opencv-headless, imagehash, Playwright tracing).
2. `benchmarks/ten_runs.py`: runs the SkyOps suite 10 times against http://127.0.0.1:8002 with a fresh memory
   home `.argus-10` (init + author `examples/skyops_suite.json` on version 1.0 first via
   `demo_app.deploy.deploy('1.0','http://127.0.0.1:8002')`), switching to version 1.1 before run 3 and
   1.2 before run 6 (use `settings.build` = version), LLM off (ARGUS_LLM=off). Record per run: steps, T0
   replayed, healed, LLM calls, naive-agent token estimate (`totals.naive_tokens_estimate`), duration.
   Save `.argus-10/ten_runs.json` and print a table. Do not start/stop servers; 8002 is already running.
3. Dashboard (`argus/report/dashboard.py` + templates): add a "10th run" chart page `/tenruns` reading
   `<home>/ten_runs.json` (bar: LLM calls per run; line: % steps replayed at T0; label the naive-agent
   estimate as an estimate), and show `diff.json` on a `/diff` page if present. Link both from the nav.
4. Run the benchmark once, run `.venv/Scripts/python.exe -m pytest -q tests/test_dashboard.py`, never open
   .env, no git. Report the ten-run table and files changed.
