"""
Entry point for the Animica Internet desktop browser.

Registers the anm:// scheme BEFORE the QApplication (a hard Qt requirement), then opens the
browser window. `--smoke` constructs the app and exits 0 (used by CI on offscreen runners).
"""

from __future__ import annotations

import sys

from .scheme import register_anm_scheme


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    smoke = "--smoke" in argv

    # MUST run before QApplication is constructed.
    register_anm_scheme()

    from PySide6.QtWidgets import QApplication
    from .app import MainWindow
    from .config import APP_ID, APP_NAME

    app = QApplication([a for a in argv if a != "--smoke"])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationDomain("animica.org")
    app.setDesktopFileName(APP_ID)

    win = MainWindow()
    if smoke:
        # Build-verification path: proved imports, scheme registration and window construction.
        print("animica-internet smoke: OK")
        return 0
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
