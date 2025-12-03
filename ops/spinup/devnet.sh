#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENV_BASELINE="${REPO_ROOT}/tests/devnet/env.devnet.example"
ENV_LOCAL="${REPO_ROOT}/tests/devnet/.env"

if [[ -f "${ENV_BASELINE}" && ! -f "${ENV_LOCAL}" ]]; then
  echo "[devnet] Creating default env file at tests/devnet/.env"
  cp "${ENV_BASELINE}" "${ENV_LOCAL}"
fi

echo "[devnet] Starting full devnet (nodes, miner, studio-services, explorer)"
exec "${SCRIPT_DIR}/spin_all.sh"
