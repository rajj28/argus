#!/usr/bin/env bash
# First boot for the live Argus home (docs/WEB_SPEC.md).
#
#   - creates the live memory home (default /data/.argus-live, override with ARGUS_LIVE_HOME)
#   - seeds it with the recorded SkyOps plans + product context so `argus run` can replay offline
#   - writes <home>/config.json through the real CLI (`argus init`)
#   - deploys SkyOps 1.0 through the real deploy helper
#   - authors examples/skyops_suite.json from those plans when it is missing
#
# Usage (container):  ARGUS_LIVE_HOME=/data/.argus-live SKYOPS_URL=http://skyops:8000 \
#                       ./scripts/bootstrap_live.sh
# Usage (local):      SKYOPS_URL=http://127.0.0.1:8000 ./scripts/bootstrap_live.sh
# Every external command is bounded; the script never reads .env (the CLI picks it up itself).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

ARGUS_LIVE_HOME="${ARGUS_LIVE_HOME:-/data/.argus-live}"
SKYOPS_URL="${SKYOPS_URL:-http://skyops:8000}"
DEMO_USER="${ARGUS_DEMO_USER:-pilot@skyops.io}"
DEMO_PASSWORD="${ARGUS_DEMO_PASSWORD:-flysafe123}"
BOOTSTRAP_TIMEOUT_S="${BOOTSTRAP_TIMEOUT_S:-120}"
AUTHOR_TIMEOUT_S="${AUTHOR_TIMEOUT_S:-600}"
DEPLOY_VERSION="${DEPLOY_VERSION:-1.0}"
export PYTHONUNBUFFERED=1

PY="${ARGUS_PYTHON:-}"
if [ -z "${PY}" ]; then
  for candidate in "${REPO_ROOT}/.venv/Scripts/python.exe" .venv/bin/python python3 python; do
    if command -v "${candidate}" >/dev/null 2>&1; then PY="${candidate}"; break; fi
  done
fi
if [ -z "${PY}" ]; then
  echo "bootstrap: no python interpreter found (set ARGUS_PYTHON)" >&2
  exit 1
fi

run() {  # run <seconds> <cmd...> - never let a step hang the boot
  local limit="$1"; shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "${limit}" "$@"
  else
    "$@"
  fi
}

echo "bootstrap: python      ${PY}"
echo "bootstrap: repo        ${REPO_ROOT}"
echo "bootstrap: live home   ${ARGUS_LIVE_HOME}"
echo "bootstrap: SkyOps      ${SKYOPS_URL}"

mkdir -p "${ARGUS_LIVE_HOME}"

# 1. product context ----------------------------------------------------------------------------------
# NOTE: no plans are copied from a developer's .argus/tests here - those baselines were evolved
# against later builds and would make `replay` on v1.0 drift. Step 4b authors clean baselines
# against the build deployed in step 3 (0 LLM calls).
if [ -d "${REPO_ROOT}/demo_app/context" ]; then
  mkdir -p "${ARGUS_LIVE_HOME}/context"
  cp -f "${REPO_ROOT}"/demo_app/context/* "${ARGUS_LIVE_HOME}/context/" 2>/dev/null || true
  echo "bootstrap: copied product context (PRODUCT.md / CHANGELOG.md)"
fi

# 2. config.json through the real CLI -------------------------------------------------------------------
run "${BOOTSTRAP_TIMEOUT_S}" "${PY}" -m argus init \
  --url "${SKYOPS_URL}" \
  --context "${REPO_ROOT}/demo_app/context" \
  --user "${DEMO_USER}" --password "${DEMO_PASSWORD}" \
  --home "${ARGUS_LIVE_HOME}"

# 3. deploy the first build ---------------------------------------------------------------------------
if [ -f "${ARGUS_LIVE_HOME}/config.json" ] && [ "${SKIP_DEPLOY:-0}" != "1" ]; then
  # `python -m demo_app.deploy` only takes a port; deploy() itself accepts a full URL.
  run "${BOOTSTRAP_TIMEOUT_S}" "${PY}" -c \
    "import sys; from demo_app.deploy import deploy; deploy(sys.argv[1], sys.argv[2])" \
    "${DEPLOY_VERSION}" "${SKYOPS_URL}"
  echo "bootstrap: SkyOps is now v${DEPLOY_VERSION}"
else
  echo "bootstrap: skipping deploy (SKIP_DEPLOY=1 or no config.json)"
fi

# 4. author examples/skyops_suite.json from the plans when it is missing --------------------------------
SUITE="${REPO_ROOT}/examples/skyops_suite.json"
if [ -f "${SUITE}" ]; then
  echo "bootstrap: examples/skyops_suite.json already present"
elif [ -z "$(ls -1 "${ARGUS_LIVE_HOME}"/tests/*.json 2>/dev/null)" ]; then
  echo "bootstrap: no suite file and no plans to author one from" >&2
else
  echo "bootstrap: authoring examples/skyops_suite.json from the recorded plans"
  run "${BOOTSTRAP_TIMEOUT_S}" "${PY}" - "${ARGUS_LIVE_HOME}" "${SUITE}" <<'PYEOF'
import json
import sys
from pathlib import Path

home, suite_path = Path(sys.argv[1]), Path(sys.argv[2])
tests = []
for plan_path in sorted((home / "tests").glob("*.json")):
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    steps = []
    for step in plan.get("steps", []):
        target = step.get("target") or {}
        find = {k: v for k, v in target.items() if k in ("role", "name", "tag", "id", "testid", "type")
                and v not in (None, "")}
        if not find and step.get("find"):
            find = dict(step["find"])
        entry = {"intent": step.get("intent") or step.get("description") or "step",
                 "action": step.get("action") or "click"}
        if find:
            entry["find"] = find
        for key in ("value", "url", "save_as"):
            if step.get(key) is not None:
                entry[key] = step[key]
        steps.append(entry)
    tests.append({"id": plan.get("id", plan_path.stem), "name": plan.get("name", plan_path.stem),
                  "tags": plan.get("tags") or [], "requires_login": bool(plan.get("requires_login")),
                  "goal": plan.get("goal", ""), "start_url": plan.get("start_url", "/"),
                  "steps": steps, "oracles": plan.get("oracles") or []})
suite_path.parent.mkdir(parents=True, exist_ok=True)
suite_path.write_text(json.dumps(tests, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"  wrote {suite_path} ({len(tests)} tests)")
PYEOF
fi
cp -f "${SUITE}" "${ARGUS_LIVE_HOME}/skyops_suite.json"

# 4b. baseline the suite when the home has no plans yet (fresh container) ------------------------------
PLAN_COUNT=0
for plan in "${ARGUS_LIVE_HOME}"/tests/*.json; do
  if [ -e "${plan}" ]; then PLAN_COUNT=$((PLAN_COUNT + 1)); fi
done
if [ "${PLAN_COUNT}" = "0" ] && [ "${SKIP_AUTHOR:-0}" != "1" ]; then
  echo "bootstrap: no plans in ${ARGUS_LIVE_HOME}/tests - authoring baselines from examples/skyops_suite.json (0 LLM calls)"
  run "${AUTHOR_TIMEOUT_S}" "${PY}" -m argus author "${SUITE}" --home "${ARGUS_LIVE_HOME}" ||
    echo "bootstrap: authoring failed; retry with: argus author examples/skyops_suite.json --home ${ARGUS_LIVE_HOME}" >&2
fi

# 5. report -------------------------------------------------------------------------------------------
run 60 "${PY}" -m argus status --home "${ARGUS_LIVE_HOME}" || echo "bootstrap: `argus status` failed (non-fatal)"
echo "bootstrap: done - live home ${ARGUS_LIVE_HOME} ready"
