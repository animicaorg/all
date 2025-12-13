#!/usr/bin/env bash
set -euo pipefail

log()  { echo "[setup] $(date -u +%FT%TZ) $*"; }
warn() { echo "[setup][WARN] $*" >&2; }
die()  { echo "[setup][ERROR] $*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT/.venv"

# Parse command-line flags
FRESH_INSTALL=false
if [ "${FRESH:-}" = "1" ]; then
  FRESH_INSTALL=true
fi

for arg in "$@"; do
  case "$arg" in
    --fresh)
      FRESH_INSTALL=true
      ;;
    --help|-h)
      cat <<EOF
Usage: $0 [OPTIONS]

Options:
  --fresh       Remove existing .venv and perform a clean installation
  -h, --help    Show this help message

Environment Variables:
  FRESH=1       Same as --fresh flag
  PIP_INDEX_URL If set, use this as the primary pip index
  PIP_EXTRA_INDEX_URL If set, use this as an additional pip index

Examples:
  # Regular idempotent setup
  ./setup.sh

  # Fresh installation (removes existing .venv)
  ./setup.sh --fresh
  # OR
  FRESH=1 ./setup.sh

  # Use custom pip index for omni-sdk
  PIP_EXTRA_INDEX_URL=https://custom-index.example.com/simple ./setup.sh
EOF
      exit 0
      ;;
  esac
done

install_system_deps() {
  if ! have apt-get; then
    warn "apt-get not found; skipping system deps install."
    return
  fi

  log "Installing minimal system dependencies via apt-get"
  
  # Only install if not already installed (idempotent)
  local NEEDED_PKGS=()
  for pkg in ca-certificates curl git python3 python3-venv python3-pip; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
      NEEDED_PKGS+=("$pkg")
    fi
  done
  
  if [ "${#NEEDED_PKGS[@]}" -gt 0 ]; then
    log "Installing packages: ${NEEDED_PKGS[*]}"
    
    # Check if sudo is available and needed
    if [ "$(id -u)" -ne 0 ]; then
      if ! have sudo; then
        warn "sudo not available and not running as root. Cannot install system packages."
        warn "Please install these packages manually: ${NEEDED_PKGS[*]}"
        return
      fi
      sudo apt-get update -y
      sudo apt-get install -y --no-install-recommends "${NEEDED_PKGS[@]}"
    else
      apt-get update -y
      apt-get install -y --no-install-recommends "${NEEDED_PKGS[@]}"
    fi
  else
    log "All required system packages already installed"
  fi
}

ensure_venv() {
  if [ "$FRESH_INSTALL" = true ] && [ -d "$VENV_DIR" ]; then
    log "FRESH mode: removing existing virtual environment at $VENV_DIR"
    rm -rf "$VENV_DIR"
  fi
  
  if [ -d "$VENV_DIR" ]; then
    log "Virtual environment already exists at $VENV_DIR (reusing)"
  else
    log "Creating virtual environment at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
  fi
  
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"

  log "Upgrading pip, setuptools, and wheel"
  python -m pip install -U pip setuptools wheel --quiet
}

install_local_dependencies() {
  log "Installing local SDK dependencies (omni-sdk)"
  
  # Install omni-sdk from local path first (required by animica[dev])
  if [ -d "$ROOT/sdk/python" ] && [ -f "$ROOT/sdk/python/pyproject.toml" ]; then
    log "Installing omni-sdk from $ROOT/sdk/python"
    if ! python -m pip install -e "$ROOT/sdk/python" --quiet; then
      die "Failed to install omni-sdk from local path. Check $ROOT/sdk/python/pyproject.toml"
    fi
  else
    warn "omni-sdk package not found at $ROOT/sdk/python"
    warn "If animica[dev] requires omni-sdk, installation may fail."
    warn "To use a custom pip index for omni-sdk, set PIP_EXTRA_INDEX_URL:"
    warn "  export PIP_EXTRA_INDEX_URL=https://your-index.example.com/simple"
    warn "  ./setup.sh"
  fi
}

install_animica() {
  log "Installing Animica package in editable mode"

  if [ -d "$ROOT/python" ] && [ -f "$ROOT/python/pyproject.toml" ]; then
    if ! python -m pip install -e "$ROOT/python[dev]"; then
      die "Failed to install animica[dev]. If omni-sdk is required but not found, ensure PIP_EXTRA_INDEX_URL is set or omni-sdk exists at $ROOT/sdk/python"
    fi
  elif [ -f "$ROOT/pyproject.toml" ]; then
    if ! python -m pip install -e "$ROOT[dev]"; then
      die "Failed to install animica[dev] from root pyproject.toml"
    fi
  else
    die "Could not find pyproject.toml (checked ./python and repo root)"
  fi
  
  # Also install the pq package (now uses pure-Python backend)
  if [ -d "$ROOT/pq" ] && [ -f "$ROOT/pq/pyproject.toml" ]; then
    log "Installing pq package (pure-Python PQ backend)"
    python -m pip install -e "$ROOT/pq"
  fi
}

verify_installation() {
  log "Verifying installation"
  
  # Check that animica command is available
  if ! python -m animica --help >/dev/null 2>&1; then
    die "Installation verification failed: 'python -m animica --help' failed"
  fi
  
  # Check that console script was installed
  if [ ! -x "$VENV_DIR/bin/animica" ]; then
    warn "Console script not found at $VENV_DIR/bin/animica"
  fi
  
  log "✓ Installation verified successfully"
}

print_usage() {
  cat <<EOF

========================================================================
  Animica Setup Complete
========================================================================

To use Animica:

  1. Activate the virtual environment:
     $ source .venv/bin/activate

  2. Run the animica CLI:
     $ animica --help

  3. Test PQ functionality:
     $ python -c "from animica.pq import kem_keygen, kem_encaps, kem_decaps; ek,dk=kem_keygen(); k,ct=kem_encaps(ek); assert kem_decaps(dk,ct)==k; print('KEM ok')"
     $ python -c "from animica.pq import sig_keygen, sig_sign, sig_verify; pk,sk=sig_keygen(); m=b'hi'; s=sig_sign(sk,m); assert sig_verify(pk,m,s); print('SIG ok')"

  4. Run tests:
     $ pytest -q python/animica/pq/tests

Post-quantum cryptography is enabled by default using pure-Python
implementations (no liboqs/oqs dependencies required).

To disable PQ (for testing fallback behavior):
  $ export ANIMICA_PQ_MODE=disabled

For more information, see:
  - docs/pq_pure_python.md
  - python/animica/pq/README.md (if exists)

========================================================================

EOF
}

main() {
  if [ "$FRESH_INSTALL" = true ]; then
    log "Animica setup starting (FRESH mode - clean install)"
  else
    log "Animica setup starting (Ubuntu 24.04 compatible, idempotent)"
  fi
  
  install_system_deps
  ensure_venv
  install_local_dependencies
  install_animica
  verify_installation
  print_usage
  
  log "Setup complete!"
}

main "$@"
