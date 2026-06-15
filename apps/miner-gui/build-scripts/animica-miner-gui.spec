# -*- mode: python ; coding: utf-8 -*-
#
# Unified, cross-platform PyInstaller spec for the Animica Miner GUI.
#
# This single spec is consumed by all three per-OS build scripts:
#   - build_linux.sh        (onedir on Linux)
#   - build_macos.sh        (onedir -> .app BUNDLE on macOS)
#   - build_windows.sh      (onedir on Windows; also used under Wine)
#
# It produces a *windowed* (no-console) GUI application named "AnimicaMiner".
# On macOS it additionally emits an "AnimicaMiner.app" bundle via BUNDLE().
#
# PyInstaller does NOT guarantee that __file__ is defined inside a spec's exec
# namespace, so we locate the spec directory defensively and allow the build
# scripts to override paths through environment variables:
#
#   ANIMICA_GUI_SPEC_DIR   - directory containing this spec (build-scripts/)
#   ANIMICA_GUI_APP_DIR    - apps/miner-gui
#   ANIMICA_GUI_REPO_ROOT  - repository root
#   ANIMICA_GUI_ENTRY      - entry script (defaults to animica_miner_gui/main.py)
#   ANIMICA_GUI_NAME       - output name (defaults to "AnimicaMiner")
#   ANIMICA_GUI_UPX        - "1"/"0" toggle for UPX (default off; safest cross-platform)
#   ANIMICA_GUI_VERSION    - version string used for the macOS bundle Info.plist
#
import os
import sys
from pathlib import Path


def _resolve_spec_dir() -> Path:
    # 1. Explicit override from the build script.
    env_dir = os.environ.get("ANIMICA_GUI_SPEC_DIR")
    if env_dir:
        return Path(env_dir).resolve()
    # 2. __file__ if PyInstaller happened to provide it.
    try:
        return Path(__file__).resolve().parent  # type: ignore[name-defined]
    except NameError:
        pass
    # 3. PyInstaller exposes the spec path via sys.argv on most versions.
    for arg in sys.argv:
        if arg.endswith(".spec"):
            return Path(arg).resolve().parent
    # 4. Last resort: current working directory.
    return Path(os.getcwd()).resolve()


SPEC_DIR = _resolve_spec_dir()
APP_DIR = Path(os.environ.get("ANIMICA_GUI_APP_DIR", str(SPEC_DIR.parent))).resolve()
REPO_ROOT = Path(
    os.environ.get("ANIMICA_GUI_REPO_ROOT", str(APP_DIR.parent.parent))
).resolve()

ENTRY = Path(
    os.environ.get(
        "ANIMICA_GUI_ENTRY", str(APP_DIR / "animica_miner_gui" / "main.py")
    )
).resolve()

APP_NAME = os.environ.get("ANIMICA_GUI_NAME", "AnimicaMiner")
APP_VERSION = os.environ.get("ANIMICA_GUI_VERSION", "0.1.0")
USE_UPX = os.environ.get("ANIMICA_GUI_UPX", "0") == "1"

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = sys.platform.startswith("win")

print(f"[spec] APP_NAME   = {APP_NAME}")
print(f"[spec] APP_DIR    = {APP_DIR}")
print(f"[spec] REPO_ROOT  = {REPO_ROOT}")
print(f"[spec] ENTRY      = {ENTRY}")
print(f"[spec] platform   = {sys.platform}  upx={USE_UPX}")

if not ENTRY.exists():
    raise SystemExit(f"[spec] entry script not found: {ENTRY}")

block_cipher = None

# ---------------------------------------------------------------------------
# Data files: logo + JSON schemas bundled with the GUI.
# ---------------------------------------------------------------------------
datas = []

logo = APP_DIR / "logo.png"
if logo.exists():
    datas.append((str(logo), "."))

schemas_dir = APP_DIR / "animica_miner_gui" / "ide" / "toolchain" / "schemas"
if schemas_dir.exists():
    for schema in ("manifest.schema.json", "abi.schema.json"):
        schema_path = schemas_dir / schema
        if schema_path.exists():
            datas.append(
                (str(schema_path), "animica_miner_gui/ide/toolchain/schemas")
            )

# Collect Qt plugins so the packaged app can find its platform plugin.
try:
    from PyInstaller.utils.hooks.qt import collect_qt_plugins

    datas += collect_qt_plugins("PySide6")
except Exception as exc:  # pragma: no cover - depends on PyInstaller version
    print(f"[spec] warning: collect_qt_plugins unavailable: {exc}")

# ---------------------------------------------------------------------------
# Hidden imports: PySide6, matplotlib, pydantic, httpx, jsonschema, and the
# bundled `animica` unified-flow package plus the miner-gui sub-packages.
# ---------------------------------------------------------------------------
hiddenimports = [
    # Qt / PySide6
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
    "shiboken6",
    # Plotting
    "matplotlib",
    "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_qt5agg",
    # Core deps
    "pydantic",
    "httpx",
    "jsonschema",
    # Animica unified flow (bundled `animica` distribution)
    "animica",
    "animica.unified",
    "animica.cli",
    "animica.cli.main",
    "animica.cli.gui",
    "animica.stratum_pool",
    "animica.stratum_pool.package_builder",
    # Optional omni SDK / VM (present in the monorepo)
    "omni_sdk",
    "omni_sdk.contracts.deployer",
    "omni_sdk.tx",
    "omni_sdk.tx.encode",
    "omni_sdk.wallet.signer",
    "vm_py",
    "vm_py.abi",
    "vm_py.compiler",
    "vm_py.runtime",
    "vm_py.stdlib",
    # GUI sub-packages that are imported lazily.
    "animica_miner_gui.ide",
    "animica_miner_gui.ide.git_integration",
    "animica_miner_gui.ide.git_panel",
    "animica_miner_gui.ide.toolchain.preflight",
    "animica_miner_gui.packaging.verify",
]

# Pull in the whole `animica` package tree so subprocess launches work offline.
try:
    from PyInstaller.utils.hooks import collect_submodules

    hiddenimports += collect_submodules("animica")
except Exception as exc:  # pragma: no cover
    print(f"[spec] warning: collect_submodules('animica') failed: {exc}")

# De-duplicate while preserving order.
hiddenimports = list(dict.fromkeys(hiddenimports))

# ---------------------------------------------------------------------------
# Runtime hook: make Qt locate its platform plugins inside the bundle.
# ---------------------------------------------------------------------------
runtime_hooks = []
qt_hook = SPEC_DIR / "qt_runtime_hook.py"
if qt_hook.exists():
    runtime_hooks.append(str(qt_hook))

a = Analysis(
    [str(ENTRY)],
    pathex=[str(APP_DIR), str(REPO_ROOT), str(REPO_ROOT / "python")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=runtime_hooks,
    excludes=[
        "tkinter",
        "test",
        "tests",
        "unittest",
        "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# On Windows attach an icon if one is present.
exe_icon = None
ico = APP_DIR / "icon.ico"
icns = APP_DIR / "icon.icns"
if IS_WINDOWS and ico.exists():
    exe_icon = str(ico)
elif IS_MACOS and icns.exists():
    exe_icon = str(icns)

# onedir layout: EXE has exclude_binaries=True and COLLECT gathers everything.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=USE_UPX,
    console=False,  # windowed app (no console)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=exe_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=USE_UPX,
    upx_exclude=[],
    name=APP_NAME,
)

# macOS: wrap the onedir COLLECT into an .app bundle.
if IS_MACOS:
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=str(icns) if icns.exists() else None,
        bundle_identifier="org.animica.miner-gui",
        version=APP_VERSION,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": "Animica Miner",
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "10.15",
            "NSRequiresAquaSystemAppearance": False,
            "CFBundlePackageType": "APPL",
            "LSApplicationCategoryType": "public.app-category.finance",
        },
    )
