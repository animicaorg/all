#!/usr/bin/env bash
# Build macOS executable for Animica Miner GUI
# Creates a standalone .app bundle and DMG installer
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
DIST_DIR="$APP_DIR/dist"
BUILD_DIR="$APP_DIR/build"
SPEC_FILE="$BUILD_DIR/animica-miner-gui-macos.spec"
PYI_WORK="$BUILD_DIR/pyinstaller-work"
RUNTIME_HOOK="$BUILD_DIR/qt_runtime_hook.py"

# ---- Platform checks ----
if [[ "$(uname -s)" != "Darwin" ]]; then
  die "This script must be run on macOS"
fi
command -v hdiutil >/dev/null 2>&1 || die "hdiutil not found (macOS tool)"

# ---- Choose python (prefer venv) ----
choose_python() {
  # Prefer active venv
  if [[ -n "${VIRTUAL_ENV:-}" ]] && [[ -x "${VIRTUAL_ENV}/bin/python3" ]]; then
    echo "${VIRTUAL_ENV}/bin/python3"
    return
  fi
  # Prefer repo-root .venv
  if [[ -x "${REPO_ROOT}/.venv/bin/python3" ]]; then
    echo "${REPO_ROOT}/.venv/bin/python3"
    return
  fi
  # Fallback
  command -v python3 >/dev/null 2>&1 || return 1
  echo "$(command -v python3)"
}
PY="$(choose_python)" || die "Python 3 not found"

PY_VERSION="$("$PY" --version 2>&1 | awk '{print $2}')"
log "Using Python $PY_VERSION ($PY)"

# ---- Clean previous builds ----
log "Cleaning previous builds..."
rm -rf "$DIST_DIR" "$BUILD_DIR"
mkdir -p "$DIST_DIR" "$BUILD_DIR" "$PYI_WORK"

# ---- Install build tooling ----
log "Installing PyInstaller tooling..."
"$PY" -m pip install --upgrade pip setuptools wheel
"$PY" -m pip install --upgrade pyinstaller pyinstaller-hooks-contrib

# ---- Install miner-gui deps ----
log "Installing miner-gui dependencies..."
"$PY" -m pip install -e "$APP_DIR"

# ---- Resolve version ----
VERSION="$("$PY" -c "import tomllib, pathlib; p=pathlib.Path('$APP_DIR')/'pyproject.toml'; print(tomllib.loads(p.read_bytes())['project']['version'])" 2>/dev/null || echo "0.1.0")"
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

# ---- Optional: disable UPX if not installed (UPX can break on macOS arm64) ----
UPX_ENABLED="False"
if command -v upx >/dev/null 2>&1; then
  UPX_ENABLED="True"
  log "UPX found; enabling UPX compression."
else
  log "UPX not found; disabling UPX (recommended on macOS arm64)."
fi

# ---- Runtime hook to ensure Qt plugin paths are set inside packaged app ----
cat > "$RUNTIME_HOOK" <<'PYEOF'
import os

def _fix_qt_plugin_paths():
    # Avoid breaking Qt discovery if user has these set weirdly (empty/invalid)
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

# ---- Create PyInstaller spec ----
log "Creating PyInstaller spec file..."
cat > "$SPEC_FILE" <<SPEC_EOF
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

# IMPORTANT:
# Spec lives in apps/miner-gui/build, so resolve paths relative to that.
SPEC_DIR = Path(__file__).resolve().parent
APP_DIR  = SPEC_DIR.parent  # apps/miner-gui
ENTRY    = Path(r"${ENTRY}").resolve()

block_cipher = None

logo = APP_DIR / "logo.png"
datas = []
if logo.exists():
    datas.append((str(logo), "."))

# Note: rely on PyInstaller's PySide6 hooks, but keep a few safety hiddenimports.
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

# ---- Build with PyInstaller (explicit dist/work paths so outputs are predictable) ----
log "Running PyInstaller..."
"$PY" -m PyInstaller --noconfirm --clean \
  --distpath "$DIST_DIR" \
  --workpath "$PYI_WORK" \
  "$SPEC_FILE"

APP_BUNDLE="$DIST_DIR/Animica Miner GUI.app"
if [[ ! -d "$APP_BUNDLE" ]]; then
  # Sometimes PyInstaller nests under a folder; try to locate it.
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
