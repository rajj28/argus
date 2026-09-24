# Task: prove the LLM-powered path end to end (generation + judge + replan)

Read `AGENTS.md`, `README.md`, `argus/explore/generator.py`, `argus/llm/providers.py`, `argus/llm/client.py`.
LLM is ON (keys are already in .env - NEVER open, print or copy .env). Check the mesh with
`.venv/Scripts/argus.exe models`. Budget: at most ~60 LLM calls total for this whole task (free quotas).
Use `PYTHONIOENCODING=utf-8`. No git. Do not touch ports 8000/8001/8002 or Docker.

You may edit ONLY: `argus/explore/generator.py`, `argus/llm/prompts.py` (GENERATE prompt only),
`tests/test_explore.py`, and create `benchmarks/llm_path.py` + `.logs/llm_path_report.md`.
If you find a bug elsewhere in argus/, do not edit it - describe it precisely in the report.

1. Start your own SkyOps: `.venv/Scripts/python.exe -m demo_app.server --port 8003` (background), then
   `.venv/Scripts/python.exe -c "from demo_app.deploy import deploy; deploy('1.0','http://127.0.0.1:8003')"`.
2. Generation for an app with NO tests (memory home `.argus-gen`):
   `argus init --home .argus-gen --url http://127.0.0.1:8003 --context demo_app/context --user pilot@skyops.io --password flysafe123`
   `argus explore --home .argus-gen` then `argus generate --home .argus-gen`.
   Check: the LLM proposal was used (not only the no-LLM fallback), tests include business-rule NEGATIVE
   tests (altitude > 120 m rejected, low-battery drone disabled) with rule_ref oracles, all generated tests
   baseline cleanly. Improve generator.py / the GENERATE prompt if tests are weak or fail to compile.
3. Replay + evolution with the generated suite (same home, build labels = versions):
   `argus run --home .argus-gen --build 1.0` (expect PASS, 0 LLM calls), deploy 1.2, run `--build 1.2`,
   deploy 1.3, run `--build 1.3`. Record verdicts, LLM calls, which provider/model answered
   (`.argus-gen/runs/<id>/report.json` -> results[].llm_calls), and whether the 1.3 bugs are caught.
4. `benchmarks/llm_path.py` should automate steps 2-3 and write `.logs/llm_path_report.md` with: generated
   tests (name, tags, oracles), per-release verdict table, LLM calls per phase + providers used, and
   honest notes on failures. Stop your 8003 server at the end.
