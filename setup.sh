#!/usr/bin/env bash
# setup.sh
set -euo pipefail

log() { echo "[setup] $(date -u +"%Y-%m-%dT%H:%M:%SZ") $*"; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

log "Repo root: $ROOT_DIR"
log "Venv: $VENV_DIR"

# --- System deps (Ubuntu/Debian) ---
if command -v apt-get >/dev/null 2>&1; then
  log "Installing OS packages..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y --no-install-recommends \
    ca-certificates curl git jq \
    build-essential pkg-config cmake ninja-build \
    "$PYTHON_BIN" "${PYTHON_BIN}-venv" "${PYTHON_BIN}-dev" \
    libssl-dev libffi-dev \
    liboqs-dev
fi

# --- Venv ---
if [ ! -d "$VENV_DIR" ]; then
  log "Creating venv..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"
log "Activate venv: source $VENV_DIR/bin/activate"

log "Upgrading pip tooling..."
python -m pip install -U pip setuptools wheel

# --- Make repo root importable (needed for `import pq.py...` paths) ---
SITE_PACKAGES="$(python -c 'import site; print(site.getsitepackages()[0])')"
log "site-packages: $SITE_PACKAGES"
echo "$ROOT_DIR" > "$SITE_PACKAGES/animica_repo_root.pth"
log "Wrote $SITE_PACKAGES/animica_repo_root.pth -> $ROOT_DIR"

# --- Python runtime deps (explicit, since omni-sdk is not on PyPI) ---
log "Installing Python dependencies..."
python -m pip install -U \
  typer rich \
  requests httpx \
  cbor2 \
  pyyaml python-dotenv \
  cryptography

# --- PQ deps ---
# NOTE: you already have liboqs-dev from apt above. Now install the python wrapper.
log "Installing liboqs-python..."
python -m pip install -U liboqs-python

# --- Install animica (WITHOUT pulling undeclared/unavailable deps like omni-sdk) ---
# This avoids:
#   ERROR: Could not find a version that satisfies the requirement omni-sdk>=0.1.0
log "Installing Animica (editable, no-deps) from ./python ..."
python -m pip install -e "$ROOT_DIR/python" --no-deps

log "Sanity checks..."
python -c "import cbor2, requests; print('cbor2 ok, requests ok')"
python -c "import animica; print('animica ok:', getattr(animica, '__version__', 'unknown'))"
python -c "import pq.py.sign as s; print('pq ok:', s.__name__)"

log "Done."
log "If you see oqs/liboqs warnings, verify enabled mechanisms:"
log "  python -c \"import oqs; print(oqs.get_enabled_sig_mechanisms())\""
