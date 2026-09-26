#!/usr/bin/env bash
# Entry point of the web container: wait for SkyOps, bootstrap the live home on first boot,
# then serve the console on :8080. Every wait is bounded; the script never reads .env
# (the CLI picks the keys up itself).
set -euo pipefail

SKYOPS_URL="${SKYOPS_URL:-http://skyops:8000}"
ARGUS_LIVE_HOME="${ARGUS_LIVE_HOME:-/data/.argus-live}"
PORT="${PORT:-8080}"
WAIT_S="${SKYOPS_WAIT_S:-120}"
export PYTHONUNBUFFERED=1

up() {
  python - "$SKYOPS_URL" <<'EOF'
import sys, urllib.request
try:
    urllib.request.urlopen(sys.argv[1].rstrip("/") + "/__admin/state", timeout=3)
except Exception:
    raise SystemExit(1)
EOF
}

echo "start_web: waiting up to ${WAIT_S}s for SkyOps at ${SKYOPS_URL}"
deadline=$(( $(date +%s) + WAIT_S ))
until up; do
  if [ "$(date +%s)" -ge "${deadline}" ]; then
    echo "start_web: SkyOps never came up; serving anyway (live jobs will fail until it does)" >&2
    break
  fi
  sleep 2
done

if [ ! -f "${ARGUS_LIVE_HOME}/config.json" ]; then
  echo "start_web: first boot - bootstrapping ${ARGUS_LIVE_HOME}"
  ARGUS_LIVE_HOME="${ARGUS_LIVE_HOME}" SKYOPS_URL="${SKYOPS_URL}" bash scripts/bootstrap_live.sh ||
    echo "start_web: bootstrap failed; continuing (re-run scripts/bootstrap_live.sh to retry)" >&2
fi

echo "start_web: uvicorn argus.web.app:app on 0.0.0.0:${PORT}"
exec python -m uvicorn argus.web.app:app --host 0.0.0.0 --port "${PORT}"
