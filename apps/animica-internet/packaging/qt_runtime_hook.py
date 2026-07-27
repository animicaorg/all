"""PyInstaller runtime hook for Animica Internet (PySide6 + QtWebEngine).

Mirrors apps/miner-gui/build-scripts/qt_runtime_hook.py (Qt platform-plugin
path repair) and adds the QtWebEngine pieces that browser bundles need:

1. Qt platform plugins — without QT_PLUGIN_PATH pointing into the bundle,
   PySide6 apps fail at startup with:
       qt.qpa.plugin: Could not find the Qt platform plugin "xcb"/"cocoa"/"windows"

2. QtWebEngineProcess — Chromium runs as a separate helper executable. In a
   frozen app Qt often looks for it in the wrong place; we locate the bundled
   helper and export QTWEBENGINEPROCESS_PATH.

3. Resources + locales — the .pak resource files and qtwebengine_locales
   translations; missing paths produce a blank white webview.

4. Linux sandbox — the Chromium setuid/userns sandbox cannot work from inside
   a PyInstaller onedir/AppImage (the helper is not setuid and Ubuntu 24.04+
   restricts unprivileged user namespaces), so we default to disabling it.
   Users can re-enable by exporting QTWEBENGINE_DISABLE_SANDBOX=0 before launch.

Everything is best-effort and uses os.environ.setdefault so explicit user
overrides always win.
"""

import os
import sys


def _fix_qt_plugin_paths() -> None:
    # Drop empty plugin-path env vars that would otherwise shadow our values.
    for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        if key in os.environ and not os.environ[key].strip():
            os.environ.pop(key, None)

    try:
        from PySide6.QtCore import QLibraryInfo

        try:
            plugins = QLibraryInfo.path(QLibraryInfo.PluginsPath)
        except AttributeError:  # PySide6 < 6.3 fallback
            plugins = QLibraryInfo.location(QLibraryInfo.PluginsPath)

        if plugins:
            os.environ.setdefault("QT_PLUGIN_PATH", plugins)
            os.environ.setdefault(
                "QT_QPA_PLATFORM_PLUGIN_PATH", os.path.join(plugins, "platforms")
            )
    except Exception:
        # Best-effort only; the app may still find plugins via its own logic.
        pass


def _fix_qtwebengine_paths() -> None:
    if not getattr(sys, "frozen", False):
        return
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return

    pyside_dir = os.path.join(base, "PySide6")
    qt_dir = os.path.join(pyside_dir, "Qt")

    # --- QtWebEngineProcess helper -------------------------------------
    if sys.platform.startswith("win"):
        candidates = [
            os.path.join(pyside_dir, "QtWebEngineProcess.exe"),
            os.path.join(qt_dir, "bin", "QtWebEngineProcess.exe"),
            os.path.join(base, "QtWebEngineProcess.exe"),
        ]
    elif sys.platform == "darwin":
        candidates = [
            os.path.join(
                qt_dir,
                "lib",
                "QtWebEngineCore.framework",
                "Helpers",
                "QtWebEngineProcess.app",
                "Contents",
                "MacOS",
                "QtWebEngineProcess",
            ),
            os.path.join(pyside_dir, "QtWebEngineProcess"),
            os.path.join(base, "QtWebEngineProcess"),
        ]
    else:  # Linux and friends
        candidates = [
            os.path.join(qt_dir, "libexec", "QtWebEngineProcess"),
            os.path.join(pyside_dir, "QtWebEngineProcess"),
            os.path.join(base, "QtWebEngineProcess"),
        ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            os.environ.setdefault("QTWEBENGINEPROCESS_PATH", candidate)
            break

    # --- Chromium resources (.pak, icudtl.dat) + locales ----------------
    for res_dir in (
        os.path.join(qt_dir, "resources"),
        os.path.join(pyside_dir, "resources"),
    ):
        if os.path.isdir(res_dir):
            os.environ.setdefault("QTWEBENGINE_RESOURCES_PATH", res_dir)
            break

    for loc_dir in (
        os.path.join(qt_dir, "translations", "qtwebengine_locales"),
        os.path.join(pyside_dir, "translations", "qtwebengine_locales"),
    ):
        if os.path.isdir(loc_dir):
            os.environ.setdefault("QTWEBENGINE_LOCALES_PATH", loc_dir)
            break

    # --- Linux: Chromium sandbox cannot run from a frozen bundle --------
    if sys.platform.startswith("linux"):
        os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")


_fix_qt_plugin_paths()
_fix_qtwebengine_paths()
