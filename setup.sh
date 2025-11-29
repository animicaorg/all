#!/usr/bin/env bash
set -euo pipefail

# Animica monorepo bootstrapper
# Installs Node workspace dependencies and the local Animica Python package.

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
    warn "pnpm not found; installing pnpm@9 globally via npm"
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
  python -m pip install --upgrade pip
  log "Installing Animica Python package in editable mode"
  python -m pip install -e "$ROOT_DIR/python[dev]"
  log "Installing SDK Python package in editable mode"
  python -m pip install -e "$ROOT_DIR/sdk/python"
}

log "Bootstrapping dependencies in $ROOT_DIR"
install_node_deps
setup_python

log "Setup complete. Activate the environment with 'source .venv/bin/activate'."
