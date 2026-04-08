#!/bin/bash
# release-all.sh - Build wallet releases for all platforms
#
# This is a convenience script that runs all platform-specific release scripts.
# Should be run on macOS to build native macOS releases.
# Can cross-compile for Linux (if tools available).
# Cannot build Windows natively from Unix (requires Windows or VM).
#
# Usage:
#   ./scripts/release-all.sh [--sign-mac] [--notarize-mac]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WALLET_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$WALLET_ROOT/.." && pwd)"

# Parse arguments
SIGN_MAC=false
NOTARIZE_MAC=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --sign-mac)
            SIGN_MAC=true
            shift
            ;;
        --notarize-mac)
            NOTARIZE_MAC=true
            SIGN_MAC=true
            shift
            ;;
        --help)
            echo "Usage: $0 [--sign-mac] [--notarize-mac]"
            echo ""
            echo "Builds wallet releases for all supported platforms."
            echo ""
            echo "Options:"
            echo "  --sign-mac       Sign macOS build (requires CODESIGN_IDENTITY)"
            echo "  --notarize-mac   Sign and notarize macOS build (requires Apple credentials)"
            echo ""
            echo "Note: This script can only build for platforms available on the current system."
            echo "      Windows builds require Windows or cross-compilation setup."
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

echo "============================================"
echo "Animica Wallet - Multi-Platform Release"
echo "============================================"
echo ""

# Detect current platform
CURRENT_OS=$(uname -s)
echo "Current platform: $CURRENT_OS"
echo ""

# Determine version
cd "$REPO_ROOT"
if git describe --tags --exact-match 2>/dev/null; then
    VERSION=$(git describe --tags --exact-match)
else
    VERSION=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.1.0")
    COMMIT=$(git rev-parse --short HEAD)
    VERSION="${VERSION}-${COMMIT}"
fi
echo "Version: $VERSION"
echo ""

# Track build status
declare -A BUILD_STATUS

# Build for current platform
case "$CURRENT_OS" in
    Darwin)
        echo "============================================"
        echo "Building macOS Release"
        echo "============================================"
        
        cd "$WALLET_ROOT"
        
        # Build macOS
        MAC_FLAGS=""
        if [ "$SIGN_MAC" = true ]; then
            MAC_FLAGS="$MAC_FLAGS --sign"
        fi
        if [ "$NOTARIZE_MAC" = true ]; then
            MAC_FLAGS="$MAC_FLAGS --notarize"
        fi
        MAC_FLAGS="$MAC_FLAGS --dmg"
        
        if ./scripts/release-mac.sh $MAC_FLAGS; then
            BUILD_STATUS[macOS]="✅ Success"
            
            # Run smoke test
            echo ""
            echo "Running macOS smoke test..."
            APP_PATH=$(find "$REPO_ROOT/dist/wallet-qt/$VERSION/macos" -name "*.app" | head -1)
            if [ -f "$SCRIPT_DIR/smoke-test-mac.sh" ] && [ -d "$APP_PATH" ]; then
                if ./scripts/smoke-test-mac.sh "$APP_PATH"; then
                    BUILD_STATUS[macOS]="✅ Success (smoke test passed)"
                else
                    BUILD_STATUS[macOS]="⚠️  Built but smoke test failed"
                fi
            fi
        else
            BUILD_STATUS[macOS]="❌ Failed"
        fi
        echo ""
        
        # Try to build Linux if tools are available
        echo "============================================"
        echo "Building Linux Release"
        echo "============================================"
        
        if command -v linuxdeployqt &> /dev/null; then
            if ./scripts/release-linux.sh; then
                BUILD_STATUS[Linux]="✅ Success"
            else
                BUILD_STATUS[Linux]="❌ Failed"
            fi
        else
            echo "Skipping Linux build (linuxdeployqt not found)"
            BUILD_STATUS[Linux]="⏭️  Skipped (no linuxdeployqt)"
        fi
        echo ""
        
        # Windows requires Windows platform
        echo "Windows build requires Windows. Skipping."
        BUILD_STATUS[Windows]="⏭️  Requires Windows platform"
        ;;
        
    Linux)
        echo "============================================"
        echo "Building Linux Release"
        echo "============================================"
        
        cd "$WALLET_ROOT"
        if ./scripts/release-linux.sh; then
            BUILD_STATUS[Linux]="✅ Success"
            
            # Run smoke test
            echo ""
            echo "Running Linux smoke test..."
            APPIMAGE_PATH=$(find "$REPO_ROOT/dist/wallet-qt/$VERSION/linux" -name "*.AppImage" | head -1)
            if [ -f "$SCRIPT_DIR/smoke-test-linux.sh" ] && [ -f "$APPIMAGE_PATH" ]; then
                chmod +x "$APPIMAGE_PATH"
                if ./scripts/smoke-test-linux.sh "$APPIMAGE_PATH"; then
                    BUILD_STATUS[Linux]="✅ Success (smoke test passed)"
                else
                    BUILD_STATUS[Linux]="⚠️  Built but smoke test failed"
                fi
            fi
        else
            BUILD_STATUS[Linux]="❌ Failed"
        fi
        echo ""
        
        # macOS requires macOS
        echo "macOS build requires macOS. Skipping."
        BUILD_STATUS[macOS]="⏭️  Requires macOS platform"
        
        # Windows requires Windows
        echo "Windows build requires Windows. Skipping."
        BUILD_STATUS[Windows]="⏭️  Requires Windows platform"
        ;;
        
    MINGW*|MSYS*|CYGWIN*)
        echo "============================================"
        echo "Building Windows Release"
        echo "============================================"
        
        cd "$WALLET_ROOT"
        # Note: This won't work from Git Bash - needs proper PowerShell
        echo "Please run release-windows.ps1 from PowerShell:"
        echo "  cd wallet-qt"
        echo "  .\\scripts\\release-windows.ps1"
        BUILD_STATUS[Windows]="⏭️  Run manually in PowerShell"
        
        BUILD_STATUS[macOS]="⏭️  Requires macOS platform"
        BUILD_STATUS[Linux]="⏭️  Requires Linux platform"
        ;;
        
    *)
        echo "Unsupported platform: $CURRENT_OS"
        exit 1
        ;;
esac

# Summary
echo ""
echo "============================================"
echo "Build Summary"
echo "============================================"
echo ""
echo "Version: $VERSION"
echo ""
echo "Platform Status:"
for platform in macOS Windows Linux; do
    status="${BUILD_STATUS[$platform]:-⏭️  Not built}"
    echo "  $platform: $status"
done
echo ""

# List artifacts
DIST_DIR="$REPO_ROOT/dist/wallet-qt/$VERSION"
if [ -d "$DIST_DIR" ]; then
    echo "Artifacts in $DIST_DIR:"
    echo ""
    
    for platform_dir in "$DIST_DIR"/*; do
        if [ -d "$platform_dir" ]; then
            platform=$(basename "$platform_dir")
            echo "  $platform:"
            find "$platform_dir" -type f \( -name "*.app" -o -name "*.dmg" -o -name "*.msi" -o -name "*.exe" -o -name "*.AppImage" -o -name "*.deb" -o -name "*.tar.gz" -o -name "SHA256SUMS" \) -exec basename {} \; | sed 's/^/    - /'
            echo ""
        fi
    done
    
    echo "To verify checksums:"
    echo "  cd $DIST_DIR/<platform>"
    echo "  sha256sum -c SHA256SUMS"
else
    echo "No artifacts generated."
fi
echo ""

# Final instructions
echo "============================================"
echo "Next Steps"
echo "============================================"
echo ""
echo "1. Test the releases on clean machines/VMs"
echo "2. Verify checksums"
echo "3. Create GitHub release with tag: $VERSION"
echo "4. Upload artifacts to GitHub release"
echo "5. Update download links on website"
echo ""
echo "For detailed instructions, see:"
echo "  wallet-qt/docs/RELEASING.md"
