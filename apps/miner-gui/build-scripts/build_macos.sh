#!/usr/bin/env bash
# Build macOS executable for Animica Miner GUI
# Creates a standalone .app bundle and DMG installer
#
# Requirements:
#   - macOS 10.15 or later
#   - Python 3.10 or higher
#   - Xcode Command Line Tools
#
# Usage:
#   ./build_macos.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(cd "$APP_DIR/../.." && pwd)"
DIST_DIR="${APP_DIR}/dist"
BUILD_DIR="${APP_DIR}/build"

log() { printf "\033[1;34m[build-macos]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err() { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die() { err "$*"; exit 1; }

# Check if running on macOS
if [[ "$(uname -s)" != "Darwin" ]]; then
    die "This script must be run on macOS"
fi

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
log "Using Python $PYTHON_VERSION"

# Check for required tools
command -v python3 >/dev/null 2>&1 || die "Python 3 is required"
command -v hdiutil >/dev/null 2>&1 || die "hdiutil not found (macOS tool)"

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

# Create PyInstaller spec if it doesn't exist
SPEC_FILE="${BUILD_DIR}/animica-miner-gui-macos.spec"
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
    [],
    exclude_binaries=True,
    name='Animica Miner GUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Animica Miner GUI',
)

app = BUNDLE(
    coll,
    name='Animica Miner GUI.app',
    icon=None,
    bundle_identifier='org.animica.miner-gui',
    version='0.1.0',
    info_plist={
        'CFBundleName': 'Animica Miner GUI',
        'CFBundleDisplayName': 'Animica Miner GUI',
        'CFBundleShortVersionString': '0.1.0',
        'CFBundleVersion': '0.1.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.15',
        'NSRequiresAquaSystemAppearance': False,
        'CFBundlePackageType': 'APPL',
        'LSApplicationCategoryType': 'public.app-category.finance',
    },
)
SPEC_EOF

# Update version in spec file
sed -i.bak "s/version='0.1.0'/version='$VERSION'/g" "$SPEC_FILE"
sed -i.bak "s/'CFBundleShortVersionString': '0.1.0'/'CFBundleShortVersionString': '$VERSION'/g" "$SPEC_FILE"
sed -i.bak "s/'CFBundleVersion': '0.1.0'/'CFBundleVersion': '$VERSION'/g" "$SPEC_FILE"
rm -f "${SPEC_FILE}.bak"

# Build with PyInstaller
log "Running PyInstaller..."
cd "$APP_DIR"
python3 -m PyInstaller --clean --noconfirm "$SPEC_FILE"

# Check if app bundle was created
APP_BUNDLE="${DIST_DIR}/Animica Miner GUI.app"
if [[ ! -d "$APP_BUNDLE" ]]; then
    die "Failed to create app bundle"
fi

log "App bundle created successfully at: $APP_BUNDLE"

# Create DMG
DMG_NAME="Animica-Miner-GUI-${VERSION}-macOS-$(uname -m).dmg"
DMG_PATH="${DIST_DIR}/${DMG_NAME}"

log "Creating DMG installer..."
hdiutil create -volname "Animica Miner GUI" \
    -srcfolder "$APP_BUNDLE" \
    -ov -format UDZO \
    "$DMG_PATH"

log "✅ Build completed successfully!"
log ""
log "Artifacts:"
log "  App Bundle: $APP_BUNDLE"
log "  DMG:        $DMG_PATH"
log ""
log "To test the app, run:"
log "  open \"$APP_BUNDLE\""
log ""
log "To install, mount the DMG and drag the app to /Applications:"
log "  open \"$DMG_PATH\""
