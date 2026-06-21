"""Application bootstrap for Animica Studio Qt.

This module must remain importable even before the UI agents have finished
implementing :class:`~animica_studio.ui.main_window.MainWindow`; therefore the
MainWindow import happens lazily *inside* :func:`main`, never at module scope.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Sequence

from . import __app_name__, __org_domain__, __org_name__, __version__
from .config import Paths, Settings

_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_STYLES_QSS = _ASSETS_DIR / "styles.qss"
_ICON_PNG = _ASSETS_DIR / "icon.png"


def load_stylesheet() -> str:
    """Return the application QSS, or an empty string if unavailable."""
    try:
        return _STYLES_QSS.read_text(encoding="utf-8")
    except OSError:
        return ""


def _build_icon():
    """Return a QIcon for the app: the bundled PNG if present, else a drawn fallback."""
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

    if _ICON_PNG.exists():
        return QIcon(str(_ICON_PNG))

    # Draw a simple teal "A" mark so the app always has an icon.
    pix = QPixmap(64, 64)
    pix.fill(QColor("#050811"))
    painter = QPainter(pix)
    try:
        painter.setPen(QColor("#16d9c3"))
        font = painter.font()
        font.setBold(True)
        font.setPointSize(34)
        painter.setFont(font)
        painter.drawText(pix.rect(), 0x84, "A")  # Qt.AlignCenter == 0x84
    finally:
        painter.end()
    return QIcon(pix)


class AnimicaStudioApp:
    """Thin owner of the QApplication, Settings, Database, and MainWindow."""

    def __init__(self, argv: Optional[Sequence[str]] = None) -> None:
        from PySide6.QtWidgets import QApplication

        self.paths: Paths = Paths.resolve().ensure()
        self.qapp = QApplication.instance() or QApplication(list(argv or sys.argv))
        self.qapp.setApplicationName(__app_name__)
        self.qapp.setApplicationDisplayName(__app_name__)
        self.qapp.setApplicationVersion(__version__)
        self.qapp.setOrganizationName(__org_name__)
        self.qapp.setOrganizationDomain(__org_domain__)
        self.qapp.setWindowIcon(_build_icon())

        qss = load_stylesheet()
        if qss:
            self.qapp.setStyleSheet(qss)

        self.settings: Settings = Settings.load(self.paths)
        self.db = None  # set up in run()
        self.window = None

    def _ensure_db(self):
        from .db import Database

        if self.db is None:
            self.db = Database(self.paths.db_path)
        return self.db

    def _build_agent_engine(self):
        """Construct the AgentEngine if available; tolerate absence during early dev."""
        try:
            from .agent.engine import AgentEngine
        except Exception:
            return None
        try:
            return AgentEngine(settings=self.settings, db=self.db)
        except Exception:
            return None

    def run(self) -> int:
        from .ui.main_window import MainWindow  # lazy: UI may be built by another agent

        self._ensure_db()
        agent_engine = self._build_agent_engine()
        self.window = MainWindow(db=self.db, settings=self.settings, agent_engine=agent_engine)
        self.window.show()
        try:
            return self.qapp.exec()
        finally:
            if self.db is not None:
                self.db.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Application entrypoint. Returns a process exit code."""
    app = AnimicaStudioApp(argv)
    return app.run()


__all__ = ["AnimicaStudioApp", "main", "load_stylesheet"]
