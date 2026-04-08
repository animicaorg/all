#!/bin/bash
# release-mac.sh - Build and package macOS release for Animica Wallet
#
# Creates:
# - .app bundle with embedded node
# - Optional DMG installer
# - Code signing and notarization stubs (requires certificates)
#
# Usage:
#   ./scripts/release-mac.sh [--dmg] [--sign] [--notarize]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WALLET_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$WALLET_ROOT/.." && pwd)"

# Defaults
BUILD_TYPE="Release"
CREATE_DMG=false
SIGN_APP=false
NOTARIZE_APP=false
OUTPUT_DIR="$REPO_ROOT/dist/wallet-qt"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --dmg)
            CREATE_DMG=true
            shift
            ;;
        --sign)
            SIGN_APP=true
            shift
            ;;
        --notarize)
            NOTARIZE_APP=true
            SIGN_APP=true  # Notarization requires signing
            shift
            ;;
        --debug)
            BUILD_TYPE="Debug"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--dmg] [--sign] [--notarize] [--debug]"
            exit 1
            ;;
    esac
done

echo "======================================"
echo "Animica Wallet - macOS Release Build"
echo "======================================"
echo "Build type: $BUILD_TYPE"
echo "Create DMG: $CREATE_DMG"
echo "Code signing: $SIGN_APP"
echo "Notarization: $NOTARIZE_APP"
echo ""

# Detect architecture
ARCH=$(uname -m)
echo "Building for architecture: $ARCH"

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

# Generate icons if needed
echo "Checking icons..."
cd "$WALLET_ROOT"
if ! python3 scripts/gen-icons.py --check 2>/dev/null; then
    echo "Generating icons..."
    python3 scripts/gen-icons.py
fi
echo ""

# Build directory
BUILD_DIR="$WALLET_ROOT/build/mac-release"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

echo "Configuring build..."
cd "$BUILD_DIR"

# Find Qt
if [ -z "$CMAKE_PREFIX_PATH" ]; then
    if [ -d "/opt/homebrew/opt/qt@6" ]; then
        export CMAKE_PREFIX_PATH="/opt/homebrew/opt/qt@6"
    elif [ -d "/usr/local/opt/qt@6" ]; then
        export CMAKE_PREFIX_PATH="/usr/local/opt/qt@6"
    fi
fi

cmake "$WALLET_ROOT" \
    -DCMAKE_BUILD_TYPE="$BUILD_TYPE" \
    -DCMAKE_OSX_ARCHITECTURES="$ARCH" \
    -DWALLET_REMOTE_RPC_ONLY=OFF \
    -DBUILD_TESTING=OFF

echo ""
echo "Building wallet..."
cmake --build . --config "$BUILD_TYPE" -j$(sysctl -n hw.ncpu)

# Locate built app bundle
APP_BUNDLE="$BUILD_DIR/bin/AnimicaWallet.app"

if [ ! -d "$APP_BUNDLE" ]; then
    echo "Error: App bundle not found at $APP_BUNDLE"
    exit 1
fi

echo ""
echo "App bundle created: $APP_BUNDLE"

# Validation
echo ""
echo "======================================"
echo "Validating Bundle"
echo "======================================"

# Check node binary exists
NODE_PYTHON="$APP_BUNDLE/Contents/Resources/node/venv/bin/python"
if [ ! -f "$NODE_PYTHON" ]; then
    echo "Error: Node Python not found at $NODE_PYTHON"
    exit 1
fi
echo "✓ Node Python found"

# Check node is executable
if [ ! -x "$NODE_PYTHON" ]; then
    echo "Error: Node Python is not executable"
    exit 1
fi
echo "✓ Node Python is executable"

# Check architecture
NODE_ARCH=$(file "$NODE_PYTHON" | grep -o "arm64\|x86_64")
if [ "$NODE_ARCH" != "$ARCH" ]; then
    echo "Warning: Node architecture ($NODE_ARCH) doesn't match build architecture ($ARCH)"
fi
echo "✓ Architecture: $NODE_ARCH"

# Check node imports
echo "Checking node imports..."
if ! "$NODE_PYTHON" -c "import rpc; import animica.qt_wallet_bridge; import omni_sdk; import core" 2>/dev/null; then
    echo "Error: Node imports failed"
    exit 1
fi
echo "✓ Node imports successful"

# Check dynamic libraries
echo "Checking dynamic libraries of wallet binary..."
WALLET_BINARY="$APP_BUNDLE/Contents/MacOS/AnimicaWallet"
otool -L "$WALLET_BINARY" | grep -E "Qt|liboqs|libssl" || true

# Code signing
if [ "$SIGN_APP" = true ]; then
    echo ""
    echo "======================================"
    echo "Code Signing"
    echo "======================================"
    
    if [ -z "$CODESIGN_IDENTITY" ]; then
        echo "Error: CODESIGN_IDENTITY environment variable not set"
        echo "Set it to your Developer ID Application certificate, e.g.:"
        echo "  export CODESIGN_IDENTITY='Developer ID Application: Your Name (TEAMID)'"
        exit 1
    fi
    
    echo "Signing with identity: $CODESIGN_IDENTITY"
    
    # Sign embedded node Python (and all dylibs in venv)
    echo "Signing embedded node binaries..."
    find "$APP_BUNDLE/Contents/Resources/node" -type f \( -name "*.so" -o -name "*.dylib" \) -exec codesign --force --sign "$CODESIGN_IDENTITY" {} \;
    codesign --force --sign "$CODESIGN_IDENTITY" "$NODE_PYTHON"
    
    # Sign the app bundle
    echo "Signing app bundle..."
    codesign --force --sign "$CODESIGN_IDENTITY" \
        --options runtime \
        --entitlements "$WALLET_ROOT/resources/macos/entitlements.plist" \
        --deep "$APP_BUNDLE"
    
    # Verify signature
    echo "Verifying signature..."
    codesign --verify --verbose=2 "$APP_BUNDLE"
    spctl --assess --verbose=2 "$APP_BUNDLE"
    
    echo "✓ Code signing complete"
else
    echo ""
    echo "Skipping code signing (use --sign to enable)"
fi

# Notarization
if [ "$NOTARIZE_APP" = true ]; then
    echo ""
    echo "======================================"
    echo "Notarization"
    echo "======================================"
    
    if [ -z "$APPLE_ID" ] || [ -z "$APPLE_TEAM_ID" ]; then
        echo "Error: APPLE_ID and APPLE_TEAM_ID environment variables required for notarization"
        echo "Set them to your Apple Developer account credentials"
        exit 1
    fi
    
    # Create ZIP for notarization
    NOTARIZE_ZIP="$BUILD_DIR/AnimicaWallet-notarize.zip"
    echo "Creating archive for notarization..."
    ditto -c -k --keepParent "$APP_BUNDLE" "$NOTARIZE_ZIP"
    
    echo "Submitting to Apple for notarization..."
    echo "This may take several minutes..."
    xcrun notarytool submit "$NOTARIZE_ZIP" \
        --apple-id "$APPLE_ID" \
        --team-id "$APPLE_TEAM_ID" \
        --password "@keychain:AC_PASSWORD" \
        --wait
    
    # Check result
    if [ $? -eq 0 ]; then
        echo "Stapling notarization ticket..."
        xcrun stapler staple "$APP_BUNDLE"
        
        echo "Verifying Gatekeeper..."
        spctl --assess --type execute --verbose=2 "$APP_BUNDLE"
        
        echo "✓ Notarization complete"
    else
        echo "Error: Notarization failed"
        exit 1
    fi
else
    echo ""
    echo "Skipping notarization (use --notarize to enable)"
fi

# Create distribution
echo ""
echo "======================================"
echo "Creating Distribution"
echo "======================================"

DIST_DIR="$OUTPUT_DIR/$VERSION/macos"
mkdir -p "$DIST_DIR"

# Copy app bundle to dist
RELEASE_APP="$DIST_DIR/AnimicaWallet-${VERSION}-macos-${ARCH}.app"
echo "Copying app bundle to $RELEASE_APP"
rm -rf "$RELEASE_APP"
cp -R "$APP_BUNDLE" "$RELEASE_APP"

# Create DMG if requested
if [ "$CREATE_DMG" = true ]; then
    echo ""
    echo "Creating DMG..."
    
    DMG_NAME="AnimicaWallet-${VERSION}-macos-${ARCH}.dmg"
    DMG_PATH="$DIST_DIR/$DMG_NAME"
    
    # Check for create-dmg
    if command -v create-dmg &> /dev/null; then
        create-dmg \
            --volname "Animica Wallet" \
            --volicon "$WALLET_ROOT/resources/icons/animica.icns" \
            --window-pos 200 120 \
            --window-size 600 400 \
            --icon-size 100 \
            --icon "AnimicaWallet.app" 175 120 \
            --hide-extension "AnimicaWallet.app" \
            --app-drop-link 425 120 \
            "$DMG_PATH" \
            "$RELEASE_APP"
    else
        echo "Warning: create-dmg not found, creating simple DMG"
        echo "Install with: brew install create-dmg"
        
        # Simple DMG creation
        hdiutil create -volname "Animica Wallet" \
            -srcfolder "$RELEASE_APP" \
            -ov -format UDZO \
            "$DMG_PATH"
    fi
    
    echo "✓ DMG created: $DMG_PATH"
fi

# Generate checksums
echo ""
echo "Generating checksums..."
cd "$DIST_DIR"
find . -type f \( -name "*.app" -o -name "*.dmg" \) -exec shasum -a 256 {} \; > SHA256SUMS
cat SHA256SUMS

echo ""
echo "======================================"
echo "Release Build Complete!"
echo "======================================"
echo "Version: $VERSION"
echo "Architecture: $ARCH"
echo "Output directory: $DIST_DIR"
echo ""
echo "Artifacts:"
ls -lh "$DIST_DIR"
echo ""
echo "To test the app:"
echo "  open $RELEASE_APP"
echo ""
echo "For code signing:"
echo "  export CODESIGN_IDENTITY='Developer ID Application: ...'"
echo "  $0 --sign"
echo ""
echo "For notarization:"
echo "  export APPLE_ID='your@email.com'"
echo "  export APPLE_TEAM_ID='TEAMID'"
echo "  security add-generic-password -a \"\$APPLE_ID\" -w 'app-specific-password' -s 'AC_PASSWORD'"
echo "  $0 --sign --notarize"
