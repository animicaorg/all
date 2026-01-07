#!/usr/bin/env bash
# Build Windows executable for Animica Miner-Wallet (Flutter)
# Creates a standalone executable and installer
#
# Requirements:
#   - Flutter SDK ≥ 3.24
#   - Windows 10 or higher
#   - Visual Studio with Desktop development with C++
#
# Usage (on Windows with Git Bash or WSL):
#   ./build_windows.sh

set -euo pipefail

log()  { printf "\033[1;34m[build-windows]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

# ---- Paths ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
BUILD_DIR="$APP_DIR/build/windows/x64/runner/Release"

# ---- Platform checks ----
OS="$(uname -s)"
case "$OS" in
    MINGW*|MSYS*|CYGWIN*)
        log "Running on Windows"
        ;;
    *)
        die "This script must be run on Windows (via Git Bash, WSL, or similar)"
        ;;
esac

# ---- Check Flutter ----
if ! command -v flutter >/dev/null 2>&1; then
    die "Flutter SDK not found. Install from: https://docs.flutter.dev/get-started/install"
fi

FLUTTER_VERSION=$(flutter --version | head -n 1 | awk '{print $2}')
log "Using Flutter $FLUTTER_VERSION"

# ---- Get version from pubspec.yaml ----
VERSION=$(grep '^version:' "$APP_DIR/pubspec.yaml" | awk '{print $2}' | cut -d'+' -f1)
if [[ -z "$VERSION" ]]; then
    VERSION="0.1.0"
fi
log "Building version: $VERSION"

# ---- Clean previous builds ----
log "Cleaning previous builds..."
cd "$APP_DIR"
flutter clean

# ---- Install dependencies ----
log "Installing dependencies..."
flutter pub get

# ---- Build for Windows ----
log "Building for Windows (release mode)..."
flutter build windows --release

# ---- Check build output ----
EXE_PATH="$BUILD_DIR/animica_miner_wallet.exe"
if [[ ! -f "$EXE_PATH" ]]; then
    # Try to find any .exe file in the release directory
    EXE_PATH=$(find "$BUILD_DIR" -maxdepth 1 -name "*.exe" -type f -print -quit || echo "")
    if [[ -z "$EXE_PATH" || ! -f "$EXE_PATH" ]]; then
        die "Build failed - executable not found in: $BUILD_DIR"
    fi
fi

log "Executable created: $EXE_PATH"

# ---- Create distribution directory ----
DIST_DIR="$APP_DIR/dist"
mkdir -p "$DIST_DIR"

# ---- Create distribution folder with all necessary files ----
DIST_FOLDER="$DIST_DIR/Animica-Miner-Wallet-Windows"
rm -rf "$DIST_FOLDER"
mkdir -p "$DIST_FOLDER"

log "Copying executable and dependencies to distribution folder..."
cp -R "$BUILD_DIR"/* "$DIST_FOLDER"/

# ---- Create ZIP package ----
ZIP_NAME="Animica-Miner-Wallet-${VERSION}-Windows-x64.zip"
ZIP_PATH="$DIST_DIR/$ZIP_NAME"

log "Creating ZIP package..."
cd "$DIST_DIR"
if command -v zip >/dev/null 2>&1; then
    zip -r "$ZIP_NAME" "Animica-Miner-Wallet-Windows" -q
elif command -v 7z >/dev/null 2>&1; then
    7z a "$ZIP_NAME" "Animica-Miner-Wallet-Windows" > /dev/null
else
    warn "No zip utility found, skipping ZIP creation"
    ZIP_PATH=""
fi

log "✅ Build completed successfully!"
log ""
log "Artifacts:"
log "  Build directory: $BUILD_DIR"
log "  Dist folder:     $DIST_FOLDER"
if [[ -n "$ZIP_PATH" && -f "$ZIP_PATH" ]]; then
    log "  ZIP:             $ZIP_PATH"
fi
log ""
log "To test the executable:"
log "  \"$EXE_PATH\""
log ""
log "To distribute, share the ZIP file or the entire dist folder contents"
log ""
log "Note: For production releases, sign the executable with a code signing certificate"
log "      signtool sign /f certificate.pfx /p \$CERT_PASSWORD /tr http://timestamp.digicert.com /td SHA256 /fd SHA256 \"$EXE_PATH\""
log "      (Store password in environment variable or use certificate store for security)"
