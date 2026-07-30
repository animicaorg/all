#!/usr/bin/env bash
# Build the macOS Animica Miner GUI binary.
#
# Produces a PyInstaller onedir build wrapped in "AnimicaMiner.app" (via the
# BUNDLE() step in the shared spec), then packages it as a compressed DMG.
# A .zip of the .app is also produced as a portable fallback.
#
# For every artifact it computes SHA256 + size and appends a single JSON line
# to the per-artifact manifest log so make_manifest.py can aggregate them.
#
# IMPORTANT: macOS apps can ONLY be built on macOS (the Apple toolchain cannot
# be cross-compiled from Linux). This script therefore runs on the macOS CI
# runner in the release workflow.
#
# Usage:
#   ./build_macos.sh
#
# Env overrides:
#   ARTIFACT_LOG     path to the JSON-lines manifest log (default: dist/artifacts.jsonl)
#   ANIMICA_GUI_UPX  "1" to enable UPX (default off; UPX is unreliable on arm64)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"          # apps/miner-gui
REPO_ROOT="$(cd "$APP_DIR/../.." && pwd)"        # repo root
DIST_DIR="${ANIMICA_GUI_DIST_DIR:-${APP_DIR}/dist}"
BUILD_DIR="${ANIMICA_GUI_BUILD_DIR:-${APP_DIR}/build}"
SPEC_FILE="${SCRIPT_DIR}/animica-miner-gui.spec"
PYI_WORK="${BUILD_DIR}/pyinstaller-work"
ARTIFACT_LOG="${ARTIFACT_LOG:-${DIST_DIR}/artifacts.jsonl}"
APP_NAME="AnimicaMiner"

log()  { printf "\033[1;34m[build-macos]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || die "This script must be run on macOS (Apple toolchain cannot be cross-compiled)"
command -v hdiutil >/dev/null 2>&1 || die "hdiutil not found (macOS tool)"

ARCH="$(uname -m)"   # arm64 | x86_64

# ---- Python (prefer venv) ----
choose_python() {
    if [[ -n "${VIRTUAL_ENV:-}" ]] && [[ -x "${VIRTUAL_ENV}/bin/python3" ]]; then
        echo "${VIRTUAL_ENV}/bin/python3"; return
    fi
    if [[ -x "${REPO_ROOT}/.venv/bin/python3" ]]; then
        echo "${REPO_ROOT}/.venv/bin/python3"; return
    fi
    command -v python3 >/dev/null 2>&1 || return 1
    command -v python3
}
PY="$(choose_python)" || die "Python 3 not found"
PY_VERSION="$("$PY" --version 2>&1 | awk '{print $2}')"
log "Using Python $PY_VERSION ($PY) on $ARCH"

# ---- Clean ----
log "Cleaning previous builds..."
rm -rf "$DIST_DIR" "$BUILD_DIR"
mkdir -p "$DIST_DIR" "$BUILD_DIR" "$PYI_WORK"

# ---- Tooling + deps ----
log "Installing PyInstaller + GUI package..."
"$PY" -m pip install --upgrade pip setuptools wheel
"$PY" -m pip install --upgrade pyinstaller pyinstaller-hooks-contrib
"$PY" -m pip install -e "$APP_DIR"
if [[ -f "$REPO_ROOT/python/pyproject.toml" ]]; then
    "$PY" -m pip install -e "$REPO_ROOT/python" || warn "could not install animica package; bundle may be incomplete"
fi

# ---- Bundled animica version (hard-fails on drift) ----
BUNDLED_ANIMICA="$("$PY" "$SCRIPT_DIR/bundle_version.py")" || die "bundled animica version check failed"
log "Bundled animica: $BUNDLED_ANIMICA"

# ---- Version ----
VERSION="$("$PY" -c "import tomllib; print(tomllib.load(open(r'$APP_DIR/pyproject.toml','rb'))['project']['version'])" 2>/dev/null || echo "0.1.0")"
log "Building version: $VERSION"

# ---- PyInstaller ----
log "Running PyInstaller (onedir -> .app, windowed)..."
export ANIMICA_GUI_SPEC_DIR="$SCRIPT_DIR"
export ANIMICA_GUI_APP_DIR="$APP_DIR"
export ANIMICA_GUI_REPO_ROOT="$REPO_ROOT"
export ANIMICA_GUI_NAME="$APP_NAME"
export ANIMICA_GUI_VERSION="$VERSION"
cd "$APP_DIR"
"$PY" -m PyInstaller --clean --noconfirm \
    --distpath "$DIST_DIR" \
    --workpath "$PYI_WORK" \
    "$SPEC_FILE"

APP_BUNDLE="${DIST_DIR}/${APP_NAME}.app"
if [[ ! -d "$APP_BUNDLE" ]]; then
    FOUND="$(find "$DIST_DIR" -maxdepth 3 -name "${APP_NAME}.app" -type d -print -quit || true)"
    [[ -n "$FOUND" ]] && APP_BUNDLE="$FOUND" || die "Failed to create .app bundle in $DIST_DIR"
fi
log ".app bundle created: $APP_BUNDLE"

# ---- Artifact-record helper (SHA256 + size + JSON line) ----
emit_artifact() {
    # $1 = file path, $2 = platform tag, $3 = min_os
    local file="$1" platform="$2" min_os="$3"
    local name size sha
    name="$(basename "$file")"
    size="$(stat -f %z "$file" 2>/dev/null || stat -c %s "$file")"
    sha="$(shasum -a 256 "$file" | awk '{print $1}')"
    "$PY" - "$ARTIFACT_LOG" "$platform" "$name" "$VERSION" "$size" "$sha" "$min_os" "$BUNDLED_ANIMICA" <<'PYEOF'
import json, sys
log, platform, name, version, size, sha, min_os, bundled = sys.argv[1:9]
rec = {
    "platform": platform,
    "name": "AnimicaMiner",
    "version": version,
    "filename": name,
    "size_bytes": int(size),
    "sha256": sha,
    "min_os": min_os,
    "bundled_animica": bundled,
}
with open(log, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(rec) + "\n")
print(f"[artifact] {platform} {name} {size} bytes sha256={sha}")
PYEOF
}

# ---- ZIP of the .app (portable fallback) ----
ZIP_NAME="AnimicaMiner-${VERSION}-macos-${ARCH}.zip"
ZIP_PATH="${DIST_DIR}/${ZIP_NAME}"
log "Creating .app zip..."
( cd "$DIST_DIR" && ditto -c -k --sequesterRsrc --keepParent "${APP_NAME}.app" "$ZIP_NAME" )
log "Zip: $ZIP_PATH"

# ---- DMG ----
DMG_NAME="AnimicaMiner-${VERSION}-macos-${ARCH}.dmg"
DMG_PATH="${DIST_DIR}/${DMG_NAME}"
log "Creating DMG installer..."
hdiutil create -volname "Animica Miner" \
    -srcfolder "$APP_BUNDLE" \
    -ov -format UDZO \
    "$DMG_PATH"
log "DMG: $DMG_PATH"

# ---- Record artifacts ----
emit_artifact "$DMG_PATH" "macos" "macOS 10.15+"
emit_artifact "$ZIP_PATH" "macos" "macOS 10.15+"

log "Build complete. Artifacts in: $DIST_DIR"
log "Manifest log: $ARTIFACT_LOG"
log "Test: open \"$APP_BUNDLE\""
