#!/usr/bin/env bash
# Development run script for Animica GUI Miner

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"

cd "$APP_DIR"

# Check if PySide6 is installed
if ! python3 -c "import PySide6" 2>/dev/null; then
    echo "PySide6 not found. Installing dependencies..."
    pip install -e ".[dev]"
fi

# Run the application
echo "Starting Animica GUI Miner..."
python3 -m animica_miner_gui.main "$@"
