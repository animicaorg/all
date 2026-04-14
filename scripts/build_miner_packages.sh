#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${ROOT_DIR}/python:${ROOT_DIR}:${PYTHONPATH:-}"

if [[ "${ANIMICA_BUILD_MINER_EXECUTABLES:-0}" == "1" ]]; then
  "${ROOT_DIR}/scripts/build_miner_executables.sh"
fi

python -c 'from animica.stratum_pool.package_builder import main; raise SystemExit(main())' "$@"
