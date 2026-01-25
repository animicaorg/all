#!/usr/bin/env bash
# Build node payload for local GUI development (macOS/Linux).

set -euo pipefail

log()  { printf "\033[1;34m[build-node]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PAYLOAD_DIR="$REPO_ROOT/dist/animica-node"
NODE_BINARY="${ANIMICA_NODE_BINARY:-$REPO_ROOT/target/release/animica-node}"

case "$(uname -s)" in
  Darwin)
    log "Using macOS node build pipeline"
    "$REPO_ROOT/ops/build/build-node-macos.sh"
    exit 0
    ;;
  Linux)
    ;;
  *)
    die "Unsupported platform: $(uname -s)"
    ;;
esac

if [[ ! -x "$NODE_BINARY" ]]; then
  if [[ -x "$REPO_ROOT/animica-node" ]]; then
    NODE_BINARY="$REPO_ROOT/animica-node"
  elif command -v animica-node >/dev/null 2>&1; then
    NODE_BINARY="$(command -v animica-node)"
  else
    die "Node binary not found. Set ANIMICA_NODE_BINARY to a built animica-node executable."
  fi
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
