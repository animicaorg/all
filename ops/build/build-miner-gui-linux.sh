#!/usr/bin/env bash
# Build Linux executable for Animica Miner GUI
# Creates a standalone binary that bundles the node
#
# Usage:
#   ./ops/build/build-miner-gui-linux.sh [OPTIONS]
#
# Options:
#   --out-dir DIR    Output directory (default: dist/)
#   --clean          Clean previous builds before building
#   --help           Show this help

set -euo pipefail

# Source common helpers
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=ops/build/common.sh
source "$SCRIPT_DIR/common.sh"

# ============================================================================
# Configuration
# ============================================================================

REPO_ROOT="$(get_repo_root)"
APP_DIR="$REPO_ROOT/apps/miner-gui"
DEFAULT_OUT_DIR="$REPO_ROOT/dist"

OUT_DIR=""
CLEAN=false

# ============================================================================
# Parse arguments
# ============================================================================

usage() {
    cat <<EOF
Build Linux executable for Animica Miner GUI

Usage:
  $0 [OPTIONS]

Options:
  --out-dir DIR    Output directory (default: dist/)
  --clean          Clean previous builds before building
  --help           Show this help

Examples:
  $0 --clean
  $0 --out-dir /tmp/build
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --out-dir)
            OUT_DIR="$2"
            shift 2
            ;;
        --clean)
            CLEAN=true
            shift
            ;;
        --help)
            usage
            exit 0
            ;;
        *)
            die "Unknown option: $1 (use --help for usage)"
            ;;
    esac
done

# Apply defaults
OUT_DIR="${OUT_DIR:-$DEFAULT_OUT_DIR}"

# ============================================================================
# Preflight checks
# ============================================================================

# Must be on Linux
if [[ "$(uname -s)" != "Linux" ]]; then
    die "This script must be run on Linux"
fi

log "Building Animica Miner GUI for Linux"
log "Repository: $REPO_ROOT"
log "App directory: $APP_DIR"
log "Output directory: $OUT_DIR"

# Find Python
PY="$(find_python3)" || die "Python 3 not found. Install Python 3.10+"
check_python_version "$PY"

# ============================================================================
# Clean if requested
# ============================================================================

if [[ "$CLEAN" == "true" ]]; then
    log "Cleaning previous builds..."
    safe_rm_rf "$OUT_DIR"
    safe_rm_rf "$APP_DIR/dist"
    safe_rm_rf "$APP_DIR/build"
fi

# Create output directory
mkdir -p "$OUT_DIR"

# ============================================================================
# Step 1: Build the node binary first
# ============================================================================

log "Step 1: Building node binary..."
"$SCRIPT_DIR/build-node-binary.sh" --out-dir "$OUT_DIR" --clean

NODE_BINARY="$OUT_DIR/animica-node"
if [[ ! -f "$NODE_BINARY" ]]; then
    die "Node binary not found after build: $NODE_BINARY"
fi

log "Node binary ready: $NODE_BINARY"

# ============================================================================
# Step 2: Setup build environment
# ============================================================================

setup_python_build_env

# Install build dependencies
log "Installing PyInstaller tooling..."
pip_install "$PY" pip setuptools wheel pyinstaller pyinstaller-hooks-contrib

# Determine and install Python project directories
# 1. Install the main animica package (for CLI dependencies)
PYTHON_PKG_DIR="$REPO_ROOT/python"
if [[ ! -f "$PYTHON_PKG_DIR/pyproject.toml" ]]; then
    if [[ -f "$REPO_ROOT/pyproject.toml" ]]; then
        PYTHON_PKG_DIR="$REPO_ROOT"
    else
        err "Cannot find main Python package. Searched:"
        err "  - $REPO_ROOT/python/pyproject.toml"
        err "  - $REPO_ROOT/pyproject.toml"
        die "No Python project found"
    fi
fi
log "Installing main Python package from: $PYTHON_PKG_DIR"
"$PY" -m pip install --quiet -e "$PYTHON_PKG_DIR"

# 2. Install miner-gui with dependencies
if [[ ! -f "$APP_DIR/pyproject.toml" ]]; then
    die "Miner GUI package not found at: $APP_DIR/pyproject.toml"
fi
log "Installing miner-gui dependencies from: $APP_DIR"
"$PY" -m pip install --quiet -e "$APP_DIR"

# ============================================================================
# Step 3: Resolve version
# ============================================================================

VERSION="$(compute_version)"
log "Building version: $VERSION"

# ============================================================================
# Step 4: Determine entry script
# ============================================================================

ENTRY=""
for candidate in \
    "$APP_DIR/animica_miner_gui/main.py" \
    "$APP_DIR/animica_miner_gui/__main__.py" \
    "$APP_DIR/animica_miner_gui/app.py"
do
    if [[ -f "$candidate" ]]; then
        ENTRY="$candidate"
        break
    fi
done

if [[ -z "$ENTRY" ]]; then
    die "Could not find miner-gui entry script in: $APP_DIR/animica_miner_gui/"
fi

log "Entry script: $ENTRY"

# ============================================================================
# Step 5: Create PyInstaller spec
# ============================================================================

BUILD_DIR="$APP_DIR/build"
SPEC_FILE="$BUILD_DIR/animica-miner-gui-linux.spec"
PYI_WORK="$BUILD_DIR/pyinstaller-work"

mkdir -p "$BUILD_DIR" "$PYI_WORK"

# Check if UPX is available (optional compression)
UPX_ENABLED="False"
if have upx; then
    UPX_ENABLED="True"
    log "UPX found; enabling UPX compression"
else
    log "UPX not found; disabling UPX"
fi

# Create spec file
log "Creating PyInstaller spec file..."
cat > "$SPEC_FILE" <<SPEC_EOF
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

APP_DIR  = Path(r"${APP_DIR}").resolve()
ENTRY    = Path(r"${ENTRY}").resolve()
NODE_BINARY = Path(r"${NODE_BINARY}").resolve()

block_cipher = None

# Include logo if present
logo = APP_DIR / "logo.png"
datas = []
if logo.exists():
    datas.append((str(logo), "."))

# Bundle node binary
binaries = []
if NODE_BINARY.exists():
    binaries.append((str(NODE_BINARY), "bin"))

hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
    "shiboken6",
    "matplotlib",
    "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_qt5agg",
    "pydantic",
    "httpx",
    "animica",
    "animica.cli",
    "animica.cli.main",
    "animica.config",
]

a = Analysis(
    [str(ENTRY)],
    pathex=[str(APP_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "test", "unittest"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="animica-miner-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=${UPX_ENABLED},
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=${UPX_ENABLED},
    name="animica-miner-gui",
)
SPEC_EOF

# ============================================================================
# Step 6: Build with PyInstaller
# ============================================================================

log "Running PyInstaller..."
"$PY" -m PyInstaller \
    --noconfirm \
    --clean \
    --distpath "$OUT_DIR/build-dist" \
    --workpath "$PYI_WORK" \
    "$SPEC_FILE"

# ============================================================================
# Step 7: Verify and copy output
# ============================================================================

BUILT_DIR="$OUT_DIR/build-dist/animica-miner-gui"
BUILT_BINARY="$BUILT_DIR/animica-miner-gui"

if [[ ! -f "$BUILT_BINARY" ]]; then
    die "Build failed: expected binary not found at $BUILT_BINARY"
fi

verify_executable "$BUILT_BINARY"

# Copy to final location
FINAL_BINARY="$OUT_DIR/animica-miner-gui"
cp "$BUILT_BINARY" "$FINAL_BINARY"
chmod +x "$FINAL_BINARY"

log "Binary copied to: $FINAL_BINARY"

# Verify node binary is bundled
BUNDLED_NODE="$BUILT_DIR/bin/animica-node"
if [[ ! -f "$BUNDLED_NODE" ]]; then
    die "Node binary not found in build output: $BUNDLED_NODE"
fi

log "Bundled node verified: $BUNDLED_NODE"

# ============================================================================
# Step 8: Create tarball
# ============================================================================

TARBALL_NAME="Animica-Miner-GUI-${VERSION}-Linux-$(uname -m).tar.gz"
TARBALL_PATH="$OUT_DIR/$TARBALL_NAME"

log "Creating tarball..."
tar -czf "$TARBALL_PATH" -C "$BUILT_DIR" .

log "Tarball created: $TARBALL_PATH"

# ============================================================================
# Step 9: Create manifest
# ============================================================================

MANIFEST_FILE="$OUT_DIR/animica-miner-gui-linux.manifest.json"
create_manifest "$MANIFEST_FILE" "animica-miner-gui-linux" "$VERSION"

# ============================================================================
# Step 10: Clean up intermediate files
# ============================================================================

log "Cleaning up intermediate build files..."
safe_rm_rf "$OUT_DIR/build-dist"
safe_rm_rf "$PYI_WORK"

# ============================================================================
# Success
# ============================================================================

log "✅ Build completed successfully!"
log ""
log "Artifacts:"
log "  Binary:   $FINAL_BINARY"
log "  Tarball:  $TARBALL_PATH"
log "  Manifest: $MANIFEST_FILE"
log ""
log "Test:"
log "  $FINAL_BINARY"
