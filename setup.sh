#!/usr/bin/env bash
set -euo pipefail

# Animica monorepo bootstrapper
# Installs Node workspace dependencies and the local Animica Python packages.
# Ensures trio is available so trio-based pytest runs.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BLUE='\033[34m'; GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; RESET='\033[0m'
log() { echo -e "${BLUE}[setup]${RESET} $*"; }
warn() { echo -e "${YELLOW}[warn]${RESET} $*"; }
fail() { echo -e "${RED}[fail]${RESET} $*"; exit 1; }

ensure_pnpm() {
  if command -v pnpm >/dev/null 2>&1; then
    echo pnpm
    return
  fi
  if command -v npm >/dev/null 2>&1; then
    warn "pnpm not found; installing pnpm@9 globally via npm" >&2
    npm install -g pnpm@9 >/dev/null 2>&1 || fail "npm could not install pnpm"
    echo pnpm
    return
  fi
  fail "Neither pnpm nor npm is installed; please install one to continue"
}

install_node_deps() {
  local mgr
  mgr=$(ensure_pnpm)
  log "Installing Node workspace dependencies with $mgr"
  (cd "$ROOT_DIR" && $mgr install)
}

setup_python() {
  log "Creating Python virtual environment (.venv)"
  python3 -m venv "$ROOT_DIR/.venv"

  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"

  log "Upgrading pip and build tooling"
  python -m pip install --upgrade pip setuptools wheel

  if [[ -f "$ROOT_DIR/requirements.txt" ]]; then
    log "Installing shared Python dependencies (requirements.txt)"
    python -m pip install -r "$ROOT_DIR/requirements.txt"
  else
    warn "requirements.txt not found; skipping shared Python dependencies"
  fi

  log "Installing Animica Python package in editable mode with dev and stratum extras"
  python -m pip install -e "$ROOT_DIR/python[dev,stratum]"

  log "Installing SDK Python package in editable mode"
  python -m pip install -e "$ROOT_DIR/sdk/python"

  # Install pq module for bech32 support (required by omni_sdk)
  log "Installing pq module for post-quantum crypto support"
  if [[ ! -f "$ROOT_DIR/.venv/lib/python3.12/site-packages/animica-pq.pth" ]] && \
     [[ ! -f "$ROOT_DIR/.venv/lib/python3.11/site-packages/animica-pq.pth" ]] && \
     [[ ! -f "$ROOT_DIR/.venv/lib/python3.10/site-packages/animica-pq.pth" ]]; then
    # Add repository root to Python path so pq module can be imported
    SITE_PACKAGES=$(python -c "import site; print(site.getsitepackages()[0])")
    echo "$ROOT_DIR" > "$SITE_PACKAGES/animica-pq.pth"
  fi

  # Install additional required dependencies
  log "Installing additional dependencies (requests for CLI, trio for RPC tests)"
  python -m pip install requests trio
}

log "Bootstrapping dependencies in $ROOT_DIR"
install_node_deps
setup_python

# Prepare a writable devnet environment file for docker-compose overrides
DEVNET_ENV_EXAMPLE="$ROOT_DIR/tests/devnet/env.devnet.example"
DEVNET_ENV_LOCAL="$ROOT_DIR/tests/devnet/.env"
if [[ -f "$DEVNET_ENV_EXAMPLE" && ! -f "$DEVNET_ENV_LOCAL" ]]; then
  log "Creating default devnet env at tests/devnet/.env (customize as needed)"
  cp "$DEVNET_ENV_EXAMPLE" "$DEVNET_ENV_LOCAL"
fi

log "Setup complete. Activate the environment with 'source .venv/bin/activate'."