#!/usr/bin/env bash
# Build DMG for Animica Miner GUI.

set -euo pipefail

log()  { printf "\033[1;34m[build-dmg]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

if [[ "$(uname -s)" != "Darwin" ]]; then
  die "This script must be run on macOS"
fi
command -v hdiutil >/dev/null 2>&1 || die "hdiutil not found"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_DIR="$REPO_ROOT/apps/miner-gui"
DIST_DIR="$APP_DIR/dist"

APP_BUNDLE="$DIST_DIR/Animica Miner GUI.app"
if [[ ! -d "$APP_BUNDLE" ]]; then
  FOUND="$(find "$DIST_DIR" -maxdepth 3 -name "Animica Miner GUI.app" -type d -print -quit || true)"
  [[ -n "$FOUND" ]] && APP_BUNDLE="$FOUND"
fi
[[ -d "$APP_BUNDLE" ]] || die "App bundle not found in $DIST_DIR"

VERSION="$(python3 -c "import tomllib; print(tomllib.load(open(r'$APP_DIR/pyproject.toml','rb'))['project']['version'])" 2>/dev/null || echo "0.1.0")"
DMG_NAME="Animica-Miner-GUI-${VERSION}-macOS-$(uname -m).dmg"
DMG_PATH="$DIST_DIR/$DMG_NAME"

log "Creating DMG at $DMG_PATH"
hdiutil create -volname "Animica Miner GUI" \
  -srcfolder "$APP_BUNDLE" \
  -ov -format UDZO \
  "$DMG_PATH"

log "DMG ready: $DMG_PATH"
