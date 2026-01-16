"""Backend coordinator for local node readiness and RPC access."""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from animica_miner_gui.core.localnode import LocalNodeManager, LocalRpcClient

logger = logging.getLogger(__name__)


class NodeStartWorker(QObject):
    """Worker to start the local node without blocking the UI thread."""

    ready = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, node_manager: LocalNodeManager):
        super().__init__()
        self.node_manager = node_manager

    @Slot()
    def run(self) -> None:
        try:
            status = self.node_manager.start()
            if status.is_ready:
                rpc = self.node_manager.get_rpc_client()
                if rpc is None:
                    self.error.emit("Node reported ready but RPC client is unavailable")
                else:
                    self.ready.emit(rpc)
            else:
                self.error.emit(status.error or "Node failed to become ready")
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished.emit()


class NodeBackend(QObject):
    """Coordinates node lifecycle and emits readiness signals for UI tabs."""

    nodeReady = Signal(object)
    nodeError = Signal(str)
    syncStatus = Signal(object)
    walletUpdated = Signal(object)
    rpcChanged = Signal(object)

    def __init__(self, node_manager: LocalNodeManager, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._node_manager = node_manager
        self._rpc_client: Optional[LocalRpcClient] = None
        self._start_thread: Optional[QThread] = None
        self._start_worker: Optional[NodeStartWorker] = None

        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(2000)
        self._sync_timer.timeout.connect(self._emit_sync_status)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._check_ready_state)
        self._status_timer.start()

    def ensureNodeRunning(self) -> None:
        """Ensure the local node is running and emit readiness when available."""
        if self.isReady():
            self._sync_timer.start()
            return

        if self._start_thread and self._start_thread.isRunning():
            return

        self._start_thread = QThread()
        self._start_worker = NodeStartWorker(self._node_manager)
        self._start_worker.moveToThread(self._start_thread)

        self._start_thread.started.connect(self._start_worker.run)
        self._start_worker.ready.connect(self._handle_ready)
        self._start_worker.error.connect(self._handle_error)
        self._start_worker.finished.connect(self._start_thread.quit)
        self._start_worker.finished.connect(self._start_worker.deleteLater)
        self._start_thread.finished.connect(self._cleanup_start_thread)

        self._start_thread.start()

    def getRpc(self) -> Optional[LocalRpcClient]:
        """Return the current local RPC client, if ready."""
        return self._rpc_client

    def isReady(self) -> bool:
        """Return True if the backend has a ready RPC client."""
        return self._rpc_client is not None

    def _cleanup_start_thread(self) -> None:
        if self._start_thread:
            self._start_thread.deleteLater()
        self._start_thread = None
        self._start_worker = None

    def _handle_ready(self, rpc_client: LocalRpcClient) -> None:
        self._set_rpc_client(rpc_client)

    def _handle_error(self, error: str) -> None:
        logger.error(f"Node backend error: {error}")
        self._rpc_client = None
        self._sync_timer.stop()
        self.nodeError.emit(error)
        self.rpcChanged.emit(None)

    def _set_rpc_client(self, rpc_client: LocalRpcClient) -> None:
        previous_url = self._rpc_client.rpc_url if self._rpc_client else None
        self._rpc_client = rpc_client
        if previous_url != rpc_client.rpc_url:
            logger.info("RPC client updated: %s", rpc_client.rpc_url)
        self.nodeReady.emit(rpc_client)
        self.rpcChanged.emit(rpc_client)
        self._sync_timer.start()
        self._emit_sync_status()

    def _emit_sync_status(self) -> None:
        if not self._rpc_client:
            return
        status = self._node_manager.get_sync_status()
        if status is None:
            return
        self.syncStatus.emit(status)

    def _check_ready_state(self) -> None:
        if self._rpc_client is None and self._node_manager.is_ready:
            rpc = self._node_manager.get_rpc_client()
            if rpc is not None:
                self._set_rpc_client(rpc)
            return

        if self._rpc_client is not None and not self._node_manager.is_ready:
            self._rpc_client = None
            self._sync_timer.stop()
            self.nodeError.emit("Node is not ready")
            self.rpcChanged.emit(None)
            return

        if self._rpc_client is not None and self._node_manager.is_ready:
            current_url = self._node_manager.rpc_url
            current_token = self._node_manager.auth_token
            if current_url and (
                current_url != self._rpc_client.rpc_url
                or current_token != self._rpc_client.auth_token
            ):
                rpc = self._node_manager.get_rpc_client()
                if rpc is not None:
                    self._set_rpc_client(rpc)
