from __future__ import annotations

import asyncio
import logging
import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from animica_qt_wallet.core.logging import setup_logging
from animica_qt_wallet.ui.main_window import MainWindow


def run() -> int:
    QCoreApplication.setOrganizationName("Animica")
    QCoreApplication.setOrganizationDomain("animica.io")
    QCoreApplication.setApplicationName("Animica Qt Wallet")

    app = QApplication(sys.argv)
    log_path = setup_logging()
    logging.getLogger(__name__).info("App data logging at %s", log_path)

    window = MainWindow()
    window.show()

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    app.aboutToQuit.connect(loop.stop)

    with loop:
        loop.run_forever()

    return 0


if __name__ == "__main__":
    raise SystemExit(run())
