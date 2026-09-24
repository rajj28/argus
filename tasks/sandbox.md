# Task: sandboxed stress-test environment (Docker)

Owner: you own ONLY `sandbox/**` and `.dockerignore`. Read `AGENTS.md`, `README.md`, `pyproject.toml`,
`argus/cli.py` (commands `init`, `demo`, `gauntlet`), `argus/chaos/gauntlet.py`, `demo_app/server.py`.

Goal: one command that stress-tests Argus in an isolated sandbox with no internet and no LLM, proving the
deterministic core (replay, self-healing, out-of-order execution, rule-based bug detection) stands on its own.

1. `sandbox/Dockerfile`: base `mcr.microsoft.com/playwright/python:v1.5x-noble` (pick the tag matching the
   installed playwright version: `.venv/Scripts/python.exe -c "import playwright,importlib.metadata as m; print(m.version('playwright'))"`),
   copy the repo (respect `.dockerignore`: exclude `.venv`, `.argus*`, `.git`, `.env`, `__pycache__`),
   `pip install -e .`. NEVER copy `.env` into the image.
2. `sandbox/docker-compose.yml`: services `skyops` (runs `python -m demo_app.server --host 0.0.0.0 --port 8000`;
   add a `--host` flag to nothing else - if demo_app.server lacks `--host`, run uvicorn directly:
   `uvicorn demo_app.server:app --host 0.0.0.0 --port 8000`) and `argus` (depends on skyops healthcheck;
   `ARGUS_LLM=off`; command runs `sandbox/stress.sh`). Put both on an `internal: true` network (no internet),
   set cpu/memory limits (e.g. 2 CPUs, 2g), mount `sandbox/out` for results.
3. `sandbox/stress.sh`: `argus init --url http://skyops:8000 --context demo_app/context --user pilot@skyops.io
   --password flysafe123`, then `argus demo --no-llm`, then `argus gauntlet --trials ${TRIALS:-10}`, then copy
   `.argus/gauntlet.json` and the runs to `/out`. Exit non-zero if bug recall < 1.0 or false-alarm rate > 0.
4. `sandbox/README.md`: how to run (`docker compose -f sandbox/docker-compose.yml up --build --abort-on-container-exit`),
   what it proves, where results land.
5. Build and run it once with TRIALS=3 (Docker Desktop is installed). Report the gauntlet summary line and any
   failures. If Argus itself misbehaves, do NOT edit argus/ - describe the failure precisely (test, verdict,
   observation text) in your final summary.
