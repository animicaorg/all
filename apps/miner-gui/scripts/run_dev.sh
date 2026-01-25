#!/usr/bin/env bash
# Development run script for Animica GUI Miner

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(cd "$APP_DIR/../.." && pwd)"

cd "$APP_DIR"

# Check if PySide6 is installed
if ! python3 -c "import PySide6" 2>/dev/null; then
    echo "PySide6 not found. Installing dependencies..."
    pip install -e ".[dev]"
fi

# Run the application
echo "Resolving node payload..."
if ! python3 - <<'PY'
from animica_miner_gui.backend.node_paths import resolve_node_executable

paths = resolve_node_executable()
print(f"node_resolve_mode={paths.mode}")
print(f"node_resolve_reason={paths.reason}")
if not paths.exe_path:
    raise SystemExit(1)
print(f"node_executable={paths.exe_path}")
PY
then
  echo "Node payload missing. Attempting to build/install..."
  "$REPO_ROOT/ops/build/install_node_payload_for_gui.sh"
  python3 - <<'PY'
from animica_miner_gui.backend.node_paths import resolve_node_executable

paths = resolve_node_executable()
print(f"node_resolve_mode={paths.mode}")
print(f"node_resolve_reason={paths.reason}")
if not paths.exe_path:
    raise SystemExit(1)
print(f"node_executable={paths.exe_path}")
PY
fi

echo "Starting Animica GUI Miner..."
python3 -m animica_miner_gui.main "$@"
