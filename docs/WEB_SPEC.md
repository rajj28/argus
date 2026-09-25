# Argus web — backend, live jobs, deployment (contract between frontend and backend)

## Process layout (one Azure VM, docker compose)
- `caddy` — TLS via sslip.io: `argus.<IP>.sslip.io` → web:8080, `skyops.<IP>.sslip.io` → skyops:8000.
- `skyops` — `python -m demo_app.server --host 0.0.0.0 --port 8000` (SkyOps must also be reachable by the web
  container at `http://skyops:8000`; public link uses the sslip hostname).
- `web` — FastAPI app `argus/web/app.py` (uvicorn :8080) + in-process job runner. Image: Playwright python base.
  Env: JEV_API, JEV2_API, GROK_API passed at runtime from the VM's `/opt/argus/.env` (never baked into the image,
  never committed). Memory home for live runs: `/data/.argus-live` (volume).

## Frontend files (served by FastAPI)
`argus/web/static/index.html` (landing), `argus/web/static/live.html` (console), `argus/web/static/assets/*`
(css/js/svg). Routes: `/` → index.html, `/live` → live.html.

## API (JSON unless noted)
- `GET /api/overview` → `{generated_at, recorded: {gauntlet, ten_runs, saucedemo, exam_before, exam_after, diff},
  live: {runs_total, last_run_at, llm_calls_today, tiers: {"0":n,"1":n,...}}}` — `recorded` comes from committed
  `site_data/*.json` (exported by `scripts/export_site_data.py` from real local artifacts, each with `measured_at` and
  `source`); `live` aggregates `/data/.argus-live/runs/*/report.json`.
- `GET /api/runs/latest` → the latest live RunReport (models.RunReport JSON) or `null`.
- `GET /api/runs/{run_id}` and `GET /api/runs/{run_id}/shots/{name}` (jpeg, path-safe).
- `GET /api/skyops/state` → proxy of SkyOps `/__admin/state` (version, bugs, chaos).
- `GET /api/llm` → per provider `{name, available, calls_this_hour, cap_per_hour, exhausted}` (no keys ever).
- `GET /api/scenarios` → list `{id, title, description, expected_cost, uses_llm}`.
- `POST /api/jobs {scenario}` → `{job_id, queue_position}`; 429 with `retry_after` if the per-IP limit is hit.
- `GET /api/jobs/{job_id}` → status; `GET /api/jobs/{job_id}/events` → **SSE** stream of events:
  `{"type":"state","state":"queued|running|done|failed"}`, `{"type":"log","stream":"stdout","line":"..."}` (CLI
  output), `{"type":"shot","name":"create-mission_03.jpg","test":"create-mission","url":"/api/runs/<id>/shots/..."}`,
  `{"type":"step","test","step_id","intent","status","tier","score"}` and `{"type":"verdict","test","category",
  "rationale","changelog_refs","bug_report"}` (parsed from report.json when a test/run finishes),
  `{"type":"mcp","dir":"->|<-","frame":{...JSON-RPC...}}`, `{"type":"meters",...RunTotals}`.

## Scenarios (whitelist only - no user-provided commands, URLs or text)
Each runs as a subprocess of the real CLI (`argus ... --home /data/.argus-live`), streaming stdout lines, while a
watcher tails `runs/<latest>/screenshots` for new files and emits `shot` events; on finish parse `report.json` into
`step`/`verdict`/`meters` events.
- `replay` - deploy 1.0 then `argus run --build 1.0`
- `refresh` - deploy 1.1 then `argus run --build 1.1`
- `redesign` - deploy 1.2 then `argus run --build 1.2`
- `hidden-bugs` - deploy 1.3 then `argus run --build 1.3 --no-update`
- `chaos` - deploy 1.0 + `/__admin/chaos` random seed (all mutations) then `argus run --build 1.0 --no-update`, then chaos off
- `diff` - needs a second SkyOps instance: run `argus diff` between `http://skyops:8000` (v1.0) and `http://skyops-b:8000`
  (compose service `skyops-b` pinned to v1.3)
- `generate` - fresh temp home: explore + generate (LLM) + one run
- `mcp` - start `argus mcp` over stdio with the official `mcp` client, stream every JSON-RPC frame: initialize,
  tools/list, tools/call `last_report`, tools/call `verify_change` (description: "Mission planner v2: parameters before
  drone selection; new required Pilot in command field")
- `cli` - run `argus doctor`, `argus models`, `argus status` in sequence
First boot: `scripts/bootstrap_live.sh` inits `/data/.argus-live`, deploys 1.0 and authors `examples/skyops_suite.json`.

## Guardrails
One job at a time (FIFO queue, max 5 queued); per-IP 1 job / 90 s; job wall timeout 6 min; LLM caps per hour:
JEV 15, JEV2 15, Groq 60 (enforced in the web layer by setting `max_llm_calls_per_run` and tracking ledger totals);
jury enabled. `/__admin` of SkyOps is NOT exposed publicly (Caddy blocks `/__admin*` on the skyops host).
Security headers (CSP allowing Google Fonts + cdnjs only), no directory listing, request logging without IP storage
beyond the rate-limit window.

## Deploy artefacts (`deploy/`)
`deploy/Dockerfile.web`, `deploy/docker-compose.yml` (caddy, web, skyops, skyops-b, volumes), `deploy/Caddyfile`
(templated with `{$ARGUS_HOST}` / `{$SKYOPS_HOST}`), `deploy/provision_azure.sh` (az CLI: resource group, VM Ubuntu
24.04, size param, NSG 22 from the operator IP only + 80/443, cloud-init installing docker + 2 GB swap),
`deploy/push.sh` (rsync repo WITHOUT .env/.venv/.argus*/.git to the VM, copy a separate env file to /opt/argus/.env,
`docker compose up -d --build`), `deploy/README.md`.
