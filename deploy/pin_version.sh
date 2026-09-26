#!/usr/bin/env bash
# pin_version.sh <version> <url> - wait for a SkyOps instance, then pin it to <version>.
# Used by the skyops-b service (v1.3, for the diff scenario) and by the HF Space start script.
set -euo pipefail

VERSION="${1:?usage: pin_version.sh <version> <url>}"
URL="${2:?usage: pin_version.sh <version> <url>}"
WAIT_S="${PIN_WAIT_S:-120}"

deadline=$(( $(date +%s) + WAIT_S ))
until python -c "import urllib.request,sys; urllib.request.urlopen(sys.argv[1].rstrip('/')+'/__admin/state', timeout=3)" "$URL" 2>/dev/null; do
  if [ "$(date +%s)" -ge "${deadline}" ]; then
    echo "pin_version: ${URL} never came up within ${WAIT_S}s" >&2
    exit 1
  fi
  sleep 2
done

python -m demo_app.deploy "$VERSION" "$URL"
echo "pin_version: ${URL} pinned to v${VERSION}"
