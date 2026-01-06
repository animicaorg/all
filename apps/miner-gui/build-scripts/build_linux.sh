#!/usr/bin/env bash
# Build Linux executable for Animica Miner GUI
# Creates a standalone executable and AppImage
#
# Requirements:
#   - Linux (x86_64 or aarch64)
#   - Python 3.10 or higher
#   - FUSE (for AppImage testing)
#
# Usage:
#   ./build_linux.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(cd "$APP_DIR/../.." && pwd)"
DIST_DIR="${APP_DIR}/dist"
BUILD_DIR="${APP_DIR}/build"

log() { printf "\033[1;34m[build-linux]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err() { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die() { err "$*"; exit 1; }

# Check if running on Linux
if [[ "$(uname -s)" != "Linux" ]]; then
    die "This script must be run on Linux"
fi

# Detect architecture
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

# Check Python version
if ! command -v python3 >/dev/null 2>&1; then
    die "Python 3 is required"
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
log "Using Python $PYTHON_VERSION"

# Install system dependencies if needed
log "Checking system dependencies..."
if command -v apt-get >/dev/null 2>&1; then
    # Debian/Ubuntu
    MISSING_DEPS=()
    for pkg in libgl1 libglib2.0-0 libxcb-xinerama0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0; do
        if ! dpkg -l | grep -q "^ii  $pkg"; then
            MISSING_DEPS+=("$pkg")
        fi
    done
    
    if [[ ${#MISSING_DEPS[@]} -gt 0 ]]; then
        log "Installing missing dependencies: ${MISSING_DEPS[*]}"
        if [[ $EUID -eq 0 ]]; then
            apt-get update && apt-get install -y "${MISSING_DEPS[@]}"
        else
            sudo apt-get update && sudo apt-get install -y "${MISSING_DEPS[@]}"
        fi
    fi
elif command -v yum >/dev/null 2>&1; then
    # RHEL/Fedora
    log "Note: Ensure Qt dependencies are installed (mesa-libGL, glib2, xcb libraries)"
fi

# Clean previous builds
log "Cleaning previous builds..."
rm -rf "$DIST_DIR" "$BUILD_DIR"
mkdir -p "$DIST_DIR"

# Install/upgrade PyInstaller
log "Installing PyInstaller..."
python3 -m pip install --upgrade pip setuptools wheel
python3 -m pip install --upgrade pyinstaller

# Install the miner-gui package and its dependencies
log "Installing miner-gui dependencies..."
cd "$APP_DIR"
python3 -m pip install -e .

# Get version from pyproject.toml
VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('$APP_DIR/pyproject.toml', 'rb'))['project']['version'])" 2>/dev/null || echo "0.1.0")
log "Building version: $VERSION"

# Create PyInstaller spec
SPEC_FILE="${BUILD_DIR}/animica-miner-gui-linux.spec"
mkdir -p "$BUILD_DIR"

log "Creating PyInstaller spec file..."
cat > "$SPEC_FILE" << 'SPEC_EOF'
# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

block_cipher = None

# Get the logo path
logo_path = Path('logo.png')
if not logo_path.exists():
    print("Warning: logo.png not found")
    logo_path = None

a = Analysis(
    ['animica_miner_gui/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('logo.png', '.') if logo_path else None,
    ],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'matplotlib.backends.backend_qt5agg',
        'pydantic',
        'httpx',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'test',
        'unittest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Filter out None from datas
a.datas = [d for d in a.datas if d is not None]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='animica-miner-gui',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
SPEC_EOF

# Build with PyInstaller
log "Running PyInstaller..."
cd "$APP_DIR"
python3 -m PyInstaller --clean --noconfirm "$SPEC_FILE"

# Check if executable was created
EXE_PATH="${DIST_DIR}/animica-miner-gui"
if [[ ! -f "$EXE_PATH" ]]; then
    die "Failed to create executable"
fi

# Make executable
chmod +x "$EXE_PATH"
log "Executable created successfully at: $EXE_PATH"

# Create tarball
TAR_NAME="Animica-Miner-GUI-${VERSION}-Linux-${ARCH_NAME}.tar.gz"
TAR_PATH="${DIST_DIR}/${TAR_NAME}"

log "Creating tarball..."
cd "$DIST_DIR"
tar -czf "$TAR_NAME" "animica-miner-gui"

log "Tarball created: $TAR_PATH"

# Try to create AppImage
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

APPDIR="${BUILD_DIR}/AnimicaMinerGUI.AppDir"
APPIMAGE_TOOL="${BUILD_DIR}/appimagetool.AppImage"

if [[ -n "$APPIMAGE_TOOL_URL" ]]; then
    # Download appimagetool if not present
    if [[ ! -f "$APPIMAGE_TOOL" ]]; then
        log "Downloading appimagetool..."
        curl -L "$APPIMAGE_TOOL_URL" -o "$APPIMAGE_TOOL"
        chmod +x "$APPIMAGE_TOOL"
    fi
    
    # Create AppDir structure
    mkdir -p "$APPDIR/usr/bin"
    mkdir -p "$APPDIR/usr/share/applications"
    mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
    
    # Copy executable
    cp "$EXE_PATH" "$APPDIR/usr/bin/"
    
    # Create desktop file
    cat > "$APPDIR/animica-miner-gui.desktop" << 'DESKTOP_EOF'
[Desktop Entry]
Type=Application
Name=Animica Miner GUI
Comment=Production-quality Qt desktop GUI miner for Animica blockchain
Exec=animica-miner-gui
Icon=animica-miner-gui
Categories=Finance;Network;
Terminal=false
DESKTOP_EOF
    
    # Copy logo if exists
    if [[ -f "$APP_DIR/logo.png" ]]; then
        cp "$APP_DIR/logo.png" "$APPDIR/animica-miner-gui.png"
        cp "$APP_DIR/logo.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/animica-miner-gui.png"
    else
        # Create a minimal icon if logo doesn't exist
        warn "Logo not found, AppImage will have no icon"
        touch "$APPDIR/animica-miner-gui.png"
    fi
    
    # Create AppRun script
    cat > "$APPDIR/AppRun" << 'APPRUN_EOF'
#!/bin/bash
SELF=$(readlink -f "$0")
HERE=${SELF%/*}
export PATH="${HERE}/usr/bin:${PATH}"
export LD_LIBRARY_PATH="${HERE}/usr/lib:${LD_LIBRARY_PATH}"
exec "${HERE}/usr/bin/animica-miner-gui" "$@"
APPRUN_EOF
    chmod +x "$APPDIR/AppRun"
    
    # Build AppImage
    APPIMAGE_NAME="Animica-Miner-GUI-${VERSION}-${ARCH_NAME}.AppImage"
    log "Building AppImage: $APPIMAGE_NAME"
    
    cd "$BUILD_DIR"
    ARCH="$APPIMAGE_ARCH" "$APPIMAGE_TOOL" "$APPDIR" "$DIST_DIR/$APPIMAGE_NAME" 2>&1 | grep -v "WARNING" || true
    
    if [[ -f "$DIST_DIR/$APPIMAGE_NAME" ]]; then
        chmod +x "$DIST_DIR/$APPIMAGE_NAME"
        log "AppImage created: $DIST_DIR/$APPIMAGE_NAME"
    else
        warn "AppImage creation failed or was skipped"
    fi
else
    warn "AppImage creation not available for architecture: $ARCH_NAME"
fi

log "✅ Build completed successfully!"
log ""
log "Artifacts:"
log "  Executable: $EXE_PATH"
log "  Tarball:    $TAR_PATH"
if [[ -f "$DIST_DIR/$APPIMAGE_NAME" ]]; then
    log "  AppImage:   $DIST_DIR/$APPIMAGE_NAME"
fi
log ""
log "To test the executable, run:"
log "  $EXE_PATH"
log ""
log "To install system-wide, copy to /usr/local/bin:"
log "  sudo cp $EXE_PATH /usr/local/bin/"
