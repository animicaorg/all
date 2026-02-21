#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="${WORK_DIR:-${ROOT_DIR}/.tmp/ena-smoke}"
LOG_DIR="${WORK_DIR}/logs"
mkdir -p "${LOG_DIR}"

log() { printf '[ena-smoke] %s\n' "$*"; }

on_fail() {
  local exit_code=$?
  log "FAILED (exit=${exit_code})"
  if [[ -f "${WORK_DIR}/report.json" ]]; then
    log "report: ${WORK_DIR}/report.json"
    cat "${WORK_DIR}/report.json" || true
  fi
  if [[ -f "${WORK_DIR}/ena_smoke_debug_bundle.zip" ]]; then
    log "debug bundle: ${WORK_DIR}/ena_smoke_debug_bundle.zip"
  fi
  exit "${exit_code}"
}
trap on_fail ERR

log "step 1: running pytest smoke test"
PYTHONPATH="${ROOT_DIR}/python" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q "${ROOT_DIR}/python/animica/tests/test_ena_e2e_smoke.py" | tee "${LOG_DIR}/pytest.log"

CLI_RUNNER=(animica)
if ! command -v animica >/dev/null 2>&1; then
  CLI_RUNNER=(python -m animica.cli.main)
fi

log "step 2: running CLI smoke test"
PYTHONPATH="${ROOT_DIR}/python" "${CLI_RUNNER[@]}" ena smoke-test --json --work-dir "${WORK_DIR}" | tee "${LOG_DIR}/cli.log"

log "SUCCESS"
