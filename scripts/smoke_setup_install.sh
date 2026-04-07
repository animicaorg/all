#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ANIMICA_SMOKE_VENV_DIR:-$(mktemp -d /tmp/animica-setup-smoke-XXXXXX)}"

warn() {
  echo "[smoke-setup][WARN] $*" >&2
}

echo "[smoke-setup] using venv: $VENV_DIR"

python3 -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

if ! python -m pip install -U pip setuptools wheel; then
  warn "pip bootstrap upgrade failed; continuing with bundled venv tooling"
fi

if [ -d "$ROOT/sdk/python" ] && [ -f "$ROOT/sdk/python/pyproject.toml" ]; then
  python -m pip install -e "$ROOT/sdk/python"
fi

python -m pip install -e "$ROOT/python[operator,dev]"
python -m pip install -r "$ROOT/requirements.txt"

PYTHON_BIN="$VENV_DIR/bin/python" "$ROOT/scripts/smoke_backend_imports.sh"

echo "[smoke-setup] success"
