#!/usr/bin/env bash
# Build node payload for macOS GUI packaging.

set -euo pipefail

log()  { printf "\033[1;34m[build-node]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

if [[ "$(uname -s)" != "Darwin" ]]; then
  die "This script must be run on macOS"
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PAYLOAD_DIR="$REPO_ROOT/dist/animica-node"
NODE_BINARY="${ANIMICA_NODE_BINARY:-$REPO_ROOT/target/release/animica-node}"

if [[ ! -x "$NODE_BINARY" ]]; then
  die "Node binary not found: $NODE_BINARY (set ANIMICA_NODE_BINARY)"
fi

log "Preparing payload at $PAYLOAD_DIR"
rm -rf "$PAYLOAD_DIR"
mkdir -p "$PAYLOAD_DIR"
cp "$NODE_BINARY" "$PAYLOAD_DIR/animica-node"

INTERNAL_DIR="$(dirname "$NODE_BINARY")/_internal"
if [[ -d "$INTERNAL_DIR" ]]; then
  log "Copying _internal payload"
  cp -R "$INTERNAL_DIR" "$PAYLOAD_DIR/_internal"
fi

chmod +x "$PAYLOAD_DIR/animica-node"
log "Node payload ready: $PAYLOAD_DIR"
