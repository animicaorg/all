#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

LOG_FILE="${LOG_DIR}/spin_nodes.log"
touch "${LOG_FILE}"

log_step "Log file: ${LOG_FILE}"
log_context
log_step "Starting node1 and node2 (RPC/WS) with compose profile '${COMPOSE_PROFILE}'."
run_compose up --build node1 node2
