#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

LOG_FILE="${LOG_DIR}/spin_web.log"
touch "${LOG_FILE}"

log_step "Log file: ${LOG_FILE}"
log_context
log_step "Starting studio-services and explorer (plus dependencies) with compose profile '${COMPOSE_PROFILE}'."
run_compose up --build services explorer
