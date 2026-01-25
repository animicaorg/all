#!/usr/bin/env bash
# Build and install node payload into the GUI dev bundle.

set -euo pipefail

log()  { printf "\033[1;34m[install-node]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GUI_NODE_DIR="$REPO_ROOT/apps/miner-gui/animica_miner_gui/node/animica-node"
NODE_PAYLOAD_DIR="${ANIMICA_NODE_PAYLOAD:-$REPO_ROOT/dist/animica-node}"

log "Building node payload"
"$REPO_ROOT/ops/build/build_node_local.sh"

NODE_BIN="$NODE_PAYLOAD_DIR/animica-node"
if [[ ! -x "$NODE_BIN" ]]; then
  die "Node payload missing or not executable at: $NODE_BIN"
fi

log "Installing node payload into GUI: $GUI_NODE_DIR"
rm -rf "$GUI_NODE_DIR"
mkdir -p "$GUI_NODE_DIR"
rsync -a "$NODE_PAYLOAD_DIR/" "$GUI_NODE_DIR/"
chmod +x "$GUI_NODE_DIR/animica-node"

log "Node payload installed for GUI development."
