#!/bin/bash
# verify_install.sh
# Verifies that CMake configure, build, and install work correctly
# for the animica-wallet target on both macOS and Linux.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

# Determine platform
if [[ "$OSTYPE" == "darwin"* ]]; then
    PLATFORM="macOS"
    IS_MACOS=true
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    PLATFORM="Linux"
    IS_MACOS=false
else
    log_warn "Unknown platform: $OSTYPE - assuming Linux behavior"
    PLATFORM="Unknown"
    IS_MACOS=false
fi

log_info "Platform detected: $PLATFORM"

# Create temporary build and install directories
BUILD_DIR=$(mktemp -d "${TMPDIR:-/tmp}/animica-wallet-build.XXXXXX")
INSTALL_PREFIX=$(mktemp -d "${TMPDIR:-/tmp}/animica-wallet-install.XXXXXX")

log_info "Build directory: $BUILD_DIR"
log_info "Install prefix: $INSTALL_PREFIX"

# Cleanup function
cleanup() {
    log_info "Cleaning up temporary directories"
    rm -rf "$BUILD_DIR"
    rm -rf "$INSTALL_PREFIX"
}

trap cleanup EXIT

# Step 1: Configure
log_info "Step 1: Configuring CMake..."
cd "$BUILD_DIR"
if ! cmake "$PROJECT_ROOT" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$INSTALL_PREFIX" -DWALLET_REMOTE_RPC_ONLY=OFF -DBUILD_TESTING=OFF; then
    log_error "CMake configuration failed"
    exit 1
fi
log_info "✓ Configuration successful"

# Step 2: Build
log_info "Step 2: Building animica-wallet..."
if ! cmake --build . --target animica-wallet --config Release -j$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 2); then
    log_error "Build failed"
    exit 1
fi
log_info "✓ Build successful"

# Step 3: Install
log_info "Step 3: Installing to $INSTALL_PREFIX..."
if ! cmake --install . --prefix "$INSTALL_PREFIX"; then
    log_error "Install failed"
    exit 1
fi
log_info "✓ Install successful"

# Step 4: Verify installation
log_info "Step 4: Verifying installation..."

if $IS_MACOS; then
    # On macOS, expect AnimicaWallet.app bundle
    BUNDLE_PATH="$INSTALL_PREFIX/AnimicaWallet.app"
    if [ -d "$BUNDLE_PATH" ]; then
        log_info "✓ Found macOS bundle at: $BUNDLE_PATH"
        
        # Check for executable inside bundle
        EXECUTABLE="$BUNDLE_PATH/Contents/MacOS/AnimicaWallet"
        if [ -x "$EXECUTABLE" ]; then
            log_info "✓ Found executable at: $EXECUTABLE"
        else
            log_error "Executable not found or not executable at: $EXECUTABLE"
            exit 1
        fi
        
        # Check for bundled node
        NODE_DIR="$BUNDLE_PATH/Contents/Resources/node"
        if [ -d "$NODE_DIR" ]; then
            log_info "✓ Found bundled node at: $NODE_DIR"
        else
            log_warn "Bundled node not found at: $NODE_DIR (may require full build)"
        fi
    else
        log_error "macOS bundle not found at: $BUNDLE_PATH"
        log_info "Checking install directory contents:"
        find "$INSTALL_PREFIX" -maxdepth 3 | head -20
        exit 1
    fi
else
    # On Linux/Windows, expect regular executable in bin/
    EXECUTABLE="$INSTALL_PREFIX/bin/animica-wallet"
    if [ -f "$EXECUTABLE" ]; then
        log_info "✓ Found executable at: $EXECUTABLE"
        
        if [ -x "$EXECUTABLE" ]; then
            log_info "✓ Executable has execute permission"
        else
            log_error "Executable lacks execute permission"
            exit 1
        fi
    else
        log_error "Executable not found at: $EXECUTABLE"
        log_info "Checking install directory contents:"
        find "$INSTALL_PREFIX" -maxdepth 3 | head -20
        exit 1
    fi
    
    NODE_DIR="$INSTALL_PREFIX/lib/animica-wallet/node"
    if [ -d "$NODE_DIR" ]; then
        log_info "✓ Found installed bundled node at: $NODE_DIR"
    else
        log_warn "Installed bundled node not found at: $NODE_DIR"
    fi
fi

log_info ""
log_info "=========================================="
log_info "✓ All verification checks passed!"
log_info "=========================================="
log_info ""
log_info "Summary:"
log_info "  Platform: $PLATFORM"
log_info "  Build: SUCCESS"
log_info "  Install: SUCCESS"
log_info "  Verification: PASSED"
log_info ""

exit 0
