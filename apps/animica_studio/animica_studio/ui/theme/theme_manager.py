from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from animica_studio.storage.config import Config, save_config
from animica_studio.ui.theme.palette import ThemePalette, build_palette


class ThemeManager(QObject):
    theme_changed = Signal(object)

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config
        self._prefs = config.wallet_settings.setdefault("ui_theme", {})
        self._prefs.setdefault("mode", "dark")
        self._prefs.setdefault("accent", "#5b8cff")
        self._prefs.setdefault("reduced_motion", False)
        self._prefs.setdefault("visual_effects", "balanced")

    def palette(self) -> ThemePalette:
        return build_palette(self._prefs.get("mode", "dark"), self._prefs.get("accent", "#5b8cff"))

    def mode(self) -> str:
        return str(self._prefs.get("mode", "dark"))

    def set_mode(self, mode: str) -> None:
        self._prefs["mode"] = "light" if mode == "light" else "dark"
        save_config(self._config)
        self.theme_changed.emit(self.palette())

    def set_accent(self, accent: str) -> None:
        self._prefs["accent"] = accent
        save_config(self._config)
        self.theme_changed.emit(self.palette())

    def reduced_motion(self) -> bool:
        return bool(self._prefs.get("reduced_motion", False))

    def set_reduced_motion(self, value: bool) -> None:
        self._prefs["reduced_motion"] = bool(value)
        save_config(self._config)

    def visual_effects(self) -> str:
        return str(self._prefs.get("visual_effects", "balanced"))

    def set_visual_effects(self, value: str) -> None:
        self._prefs["visual_effects"] = value if value in {"off", "balanced", "high"} else "balanced"
        save_config(self._config)
