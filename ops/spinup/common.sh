#!/usr/bin/env bash
set -euo pipefail

SPINUP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SPINUP_ROOT}/../.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-${REPO_ROOT}/tests/devnet/docker-compose.yml}"
COMPOSE_PROFILE="${COMPOSE_PROFILE:-dev}"
LOG_DIR="${LOG_DIR:-${REPO_ROOT}/logs/spinup}"
mkdir -p "${LOG_DIR}"

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log_step() {
  echo "[$(timestamp)] $*" | tee -a "${LOG_FILE}"
}

log_context() {
  log_step "Using LOG_DIR=${LOG_DIR}"
  log_step "Using COMPOSE_FILE=${COMPOSE_FILE}"
  log_step "Using COMPOSE_PROFILE=${COMPOSE_PROFILE}"
}

ensure_compose_file() {
  if [[ ! -f "${COMPOSE_FILE}" ]]; then
    echo "Compose file not found: ${COMPOSE_FILE}" >&2
    exit 1
  fi
}

run_compose() {
  ensure_compose_file
  log_step "docker compose -f ${COMPOSE_FILE} --profile ${COMPOSE_PROFILE} $*"
  # Disable colors for cleaner logs and stream everything to the log file.
  if ! docker compose --no-ansi -f "${COMPOSE_FILE}" --profile "${COMPOSE_PROFILE}" "$@" \
    2>&1 | tee -a "${LOG_FILE}"; then
    status=${PIPESTATUS[0]:-$?}
    log_step "docker compose failed with exit code ${status}."
    log_step "Recently exited containers:"
    docker compose --no-ansi -f "${COMPOSE_FILE}" --profile "${COMPOSE_PROFILE}" ps --status exited \
      2>&1 | tee -a "${LOG_FILE}" || true
    log_step "Full container status (including healthy dependencies):"
    docker compose --no-ansi -f "${COMPOSE_FILE}" --profile "${COMPOSE_PROFILE}" ps \
      2>&1 | tee -a "${LOG_FILE}" || true
    log_step "Tip: if a dependency (e.g., node1) exited early, inspect its logs with:"
    log_step "  docker compose -f ${COMPOSE_FILE} --profile ${COMPOSE_PROFILE} logs --tail=200 <service>"
    exit "${status}"
  fi
}
