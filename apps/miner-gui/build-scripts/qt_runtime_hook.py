"""PyInstaller runtime hook for the Animica Miner GUI.

Ensures Qt can locate its platform plugins (the "platforms" plugin, e.g. the
xcb/cocoa/windows plugin) inside the frozen onedir/.app bundle. Without this,
PySide6 apps frequently fail at startup with:

    qt.qpa.plugin: Could not find the Qt platform plugin "xcb"/"cocoa"/"windows"
"""

import os


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


_fix_qt_plugin_paths()
