#!/bin/bash
#
# build-mac.sh - Native macOS bundle build for Animica Wallet
#
# Produces an installed, self-contained .app bundle in build/mac/stage.
#
# Usage:
#   ./scripts/build-mac.sh [--debug] [--clean] [--qt <path>] [--jobs <n>]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$PROJECT_ROOT/.." && pwd)"

BUILD_TYPE="Release"
CLEAN_BUILD=false
QT_PATH=""
JOBS=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[BUILD]${NC} $*"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $*" >&2
}

die() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --debug)
            BUILD_TYPE="Debug"
            shift
            ;;
        --clean)
            CLEAN_BUILD=true
            shift
            ;;
        --qt)
            QT_PATH="$2"
            shift 2
            ;;
        --jobs)
            JOBS="$2"
            shift 2
            ;;
        --help)
            sed -n '2,8p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *)
            die "Unknown option: $1"
            ;;
    esac
done

command -v cmake >/dev/null || die "CMake not found. Install with: brew install cmake"
command -v python3 >/dev/null || die "Python 3 not found."
xcode-select -p >/dev/null 2>&1 || die "Xcode Command Line Tools are required."

if [[ -n "$QT_PATH" ]]; then
    export CMAKE_PREFIX_PATH="$QT_PATH"
else
    for candidate in /opt/homebrew/opt/qt@6 /usr/local/opt/qt@6 /opt/homebrew/opt/qt /usr/local/opt/qt; do
        if [[ -d "$candidate" ]]; then
            export CMAKE_PREFIX_PATH="$candidate"
            break
        fi
    done
fi

BUILD_DIR="$PROJECT_ROOT/build/mac"
INSTALL_DIR="$BUILD_DIR/stage"
APP_BUNDLE="$INSTALL_DIR/AnimicaWallet.app"

if [[ "$CLEAN_BUILD" == "true" ]]; then
    rm -rf "$BUILD_DIR"
fi

mkdir -p "$BUILD_DIR"

if [[ -z "$JOBS" ]]; then
    JOBS="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"
fi

log "Configuring macOS build in $BUILD_DIR"
cmake -S "$PROJECT_ROOT" -B "$BUILD_DIR" \
    -DCMAKE_BUILD_TYPE="$BUILD_TYPE" \
    -DBUILD_TESTING=OFF

log "Building wallet bundle"
cmake --build "$BUILD_DIR" --config "$BUILD_TYPE" -j "$JOBS"

log "Installing staged app bundle"
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cmake --install "$BUILD_DIR" --config "$BUILD_TYPE" --prefix "$INSTALL_DIR"

log "Verifying staged macOS bundle layout"
python3 "$SCRIPT_DIR/verify-bundle-layout.py" --platform macos --path "$APP_BUNDLE"

log ""
log "Build completed successfully"
log "  Staged app:  $APP_BUNDLE"
log "  Smoke test:  $SCRIPT_DIR/smoke-test-mac.sh \"$APP_BUNDLE\""
log "  Launch:      open \"$APP_BUNDLE\""
