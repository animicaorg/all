#!/usr/bin/env bash
# Build macOS executable for Animica Miner-Wallet (Flutter)
# Creates a standalone .app bundle and DMG installer
#
# Requirements:
#   - Flutter SDK ≥ 3.24
#   - macOS 10.15 or higher
#   - Xcode and CocoaPods
#
# Usage:
#   ./build_macos.sh

set -euo pipefail

log()  { printf "\033[1;34m[build-macos]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

# ---- Paths ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
BUILD_DIR="$APP_DIR/build/macos/Build/Products/Release"

# ---- Platform checks ----
if [[ "$(uname -s)" != "Darwin" ]]; then
    die "This script must be run on macOS"
fi

command -v hdiutil >/dev/null 2>&1 || die "hdiutil not found (macOS tool)"

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

# ---- Install pods if needed ----
if [[ -d "$APP_DIR/macos" ]]; then
    log "Installing CocoaPods dependencies..."
    cd "$APP_DIR/macos"
    pod install
    cd "$APP_DIR"
fi

# ---- Build for macOS ----
log "Building for macOS (release mode)..."
flutter build macos --release

# ---- Check build output ----
APP_BUNDLE="$BUILD_DIR/animica_miner_wallet.app"
if [[ ! -d "$APP_BUNDLE" ]]; then
    # Try alternative name
    APP_BUNDLE=$(find "$BUILD_DIR" -maxdepth 1 -name "*.app" -type d -print -quit || echo "")
    if [[ -z "$APP_BUNDLE" || ! -d "$APP_BUNDLE" ]]; then
        die "Build failed - .app bundle not found in: $BUILD_DIR"
    fi
fi

log "App bundle created: $APP_BUNDLE"

# ---- Create distribution directory ----
DIST_DIR="$APP_DIR/dist"
mkdir -p "$DIST_DIR"

# ---- Copy app bundle to dist ----
DIST_APP_BUNDLE="$DIST_DIR/Animica Miner Wallet.app"
log "Copying app bundle to dist directory..."
rm -rf "$DIST_APP_BUNDLE"
cp -R "$APP_BUNDLE" "$DIST_APP_BUNDLE"

# ---- Create DMG ----
DMG_NAME="Animica-Miner-Wallet-${VERSION}-macOS-$(uname -m).dmg"
DMG_PATH="$DIST_DIR/$DMG_NAME"

log "Creating DMG installer..."
rm -f "$DMG_PATH"
hdiutil create -volname "Animica Miner Wallet" \
    -srcfolder "$DIST_APP_BUNDLE" \
    -ov -format UDZO \
    "$DMG_PATH"

log "✅ Build completed successfully!"
log ""
log "Artifacts:"
log "  App Bundle: $DIST_APP_BUNDLE"
log "  DMG:        $DMG_PATH"
log ""
log "To test the app:"
log "  open \"$DIST_APP_BUNDLE\""
log ""
log "To install, drag the app from the DMG to your Applications folder"
log ""
log "Note: For production releases, sign the app with:"
log "  codesign --deep --force --verify --verbose --sign \"Developer ID\" \"$DIST_APP_BUNDLE\""
