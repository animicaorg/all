#!/usr/bin/env bash
set -euo pipefail
set -o errtrace
IFS=$'\n\t'

trap 'echo "[setup] $(date -u +"%Y-%m-%dT%H:%M:%SZ") ERROR line ${LINENO}: ${BASH_COMMAND:-unknown}" >&2' ERR

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"

WITH_PQ=false
SKIP_NODE=false
SKIP_PNPM=false
SKIP_PYTHON=false
CLEAN=false

log()  { echo "[setup] $(date -u +"%Y-%m-%dT%H:%M:%SZ") $*"; }
warn() { echo "[setup] $(date -u +"%Y-%m-%dT%H:%M:%SZ") WARN: $*" >&2; }

usage() {
  cat <<'USAGE'
Usage: ./setup.sh [options]

Options:
  --clean         Remove .venv before installing
  --with-pq       Install PQ deps (oqs/liboqs-python) needed for Dilithium signing
  --skip-python   Skip python venv + pip installs
  --skip-node     Skip Node.js install/check
  --skip-pnpm     Skip pnpm install/check
  -h, --help      Show help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean) CLEAN=true ;;
    --with-pq) WITH_PQ=true ;;
    --skip-python) SKIP_PYTHON=true ;;
    --skip-node) SKIP_NODE=true ;;
    --skip-pnpm) SKIP_PNPM=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

require_root() {
  if [[ $EUID -ne 0 ]]; then
    echo "[setup] This script must be run as root (try: sudo ./setup.sh)." >&2
    exit 1
  fi
}

require_ubuntu() {
  if [[ ! -f /etc/os-release ]]; then
    echo "[setup] Missing /etc/os-release; cannot detect OS." >&2
    exit 1
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    echo "[setup] Detected OS: ${PRETTY_NAME:-unknown}. This script expects Ubuntu." >&2
    exit 1
  fi
  log "Detected ${PRETTY_NAME:-Ubuntu}"
}

apt_update() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
}

apt_install() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -y --no-install-recommends "$@"
}

clean_state() {
  if [[ "$CLEAN" == "true" ]]; then
    log "Cleaning previous state"
    rm -rf "$VENV_DIR"
  fi
}

ensure_base_packages() {
  log "Installing base packages"
  apt_update
  apt_install ca-certificates curl git build-essential pkg-config lsof jq
}

ensure_python() {
  log "Installing Python packages"
  apt_install python3 python3-venv python3-pip python3-dev
}

ensure_node() {
  if [[ "$SKIP_NODE" == "true" ]]; then
    log "Skipping Node.js"
    return
  fi
  if command -v node >/dev/null 2>&1; then
    log "Node already installed: $(node -v)"
    return
  fi
  log "Installing Node.js (Ubuntu repo version)"
  apt_install nodejs npm
  log "Node installed: $(node -v)"
}

ensure_pnpm() {
  if [[ "$SKIP_PNPM" == "true" ]]; then
    log "Skipping pnpm"
    return
  fi
  if command -v pnpm >/dev/null 2>&1; then
    log "pnpm already installed: $(pnpm -v)"
    return
  fi
  if command -v corepack >/dev/null 2>&1; then
    log "Enabling pnpm via corepack"
    corepack enable || true
    corepack prepare pnpm@latest --activate || true
  fi
  if ! command -v pnpm >/dev/null 2>&1; then
    log "Installing pnpm via npm"
    npm install -g pnpm
  fi
  log "pnpm installed: $(pnpm -v)"
}

create_venv() {
  log "Creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  python -m pip install -U pip setuptools wheel
}

install_python_deps() {
  if [[ "$SKIP_PYTHON" == "true" ]]; then
    log "Skipping Python environment"
    return
  fi

  create_venv

  if [[ "$WITH_PQ" == "true" ]]; then
    log "Installing PQ deps (oqs/liboqs-python)"
    # The module is imported as "oqs"
    python -m pip install -U oqs || python -m pip install -U liboqs-python || true
  fi

  # If the repo contains a local omni-sdk, install it (prevents 'omni-sdk not found' if any legacy deps still reference it).
  if [[ -d "$ROOT_DIR/omni-sdk" ]]; then
    log "Installing local omni-sdk from $ROOT_DIR/omni-sdk"
    python -m pip install -e "$ROOT_DIR/omni-sdk" || true
  fi
  if [[ -d "$ROOT_DIR/sdk/omni-sdk" ]]; then
    log "Installing local omni-sdk from $ROOT_DIR/sdk/omni-sdk"
    python -m pip install -e "$ROOT_DIR/sdk/omni-sdk" || true
  fi

  log "Installing Animica python package (editable)"
  set +e
  python -m pip install -e "$ROOT_DIR/python[dev,stratum]"
  status=$?
  set -e

  if [[ $status -ne 0 ]]; then
    warn "Editable install failed. Retrying without dependency resolution (workaround for missing omni-sdk on PyPI)."
    python -m pip install -e "$ROOT_DIR/python[dev,stratum]" --no-deps
    python -m pip install -U \
      typer httpx respx cryptography fastapi uvicorn pytest \
      cbor2 requests
  else
    log "Ensuring required runtime deps are present (cbor2, requests)"
    python -m pip install -U cbor2 requests
  fi

  log "Python setup complete"
}

install_pnpm_workspace() {
  if [[ "$SKIP_PNPM" == "true" ]]; then
    return
  fi
  if [[ -f "$ROOT_DIR/pnpm-workspace.yaml" ]]; then
    log "Installing pnpm workspace deps"
    (cd "$ROOT_DIR" && pnpm install)
  fi
}

main() {
  require_root
  require_ubuntu
  clean_state
  ensure_base_packages
  ensure_python
  ensure_node
  ensure_pnpm
  install_python_deps
  install_pnpm_workspace

  log "Done."
  log "Activate venv: source $VENV_DIR/bin/activate"
}

main "$@"
