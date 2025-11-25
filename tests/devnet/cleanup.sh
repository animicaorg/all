#!/usr/bin/env bash
# Stop the devnet stack and remove containers, networks, and (optionally) volumes/bind-mount data.
#
# Usage:
#   bash tests/devnet/cleanup.sh
#
# Environment overrides:
#   COMPOSE_FILE            (default: tests/devnet/docker-compose.yml)
#   COMPOSE_PROJECT_NAME    (default: animica-devnet)
#   VOLUMES                 (default: 1)  # pass 0 to keep docker volumes
#   WIPE_BIND_MOUNTS        (default: 0)  # pass 1 to rm -rf the bind data dir (dangerous)
#   BIND_DATA_DIR           (default: tests/devnet/.data) # only used if WIPE_BIND_MOUNTS=1
#   FORCE                   (default: 0)  # pass 1 to skip confirmation prompt
set -euo pipefail

bold()   { printf "\033[1m%s\033[0m" "$*"; }
green()  { printf "\033[32m%s\033[0m" "$*"; }
yellow() { printf "\033[33m%s\033[0m" "$*"; }
red()    { printf "\033[31m%s\033[0m" "$*"; }

have_cmd() { command -v "$1" >/dev/null 2>&1; }

# Resolve repo root from this script's location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Defaults
COMPOSE_FILE="${COMPOSE_FILE:-${SCRIPT_DIR}/docker-compose.yml}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-animica-devnet}"
VOLUMES="${VOLUMES:-1}"
WIPE_BIND_MOUNTS="${WIPE_BIND_MOUNTS:-0}"
BIND_DATA_DIR="${BIND_DATA_DIR:-${SCRIPT_DIR}/.data}"
FORCE="${FORCE:-0}"

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
echo "🧹  $(bold "Animica devnet cleanup")"
echo "    Compose file:       ${COMPOSE_FILE}"
echo "    Project name:       ${COMPOSE_PROJECT_NAME}"
echo "    Remove volumes:     ${VOLUMES}"
echo "    Wipe bind mounts:   ${WIPE_BIND_MOUNTS} (${BIND_DATA_DIR})"
echo ""

ask_confirm() {
  local prompt="$1"
  read -r -p "$(yellow "${prompt} [y/N] ")"
  case "${REPLY:-}" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

if [[ "$FORCE" != "1" ]]; then
  ask_confirm "Proceed to stop containers for project '${COMPOSE_PROJECT_NAME}'?" || {
    echo "Aborted."
    exit 0
  }
fi

DOWN_ARGS=(--remove-orphans)
if [[ "$VOLUMES" == "1" ]]; then
  DOWN_ARGS+=(-v)
fi

echo "→ Stopping stack via: ${COMPOSE_BIN[*]} -f ${COMPOSE_FILE} -p ${COMPOSE_PROJECT_NAME} down ${DOWN_ARGS[*]}"
"${COMPOSE_BIN[@]}" -f "${COMPOSE_FILE}" -p "${COMPOSE_PROJECT_NAME}" down "${DOWN_ARGS[@]}"

# Extra sweep: remove any dangling project-scoped volumes (harmless if none)
if [[ "$VOLUMES" == "1" ]]; then
  if have_cmd docker; then
    mapfile -t _vols < <(docker volume ls --format '{{.Name}}' | grep -E "^${COMPOSE_PROJECT_NAME}_" || true)
    if ((${#_vols[@]})); then
      echo "→ Removing project volumes: ${_vols[*]}"
      docker volume rm -f "${_vols[@]}" >/dev/null 2>&1 || true
    fi
  fi
fi

# Optional: wipe bind-mounted data directory (ONLY if explicitly requested)
safe_rm_rf() {
  local path="$1"
  # Safety checks: must be under repo root and include '/tests/devnet/'
  case "$path" in
    "/"|"/*"|"" ) echo "$(red "Refusing to remove unsafe path: '${path}'")"; return 1 ;;
  esac
  # Must be inside repo root
  if [[ "$(cd "$path/.." 2>/dev/null && pwd -P)" != "${REPO_ROOT}"* ]]; then
    echo "$(red "Refusing: '${path}' not under repo root '${REPO_ROOT}'")"
    return 1
  fi
  # Must contain tests/devnet in its path to reduce risk
  if [[ "$path" != *"/tests/devnet/"* && "$path" != *"/tests/devnet" ]]; then
    echo "$(red "Refusing: '${path}' is not in tests/devnet/")"
    return 1
  fi
  rm -rf -- "$path"
  return 0
}

if [[ "$WIPE_BIND_MOUNTS" == "1" ]]; then
  if [[ "$FORCE" != "1" ]]; then
    ask_confirm "Also wipe bind-mounted data at '${BIND_DATA_DIR}' (rm -rf)?" || {
      echo "Skipping bind-mount wipe."
      WIPE_BIND_MOUNTS=0
    }
  fi
  if [[ "$WIPE_BIND_MOUNTS" == "1" ]]; then
    echo "→ Wiping bind data dir: ${BIND_DATA_DIR}"
    safe_rm_rf "${BIND_DATA_DIR}" || {
      echo "$(red "Bind-mount wipe skipped due to safety checks.")"
    }
  fi
fi

echo ""
echo "$(green "✔ Cleanup complete.")"
echo "You can re-create the stack with:"
echo "  ${COMPOSE_BIN[*]} -f ${COMPOSE_FILE} -p ${COMPOSE_PROJECT_NAME} up -d"
echo ""
