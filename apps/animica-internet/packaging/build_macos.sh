#!/usr/bin/env bash
# Build the macOS Animica Internet browser.
#
# Produces a PyInstaller onedir build wrapped in "AnimicaInternet.app" (via the
# BUNDLE() step in the shared spec), ad-hoc signs the bundle (QtWebEngine's
# helper processes refuse to launch unsigned on Apple Silicon), then packages:
#   - animica-internet-macos.dmg   (compressed DMG installer)
#   - animica-internet-macos.zip   (portable .app zip fallback)
# plus a detached .sha256 file per artifact.
#
# For every artifact it also computes SHA256 + size and appends a single JSON
# line to the per-artifact manifest log so make_manifest.py can aggregate them.
#
# IMPORTANT: macOS apps can ONLY be built on macOS (the Apple toolchain cannot
# be cross-compiled). This script therefore runs on the macos-latest CI runner
# in the release workflow.
#
# Usage:
#   ./build_macos.sh
#
# Env overrides:
#   ARTIFACT_LOG            path to the JSON-lines manifest log (default: dist/artifacts.jsonl)
#   ANIMICA_INTERNET_UPX    "1" to enable UPX (default off; unsafe for Qt/Chromium)
#   CODESIGN_IDENTITY       signing identity (default "-" = ad-hoc)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"          # apps/animica-internet
REPO_ROOT="$(cd "$APP_DIR/../.." && pwd)"        # repo root
DIST_DIR="${APP_DIR}/dist"
BUILD_DIR="${APP_DIR}/build"
SPEC_FILE="${SCRIPT_DIR}/animica-internet.spec"
PYI_WORK="${BUILD_DIR}/pyinstaller-work"
ARTIFACT_LOG="${ARTIFACT_LOG:-${DIST_DIR}/artifacts.jsonl}"
APP_NAME="AnimicaInternet"
CODESIGN_IDENTITY="${CODESIGN_IDENTITY:--}"

log()  { printf "\033[1;34m[build-macos]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || die "This script must be run on macOS (Apple toolchain cannot be cross-compiled)"
command -v hdiutil >/dev/null 2>&1 || die "hdiutil not found (macOS tool)"
command -v codesign >/dev/null 2>&1 || die "codesign not found (macOS tool)"

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
log "Installing PyInstaller + app package..."
"$PY" -m pip install --upgrade pip setuptools wheel
"$PY" -m pip install --upgrade pyinstaller pyinstaller-hooks-contrib
"$PY" -m pip install -e "$APP_DIR"
if [[ -f "$REPO_ROOT/python/pyproject.toml" ]]; then
    "$PY" -m pip install -e "$REPO_ROOT/python" || warn "could not install animica package; wallet backend may be incomplete"
fi

# ---- Version ----
VERSION="$("$PY" -c "import tomllib; print(tomllib.load(open(r'$APP_DIR/pyproject.toml','rb'))['project']['version'])" 2>/dev/null || echo "0.1.0")"
log "Building version: $VERSION"

# ---- PyInstaller ----
log "Running PyInstaller (onedir -> .app, windowed)..."
export ANIMICA_INTERNET_SPEC_DIR="$SCRIPT_DIR"
export ANIMICA_INTERNET_APP_DIR="$APP_DIR"
export ANIMICA_INTERNET_REPO_ROOT="$REPO_ROOT"
export ANIMICA_INTERNET_NAME="$APP_NAME"
export ANIMICA_INTERNET_VERSION="$VERSION"
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

# ---- Ad-hoc sign (QtWebEngine helpers first, then the outer bundle) ----
log "Codesigning (identity: $CODESIGN_IDENTITY)..."
HELPER_APP="$(find "$APP_BUNDLE" -type d -name "QtWebEngineProcess.app" -print -quit || true)"
if [[ -n "$HELPER_APP" ]]; then
    codesign --force --deep --sign "$CODESIGN_IDENTITY" "$HELPER_APP" \
        || warn "helper codesign failed (continuing)"
fi
codesign --force --deep --sign "$CODESIGN_IDENTITY" "$APP_BUNDLE" \
    || warn "bundle codesign failed (continuing; app may be blocked by Gatekeeper)"
codesign --verify --verbose=1 "$APP_BUNDLE" || warn "codesign verification failed"

# ---- Smoke test (best-effort) ----
log "Smoke test (--smoke, offscreen)..."
if QT_QPA_PLATFORM=offscreen \
   "$APP_BUNDLE/Contents/MacOS/$APP_NAME" --smoke; then
    log "Smoke test OK"
else
    warn "Smoke test failed (continuing; may be a headless-runner limitation)"
fi

# ---- Artifact-record helpers (SHA256 sidecar + size + JSON line) ----
emit_artifact() {
    # $1 = file path, $2 = platform tag, $3 = min_os
    local file="$1" platform="$2" min_os="$3"
    local name size sha
    name="$(basename "$file")"
    size="$(stat -f %z "$file" 2>/dev/null || stat -c %s "$file")"
    sha="$(shasum -a 256 "$file" | awk '{print $1}')"
    printf '%s  %s\n' "$sha" "$name" > "${file}.sha256"
    "$PY" - "$ARTIFACT_LOG" "$platform" "$name" "$VERSION" "$size" "$sha" "$min_os" <<'PYEOF'
import json, sys
log, platform, name, version, size, sha, min_os = sys.argv[1:8]
rec = {
    "platform": platform,
    "name": "AnimicaInternet",
    "version": version,
    "filename": name,
    "size_bytes": int(size),
    "sha256": sha,
    "min_os": min_os,
}
with open(log, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(rec) + "\n")
print(f"[artifact] {platform} {name} {size} bytes sha256={sha}")
PYEOF
}

# ---- ZIP of the .app (portable fallback) ----
ZIP_NAME="animica-internet-macos.zip"
ZIP_PATH="${DIST_DIR}/${ZIP_NAME}"
log "Creating .app zip..."
( cd "$DIST_DIR" && ditto -c -k --sequesterRsrc --keepParent "${APP_NAME}.app" "$ZIP_NAME" )
log "Zip: $ZIP_PATH"

# ---- DMG ----
DMG_NAME="animica-internet-macos.dmg"
DMG_PATH="${DIST_DIR}/${DMG_NAME}"
log "Creating DMG installer..."
hdiutil create -volname "Animica Internet" \
    -srcfolder "$APP_BUNDLE" \
    -ov -format UDZO \
    "$DMG_PATH"
log "DMG: $DMG_PATH"

# ---- Record artifacts ----
emit_artifact "$DMG_PATH" "macos" "macOS 11.0+"
emit_artifact "$ZIP_PATH" "macos" "macOS 11.0+"

log "Build complete. Artifacts in: $DIST_DIR"
log "Manifest log: $ARTIFACT_LOG"
log "Test: open \"$APP_BUNDLE\""
