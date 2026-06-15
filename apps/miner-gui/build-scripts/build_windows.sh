#!/usr/bin/env bash
# Build the Windows Animica Miner GUI binary (AnimicaMiner.exe).
#
# Produces a PyInstaller onedir build of "AnimicaMiner" and packages it as a
# .zip. For every artifact it computes SHA256 + size and appends a single JSON
# line to the per-artifact manifest log so make_manifest.py can aggregate them.
#
# This script is designed to run:
#   - natively on Windows (GitHub `windows-latest` runner, Git Bash), and
#   - under Wine via build_windows_wine.sh (it auto-detects `wine` Python).
#
# Usage:
#   On Windows (Git Bash):     ./build_windows.sh
#   Under Wine (Linux):        WINE=wine PYTHON="wine python" ./build_windows.sh
#                              (build_windows_wine.sh wires this up for you)
#
# Env overrides:
#   ARTIFACT_LOG   path to the JSON-lines manifest log (default: dist/artifacts.jsonl)
#   PYTHON         python invocation (default auto: "python"/"python3", or "wine python")
#   WINE           set to "wine" when running through Wine (affects PyInstaller call)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"          # apps/miner-gui
REPO_ROOT="$(cd "$APP_DIR/../.." && pwd)"        # repo root
DIST_DIR="${APP_DIR}/dist"
BUILD_DIR="${APP_DIR}/build"
SPEC_FILE="${SCRIPT_DIR}/animica-miner-gui.spec"
ARTIFACT_LOG="${ARTIFACT_LOG:-${DIST_DIR}/artifacts.jsonl}"
APP_NAME="AnimicaMiner"

log()  { printf "\033[1;34m[build-windows]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

# ---- Detect environment ----
WINE="${WINE:-}"
ON_WINDOWS=false
case "$(uname -s 2>/dev/null || echo unknown)" in
    MINGW*|MSYS*|CYGWIN*) ON_WINDOWS=true ;;
esac

# ---- Resolve python + pyinstaller invocations ----
if [[ -n "$WINE" ]]; then
    PY="${PYTHON:-wine python}"
    log "Wine cross-build mode: PYTHON='$PY'"
elif [[ "$ON_WINDOWS" == "true" ]]; then
    if command -v python >/dev/null 2>&1; then PY="${PYTHON:-python}";
    elif command -v python3 >/dev/null 2>&1; then PY="${PYTHON:-python3}";
    else die "Python not found. Install Python 3.10+ for Windows."; fi
else
    die "Not on Windows and WINE not set. Run on the windows-latest runner, or use build_windows_wine.sh for a Linux cross-build."
fi

PY_VERSION="$($PY --version 2>&1 | awk '{print $2}')"
log "Using Python $PY_VERSION"

# ---- Clean ----
log "Cleaning previous builds..."
rm -rf "$DIST_DIR" "$BUILD_DIR"
mkdir -p "$DIST_DIR" "$BUILD_DIR"

# ---- Tooling + deps ----
log "Installing PyInstaller + GUI package..."
$PY -m pip install --upgrade pip setuptools wheel
$PY -m pip install --upgrade pyinstaller pyinstaller-hooks-contrib
$PY -m pip install -e "$APP_DIR"
if [[ -f "$REPO_ROOT/python/pyproject.toml" ]]; then
    $PY -m pip install -e "$REPO_ROOT/python" || warn "could not install animica package; bundle may be incomplete"
fi

# ---- Version ----
VERSION="$($PY -c "import tomllib; print(tomllib.load(open(r'$APP_DIR/pyproject.toml','rb'))['project']['version'])" 2>/dev/null || echo "0.1.0")"
log "Building version: $VERSION"

# ---- PyInstaller ----
log "Running PyInstaller (onedir, windowed)..."
export ANIMICA_GUI_SPEC_DIR="$SCRIPT_DIR"
export ANIMICA_GUI_APP_DIR="$APP_DIR"
export ANIMICA_GUI_REPO_ROOT="$REPO_ROOT"
export ANIMICA_GUI_NAME="$APP_NAME"
export ANIMICA_GUI_VERSION="$VERSION"
cd "$APP_DIR"
$PY -m PyInstaller --clean --noconfirm \
    --distpath "$DIST_DIR" \
    --workpath "$BUILD_DIR/pyinstaller-work" \
    "$SPEC_FILE"

ONEDIR="${DIST_DIR}/${APP_NAME}"
EXE_PATH="${ONEDIR}/${APP_NAME}.exe"
[[ -d "$ONEDIR" ]] || die "Failed to create onedir build at $ONEDIR"
[[ -f "$EXE_PATH" ]] || warn "Expected ${APP_NAME}.exe not found (continuing; check dist)"
log "onedir build created at: $ONEDIR"

# ---- ZIP package ----
ZIP_NAME="AnimicaMiner-${VERSION}-windows-x64.zip"
ZIP_PATH="${DIST_DIR}/${ZIP_NAME}"
log "Creating ZIP package..."
if command -v 7z >/dev/null 2>&1; then
    ( cd "$DIST_DIR" && 7z a -y "$ZIP_NAME" "$APP_NAME" >/dev/null )
elif command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command \
        "Compress-Archive -Path '$ONEDIR' -DestinationPath '$ZIP_PATH' -Force"
elif command -v zip >/dev/null 2>&1; then
    ( cd "$DIST_DIR" && zip -q -r "$ZIP_NAME" "$APP_NAME" )
else
    # Fallback: Python's shutil works everywhere (incl. Wine).
    $PY -c "import shutil; shutil.make_archive(r'${DIST_DIR}/AnimicaMiner-${VERSION}-windows-x64','zip',r'$DIST_DIR',r'$APP_NAME')"
fi
[[ -f "$ZIP_PATH" ]] || die "ZIP not created at $ZIP_PATH"
log "Zip: $ZIP_PATH"

# ---- Artifact-record helper (SHA256 + size + JSON line) ----
emit_artifact() {
    # $1 = file path, $2 = platform tag, $3 = min_os
    local file="$1" platform="$2" min_os="$3"
    local name size sha
    name="$(basename "$file")"
    size="$(stat -c %s "$file" 2>/dev/null || stat -f %z "$file")"
    if command -v sha256sum >/dev/null 2>&1; then
        sha="$(sha256sum "$file" | awk '{print $1}')"
    elif command -v shasum >/dev/null 2>&1; then
        sha="$(shasum -a 256 "$file" | awk '{print $1}')"
    else
        sha="$($PY -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$file")"
    fi
    $PY - "$ARTIFACT_LOG" "$platform" "$name" "$VERSION" "$size" "$sha" "$min_os" <<'PYEOF'
import json, sys
log, platform, name, version, size, sha, min_os = sys.argv[1:8]
rec = {
    "platform": platform,
    "name": "AnimicaMiner",
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

emit_artifact "$ZIP_PATH" "windows" "Windows 10+"

log "Build complete. Artifacts in: $DIST_DIR"
log "Manifest log: $ARTIFACT_LOG"
