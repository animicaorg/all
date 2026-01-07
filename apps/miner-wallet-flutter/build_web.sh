#!/usr/bin/env bash
# Build Web executable for Animica Miner-Wallet (Flutter)
# Creates a production-ready web build
#
# Requirements:
#   - Flutter SDK ≥ 3.24
#
# Usage:
#   ./build_web.sh

set -euo pipefail

log()  { printf "\033[1;34m[build-web]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

# ---- Paths ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
BUILD_DIR="$APP_DIR/build/web"

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
rm -rf "$BUILD_DIR"

# ---- Install dependencies ----
log "Installing dependencies..."
flutter pub get

# ---- Build for web ----
log "Building for Web (release mode)..."
flutter build web --release

# ---- Check build output ----
if [[ ! -d "$BUILD_DIR" ]]; then
    die "Build failed - output directory not found: $BUILD_DIR"
fi

if [[ ! -f "$BUILD_DIR/index.html" ]]; then
    die "Build failed - index.html not found in: $BUILD_DIR"
fi

# ---- Create distribution archive ----
DIST_DIR="$APP_DIR/dist"
mkdir -p "$DIST_DIR"

ARCHIVE_NAME="Animica-Miner-Wallet-${VERSION}-Web.tar.gz"
ARCHIVE_PATH="$DIST_DIR/$ARCHIVE_NAME"

log "Creating distribution archive..."
cd "$APP_DIR/build"
tar -czf "$ARCHIVE_PATH" web/

# Also create a zip for convenience
if command -v zip >/dev/null 2>&1; then
    ZIP_NAME="Animica-Miner-Wallet-${VERSION}-Web.zip"
    ZIP_PATH="$DIST_DIR/$ZIP_NAME"
    log "Creating ZIP archive..."
    cd "$BUILD_DIR"
    zip -r "$ZIP_PATH" . -q
fi

log "✅ Build completed successfully!"
log ""
log "Artifacts:"
log "  Build directory: $BUILD_DIR"
log "  Archive:         $ARCHIVE_PATH"
if [[ -n "${ZIP_PATH:-}" && -f "$ZIP_PATH" ]]; then
    log "  ZIP:             $ZIP_PATH"
fi
log ""
log "To test locally, run:"
log "  cd $BUILD_DIR && python3 -m http.server 8000"
log "  Then open http://localhost:8000 in your browser"
log ""
log "To deploy, upload the contents of $BUILD_DIR to your web server"
