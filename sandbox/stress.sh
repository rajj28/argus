#!/usr/bin/env bash
# sandbox/stress.sh — Argus deterministic-core stress test
# Runs inside the `argus` container; SkyOps is reachable at http://skyops:8000
#
# Steps:
#   1. argus init      — point Argus at SkyOps inside the sandbox network
#   2. argus demo --no-llm  — four-act showcase (baseline → refactor → redesign → bugs)
#   3. argus gauntlet  — robustness benchmark (heal + bug-recall, no LLM)
#   4. copy results to /out
#   5. exit non-zero if bug_recall < 1.0 OR false_alarm_rate > 0
set -euo pipefail

TRIALS="${TRIALS:-10}"
ARGUS_HOME=".argus"
OUT_DIR="/out"

echo "========================================================"
echo "  Argus sandbox stress-test"
echo "  Target : http://skyops:8000"
echo "  Trials : ${TRIALS}"
echo "  LLM    : off (deterministic core only)"
echo "========================================================"

# ── 1. Init ──────────────────────────────────────────────────────────────────
echo ""
echo "▶ Step 1/3  argus init"
argus init \
  --url http://skyops:8000 \
  --context demo_app/context \
  --user pilot@skyops.io \
  --password flysafe123

# ── 2. Demo (no LLM) ─────────────────────────────────────────────────────────
echo ""
echo "▶ Step 2/3  argus demo --no-llm"
argus demo --no-llm

# ── 3. Gauntlet ──────────────────────────────────────────────────────────────
echo ""
echo "▶ Step 3/3  argus gauntlet --trials ${TRIALS}"
argus gauntlet --trials "${TRIALS}"
# (--no-llm is the default for gauntlet; LLM flag not passed)

# ── 4. Copy results ──────────────────────────────────────────────────────────
echo ""
echo "▶ Copying results to ${OUT_DIR}/"
mkdir -p "${OUT_DIR}"

# gauntlet summary
if [ -f "${ARGUS_HOME}/gauntlet.json" ]; then
  cp "${ARGUS_HOME}/gauntlet.json" "${OUT_DIR}/gauntlet.json"
  echo "  Copied gauntlet.json"
fi

# all run directories
if [ -d "${ARGUS_HOME}/runs" ]; then
  cp -r "${ARGUS_HOME}/runs" "${OUT_DIR}/runs"
  echo "  Copied runs/"
fi

# test memory snapshot
if [ -d "${ARGUS_HOME}/tests" ]; then
  cp -r "${ARGUS_HOME}/tests" "${OUT_DIR}/tests"
  echo "  Copied tests/"
fi

# ── 5. Evaluate pass / fail ──────────────────────────────────────────────────
echo ""
echo "▶ Evaluating gauntlet thresholds ..."

GAUNTLET_FILE="${ARGUS_HOME}/gauntlet.json"
if [ ! -f "${GAUNTLET_FILE}" ]; then
  echo "ERROR: gauntlet.json not found — gauntlet did not produce output." >&2
  exit 1
fi

# Parse with Python (already in PATH inside the container)
python3 - <<'PYEOF'
import json, sys
data = json.loads(open(".argus/gauntlet.json", encoding="utf-8").read())
s = data["summary"]
print(f"  heal_success_rate : {s['heal_success_rate']:.3f}")
print(f"  false_alarm_rate  : {s['false_alarm_rate']:.3f}")
print(f"  bug_recall        : {s['bug_recall']:.3f}")
print(f"  avg_llm_calls/run : {s['avg_llm_calls_per_run']}")
print(f"  steps_healed      : {s['steps_healed_total']}")

failures = []
if s["bug_recall"] < 1.0:
    failures.append(f"bug_recall {s['bug_recall']:.3f} < 1.0")
if s["false_alarm_rate"] > 0.0:
    failures.append(f"false_alarm_rate {s['false_alarm_rate']:.3f} > 0.0")

if failures:
    print("\nFAIL — thresholds not met:", ", ".join(failures), file=sys.stderr)
    sys.exit(1)
else:
    print("\nPASS — all gauntlet thresholds met.")
PYEOF

echo ""
echo "========================================================"
echo "  Argus sandbox stress-test COMPLETE"
echo "  Results : ${OUT_DIR}/"
echo "========================================================"
