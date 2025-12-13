#!/usr/bin/env bash
set -euo pipefail

log()  { echo "[setup] $(date -u +%FT%TZ) $*"; }
warn() { echo "[setup][WARN] $*" >&2; }
die()  { echo "[setup][ERROR] $*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT/.venv"

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
    sudo apt-get update -y
    sudo apt-get install -y --no-install-recommends "${NEEDED_PKGS[@]}"
  else
    log "All required system packages already installed"
  fi
}

ensure_venv() {
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

install_animica() {
  log "Installing Animica package in editable mode"
  
  if [ -d "$ROOT/python" ] && [ -f "$ROOT/python/pyproject.toml" ]; then
    python -m pip install -e "$ROOT/python"
  elif [ -f "$ROOT/pyproject.toml" ]; then
    python -m pip install -e "$ROOT"
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
  log "Animica setup starting (Ubuntu 24.04 compatible, idempotent)"
  
  install_system_deps
  ensure_venv
  install_animica
  verify_installation
  print_usage
  
  log "Setup complete!"
}

main "$@"
