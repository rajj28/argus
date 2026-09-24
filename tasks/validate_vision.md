# Task: validate + fix the canvas/vision test end to end

Read `AGENTS.md`. You may edit ONLY: `argus/vision/**`, `argus/runner/runner.py` (function `_execute_visual`
and the `if step.visual` branches only), `argus/runner/author.py` (the `visual` authoring branch only),
`demo_app/templates/map.html`, `tests/test_vision.py`.

Context: SkyOps has a canvas-only page `/map` (no DOM per drone). From v1.1 the map is panned/zoomed.
The suite `examples/skyops_suite.json` contains test `map-select-drone` which clicks the Falcon-2 marker
through a VisualMark (V0 cached coords -> V1 template -> V2 multiscale -> V3 VLM).

Steps (LLM off: set env `ARGUS_LLM=off`, `PYTHONIOENCODING=utf-8`). SkyOps is already running on
http://127.0.0.1:8001 (do NOT start/stop servers on 8000/8001/8002; do not touch Docker):
1. `.venv/Scripts/python.exe -c "import json; s=json.load(open('examples/skyops_suite.json',encoding='utf-8')); json.dump([t for t in s if t['id']=='map-select-drone'], open('.logs/map_suite.json','w'))"`
2. `.venv/Scripts/argus.exe init --home .argus-v --url http://127.0.0.1:8001 --context demo_app/context --user pilot@skyops.io --password flysafe123`
3. `.venv/Scripts/python.exe -c "from demo_app.deploy import deploy; deploy('1.0','http://127.0.0.1:8001')"`
   then `.venv/Scripts/argus.exe author --home .argus-v .logs/map_suite.json`
4. `.venv/Scripts/argus.exe run --home .argus-v --build 1.0 --no-update`  -> expect PASS (tier 0)
5. deploy '1.1' the same way, then `.venv/Scripts/argus.exe run --home .argus-v --build 1.1` -> expect
   COSMETIC_DRIFT with a tier-5 (template/multiscale) heal and the oracle "Selected drone: Falcon-2" holding.
6. Also run with chaos: `curl -X POST http://127.0.0.1:8001/__admin/chaos -H "Content-Type: application/json" -d "{\"seed\": 7, \"mutations\": [\"layout\"]}"`
   then run build 1.1 again with `--no-update`; then disable chaos (`{"seed": null}`).
If any step fails, debug and fix within your allowed files, re-run until 4, 5 and 6 pass, then run
`.venv/Scripts/python.exe -m pytest -q tests/test_vision.py`. Never open .env; no git commands.
Finish with a short report: verdicts per run, tiers/methods used, and every change you made (file + why).
