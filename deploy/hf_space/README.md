---
title: Argus — the tireless hand
emoji: 🦅
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
license: mit
tags:
  - testing
  - playwright
  - agents
short_description: Autonomous, self-healing, cost-aware UI testing agent
---

# Argus — autonomous, self-healing, cost-aware UI testing agent

Argus tests the SkyOps demo app like a user, heals when the UI changes, and knows a bug from a
feature. Each journey is compiled once into a deterministic plan and replayed with **zero LLM
calls**; models are only consulted for replanning and triage, under hourly caps.

- **Landing page** (`/`): recorded evidence — gauntlet, ten-runs cost curve, SauceDemo, exam
  scorecards, diff — exported from real local artefacts (`site_data/`).
- **Live console** (`/live`): run a whitelisted scenario (replay v1.0 → hidden-bug v1.3, chaos,
  diff, generate, MCP, CLI) and watch a real browser driven by Argus over SSE, with screenshots,
  step tiers, verdicts and meters.

## How this Space runs

One container (Docker SDK): SkyOps A on `127.0.0.1:8000` (app under test), SkyOps B on
`127.0.0.1:8001` pinned to v1.3 (diff candidate), the FastAPI console + job runner on `0.0.0.0:7860`.
Live memory lives in `/data/.argus-live` (bootstrapped by `scripts/bootstrap_live.sh` on first
boot). Guardrails: one job at a time (FIFO, max 5 queued), 1 job / 90 s per IP, 6 min wall timeout,
hourly LLM caps (JEV 15 / JEV2 15 / Groq 60).

Set the LLM keys as Space **Variables/Secrets** (`JEV_API`, `JEV2_API`, `GROK_API`) — scenarios
that need no LLM work without them.

## Files in this Space

- `Dockerfile` (root) ← `deploy/hf_space/Dockerfile`
- `deploy/hf_space/start.sh` — container entrypoint
- `deploy/hf_space/push.py` — publishes this Space from the working repo (needs `HF_TOKEN`)
