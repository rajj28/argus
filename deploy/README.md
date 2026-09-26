# deploy/ — Argus on one Azure VM (docs/WEB_SPEC.md)

Single VM, docker compose: **caddy** (TLS via sslip.io) → **web** (FastAPI console + live job
runner, :8080) → **skyops** (app under test, :8000) and **skyops-b** (pinned to v1.3 for the
`diff` scenario). The web container keeps its live memory in the `argus-live` volume at
`/data/.argus-live`; first boot runs `scripts/bootstrap_live.sh`.

| file | what it is |
| --- | --- |
| `Dockerfile.web` | Playwright python 1.63 base, `pip install -e .`, runs `deploy/start_web.sh` |
| `start_web.sh` | waits for SkyOps, bootstraps `/data/.argus-live` once, then uvicorn :8080 |
| `docker-compose.yml` | caddy + web + skyops + skyops-b + volumes |
| `Caddyfile` | `{$ARGUS_HOST}` → web, `{$SKYOPS_HOST}` → skyops with `/__admin*` blocked publicly |
| `pin_version.sh` | wait for a SkyOps URL, then pin it to a version (skyops-b → 1.3) |
| `provision_azure.sh` | az CLI: RG, NSG (22 from your IP only + 80/443), Ubuntu 24.04 VM, docker + 2 GB swap |
| `push.sh` | rsync repo (never `.env`/`.venv`/`.argus*`/`.git`), copy a separate env file to `/opt/argus/.env`, compose up |

## 1. Provision (once)

```bash
az login
RESOURCE_GROUP=argus LOCATION=westeurope VM_SIZE=Standard_B2s ADMIN_IP=<your-ip> \
  ./deploy/provision_azure.sh        # prints the public IP
```

## 2. Keys

Create an env file **outside the repo** (or reuse the local `.env` — it is never committed):

```
JEV_API=sk-or-v1-...        # OpenRouter, layer 1
JEV2_API=sk-or-v1-...       # second OpenRouter key (optional)
GROK_API=gsk_...            # Groq (optional)
```

## 3. Push & start

```bash
VM_IP=<public-ip> ENV_FILE=/path/to/keys.env ./deploy/push.sh
```

Console: `https://argus.<IP>.sslip.io` · SkyOps: `https://skyops.<IP>.sslip.io`
(`/__admin*` returns 403 publicly; the web container talks to it over the compose network).

## Local dry run (no VM)

```bash
ARGUS_HOST=localhost SKYOPS_HOST=skyops.localhost docker compose -f deploy/docker-compose.yml up --build
# console on http://localhost  (Caddy :80)
```

## Guardrails recap (enforced in the web layer)

One job at a time (FIFO, max 5 queued) · 1 job / 90 s per IP · 6 min wall timeout · hourly LLM caps
JEV 15 / JEV2 15 / Groq 60 pushed into the CLI via `max_llm_calls_per_run` · jury enabled ·
security headers + CSP on every response · no IPs stored beyond the rate-limit window.

## Hugging Face Spaces

`deploy/hf_space/` holds the single-container Space variant (Docker SDK, port 7860, both SkyOps
instances on loopback) and `push.py` to publish `rajj28/argus`. See `deploy/hf_space/README.md`.
