#!/usr/bin/env bash
# Build Linux executable for Animica Miner-Wallet (Flutter)
# Creates a standalone executable, tarball, and optionally AppImage
#
# Requirements:
#   - Flutter SDK ≥ 3.24
#   - Linux (x86_64 or aarch64)
#   - GTK3 development packages
#
# Usage:
#   ./build_linux.sh

set -euo pipefail

log()  { printf "\033[1;34m[build-linux]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

# ---- Paths ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
BUILD_DIR="$APP_DIR/build/linux/x64/release/bundle"

# ---- Platform checks ----
if [[ "$(uname -s)" != "Linux" ]]; then
    die "This script must be run on Linux"
fi

# ---- Detect architecture ----
ARCH="$(uname -m)"
case "$ARCH" in
    x86_64|amd64)
        ARCH_NAME="x86_64"
        APPIMAGE_ARCH="x86_64"
        ;;
    aarch64|arm64)
        ARCH_NAME="aarch64"
        APPIMAGE_ARCH="aarch64"
        ;;
    *)
        die "Unsupported architecture: $ARCH"
        ;;
esac

log "Building for Linux $ARCH_NAME"

# ---- Check Flutter ----
if ! command -v flutter >/dev/null 2>&1; then
    die "Flutter SDK not found. Install from: https://docs.flutter.dev/get-started/install"
fi

FLUTTER_VERSION=$(flutter --version | head -n 1 | awk '{print $2}')
log "Using Flutter $FLUTTER_VERSION"

# ---- Check GTK dependencies ----
log "Checking GTK3 dependencies..."
MISSING_DEPS=()
if command -v apt-get >/dev/null 2>&1; then
    # Debian/Ubuntu
    for pkg in libgtk-3-dev libglib2.0-dev libblkid-dev liblzma-dev; do
        if ! dpkg -l | grep -q "^ii  $pkg"; then
            MISSING_DEPS+=("$pkg")
        fi
    done
    
    if [[ ${#MISSING_DEPS[@]} -gt 0 ]]; then
        warn "Missing dependencies: ${MISSING_DEPS[*]}"
        warn "Install with: sudo apt-get install ${MISSING_DEPS[*]}"
        warn "Attempting to continue anyway..."
    fi
elif command -v yum >/dev/null 2>&1; then
    # RHEL/Fedora
    log "Note: Ensure GTK3 development packages are installed"
fi

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

# ---- Build for Linux ----
log "Building for Linux (release mode)..."
flutter build linux --release

# ---- Check build output ----
if [[ ! -d "$BUILD_DIR" ]]; then
    die "Build failed - bundle directory not found: $BUILD_DIR"
fi

EXE_PATH="$BUILD_DIR/animica_miner_wallet"
if [[ ! -f "$EXE_PATH" ]]; then
    # Try to find any executable in the bundle
    EXE_PATH=$(find "$BUILD_DIR" -maxdepth 1 -type f -executable -print -quit || echo "")
    if [[ -z "$EXE_PATH" || ! -f "$EXE_PATH" ]]; then
        die "Build failed - executable not found in: $BUILD_DIR"
    fi
fi

log "Executable created: $EXE_PATH"

# ---- Create distribution directory ----
DIST_DIR="$APP_DIR/dist"
mkdir -p "$DIST_DIR"

# ---- Create tarball with the entire bundle ----
TAR_NAME="Animica-Miner-Wallet-${VERSION}-Linux-${ARCH_NAME}.tar.gz"
TAR_PATH="$DIST_DIR/$TAR_NAME"

log "Creating tarball..."
cd "$APP_DIR/build/linux/x64/release"
tar -czf "$TAR_PATH" bundle/

log "Tarball created: $TAR_PATH"

# ---- Try to create AppImage ----
log "Attempting to create AppImage..."

APPIMAGE_TOOL_URL=""
case "$ARCH_NAME" in
    x86_64)
        APPIMAGE_TOOL_URL="https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
        ;;
    aarch64)
        APPIMAGE_TOOL_URL="https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-aarch64.AppImage"
        ;;
esac

APPDIR="$APP_DIR/build/AnimicaMinerWallet.AppDir"
APPIMAGE_TOOL="$APP_DIR/build/appimagetool.AppImage"
APPIMAGE_NAME=""

if [[ -n "$APPIMAGE_TOOL_URL" ]]; then
    # Download appimagetool if not present
    if [[ ! -f "$APPIMAGE_TOOL" ]]; then
        log "Downloading appimagetool..."
        if command -v curl >/dev/null 2>&1; then
            curl -L "$APPIMAGE_TOOL_URL" -o "$APPIMAGE_TOOL"
            chmod +x "$APPIMAGE_TOOL"
        elif command -v wget >/dev/null 2>&1; then
            wget "$APPIMAGE_TOOL_URL" -O "$APPIMAGE_TOOL"
            chmod +x "$APPIMAGE_TOOL"
        else
            warn "Neither curl nor wget found, skipping AppImage creation"
            APPIMAGE_TOOL=""
        fi
    fi
    
    if [[ -n "$APPIMAGE_TOOL" && -f "$APPIMAGE_TOOL" ]]; then
        # Create AppDir structure
        rm -rf "$APPDIR"
        mkdir -p "$APPDIR/usr/bin"
        mkdir -p "$APPDIR/usr/lib"
        mkdir -p "$APPDIR/usr/share/applications"
        mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
        
        # Copy the entire bundle
        log "Copying bundle to AppDir..."
        cp -R "$BUILD_DIR"/* "$APPDIR/usr/bin/"
        
        # Create wrapper script
        cat > "$APPDIR/usr/bin/animica-miner-wallet-wrapper" << 'WRAPPER_EOF'
#!/usr/bin/env bash
SELF=$(readlink -f "$0")
HERE=${SELF%/*}
export LD_LIBRARY_PATH="${HERE}:${HERE}/lib:${LD_LIBRARY_PATH}"
exec "${HERE}/animica_miner_wallet" "$@"
WRAPPER_EOF
        chmod +x "$APPDIR/usr/bin/animica-miner-wallet-wrapper"
        
        # Create desktop file
        cat > "$APPDIR/animica-miner-wallet.desktop" << 'DESKTOP_EOF'
[Desktop Entry]
Type=Application
Name=Animica Miner Wallet
Comment=Unified mining and wallet application for Animica blockchain
Exec=animica-miner-wallet-wrapper
Icon=animica-miner-wallet
Categories=Finance;Network;
Terminal=false
DESKTOP_EOF
        
        # Create/copy icon
        if [[ -f "$APP_DIR/assets/icons/app_icon.png" ]]; then
            cp "$APP_DIR/assets/icons/app_icon.png" "$APPDIR/animica-miner-wallet.png"
            cp "$APP_DIR/assets/icons/app_icon.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/animica-miner-wallet.png"
        else
            # Create a minimal placeholder icon
            warn "App icon not found, AppImage will have no icon"
            touch "$APPDIR/animica-miner-wallet.png"
        fi
        
        # Create AppRun script
        cat > "$APPDIR/AppRun" << 'APPRUN_EOF'
#!/usr/bin/env bash
SELF=$(readlink -f "$0")
HERE=${SELF%/*}
export PATH="${HERE}/usr/bin:${PATH}"
export LD_LIBRARY_PATH="${HERE}/usr/lib:${HERE}/usr/bin/lib:${LD_LIBRARY_PATH}"
exec "${HERE}/usr/bin/animica-miner-wallet-wrapper" "$@"
APPRUN_EOF
        chmod +x "$APPDIR/AppRun"
        
        # Build AppImage
        APPIMAGE_NAME="Animica-Miner-Wallet-${VERSION}-${ARCH_NAME}.AppImage"
        log "Building AppImage: $APPIMAGE_NAME"
        
        cd "$APP_DIR/build"
        ARCH="$APPIMAGE_ARCH" "$APPIMAGE_TOOL" "$APPDIR" "$DIST_DIR/$APPIMAGE_NAME" 2>&1 | grep -v "WARNING" || true
        
        if [[ -f "$DIST_DIR/$APPIMAGE_NAME" ]]; then
            chmod +x "$DIST_DIR/$APPIMAGE_NAME"
            log "AppImage created: $DIST_DIR/$APPIMAGE_NAME"
        else
            warn "AppImage creation failed or was skipped"
            APPIMAGE_NAME=""
        fi
    fi
else
    warn "AppImage creation not available for architecture: $ARCH_NAME"
fi

log "✅ Build completed successfully!"
log ""
log "Artifacts:"
log "  Bundle:  $BUILD_DIR"
log "  Tarball: $TAR_PATH"
if [[ -n "$APPIMAGE_NAME" && -f "$DIST_DIR/$APPIMAGE_NAME" ]]; then
    log "  AppImage: $DIST_DIR/$APPIMAGE_NAME"
fi
log ""
log "To test the executable:"
log "  $EXE_PATH"
log ""
log "To install system-wide, extract the tarball and copy to /opt:"
log "  sudo tar -xzf $TAR_PATH -C /opt/"
log "  sudo ln -s /opt/bundle/animica_miner_wallet /usr/local/bin/animica-miner-wallet"
log ""
if [[ -n "$APPIMAGE_NAME" && -f "$DIST_DIR/$APPIMAGE_NAME" ]]; then
    log "Or simply run the AppImage directly:"
    log "  $DIST_DIR/$APPIMAGE_NAME"
fi
