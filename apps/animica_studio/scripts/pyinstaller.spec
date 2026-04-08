# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Animica Studio desktop application."""
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # repo root of animica_studio package

a = Analysis(
    [str(ROOT / "animica_studio" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Include packaged web assets, UI resources, and token/templates that Studio loads at runtime.
        (str(ROOT / "animica_studio" / "ui" / "web"), "animica_studio/ui/web"),
        (str(ROOT / "animica_studio" / "ui" / "resources"), "animica_studio/ui/resources"),
        (str(ROOT / "animica_studio" / "templates"), "animica_studio/templates"),
        (str(ROOT / "animica_studio" / "resources" / "templates"), "animica_studio/resources/templates"),
    ],
    hiddenimports=[
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebChannel",
        "animica_studio.ui.pages.ide_page",
        "animica_studio.ui.pages.console_page",
        "animica_studio.services.ide_bridge",
        "animica_studio.services.ide_service",
        "animica_studio.services.console_service",
        "animica_studio.services.deterministic_runner",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AnimicaStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI application; no terminal window on Windows
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AnimicaStudio",
)

# macOS .app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="AnimicaStudio.app",
        icon=None,
        bundle_identifier="org.animica.studio",
    )
