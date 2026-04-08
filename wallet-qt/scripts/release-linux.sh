#!/bin/bash
# release-linux.sh - Build and package Linux release for Animica Wallet
#
# Creates:
# - AppImage (portable, universal)
# - .deb package (Ubuntu/Debian)
#
# Usage:
#   ./scripts/release-linux.sh [--appimage-only] [--deb-only] [--debug]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WALLET_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$WALLET_ROOT/.." && pwd)"

# Defaults
BUILD_TYPE="Release"
BUILD_APPIMAGE=true
BUILD_DEB=true
OUTPUT_DIR="$REPO_ROOT/dist/wallet-qt"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --appimage-only)
            BUILD_DEB=false
            shift
            ;;
        --deb-only)
            BUILD_APPIMAGE=false
            shift
            ;;
        --debug)
            BUILD_TYPE="Debug"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--appimage-only] [--deb-only] [--debug]"
            exit 1
            ;;
    esac
done

echo "======================================"
echo "Animica Wallet - Linux Release Build"
echo "======================================"
echo "Build type: $BUILD_TYPE"
echo "Build AppImage: $BUILD_APPIMAGE"
echo "Build DEB: $BUILD_DEB"
echo ""

# Detect architecture
ARCH=$(uname -m)
echo "Building for architecture: $ARCH"

# Map arch to Debian naming
case "$ARCH" in
    x86_64)
        DEB_ARCH="amd64"
        ;;
    aarch64)
        DEB_ARCH="arm64"
        ;;
    *)
        DEB_ARCH="$ARCH"
        ;;
esac

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

# Check prerequisites
echo "Checking prerequisites..."
if [ "$BUILD_APPIMAGE" = true ]; then
    if ! command -v linuxdeployqt &> /dev/null; then
        echo "Warning: linuxdeployqt not found"
        echo "Download from: https://github.com/probonopd/linuxdeployqt/releases"
        echo "Or: wget https://github.com/probonopd/linuxdeployqt/releases/download/continuous/linuxdeployqt-continuous-x86_64.AppImage"
        echo "       chmod +x linuxdeployqt-*.AppImage"
        echo "       sudo mv linuxdeployqt-*.AppImage /usr/local/bin/linuxdeployqt"
        BUILD_APPIMAGE=false
    fi
fi

# Generate icons if needed
echo "Checking icons..."
cd "$WALLET_ROOT"
if ! python3 scripts/gen-icons.py --check 2>/dev/null; then
    echo "Generating icons..."
    python3 scripts/gen-icons.py
fi
echo ""

# Build directory
BUILD_DIR="$WALLET_ROOT/build/linux-release"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

echo "Configuring build..."
cd "$BUILD_DIR"

cmake "$WALLET_ROOT" \
    -DCMAKE_BUILD_TYPE="$BUILD_TYPE" \
    -DCMAKE_INSTALL_PREFIX=/usr \
    -DWALLET_REMOTE_RPC_ONLY=OFF \
    -DBUILD_TESTING=OFF

echo ""
echo "Building wallet..."
cmake --build . --config "$BUILD_TYPE" -j$(nproc)

# Locate built executable
WALLET_EXE="$BUILD_DIR/bin/animica-wallet"

if [ ! -f "$WALLET_EXE" ]; then
    echo "Error: Executable not found at $WALLET_EXE"
    exit 1
fi

echo ""
echo "Executable created: $WALLET_EXE"

# Validation
echo ""
echo "======================================"
echo "Validating Build"
echo "======================================"

# Check node binary exists
NODE_PYTHON="$BUILD_DIR/bin/node/venv/bin/python"
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
NODE_ARCH=$(file "$NODE_PYTHON" | grep -o "ARM aarch64\|x86-64\|x86_64")
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
ldd "$WALLET_EXE" | grep -E "Qt|libssl|liboqs" || true

# Create distribution directory
DIST_DIR="$OUTPUT_DIR/$VERSION/linux"
mkdir -p "$DIST_DIR"

# Build AppImage
if [ "$BUILD_APPIMAGE" = true ]; then
    echo ""
    echo "======================================"
    echo "Creating AppImage"
    echo "======================================"
    
    # Create AppDir structure
    APPDIR="$BUILD_DIR/AppDir"
    rm -rf "$APPDIR"
    mkdir -p "$APPDIR"
    
    # Install to AppDir
    DESTDIR="$APPDIR" cmake --install "$BUILD_DIR"
    
    # Copy node into AppDir
    mkdir -p "$APPDIR/usr/lib/node"
    cp -r "$BUILD_DIR/bin/node/venv" "$APPDIR/usr/lib/node/"
    
    # Create desktop file
    DESKTOP_FILE="$APPDIR/animica-wallet.desktop"
    cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Type=Application
Name=Animica Wallet
Comment=Animica blockchain wallet with embedded node
Exec=animica-wallet
Icon=animica-wallet
Categories=Finance;Network;
Terminal=false
EOF
    
    # Copy icon
    mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
    cp "$WALLET_ROOT/resources/icons/hicolor/256x256/apps/animica-wallet.png" \
       "$APPDIR/usr/share/icons/hicolor/256x256/apps/"
    
    # Also copy to AppDir root (required by linuxdeployqt)
    cp "$WALLET_ROOT/resources/icons/hicolor/256x256/apps/animica-wallet.png" \
       "$APPDIR/animica-wallet.png"
    
    # Run linuxdeployqt
    echo "Running linuxdeployqt..."
    cd "$BUILD_DIR"
    
    # Set Qt plugin path if needed
    export QT_PLUGIN_PATH=${QT_PLUGIN_PATH:-/usr/lib/x86_64-linux-gnu/qt6/plugins}
    
    linuxdeployqt "$APPDIR/usr/share/applications/animica-wallet.desktop" \
        -appimage \
        -bundle-non-qt-libs \
        -no-translations \
        -verbose=1
    
    # Find generated AppImage
    APPIMAGE=$(find "$BUILD_DIR" -maxdepth 1 -name "*.AppImage" | head -1)
    
    if [ -f "$APPIMAGE" ]; then
        # Rename to standard format
        RELEASE_APPIMAGE="$DIST_DIR/AnimicaWallet-${VERSION}-linux-${ARCH}.AppImage"
        mv "$APPIMAGE" "$RELEASE_APPIMAGE"
        chmod +x "$RELEASE_APPIMAGE"
        echo "✓ AppImage created: $RELEASE_APPIMAGE"
    else
        echo "Warning: AppImage not created"
    fi
fi

# Build DEB package
if [ "$BUILD_DEB" = true ]; then
    echo ""
    echo "======================================"
    echo "Creating DEB Package"
    echo "======================================"
    
    # Clean build for DEB
    DEB_BUILD_DIR="$WALLET_ROOT/build/linux-deb"
    rm -rf "$DEB_BUILD_DIR"
    mkdir -p "$DEB_BUILD_DIR"
    
    cd "$DEB_BUILD_DIR"
    
    # Configure with CPack settings
    cmake "$WALLET_ROOT" \
        -DCMAKE_BUILD_TYPE="$BUILD_TYPE" \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DWALLET_REMOTE_RPC_ONLY=OFF \
        -DBUILD_TESTING=OFF \
        -DCPACK_GENERATOR="DEB" \
        -DCPACK_PACKAGE_NAME="animica-wallet" \
        -DCPACK_PACKAGE_VERSION="${VERSION#v}" \
        -DCPACK_PACKAGE_CONTACT="Animica Team <support@animica.org>" \
        -DCPACK_DEBIAN_PACKAGE_DEPENDS="libc6, libqt6core6, libqt6gui6, libqt6widgets6, libqt6network6, libqt6sql6, libssl3, python3 (>= 3.10)" \
        -DCPACK_DEBIAN_PACKAGE_DESCRIPTION="Animica blockchain wallet with embedded node"
    
    # Build
    cmake --build . --config "$BUILD_TYPE" -j$(nproc)
    
    # Install to staging
    DEB_STAGE="$DEB_BUILD_DIR/deb-stage"
    mkdir -p "$DEB_STAGE/usr/bin"
    mkdir -p "$DEB_STAGE/usr/lib/animica-wallet/node"
    mkdir -p "$DEB_STAGE/usr/share/applications"
    mkdir -p "$DEB_STAGE/usr/share/icons/hicolor"
    
    # Copy files
    cp "$DEB_BUILD_DIR/bin/animica-wallet" "$DEB_STAGE/usr/bin/"
    cp -r "$DEB_BUILD_DIR/bin/node/venv" "$DEB_STAGE/usr/lib/animica-wallet/node/"
    
    # Create desktop file
    cat > "$DEB_STAGE/usr/share/applications/animica-wallet.desktop" << EOF
[Desktop Entry]
Type=Application
Name=Animica Wallet
Comment=Animica blockchain wallet with embedded node
Exec=/usr/bin/animica-wallet
Icon=animica-wallet
Categories=Finance;Network;
Terminal=false
EOF
    
    # Copy icons
    for SIZE in 16 32 48 64 128 256 512; do
        ICON_DIR="$DEB_STAGE/usr/share/icons/hicolor/${SIZE}x${SIZE}/apps"
        mkdir -p "$ICON_DIR"
        cp "$WALLET_ROOT/resources/icons/hicolor/${SIZE}x${SIZE}/apps/animica-wallet.png" \
           "$ICON_DIR/"
    done
    
    # Create DEBIAN control directory
    DEBIAN_DIR="$DEB_STAGE/DEBIAN"
    mkdir -p "$DEBIAN_DIR"
    
    # Calculate installed size (in KB)
    INSTALLED_SIZE=$(du -sk "$DEB_STAGE" | cut -f1)
    
    # Create control file
    cat > "$DEBIAN_DIR/control" << EOF
Package: animica-wallet
Version: ${VERSION#v}
Section: net
Priority: optional
Architecture: $DEB_ARCH
Depends: libc6, libqt6core6, libqt6gui6, libqt6widgets6, libqt6network6, libqt6sql6, libssl3, python3 (>= 3.10)
Installed-Size: $INSTALLED_SIZE
Maintainer: Animica Team <support@animica.org>
Description: Animica blockchain wallet with embedded node
 A cross-platform desktop wallet for Animica blockchain with embedded
 node support. Provides a seamless experience without requiring users
 to manually manage the node.
EOF
    
    # Build DEB
    DEB_NAME="animica-wallet_${VERSION#v}_${DEB_ARCH}.deb"
    DEB_PATH="$DEB_BUILD_DIR/$DEB_NAME"
    
    dpkg-deb --build "$DEB_STAGE" "$DEB_PATH"
    
    if [ -f "$DEB_PATH" ]; then
        # Copy to dist
        cp "$DEB_PATH" "$DIST_DIR/"
        echo "✓ DEB created: $DIST_DIR/$DEB_NAME"
        
        # Verify DEB
        echo "DEB package info:"
        dpkg-deb --info "$DEB_PATH" | grep -E "Package|Version|Architecture|Depends|Description"
    else
        echo "Warning: DEB not created"
    fi
fi

# Generate checksums
echo ""
echo "Generating checksums..."
cd "$DIST_DIR"
find . -type f \( -name "*.AppImage" -o -name "*.deb" \) -exec sha256sum {} \; > SHA256SUMS
cat SHA256SUMS

echo ""
echo "======================================"
echo "Release Build Complete!"
echo "======================================"
echo "Version: $VERSION"
echo "Architecture: $ARCH (DEB: $DEB_ARCH)"
echo "Output directory: $DIST_DIR"
echo ""
echo "Artifacts:"
ls -lh "$DIST_DIR"
echo ""
echo "To test AppImage:"
echo "  chmod +x $DIST_DIR/*.AppImage"
echo "  $DIST_DIR/*.AppImage"
echo ""
echo "To test DEB:"
echo "  sudo dpkg -i $DIST_DIR/*.deb"
echo "  animica-wallet"
