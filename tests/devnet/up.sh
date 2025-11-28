#!/usr/bin/env bash
# Start (or restart) the local Animica devnet stack defensively.
#
# This wrapper stops any existing stack for the configured compose project,
# frees the well-known host ports if another Animica container is holding
# them, and then brings the stack back up.
#
# Usage:
#   bash tests/devnet/up.sh             # uses defaults/ENV, profile=dev
#   COMPOSE_PROFILES=dev bash tests/devnet/up.sh
#
# Environment overrides:
#   COMPOSE_FILE            (default: tests/devnet/docker-compose.yml)
#   COMPOSE_PROJECT_NAME    (default: animica-devnet)
#   COMPOSE_PROFILES        (default: dev)
#   HOST_NODE1_RPC          (default: 8545)
#   HOST_NODE2_RPC          (default: 9545)
#   HOST_STUDIO_SERVICES    (default: 8787)
#   HOST_EXPLORER           (default: 5173)

set -euo pipefail

bold()   { printf "\033[1m%s\033[0m" "$*"; }
green()  { printf "\033[32m%s\033[0m" "$*"; }
yellow() { printf "\033[33m%s\033[0m" "$*"; }
red()    { printf "\033[31m%s\033[0m" "$*"; }

have_cmd() { command -v "$1" >/dev/null 2>&1; }

# Resolve repo root from this script's location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-${SCRIPT_DIR}/docker-compose.yml}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-animica-devnet}"
COMPOSE_PROFILES="${COMPOSE_PROFILES:-dev}"

HOST_NODE1_RPC="${HOST_NODE1_RPC:-8545}"
HOST_NODE2_RPC="${HOST_NODE2_RPC:-9545}"
HOST_STUDIO_SERVICES="${HOST_STUDIO_SERVICES:-8787}"
HOST_EXPLORER="${HOST_EXPLORER:-5173}"

# Detect docker compose command
if have_cmd docker && docker compose version >/dev/null 2>&1; then
  COMPOSE_BIN=(docker compose)
elif have_cmd docker-compose; then
  COMPOSE_BIN=(docker-compose)
else
  echo "$(red "ERROR") docker compose is required." >&2
  exit 1
fi

echo ""
echo "🔁  $(bold "Animica devnet restart")"
echo "    Compose file:      ${COMPOSE_FILE}"
echo "    Project name:      ${COMPOSE_PROJECT_NAME}"
echo "    Profiles:          ${COMPOSE_PROFILES}"
echo "    Host ports:        node1=${HOST_NODE1_RPC}, node2=${HOST_NODE2_RPC}, services=${HOST_STUDIO_SERVICES}, explorer=${HOST_EXPLORER}"
echo ""

# Stop any existing stack for this project (frees ports if the old stack is running)
echo "→ Stopping existing stack (if any)"
"${COMPOSE_BIN[@]}" -f "${COMPOSE_FILE}" -p "${COMPOSE_PROJECT_NAME}" down --remove-orphans >/dev/null 2>&1 || true

# If other Animica containers are holding the same published ports, stop them
stop_conflicts_for_port() {
  local port="$1"
  mapfile -t conflicts < <(docker ps --filter "publish=${port}" --format '{{.ID}} {{.Names}}' || true)
  (( ${#conflicts[@]} == 0 )) && return 0

  echo "→ Port ${port} is in use; stopping conflicting containers:"
  local ids=()
  for entry in "${conflicts[@]}"; do
    echo "   - ${entry}"
    ids+=("${entry%% *}")
  done
  docker stop "${ids[@]}" >/dev/null 2>&1 || true
  docker rm -f "${ids[@]}" >/dev/null 2>&1 || true
}

stop_conflicts_for_port "${HOST_NODE1_RPC}"
stop_conflicts_for_port "${HOST_NODE2_RPC}"
stop_conflicts_for_port "${HOST_STUDIO_SERVICES}"
stop_conflicts_for_port "${HOST_EXPLORER}"

# Bring the stack back up with the requested profiles
IFS=',' read -r -a PROFILE_LIST <<<"${COMPOSE_PROFILES}"
PROFILE_FLAGS=()
for p in "${PROFILE_LIST[@]}"; do
  [[ -n "$p" ]] && PROFILE_FLAGS+=(--profile "$p")
done

echo "→ Starting stack"
"${COMPOSE_BIN[@]}" -f "${COMPOSE_FILE}" -p "${COMPOSE_PROJECT_NAME}" "${PROFILE_FLAGS[@]}" up -d --build --remove-orphans

echo ""
echo "$(green "✔ Devnet is starting.") Check status with: ${COMPOSE_BIN[*]} -f ${COMPOSE_FILE} -p ${COMPOSE_PROJECT_NAME} ps"
echo ""
