"""QApplication bootstrap, global exception handler, and theme defaults."""

from __future__ import annotations

import logging
import sys
import traceback
from typing import Type

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from animica_studio import __app_name__, __org_name__, __version__
from animica_studio.storage.config import load_config
from animica_studio.util.logging import setup_logging
from animica_studio.util.paths import logs_dir

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stylesheet — minimal, no external theme libs
# ---------------------------------------------------------------------------

_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-family: "Segoe UI", "SF Pro Text", "Ubuntu", sans-serif;
    font-size: 13px;
}

QFrame#headerBar {
    background-color: #181825;
    border-bottom: 1px solid #313244;
}

QLabel#headerTitle {
    color: #cba6f7;
    font-size: 15px;
    font-weight: bold;
}

QLabel#headerMeta {
    color: #a6adc8;
    font-size: 12px;
}

QLabel#headerSep {
    color: #45475a;
}

QFrame#sidebar {
    background-color: #181825;
    border-right: 1px solid #313244;
}

QPushButton#navButton {
    background-color: transparent;
    color: #cdd6f4;
    text-align: left;
    padding: 10px 16px;
    border: none;
    border-radius: 6px;
    font-size: 13px;
}

QPushButton#navButton:hover {
    background-color: #313244;
}

QPushButton#navButton:checked {
    background-color: #45475a;
    color: #cba6f7;
    font-weight: bold;
}

QLabel#placeholderLabel {
    color: #6c7086;
    font-size: 18px;
}
"""


# ---------------------------------------------------------------------------
# Global exception hook
# ---------------------------------------------------------------------------


def _exception_hook(
    exc_type: Type[BaseException],
    exc_value: BaseException,
    exc_tb: object,
) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log.critical("Unhandled exception:\n%s", tb_str)

    app = QApplication.instance()
    if app is not None:
        msg = QMessageBox()
        msg.setWindowTitle("Unexpected Error")
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.setText(
            f"<b>An unexpected error occurred.</b><br><br>"
            f"<code>{exc_type.__name__}: {exc_value}</code>"
        )
        msg.setDetailedText(tb_str)
        msg.exec()


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def _create_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    app.setApplicationName(__app_name__)
    app.setOrganizationName(__org_name__)
    app.setApplicationVersion(__version__)
    app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    app.setStyleSheet(_STYLESHEET)
    return app  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Bootstrap and run the Animica Studio application."""
    # Logging must be set up before anything else
    setup_logging(logs_dir(), app_version=__version__)
    log.info("Starting %s v%s", __app_name__, __version__)

    # Install global exception hook
    sys.excepthook = _exception_hook  # type: ignore[assignment]

    # Load config (creates defaults on first run)
    config = load_config()

    # Create application
    app = _create_app()

    # Import here so Qt is already initialised
    from animica_studio.ui.main_window import MainWindow  # noqa: PLC0415

    window = MainWindow(config)
    window.show()

    log.info("Application window shown")
    exit_code = app.exec()
    log.info("Application exiting with code %d", exit_code)
    sys.exit(exit_code)
