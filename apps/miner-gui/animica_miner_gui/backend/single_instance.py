"""Single-instance helper for the GUI."""

from __future__ import annotations

import logging
import os
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QAbstractSocket, QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)


class SingleInstance(QObject):
    """Ensure only one instance of the GUI runs."""

    raiseRequested = Signal()

    def __init__(self, key: str, parent: Optional[QObject] = None):
        super().__init__(parent)
        # Namespace the key per user: on a shared Unix host the socket lives in
        # a common runtime dir, so a second user's GUI would see "already
        # running", exit 0 and appear to do nothing.
        try:
            self.key = f"{key}-{os.getuid()}"
        except AttributeError:  # Windows has no getuid
            self.key = f"{key}-{os.environ.get('USERNAME', 'user')}"
        self._degraded = False
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_connection)

    def start(self) -> bool:
        """Claim the single-instance lock. False means another instance owns it.

        ``QLocalServer.removeServer`` is a crash-recovery tool: on Unix it just
        unlinks the socket file without asking whether anyone is listening. The
        original code called it on *any* listen failure, so a second process
        would delete a live instance's socket, successfully listen, and return
        True — two full GUIs, and the original's IPC endpoint destroyed.

        Probe first, and re-probe after removing a socket we believed was
        stale: two processes starting together can both see "nobody home", and
        without the second check both would claim the lock and reproduce the
        very bug this guards against.
        """
        if self.server.listen(self.key):
            return True

        if self.server.serverError() != QAbstractSocket.SocketError.AddressInUseError:
            # Not "already running" — a genuine failure (bad permissions, path
            # too long, sandbox). Returning False here made main() treat it as
            # "another instance owns it" and exit 0 with no window and no
            # message. Run without the guard instead; a duplicate window is far
            # better than an app that silently refuses to start.
            logger.warning(
                "single-instance guard unavailable (%s); continuing without it",
                self.server.errorString(),
            )
            self._degraded = True
            return True

        if self._owner_is_alive():
            logger.info("Another instance already holds the lock.")
            return False

        logger.info("Stale single-instance socket found; reclaiming it.")
        QLocalServer.removeServer(self.key)
        if self.server.listen(self.key):
            return True

        # Someone beat us to it between the probe and the listen.
        if self._owner_is_alive():
            logger.info("Lost the race to reclaim the socket; another instance won.")
            return False
        logger.warning(
            "could not claim the single-instance socket (%s); continuing without it",
            self.server.errorString(),
        )
        self._degraded = True
        return True

    def _owner_is_alive(self) -> bool:
        """True if something is accepting connections on our key."""
        probe = QLocalSocket()
        try:
            probe.connectToServer(self.key)
            return probe.waitForConnected(500)
        finally:
            # abort() drops it immediately; without this the owner keeps a
            # half-open QLocalSocket per probe for its whole lifetime.
            probe.abort()
            probe.close()

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
