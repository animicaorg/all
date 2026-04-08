#!/bin/bash
#
# build-mac.sh - Cross-platform deterministic build script for Animica Wallet (macOS)
#
# This script builds the Animica Qt wallet and bundles the node for macOS.
# It performs strict prerequisite checking and provides actionable error messages.
#
# Usage:
#   ./build-mac.sh [OPTIONS]
#
# Options:
#   --debug         Build in Debug mode (default: Release)
#   --clean         Clean build directory before building
#   --qt <path>     Override Qt installation path
#   --jobs <n>      Number of parallel build jobs (default: auto-detect)
#   --help          Show this help message
#

set -euo pipefail

# Script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$PROJECT_ROOT/.." && pwd)"

# Default options
BUILD_TYPE="Release"
CLEAN_BUILD=false
QT_PATH=""
JOBS=""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Helper functions
log() {
    echo -e "${GREEN}[BUILD]${NC} $*"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $*" >&2
}

error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

die() {
    error "$*"
    exit 1
}

show_help() {
    sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
    exit 0
}

# Parse command line arguments
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
            show_help
            ;;
        *)
            die "Unknown option: $1. Use --help for usage information."
            ;;
    esac
done

log "=========================================="
log "Animica Wallet Build Script (macOS)"
log "=========================================="
log "Build type: $BUILD_TYPE"
log "Project root: $PROJECT_ROOT"
log ""

# ==================== Prerequisite Checks ====================

log "Checking prerequisites..."

# Check CMake
if ! command -v cmake &> /dev/null; then
    die "CMake not found. Install with: brew install cmake"
fi

CMAKE_VERSION=$(cmake --version | head -n1 | awk '{print $3}')
log "✓ CMake $CMAKE_VERSION found"

if ! [[ "$CMAKE_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    die "Could not parse CMake version"
fi

CMAKE_MAJOR=$(echo "$CMAKE_VERSION" | cut -d. -f1)
CMAKE_MINOR=$(echo "$CMAKE_VERSION" | cut -d. -f2)
if [[ $CMAKE_MAJOR -lt 3 ]] || [[ $CMAKE_MAJOR -eq 3 && $CMAKE_MINOR -lt 16 ]]; then
    die "CMake 3.16+ required (found $CMAKE_VERSION). Install with: brew install cmake"
fi

# Check Xcode Command Line Tools
if ! xcode-select -p &> /dev/null; then
    die "Xcode Command Line Tools not found. Install with: xcode-select --install"
fi

log "✓ Xcode Command Line Tools installed"

# Check Qt
log "Checking Qt installation..."

if [[ -n "$QT_PATH" ]]; then
    # User provided Qt path
    if [[ ! -d "$QT_PATH" ]]; then
        die "Qt path does not exist: $QT_PATH"
    fi
    export CMAKE_PREFIX_PATH="$QT_PATH"
    log "✓ Using Qt from: $QT_PATH"
else
    # Try to find Qt automatically
    QT_FOUND=false
    
    # Common Homebrew locations
    for qt_path in /opt/homebrew/opt/qt@6 /usr/local/opt/qt@6 /opt/homebrew/opt/qt /usr/local/opt/qt; do
        if [[ -d "$qt_path" ]]; then
            export CMAKE_PREFIX_PATH="$qt_path"
            QT_VERSION=$("$qt_path/bin/qmake" -query QT_VERSION 2>/dev/null || echo "")
            if [[ -n "$QT_VERSION" ]]; then
                QT_FOUND=true
                log "✓ Qt $QT_VERSION found at: $qt_path"
                break
            fi
        fi
    done
    
    # Try qmake in PATH
    if [[ "$QT_FOUND" == "false" ]] && command -v qmake6 &> /dev/null; then
        QT_DIR=$(qmake6 -query QT_INSTALL_PREFIX)
        export CMAKE_PREFIX_PATH="$QT_DIR"
        QT_VERSION=$(qmake6 -query QT_VERSION)
        QT_FOUND=true
        log "✓ Qt $QT_VERSION found at: $QT_DIR"
    elif [[ "$QT_FOUND" == "false" ]] && command -v qmake &> /dev/null; then
        QT_DIR=$(qmake -query QT_INSTALL_PREFIX)
        QT_VERSION=$(qmake -query QT_VERSION)
        if [[ "$QT_VERSION" =~ ^6\. ]] || [[ "$QT_VERSION" =~ ^5\.15 ]]; then
            export CMAKE_PREFIX_PATH="$QT_DIR"
            QT_FOUND=true
            log "✓ Qt $QT_VERSION found at: $QT_DIR"
        fi
    fi
    
    if [[ "$QT_FOUND" == "false" ]]; then
        error "Qt 6 (or Qt 5.15+) not found."
        error ""
        error "Install Qt with:"
        error "  Homebrew:  brew install qt@6"
        error "  Or download from: https://www.qt.io/download-open-source"
        error ""
        error "If Qt is installed in a custom location, use: --qt /path/to/qt"
        die "Qt not found"
    fi
fi

# Check Python
log "Checking Python installation..."

PYTHON=""
for py in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$py" &> /dev/null; then
        PY_VERSION=$($py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "")
        if [[ -n "$PY_VERSION" ]]; then
            PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
            PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
            if [[ $PY_MAJOR -eq 3 && $PY_MINOR -ge 10 ]]; then
                PYTHON="$py"
                log "✓ Python $PY_VERSION found"
                break
            fi
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    error "Python 3.10+ not found."
    error ""
    error "Install Python with:"
    error "  Homebrew: brew install python@3.11"
    die "Python not found"
fi

# Determine number of jobs
if [[ -z "$JOBS" ]]; then
    JOBS=$(sysctl -n hw.ncpu 2>/dev/null || echo 4)
    log "Auto-detected $JOBS CPU cores for parallel build"
else
    log "Using $JOBS parallel jobs"
fi

log "✓ All prerequisites satisfied"
log ""

# ==================== Build ====================

BUILD_DIR="$PROJECT_ROOT/build/mac"

if [[ "$CLEAN_BUILD" == "true" ]]; then
    log "Cleaning build directory..."
    rm -rf "$BUILD_DIR"
fi

log "Creating build directory..."
mkdir -p "$BUILD_DIR"

log "Configuring CMake..."
cd "$BUILD_DIR"

cmake "$PROJECT_ROOT" \
    -DCMAKE_BUILD_TYPE="$BUILD_TYPE" \
    -DWALLET_REMOTE_RPC_ONLY=OFF \
    -G "Unix Makefiles" \
    || die "CMake configuration failed"

log "Building wallet and node..."
cmake --build . -j "$JOBS" \
    || die "Build failed"

log ""
log "=========================================="
log "Build completed successfully!"
log "=========================================="
log ""
log "Artifacts:"
log "  App bundle:   $BUILD_DIR/bin/AnimicaWallet.app"
log "  Bundled node: $BUILD_DIR/bin/AnimicaWallet.app/Contents/Resources/node/"
log ""
log "To run the wallet:"
log "  open $BUILD_DIR/bin/AnimicaWallet.app"
log "  # or"
log "  $BUILD_DIR/bin/AnimicaWallet.app/Contents/MacOS/AnimicaWallet"
log ""
log "To create a DMG (requires create-dmg):"
log "  brew install create-dmg"
log "  create-dmg \\"
log "    --volname 'Animica Wallet' \\"
log "    --window-size 600 400 \\"
log "    --icon-size 100 \\"
log "    --app-drop-link 450 150 \\"
log "    $PROJECT_ROOT/dist/AnimicaWallet-$BUILD_TYPE.dmg \\"
log "    $BUILD_DIR/bin/AnimicaWallet.app"
log ""
