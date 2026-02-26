#!/usr/bin/env bash
# ops/setup_studio.sh – One-click Animica Studio installer (Linux)
#
# Usage:
#   ./ops/setup_studio.sh           # interactive
#   ./ops/setup_studio.sh --no-launch   # install only, don't launch
#
# What it does:
#   1. Checks Python 3.11+ is present.
#   2. Creates / activates .venv in the repo root.
#   3. Installs the animica-studio package (and optional torch).
#   4. Verifies PyTorch availability.
#   5. Launches Studio (unless --no-launch is passed).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VENV_DIR="$REPO_ROOT/.venv"
STUDIO_PKG="$REPO_ROOT/apps/animica_studio"
LAUNCH=1

for arg in "$@"; do
  case "$arg" in
    --no-launch) LAUNCH=0 ;;
  esac
done

log() { printf '\033[1;34m[setup_studio]\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# 1. Check Python
# ---------------------------------------------------------------------------
PYTHON_BIN=""
for candidate in python3.12 python3.11 python3; do
  if command -v "$candidate" &>/dev/null; then
    version_ok=$("$candidate" -c "import sys; print(1 if sys.version_info >= (3,11) else 0)" 2>/dev/null || echo 0)
    if [ "$version_ok" = "1" ]; then
      PYTHON_BIN="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  err "Python 3.11+ is required.  Install it and re-run."
  err "  Ubuntu/Debian:   sudo apt install python3.11 python3.11-venv"
  err "  Fedora/RHEL:     sudo dnf install python3.11"
  exit 1
fi

ok "Python: $($PYTHON_BIN --version)"

# ---------------------------------------------------------------------------
# 2. Create / activate venv
# ---------------------------------------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
  log "Creating virtualenv at $VENV_DIR …"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
ok "venv activated: $VIRTUAL_ENV"

# Upgrade pip silently
python -m pip install --upgrade pip --quiet

# ---------------------------------------------------------------------------
# 3. Install animica-studio
# ---------------------------------------------------------------------------
log "Installing animica-studio …"
if [ -d "$STUDIO_PKG" ]; then
  pip install -e "$STUDIO_PKG[dev]" --quiet
else
  err "animica-studio package not found at $STUDIO_PKG"
  exit 1
fi

# Install repo-level requirements if present
if [ -f "$REPO_ROOT/requirements.txt" ]; then
  log "Installing repo requirements.txt …"
  pip install -r "$REPO_ROOT/requirements.txt" --quiet
fi

ok "animica-studio installed"

# ---------------------------------------------------------------------------
# 4. Verify PyTorch (best-effort; not required for Studio UI)
# ---------------------------------------------------------------------------
if python -c "import torch; print('torch', torch.__version__)" 2>/dev/null; then
  ok "PyTorch present"
else
  log "PyTorch not detected.  ENA training will be unavailable."
  log "To install: pip install torch  (or see https://pytorch.org/get-started)"
fi

# ---------------------------------------------------------------------------
# 5. Run doctor to confirm environment
# ---------------------------------------------------------------------------
log "Running animica-studio doctor …"
if animica-studio doctor --verbose 2>&1 | sed 's/^/  /'; then
  ok "Doctor: environment looks good"
else
  warn "Doctor reported issues – see above.  Studio may still launch in degraded mode."
fi

# ---------------------------------------------------------------------------
# 6. Launch
# ---------------------------------------------------------------------------
if [ "$LAUNCH" = "1" ]; then
  log "Launching Animica Studio …"
  exec animica-studio
fi

ok "Setup complete.  Run: animica-studio"
