"""Single instance enforcement for the GUI application."""

import logging
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)


class SingleInstanceGuard(QObject):
    """Ensures only one instance of the application runs at a time.
    
    Uses Qt's QLocalServer/QLocalSocket for cross-platform single-instance
    enforcement. If another instance is already running, sends it a "raise"
    message and exits immediately.
    """
    
    raise_requested = Signal()
    
    def __init__(self, app_id: str = "animica.miner-gui"):
        """Initialize the single instance guard.
        
        Args:
            app_id: Unique identifier for this application
        """
        super().__init__()
        self.app_id = app_id
        self.server: Optional[QLocalServer] = None
        self.is_primary = False
    
    def check_and_acquire(self) -> bool:
        """Check if another instance is running and acquire the lock.
        
        Returns:
            True if this is the primary instance (no other running),
            False if another instance is already running
        """
        # Try to connect to existing instance
        socket = QLocalSocket()
        socket.connectToServer(self.app_id)
        
        if socket.waitForConnected(500):
            # Another instance is running
            logger.info("Another instance is already running, sending raise signal")
            socket.write(b"raise")
            socket.waitForBytesWritten(1000)
            socket.disconnectFromServer()
            return False
        
        # No other instance running, create server
        self.server = QLocalServer()
        
        # Remove any stale server (from crashed previous instance)
        QLocalServer.removeServer(self.app_id)
        
        if not self.server.listen(self.app_id):
            logger.error(f"Failed to create local server: {self.server.errorString()}")
            return False
        
        # Connect to handle new connections
        self.server.newConnection.connect(self._on_new_connection)
        
        self.is_primary = True
        logger.info("Acquired single instance lock")
        return True
    
    def _on_new_connection(self) -> None:
        """Handle incoming connection from another instance."""
        if not self.server:
            return
        
        socket = self.server.nextPendingConnection()
        if not socket:
            return
        
        # Read the message
        socket.waitForReadyRead(1000)
        data = socket.readAll().data()
        
        logger.info(f"Received message from another instance: {data}")
        
        if data == b"raise":
            # Another instance wants us to raise our window
            self.raise_requested.emit()
        
        socket.disconnectFromServer()
    
    def release(self) -> None:
        """Release the single instance lock."""
        if self.server:
            self.server.close()
            QLocalServer.removeServer(self.app_id)
            logger.info("Released single instance lock")
            self.is_primary = False
