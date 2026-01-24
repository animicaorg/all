"""Single-instance helper for the GUI."""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)


class SingleInstance(QObject):
    """Ensure only one instance of the GUI runs."""

    raiseRequested = Signal()

    def __init__(self, key: str, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.key = key
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_connection)

    def start(self) -> bool:
        if self.server.listen(self.key):
            return True
        if self.server.serverError():
            QLocalServer.removeServer(self.key)
            if self.server.listen(self.key):
                return True
        return False

    def notify_existing(self) -> None:
        socket = QLocalSocket()
        socket.connectToServer(self.key)
        if socket.waitForConnected(1000):
            socket.write(b"raise")
            socket.flush()
            socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()

    def _on_connection(self) -> None:
        socket = self.server.nextPendingConnection()
        if socket is None:
            return
        socket.readyRead.connect(lambda: self._handle_message(socket))

    def _handle_message(self, socket: QLocalSocket) -> None:
        _ = socket.readAll()
        self.raiseRequested.emit()
        socket.disconnectFromServer()
