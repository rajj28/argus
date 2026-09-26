#!/usr/bin/env bash
# Ship the repo to the Argus VM and (re)start the stack (docs/WEB_SPEC.md).
#
#   VM_IP=1.2.3.4 ENV_FILE=/path/to/keys.env ./push.sh
#
# rsync copies the repo WITHOUT .env/.venv/.argus*/.git (and the usual junk) to /opt/argus,
# the separate ENV_FILE (JEV_API/JEV2_API/GROK_API...) is copied to /opt/argus/.env - it is never
# printed, never committed, never baked into an image. Then: docker compose up -d --build.
set -euo pipefail

VM_IP="${VM_IP:?set VM_IP to the public IP of the VM}"
SSH_USER="${SSH_USER:-azureuser}"
ENV_FILE="${ENV_FILE:?set ENV_FILE to the env file with the LLM keys (copied to /opt/argus/.env)}"
REMOTE_DIR="${REMOTE_DIR:-/opt/argus}"
SSH="ssh ${SSH_USER}@${VM_IP}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

[ -f "${ENV_FILE}" ] || { echo "push: ${ENV_FILE} does not exist" >&2; exit 1; }

ARGUS_HOST="${ARGUS_HOST:-argus.${VM_IP}.sslip.io}"
SKYOPS_HOST="${SKYOPS_HOST:-skyops.${VM_IP}.sslip.io}"

echo "push: ${REPO_ROOT} -> ${SSH_USER}@${VM_IP}:${REMOTE_DIR}"
${SSH} "sudo mkdir -p ${REMOTE_DIR} && sudo chown -R \$(id -u):\$(id -g) ${REMOTE_DIR}"

rsync -az --delete \
  --exclude '.env' --exclude '.env.*' --include '.env.example' \
  --exclude '.venv/' --exclude '.git/' --exclude '.argus' --exclude '.argus-*' --exclude '.argus_*' \
  --exclude '.logs/' --exclude '.logs-*' --exclude '.pytest_cache/' --exclude 'node_modules/' \
  --exclude '__pycache__/' --exclude '*.pyc' --exclude 'sandbox/out/' \
  --exclude 'deploy/.env' \
  -e ssh ./ "${SSH_USER}@${VM_IP}:${REMOTE_DIR}/"

echo "push: copying the env file to ${REMOTE_DIR}/.env (contents never printed)"
scp -q "${ENV_FILE}" "${SSH_USER}@${VM_IP}:${REMOTE_DIR}/.env"
${SSH} "chmod 600 ${REMOTE_DIR}/.env"

echo "push: docker compose up -d --build"
${SSH} "cd ${REMOTE_DIR} && set -a && . ${REMOTE_DIR}/.env && set +a \
  && ARGUS_HOST='${ARGUS_HOST}' SKYOPS_HOST='${SKYOPS_HOST}' \
     docker compose -f deploy/docker-compose.yml up -d --build"

echo
echo "push: done."
echo "  console : https://${ARGUS_HOST}"
echo "  skyops  : https://${SKYOPS_HOST}   (/__admin* blocked publicly)"
echo "  first boot runs scripts/bootstrap_live.sh inside the web container (watch: docker compose -f deploy/docker-compose.yml logs -f web)"
