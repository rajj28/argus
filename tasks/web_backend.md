# Task: Argus web backend + live job runner + deploy artefacts

Read `AGENTS.md`, `docs/WEB_SPEC.md` (the contract - implement it exactly), `argus/cli.py`, `argus/mcp_server.py`,
`argus/models.py`, `argus/report/dashboard.py` (reuse loaders where sensible). Own: `argus/web/app.py`,
`argus/web/jobs.py`, `argus/web/__init__.py`, `scripts/export_site_data.py`, `scripts/bootstrap_live.sh`, `site_data/**`,
`deploy/**`, `tests/test_web.py`. Do NOT touch `argus/web/static/**` (frontend agent) except creating the folder.
Never open .env; no git; low memory - one browser at a time.

1. `scripts/export_site_data.py`: export REAL recorded artefacts into `site_data/` with `measured_at` + `source`:
   gauntlet (`.argus/gauntlet.json`), ten_runs (`.argus-10/ten_runs.json`), saucedemo (`.argus-saucedemo/realworld.json`),
   diff (`.argus-d/diff.json`), exam scorecards (`exam/results/scorecard.json` as exam_before; exam_after if present),
   one example INTENDED_CHANGE verdict with citation and one BUG verdict with bug report + its screenshot (copy the jpg
   into `site_data/shots/`) from `.argus/runs/*/report.json`, and a recorded `argus run` terminal transcript + an MCP
   `tools/list` + `verify_change` transcript (run them once against local SkyOps on 127.0.0.1:8000 if it is up; if not,
   start `python -m demo_app.server --port 8000` in the background with a timeout and stop it after).
2. `argus/web/app.py` + `jobs.py`: everything in WEB_SPEC (routes, SSE via `StreamingResponse` text/event-stream,
   single-slot FIFO queue, per-IP rate limit, whitelisted scenarios launching the real CLI as subprocesses with
   `--home` from env `ARGUS_LIVE_HOME` (default `.argus-live`), screenshot watcher, report parsing into events, MCP
   scenario using `mcp` client stdio transport streaming JSON-RPC frames, LLM caps + `/api/llm`, SkyOps admin via env
   `SKYOPS_URL` / `SKYOPS_B_URL`). CLI: add nothing to argus/cli.py; run with
   `.venv/Scripts/python.exe -m uvicorn argus.web.app:app --port 8080`.
3. `deploy/` per WEB_SPEC (Dockerfile.web from mcr.microsoft.com/playwright/python:v1.63.0-noble, compose with caddy,
   web, skyops, skyops-b pinned to 1.3 via a startup call, Caddyfile blocking /__admin* publicly, provision_azure.sh,
   push.sh, README). The provision script must take RESOURCE_GROUP, LOCATION, VM_SIZE, ADMIN_IP as variables and print
   the public IP; do not run it.
4. `tests/test_web.py`: API contract tests with FastAPI TestClient (overview shape, scenarios list, 429 limit, SSE framing
   on a fake job, path-traversal-safe shots). Then a local smoke: start SkyOps :8000 + web :8080 (background with
   timeouts), POST scenario `cli`, read the SSE stream to completion, then `replay`; stop both servers. Report results.
