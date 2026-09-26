#!/usr/bin/env bash
# HF Space entrypoint (single container, port 7860):
#   1. SkyOps A on 127.0.0.1:8000 (the app under test; scenarios deploy versions to it)
#   2. SkyOps B on 127.0.0.1:8001, pinned to v1.3 (the `diff` scenario's candidate)
#   3. bootstrap /data/.argus-live (first boot: seeds plans, deploys 1.0, authors the suite)
#   4. uvicorn argus.web.app:app on 0.0.0.0:7860 with SKYOPS_URL / SKYOPS_B_URL set
# Secrets (JEV_API/JEV2_API/GROK_API) come from the Space's Variables/Secrets settings - the repo
# never carries a .env.
set -euo pipefail

cd /app
export PYTHONUNBUFFERED=1
export ARGUS_LIVE_HOME="${ARGUS_LIVE_HOME:-/data/.argus-live}"
export SKYOPS_URL="http://127.0.0.1:8000"
export SKYOPS_B_URL="http://127.0.0.1:8001"
export ARGUS_REPO_URL="${ARGUS_REPO_URL:-https://huggingface.co/spaces/rajj28/argus}"

mkdir -p /data "${ARGUS_LIVE_HOME}" /app/.logs

echo "hf_space: starting SkyOps A on ${SKYOPS_URL}"
python -m demo_app.server --host 127.0.0.1 --port 8000 > /app/.logs/skyops-a.log 2>&1 &
SKYOPS_A_PID=$!

echo "hf_space: starting SkyOps B on ${SKYOPS_B_URL} (pinned to v1.3)"
python -m demo_app.server --host 127.0.0.1 --port 8001 > /app/.logs/skyops-b.log 2>&1 &
SKYOPS_B_PID=$!

cleanup() {
  echo "hf_space: stopping SkyOps A/B"
  kill "${SKYOPS_A_PID}" "${SKYOPS_B_PID}" 2>/dev/null || true
  wait "${SKYOPS_A_PID}" "${SKYOPS_B_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

bash deploy/pin_version.sh 1.3 "${SKYOPS_B_URL}" ||
  echo "hf_space: could not pin SkyOps B to v1.3 (diff scenario may show the wrong build)" >&2

if [ ! -f "${ARGUS_LIVE_HOME}/config.json" ]; then
  echo "hf_space: first boot - bootstrapping ${ARGUS_LIVE_HOME}"
  ARGUS_LIVE_HOME="${ARGUS_LIVE_HOME}" SKYOPS_URL="${SKYOPS_URL}" bash scripts/bootstrap_live.sh ||
    echo "hf_space: bootstrap failed; the console still serves recorded data" >&2
fi

echo "hf_space: uvicorn argus.web.app:app on 0.0.0.0:7860"
exec python -m uvicorn argus.web.app:app --host 0.0.0.0 --port 7860
