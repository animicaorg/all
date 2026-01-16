#!/usr/bin/env bash
# Build macOS .app bundle for Animica Miner GUI
# Bundles the node binary inside the .app for local-only operation
#
# Usage:
#   ./ops/build/build-miner-gui-macos.sh [OPTIONS]
#
# Options:
#   --out-dir DIR    Output directory (default: dist/)
#   --clean          Clean previous builds before building
#   --dev            Development build (no codesigning)
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
DEV_MODE=false

# ============================================================================
# Parse arguments
# ============================================================================

usage() {
    cat <<EOF
Build macOS .app bundle for Animica Miner GUI

Usage:
  $0 [OPTIONS]

Options:
  --out-dir DIR    Output directory (default: dist/)
  --clean          Clean previous builds before building
  --dev            Development build (no codesigning)
  --help           Show this help

Examples:
  $0 --clean
  $0 --dev --out-dir /tmp/build
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
        --dev)
            DEV_MODE=true
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

# Must be on macOS
if [[ "$(uname -s)" != "Darwin" ]]; then
    die "This script must be run on macOS"
fi

require hdiutil "macOS hdiutil"

log "Building Animica Miner GUI for macOS"
log "Repository: $REPO_ROOT"
log "App directory: $APP_DIR"
log "Output directory: $OUT_DIR"
log "Dev mode: $DEV_MODE"

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

# Install miner-gui with dependencies
log "Installing miner-gui dependencies..."
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
SPEC_FILE="$BUILD_DIR/animica-miner-gui-macos.spec"
PYI_WORK="$BUILD_DIR/pyinstaller-work"
RUNTIME_HOOK="$BUILD_DIR/qt_runtime_hook.py"

mkdir -p "$BUILD_DIR" "$PYI_WORK"

# Runtime hook for Qt plugins
cat > "$RUNTIME_HOOK" <<'PYEOF'
import os

def _fix_qt_plugin_paths():
    for k in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        if k in os.environ and not os.environ[k].strip():
            os.environ.pop(k, None)
    
    try:
        from PySide6.QtCore import QLibraryInfo
        plugins = QLibraryInfo.path(QLibraryInfo.PluginsPath)
        if plugins:
            os.environ.setdefault("QT_PLUGIN_PATH", plugins)
            plat = os.path.join(plugins, "platforms")
            os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", plat)
    except Exception:
        pass

_fix_qt_plugin_paths()
PYEOF

# Check if UPX is available (optional compression)
UPX_ENABLED="False"
if have upx; then
    UPX_ENABLED="True"
    log "UPX found; enabling UPX compression"
else
    log "UPX not found; disabling UPX (recommended on macOS arm64)"
fi

# Create spec file
log "Creating PyInstaller spec file..."
cat > "$SPEC_FILE" <<SPEC_EOF
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

SPEC_DIR = Path(r"${BUILD_DIR}").resolve()
APP_DIR  = Path(r"${APP_DIR}").resolve()
ENTRY    = Path(r"${ENTRY}").resolve()
NODE_BINARY = Path(r"${NODE_BINARY}").resolve()

block_cipher = None

# Include logo if present
logo = APP_DIR / "logo.png"
datas = []
if logo.exists():
    datas.append((str(logo), "."))

# Bundle node binary inside the .app
binaries = []
if NODE_BINARY.exists():
    # Place in Contents/Resources/bin/ (standard location for bundled tools)
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
    # Animica CLI and dependencies (for embedded operations if needed)
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
    runtime_hooks=[str(SPEC_DIR / "qt_runtime_hook.py")],
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
    name="Animica Miner GUI",
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
    name="Animica Miner GUI",
)

app = BUNDLE(
    coll,
    name="Animica Miner GUI.app",
    icon=None,
    bundle_identifier="org.animica.miner-gui",
    version="${VERSION}",
    info_plist={
        "CFBundleName": "Animica Miner GUI",
        "CFBundleDisplayName": "Animica Miner GUI",
        "CFBundleShortVersionString": "${VERSION}",
        "CFBundleVersion": "${VERSION}",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "10.15",
        "NSRequiresAquaSystemAppearance": False,
        "CFBundlePackageType": "APPL",
        "LSApplicationCategoryType": "public.app-category.finance",
    },
)
SPEC_EOF

# ============================================================================
# Step 6: Build with PyInstaller
# ============================================================================

log "Running PyInstaller..."
"$PY" -m PyInstaller \
    --noconfirm \
    --clean \
    --distpath "$OUT_DIR" \
    --workpath "$PYI_WORK" \
    "$SPEC_FILE"

# ============================================================================
# Step 7: Verify the .app bundle
# ============================================================================

APP_BUNDLE="$OUT_DIR/Animica Miner GUI.app"
if [[ ! -d "$APP_BUNDLE" ]]; then
    # Try to find it
    FOUND="$(find "$OUT_DIR" -maxdepth 3 -name "Animica Miner GUI.app" -type d -print -quit || true)"
    if [[ -n "$FOUND" ]]; then
        APP_BUNDLE="$FOUND"
    else
        die "Failed to create app bundle. Check: $OUT_DIR"
    fi
fi

verify_directory "$APP_BUNDLE"
log "App bundle created: $APP_BUNDLE"

# Verify node binary is inside
BUNDLED_NODE="$APP_BUNDLE/Contents/Resources/bin/animica-node"
if [[ ! -f "$BUNDLED_NODE" ]]; then
    die "Node binary not found in .app bundle: $BUNDLED_NODE"
fi

verify_executable "$BUNDLED_NODE"
log "Bundled node verified: $BUNDLED_NODE"

# ============================================================================
# Step 8: Create DMG (optional)
# ============================================================================

DMG_NAME="Animica-Miner-GUI-${VERSION}-macOS-$(uname -m).dmg"
DMG_PATH="$OUT_DIR/$DMG_NAME"

log "Creating DMG installer..."
hdiutil create \
    -volname "Animica Miner GUI" \
    -srcfolder "$APP_BUNDLE" \
    -ov \
    -format UDZO \
    "$DMG_PATH"

log "DMG created: $DMG_PATH"

# ============================================================================
# Step 9: Create manifest
# ============================================================================

MANIFEST_FILE="$OUT_DIR/animica-miner-gui-macos.manifest.json"
create_manifest "$MANIFEST_FILE" "animica-miner-gui-macos" "$VERSION"

# ============================================================================
# Success
# ============================================================================

log "✅ Build completed successfully!"
log ""
log "Artifacts:"
log "  App Bundle: $APP_BUNDLE"
log "  DMG:        $DMG_PATH"
log "  Manifest:   $MANIFEST_FILE"
log ""
log "Test:"
log "  open \"$APP_BUNDLE\""
log ""
if [[ "$DEV_MODE" == "false" ]]; then
    warn "Note: App is not code-signed. For distribution, sign with:"
    warn "  codesign --deep --force --sign \"Developer ID Application\" \"$APP_BUNDLE\""
fi
