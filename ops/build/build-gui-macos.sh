#!/usr/bin/env bash
# Build the macOS GUI app with bundled node payload and manifests.

set -euo pipefail

log()  { printf "\033[1;34m[build-gui]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

if [[ "$(uname -s)" != "Darwin" ]]; then
  die "This script must be run on macOS"
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_DIR="$REPO_ROOT/apps/miner-gui"
DIST_DIR="$APP_DIR/dist"

if [[ -z "${SKIP_NODE_BUILD:-}" ]]; then
  "$REPO_ROOT/ops/build/build-node-macos.sh"
fi

export ANIMICA_NODE_PAYLOAD="${ANIMICA_NODE_PAYLOAD:-$REPO_ROOT/dist/animica-node}"

log "Building GUI app bundle"
(cd "$APP_DIR/build-scripts" && ./build_macos.sh)

APP_BUNDLE="$DIST_DIR/Animica Miner GUI.app"
if [[ ! -d "$APP_BUNDLE" ]]; then
  FOUND="$(find "$DIST_DIR" -maxdepth 3 -name "Animica Miner GUI.app" -type d -print -quit || true)"
  [[ -n "$FOUND" ]] && APP_BUNDLE="$FOUND"
fi
[[ -d "$APP_BUNDLE" ]] || die "App bundle not found in $DIST_DIR"

NODE_DEST="$APP_BUNDLE/Contents/Resources/node/animica-node"
NODE_ROOT="${ANIMICA_NODE_PAYLOAD}"
[[ -x "$NODE_ROOT/animica-node" ]] || die "Node payload missing: $NODE_ROOT/animica-node"

log "Generating manifests"
python3 -m animica_miner_gui.backend.manifest \
  --node-root "$NODE_DEST" \
  --app-bundle "$APP_BUNDLE" \
  --node-out "$APP_BUNDLE/Contents/Resources/node-manifest.json" \
  --gui-out "$APP_BUNDLE/Contents/Resources/gui-manifest.json"

log "Rebuilding DMG with manifests"
"$REPO_ROOT/ops/build/build-dmg-macos.sh"

log "Running artifact verification"
"$REPO_ROOT/ops/build/verify-artifacts.sh"

log "GUI build complete: $APP_BUNDLE"
