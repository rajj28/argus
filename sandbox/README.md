# Argus Sandbox — Docker Stress-Test Environment

A fully isolated, reproducible environment that stress-tests Argus's
**deterministic core** — no internet, no LLM keys required.

---

## What it proves

| Capability | How it's exercised |
|---|---|
| **Replay (T0)** | `argus demo --no-llm` baselines 8 tests against SkyOps v1.0, then replays them across v1.1 → v1.3 |
| **Self-healing (T1)** | Gauntlet applies random UI mutations (ids, classes, test-ids, wrappers, order, labels, tags, layout); every test must still pass with 0 LLM calls |
| **Out-of-order execution (T2)** | Wizard-reorder scenario in the demo (v1.2) |
| **Rule-based bug detection** | Gauntlet injects one real regression at a time; Argus must report `BUG` for every one |
| **No false alarms** | A `NEEDS_REVIEW` or `BUG` verdict on a pure-cosmetic change counts as a false alarm — must be 0 |
| **Cost** | `avg_llm_calls/run` must be 0 for the gauntlet (no LLM keys are set in the image) |

Pass criteria (enforced by `stress.sh` exit code):

```
bug_recall        == 1.0   (all injected regressions caught)
false_alarm_rate  == 0.0   (no cosmetic change mistaken for a bug)
```

---

## Quick start

```bash
# From the repo root:
docker compose -f sandbox/docker-compose.yml up --build --abort-on-container-exit

# Override the number of gauntlet mutation trials (default 10):
TRIALS=3 docker compose -f sandbox/docker-compose.yml up --build --abort-on-container-exit
```

The build takes ~2–3 minutes on first run (pip install + Chromium already in the
base image). Subsequent runs reuse the Docker layer cache.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Docker bridge network: sandbox-net  (internal: true)       │
│                                                             │
│  ┌──────────────────────┐     ┌───────────────────────────┐ │
│  │  skyops (port 8000)  │◄────│  argus (stress.sh)        │ │
│  │  SkyOps v1.0–v1.3    │     │  1. argus init            │ │
│  │  + chaos engine      │     │  2. argus demo --no-llm   │ │
│  └──────────────────────┘     │  3. argus gauntlet        │ │
│                               │  4. copy → /out           │ │
│                               └───────────────────────────┘ │
└──────────────────────────────────────────┬──────────────────┘
                                           │ bind-mount
                                    sandbox/out/  (host)
```

- `skyops` is **not** published on any host port (host port 8000 is left free).
- Both containers are CPU/memory-limited (2 CPUs, 2 GB).
- The image never contains `.env` or any API key (see `../.dockerignore`).

---

## Results

After the run, `sandbox/out/` on the host contains:

| File / folder | Contents |
|---|---|
| `gauntlet.json` | Full gauntlet summary + per-trial breakdown |
| `runs/` | Every Argus run report (JSON + evidence) |
| `tests/` | Final state of the baselined test suite |

To inspect the gauntlet summary:

```bash
python -c "import json; d=json.load(open('sandbox/out/gauntlet.json')); print(d['summary'])"
```

---

## Resource limits

Each service is limited to **2 CPUs / 2 GB RAM** via the `deploy.resources.limits`
section in `docker-compose.yml`.  Raise them if Playwright OOMs on a constrained CI runner.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `skyops` unhealthy timeout | Slow image pull / pip install | Wait longer or check Docker logs |
| `argus demo` exits 1 "SkyOps is not running" | `depends_on` healthcheck failed | `docker compose logs skyops` |
| `gauntlet.json` not in `sandbox/out/` | Gauntlet crashed before writing | `docker compose logs argus` |
| Build fails `pip install` | No internet in `internal: true` network | Build happens before the container joins the network; if the build itself fails, check Docker host DNS |

> **Note:** the `internal: true` flag cuts internet at *runtime*, not at *build time*.
> Docker build runs on the host network and can reach PyPI normally.
