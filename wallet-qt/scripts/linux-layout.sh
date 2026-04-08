#!/bin/bash

LINUX_LAYOUT_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LINUX_LAYOUT_PYTHON="${LINUX_LAYOUT_SCRIPT_DIR}/linux_layout.py"

resolve_linux_node_root_from_root() {
    python3 "$LINUX_LAYOUT_PYTHON" resolve-root --path "$1"
}

list_linux_node_root_candidates_from_root() {
    python3 "$LINUX_LAYOUT_PYTHON" list-root-candidates --path "$1"
}

resolve_linux_node_root_from_wallet() {
    python3 "$LINUX_LAYOUT_PYTHON" resolve-wallet --path "$1"
}

list_linux_node_root_candidates_from_wallet() {
    python3 "$LINUX_LAYOUT_PYTHON" list-wallet-candidates --path "$1"
}
