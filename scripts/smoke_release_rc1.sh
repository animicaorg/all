#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

failures=0

run_step() {
  local name="$1"
  shift
  echo "## ${name}"
  if "$@"; then
    echo "PASS: ${name}"
  else
    local rc=$?
    echo "FAIL(${rc}): ${name}"
    failures=$((failures + 1))
  fi
  echo
}

run_step "sync smoke" "${ROOT_DIR}/scripts/smoke_sync_e2e.sh"
run_step "wallet and tx smoke" "${ROOT_DIR}/scripts/smoke_wallet_and_tx.sh"
run_step "frontend surfaces smoke" "${ROOT_DIR}/scripts/smoke_frontend_surfaces.sh"
run_step "exchange surfaces smoke" "${ROOT_DIR}/scripts/smoke_exchange_surfaces.sh"

if (( failures > 0 )); then
  echo "RC1 smoke failed: ${failures} top-level step(s) failed."
  exit 1
fi

echo "RC1 smoke passed."
