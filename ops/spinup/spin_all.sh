#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

LOG_FILE="${LOG_DIR}/spin_all.log"
touch "${LOG_FILE}"

log_step "Log file: ${LOG_FILE}"
log_context
log_step "Starting full devnet stack (nodes, miner, services, explorer) with compose profile '${COMPOSE_PROFILE}'."
run_compose up --build node1 node2 miner services explorer
