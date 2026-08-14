#!/usr/bin/env bash
# Build the Linux Animica Internet browser binary.
#
# Produces a PyInstaller *onedir* build of "AnimicaInternet", then packages it as:
#   - animica-internet-linux-x86_64.AppImage   (if appimagetool is available)
#   - animica-internet-linux-x86_64.tar.gz     (always; portable fallback)
# plus a detached .sha256 file per artifact.
#
# For every artifact it also computes SHA256 + size and appends a single JSON
# line to the per-artifact manifest log so make_manifest.py can aggregate them.
#
# Requirements:
#   - Linux (x86_64 or aarch64)
#   - Python 3.11+ (tomllib)
#   - FUSE for AppImage (optional; falls back to tarball only)
#
# Usage:
#   ./build_linux.sh
#
# Env overrides:
#   ARTIFACT_LOG            path to the JSON-lines manifest log (default: dist/artifacts.jsonl)
#   ANIMICA_INTERNET_UPX    "1" to enable UPX (default off; unsafe for Qt/Chromium)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"          # apps/animica-internet
REPO_ROOT="$(cd "$APP_DIR/../.." && pwd)"        # repo root
DIST_DIR="${APP_DIR}/dist"
BUILD_DIR="${APP_DIR}/build"
SPEC_FILE="${SCRIPT_DIR}/animica-internet.spec"
ARTIFACT_LOG="${ARTIFACT_LOG:-${DIST_DIR}/artifacts.jsonl}"
APP_NAME="AnimicaInternet"

log()  { printf "\033[1;34m[build-linux]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

[[ "$(uname -s)" == "Linux" ]] || die "This script must be run on Linux"

# ---- Architecture ----
ARCH="$(uname -m)"
case "$ARCH" in
    x86_64|amd64)   ARCH_NAME="x86_64";  APPIMAGE_ARCH="x86_64" ;;
    aarch64|arm64)  ARCH_NAME="aarch64"; APPIMAGE_ARCH="aarch64" ;;
    *)              die "Unsupported architecture: $ARCH" ;;
esac
log "Building for Linux $ARCH_NAME"

# ---- Python ----
command -v python3 >/dev/null 2>&1 || die "Python 3 is required"
PY="python3"
PY_VERSION="$("$PY" --version 2>&1 | awk '{print $2}')"
log "Using Python $PY_VERSION"

# ---- System deps (best effort on Debian/Ubuntu; QtWebEngine needs nss/xcomposite) ----
if command -v apt-get >/dev/null 2>&1; then
    MISSING_DEPS=()
    for pkg in libgl1 libglib2.0-0 libxcb-xinerama0 libxcb-icccm4 libxcb-image0 \
               libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 \
               libxcb-cursor0 libnss3 libxcomposite1 libxdamage1 libxrandr2 \
               libxtst6 libxkbfile1 libasound2t64; do
        if ! dpkg -l 2>/dev/null | grep -q "^ii  $pkg"; then
            MISSING_DEPS+=("$pkg")
        fi
    done
    if [[ ${#MISSING_DEPS[@]} -gt 0 ]]; then
        log "Installing missing dependencies: ${MISSING_DEPS[*]}"
        if [[ $EUID -eq 0 ]]; then
            apt-get update && apt-get install -y "${MISSING_DEPS[@]}" || warn "apt install failed; continuing"
        elif command -v sudo >/dev/null 2>&1; then
            sudo apt-get update && sudo apt-get install -y "${MISSING_DEPS[@]}" || warn "apt install failed; continuing"
        else
            warn "Cannot install system deps (no root/sudo): ${MISSING_DEPS[*]}"
        fi
    fi
fi

# ---- Clean ----
log "Cleaning previous builds..."
rm -rf "$DIST_DIR" "$BUILD_DIR"
mkdir -p "$DIST_DIR" "$BUILD_DIR"

# ---- Tooling + deps ----
log "Installing PyInstaller + app package..."
"$PY" -m pip install --upgrade pip setuptools wheel
"$PY" -m pip install --upgrade pyinstaller pyinstaller-hooks-contrib
"$PY" -m pip install -e "$APP_DIR"
# Bundle the `animica` wallet-backend package when present in the monorepo.
if [[ -f "$REPO_ROOT/python/pyproject.toml" ]]; then
    "$PY" -m pip install -e "$REPO_ROOT/python" || warn "could not install animica package; wallet backend may be incomplete"
fi

# ---- Version ----
VERSION="$("$PY" -c "import tomllib; print(tomllib.load(open(r'$APP_DIR/pyproject.toml','rb'))['project']['version'])" 2>/dev/null || echo "0.1.0")"
log "Building version: $VERSION"

# ---- PyInstaller ----
log "Running PyInstaller (onedir, windowed)..."
export ANIMICA_INTERNET_SPEC_DIR="$SCRIPT_DIR"
export ANIMICA_INTERNET_APP_DIR="$APP_DIR"
export ANIMICA_INTERNET_REPO_ROOT="$REPO_ROOT"
export ANIMICA_INTERNET_NAME="$APP_NAME"
export ANIMICA_INTERNET_VERSION="$VERSION"
cd "$APP_DIR"
"$PY" -m PyInstaller --clean --noconfirm \
    --distpath "$DIST_DIR" \
    --workpath "$BUILD_DIR/pyinstaller-work" \
    "$SPEC_FILE"

ONEDIR="${DIST_DIR}/${APP_NAME}"
EXE_PATH="${ONEDIR}/${APP_NAME}"
[[ -d "$ONEDIR" && -f "$EXE_PATH" ]] || die "Failed to create onedir build at $ONEDIR"
chmod +x "$EXE_PATH"
log "onedir build created at: $ONEDIR"

# ---- Smoke test (best-effort; offscreen QtWebEngine may lack GPU on CI) ----
log "Smoke test (--smoke, offscreen)..."
if QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 \
   timeout 120 "$EXE_PATH" --smoke; then
    log "Smoke test OK"
else
    warn "Smoke test failed or timed out (continuing; may be a headless-runner limitation)"
fi

# ---- Artifact-record helpers (SHA256 sidecar + size + JSON line) ----
emit_artifact() {
    # $1 = file path, $2 = platform tag, $3 = min_os
    local file="$1" platform="$2" min_os="$3"
    local name size sha
    name="$(basename "$file")"
    size="$(stat -c %s "$file" 2>/dev/null || stat -f %z "$file")"
    sha="$(sha256sum "$file" | awk '{print $1}')"
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

# ---- Tarball (always) ----
TAR_NAME="animica-internet-linux-${ARCH_NAME}.tar.gz"
TAR_PATH="${DIST_DIR}/${TAR_NAME}"
log "Creating tarball..."
tar -C "$DIST_DIR" -czf "$TAR_PATH" "$APP_NAME"
log "Tarball: $TAR_PATH"

# ---- AppImage (if appimagetool present, else download attempt; else skip) ----
APPIMAGE_NAME="animica-internet-linux-${ARCH_NAME}.AppImage"
APPIMAGE_PATH="${DIST_DIR}/${APPIMAGE_NAME}"
APPIMAGE_TOOL=""

if command -v appimagetool >/dev/null 2>&1; then
    APPIMAGE_TOOL="appimagetool"
elif [[ -x "${APPIMAGETOOL:-}" ]]; then
    APPIMAGE_TOOL="$APPIMAGETOOL"
else
    # Best-effort download (skip silently if offline).
    TOOL_URL=""
    case "$ARCH_NAME" in
        x86_64)  TOOL_URL="https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage" ;;
        aarch64) TOOL_URL="https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-aarch64.AppImage" ;;
    esac
    if [[ -n "$TOOL_URL" ]] && command -v curl >/dev/null 2>&1; then
        DL="${BUILD_DIR}/appimagetool.AppImage"
        if curl -fsSL "$TOOL_URL" -o "$DL" 2>/dev/null; then
            chmod +x "$DL"
            APPIMAGE_TOOL="$DL"
        else
            warn "Could not download appimagetool; will ship tarball only."
        fi
    fi
fi

if [[ -n "$APPIMAGE_TOOL" ]]; then
    log "Building AppImage with $APPIMAGE_TOOL..."
    APPDIR="${BUILD_DIR}/AnimicaInternet.AppDir"
    rm -rf "$APPDIR"
    mkdir -p "$APPDIR/usr/bin"
    cp -r "$ONEDIR/." "$APPDIR/usr/bin/"

    cat > "$APPDIR/animica-internet.desktop" <<'DESKTOP_EOF'
[Desktop Entry]
Type=Application
Name=Animica Internet
Comment=The .anm-only web — a standalone Animica browser with a built-in wallet
Exec=AnimicaInternet
Icon=animica-internet
Categories=Network;WebBrowser;
MimeType=x-scheme-handler/anm;
Terminal=false
DESKTOP_EOF

    ICON_SRC=""
    for cand in "$APP_DIR/assets/icon.png" "$APP_DIR/assets/logo.png"; do
        [[ -f "$cand" ]] && ICON_SRC="$cand" && break
    done
    if [[ -n "$ICON_SRC" ]]; then
        cp "$ICON_SRC" "$APPDIR/animica-internet.png"
    else
        warn "no icon found in assets/; AppImage will have no icon"
        : > "$APPDIR/animica-internet.png"
    fi

    cat > "$APPDIR/AppRun" <<'APPRUN_EOF'
#!/bin/bash
SELF=$(readlink -f "$0")
HERE=${SELF%/*}
export PATH="${HERE}/usr/bin:${PATH}"
export LD_LIBRARY_PATH="${HERE}/usr/bin:${HERE}/usr/lib:${LD_LIBRARY_PATH:-}"
# Chromium sandbox cannot run from an AppImage mount (helper is not setuid).
export QTWEBENGINE_DISABLE_SANDBOX="${QTWEBENGINE_DISABLE_SANDBOX:-1}"
exec "${HERE}/usr/bin/AnimicaInternet" "$@"
APPRUN_EOF
    chmod +x "$APPDIR/AppRun"

    if ARCH="$APPIMAGE_ARCH" "$APPIMAGE_TOOL" "$APPDIR" "$APPIMAGE_PATH" 2>&1 | grep -v "WARNING" || true; then :; fi
    if [[ -f "$APPIMAGE_PATH" ]]; then
        chmod +x "$APPIMAGE_PATH"
        log "AppImage: $APPIMAGE_PATH"
        emit_artifact "$APPIMAGE_PATH" "linux" "Ubuntu 22.04+ / glibc 2.35+"
    else
        warn "AppImage build failed; tarball remains the Linux artifact."
    fi
else
    log "appimagetool not present; shipping tarball only."
fi

# Always record the tarball too (portable fallback artifact).
emit_artifact "$TAR_PATH" "linux" "Ubuntu 22.04+ / glibc 2.35+"

log "Build complete. Artifacts in: $DIST_DIR"
log "Manifest log: $ARTIFACT_LOG"
