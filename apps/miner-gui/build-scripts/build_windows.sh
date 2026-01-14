#!/usr/bin/env bash
# Build Windows executable for Animica Miner GUI
# Creates a standalone .exe and installer
#
# Requirements:
#   - Windows 10/11 or cross-compilation environment
#   - Python 3.10 or higher for Windows
#   - Wine (if cross-compiling from Mac/Linux)
#
# Usage:
#   On Windows (Git Bash/WSL):  ./build_windows.sh
#   On Mac/Linux:               ./build_windows.sh --cross-compile

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(cd "$APP_DIR/../.." && pwd)"
DIST_DIR="${APP_DIR}/dist"
BUILD_DIR="${APP_DIR}/build"

CROSS_COMPILE=false

log() { printf "\033[1;34m[build-windows]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err() { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die() { err "$*"; exit 1; }

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --cross-compile)
            CROSS_COMPILE=true
            shift
            ;;
        *)
            die "Unknown option: $1"
            ;;
    esac
done

# Detect OS
OS="$(uname -s)"
case "$OS" in
    MINGW*|MSYS*|CYGWIN*)
        ON_WINDOWS=true
        ;;
    *)
        ON_WINDOWS=false
        ;;
esac

if [[ "$ON_WINDOWS" == "false" && "$CROSS_COMPILE" == "false" ]]; then
    die "Not running on Windows. Use --cross-compile flag to build for Windows from Mac/Linux (requires Wine)"
fi

# Clean previous builds
log "Cleaning previous builds..."
rm -rf "$DIST_DIR" "$BUILD_DIR"
mkdir -p "$DIST_DIR"

if [[ "$CROSS_COMPILE" == "true" ]]; then
    log "Cross-compiling for Windows..."
    
    # Check for Wine
    if ! command -v wine >/dev/null 2>&1; then
        die "Wine is required for cross-compilation. Install with: brew install wine (Mac) or apt install wine (Linux)"
    fi
    
    # Check for Python Windows installation in Wine
    WINE_PYTHON="wine python"
    if ! $WINE_PYTHON --version >/dev/null 2>&1; then
        err "Python for Windows not found in Wine environment"
        err "Please install Python 3.10+ for Windows in Wine:"
        err "  1. Download Python installer: https://www.python.org/downloads/windows/"
        err "  2. Run: wine python-3.XX.X-amd64.exe /quiet InstallAllUsers=1 PrependPath=1"
        die "Cannot proceed without Windows Python in Wine"
    fi
    
    PYTHON_CMD="$WINE_PYTHON"
    PIP_CMD="wine pip"
else
    # Native Windows build
    log "Building on Windows..."
    
    # Try to find Python
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_CMD="python3"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_CMD="python"
    else
        die "Python not found. Please install Python 3.10 or higher"
    fi
    
    PIP_CMD="$PYTHON_CMD -m pip"
fi

# Check Python version
PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}')
log "Using Python $PYTHON_VERSION"

# Install/upgrade PyInstaller
log "Installing PyInstaller..."
$PIP_CMD install --upgrade pip setuptools wheel
$PIP_CMD install --upgrade pyinstaller

# Install the miner-gui package and its dependencies
log "Installing miner-gui dependencies..."
cd "$APP_DIR"
$PIP_CMD install -e .

# Get version from pyproject.toml
VERSION=$($PYTHON_CMD -c "import tomllib; print(tomllib.load(open('$APP_DIR/pyproject.toml', 'rb'))['project']['version'])" 2>/dev/null || echo "0.1.0")
log "Building version: $VERSION"

# Create PyInstaller spec
SPEC_FILE="${BUILD_DIR}/animica-miner-gui-windows.spec"
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
        # Animica CLI and dependencies for embedded node support
        'animica',
        'animica.cli',
        'animica.cli.main',
        'animica.cli.node',
        'animica.config',
        'mining',
        'mining.cli',
        'mining.cli.miner',
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
    name='Animica-Miner-GUI',
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
    icon=None,
    version='file_version_info.txt',
)
SPEC_EOF

# Create version info file
VERSION_INFO="${BUILD_DIR}/file_version_info.txt"
cat > "$VERSION_INFO" << VERSION_EOF
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(0, 1, 0, 0),
    prodvers=(0, 1, 0, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'Animica'),
        StringStruct(u'FileDescription', u'Animica Miner GUI'),
        StringStruct(u'FileVersion', u'$VERSION'),
        StringStruct(u'InternalName', u'Animica-Miner-GUI'),
        StringStruct(u'LegalCopyright', u'Copyright (c) 2024 Animica'),
        StringStruct(u'OriginalFilename', u'Animica-Miner-GUI.exe'),
        StringStruct(u'ProductName', u'Animica Miner GUI'),
        StringStruct(u'ProductVersion', u'$VERSION')])
      ]
    ),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
VERSION_EOF

# Build with PyInstaller
log "Running PyInstaller..."
cd "$APP_DIR"

if [[ "$CROSS_COMPILE" == "true" ]]; then
    wine pyinstaller --clean --noconfirm "$SPEC_FILE"
else
    $PYTHON_CMD -m PyInstaller --clean --noconfirm "$SPEC_FILE"
fi

# Check if executable was created
EXE_PATH="${DIST_DIR}/Animica-Miner-GUI.exe"
if [[ ! -f "$EXE_PATH" ]]; then
    die "Failed to create executable"
fi

log "Executable created successfully at: $EXE_PATH"

# Create a zip package
ZIP_NAME="Animica-Miner-GUI-${VERSION}-Windows-x64.zip"
ZIP_PATH="${DIST_DIR}/${ZIP_NAME}"

log "Creating ZIP package..."
cd "$DIST_DIR"
if command -v zip >/dev/null 2>&1; then
    zip -q "$ZIP_NAME" "Animica-Miner-GUI.exe"
elif command -v 7z >/dev/null 2>&1; then
    7z a "$ZIP_NAME" "Animica-Miner-GUI.exe" > /dev/null
else
    warn "No zip utility found, skipping ZIP creation"
    ZIP_PATH=""
fi

log "✅ Build completed successfully!"
log ""
log "Artifacts:"
log "  Executable: $EXE_PATH"
if [[ -n "$ZIP_PATH" && -f "$ZIP_PATH" ]]; then
    log "  ZIP:        $ZIP_PATH"
fi
log ""
log "To test the executable:"
if [[ "$CROSS_COMPILE" == "true" ]]; then
    log "  wine \"$EXE_PATH\""
else
    log "  \"$EXE_PATH\""
fi
log ""
log "Note: For production releases, sign the executable with a code signing certificate"
