#!/usr/bin/env bash
# Build macOS executable for Animica Miner GUI
# Creates a standalone .app bundle and DMG installer
#
# NOTE: This script is deprecated. Use the unified build system instead:
#   ../../ops/build/build-miner-gui-macos.sh
#
# The unified scripts in ops/build/ bundle the node binary inside the .app
# and include additional protections against macOS infinite spawn issues.
#
# Usage:
#   ./build-scripts/build_macos.sh

set -euo pipefail

log()  { printf "\033[1;34m[build-macos]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

# ---- Paths ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"          # apps/miner-gui
REPO_ROOT="$(cd "$APP_DIR/../.." && pwd)"        # repo root

# ---- Show deprecation warning ----
warn "========================================================================="
warn "DEPRECATION NOTICE:"
warn "This script is deprecated. Please use the unified build system instead:"
warn "  $REPO_ROOT/ops/build/build-miner-gui-macos.sh"
warn ""
warn "The unified scripts bundle the node binary inside the .app and include"
warn "protections against macOS infinite spawn issues."
warn ""
warn "See: ops/build/README.md for documentation"
warn "========================================================================="
warn ""
warn "Delegating to unified build script in 3 seconds..."
sleep 3

# ---- Delegate to unified script ----
UNIFIED_SCRIPT="$REPO_ROOT/ops/build/build-miner-gui-macos.sh"

if [[ ! -x "$UNIFIED_SCRIPT" ]]; then
    die "Unified build script not found or not executable: $UNIFIED_SCRIPT"
fi

exec "$UNIFIED_SCRIPT" --out-dir "$APP_DIR/dist" "$@"

# ---- Install build tooling ----
log "Installing PyInstaller tooling..."
"$PY" -m pip install --upgrade pip setuptools wheel
"$PY" -m pip install --upgrade pyinstaller pyinstaller-hooks-contrib

# ---- Install miner-gui deps ----
log "Installing miner-gui dependencies..."
"$PY" -m pip install -e "$APP_DIR"

# ---- Resolve version (robust) ----
VERSION="$("$PY" -c "import tomllib; print(tomllib.load(open(r'$APP_DIR/pyproject.toml','rb'))['project']['version'])" 2>/dev/null || echo "0.1.0")"
log "Building version: $VERSION"

# ---- Determine entry script robustly ----
ENTRY=""
for c in \
  "$APP_DIR/animica_miner_gui/main.py" \
  "$APP_DIR/animica_miner_gui/__main__.py" \
  "$APP_DIR/animica_miner_gui/app.py"
do
  if [[ -f "$c" ]]; then ENTRY="$c"; break; fi
done
if [[ -z "$ENTRY" ]]; then
  die "Could not find an entry script. Expected one of:
  - $APP_DIR/animica_miner_gui/main.py
  - $APP_DIR/animica_miner_gui/__main__.py
  - $APP_DIR/animica_miner_gui/app.py"
fi
log "Entry script: $ENTRY"

# ---- Optional: disable UPX if not installed ----
UPX_ENABLED="False"
if command -v upx >/dev/null 2>&1; then
  UPX_ENABLED="True"
  log "UPX found; enabling UPX compression."
else
  log "UPX not found; disabling UPX (recommended on macOS arm64)."
fi

# ---- Runtime hook: help Qt find platform plugins inside packaged build ----
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

# ---- Create spec (NO __file__ usage; hardcode paths from bash) ----
log "Creating PyInstaller spec file..."
cat > "$SPEC_FILE" <<SPEC_EOF
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

# PyInstaller does NOT guarantee __file__ exists in the spec exec namespace.
# Use absolute paths injected by the build script instead.
SPEC_DIR = Path(r"${BUILD_DIR}").resolve()
APP_DIR  = Path(r"${APP_DIR}").resolve()
ENTRY    = Path(r"${ENTRY}").resolve()

block_cipher = None

logo = APP_DIR / "logo.png"
datas = []
if logo.exists():
    datas.append((str(logo), "."))

hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "shiboken6",
    "matplotlib",
    "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_qt5agg",
    "pydantic",
    "httpx",
    # Animica CLI and dependencies for embedded node support
    "animica",
    "animica.cli",
    "animica.cli.main",
    "animica.cli.node",
    "animica.config",
    "mining",
    "mining.cli",
    "mining.cli.miner",
]

a = Analysis(
    [str(ENTRY)],
    pathex=[str(APP_DIR)],
    binaries=[],
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

# ---- Build with PyInstaller ----
log "Running PyInstaller..."
"$PY" -m PyInstaller --noconfirm --clean \
  --distpath "$DIST_DIR" \
  --workpath "$PYI_WORK" \
  "$SPEC_FILE"

APP_BUNDLE="$DIST_DIR/Animica Miner GUI.app"
if [[ ! -d "$APP_BUNDLE" ]]; then
  FOUND="$(find "$DIST_DIR" -maxdepth 3 -name "Animica Miner GUI.app" -type d -print -quit || true)"
  if [[ -n "$FOUND" ]]; then
    APP_BUNDLE="$FOUND"
  else
    die "Failed to create app bundle. Look in: $DIST_DIR"
  fi
fi

log "App bundle created successfully at: $APP_BUNDLE"

# ---- Create DMG ----
DMG_NAME="Animica-Miner-GUI-${VERSION}-macOS-$(uname -m).dmg"
DMG_PATH="$DIST_DIR/$DMG_NAME"

log "Creating DMG installer..."
hdiutil create -volname "Animica Miner GUI" \
  -srcfolder "$APP_BUNDLE" \
  -ov -format UDZO \
  "$DMG_PATH"

log "✅ Build completed successfully!"
log "Artifacts:"
log "  App Bundle: $APP_BUNDLE"
log "  DMG:        $DMG_PATH"
log "Test:"
log "  open \"$APP_BUNDLE\""
