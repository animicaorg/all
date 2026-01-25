"""Local node lifecycle controller for the GUI."""

from __future__ import annotations

import logging
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from animica_miner_gui.backend.app_paths import (
    ensure_dirs,
    get_node_data_dir,
    get_node_logs_dir,
    get_node_manifest_path,
    get_node_token_path,
    is_frozen,
)
from animica_miner_gui.backend.manifest import load_manifest, verify_manifest
from animica_miner_gui.backend.node_paths import resolve_node_executable
from animica_miner_gui.backend.rpc_client import RPCClient, RPCError

logger = logging.getLogger(__name__)


class NodeController(QObject):
    """Manage the bundled Animica node process."""

    nodeStarting = Signal()
    nodeStarted = Signal(str, str)
    nodeFailed = Signal(str)
    rpcChanged = Signal(str, str)
    statusUpdated = Signal(dict)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._process: Optional[subprocess.Popen] = None
        self._stdout_handle: Optional[Any] = None
        self._stderr_handle: Optional[Any] = None
        self._rpc_url: Optional[str] = None
        self._token: Optional[str] = None
        self._start_time: Optional[float] = None
        self._rpc_client: Optional[RPCClient] = None
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(5000)
        self._status_timer.timeout.connect(self._poll_status)

    def start(self) -> None:
        """Start the bundled node."""
        if self._process and self._process.poll() is None:
            return

        self.nodeStarting.emit()
        node_paths = resolve_node_executable()
        node_exe = node_paths.exe_path
        node_root = node_paths.base_dir
        logger.info("node_resolve_mode=%s", node_paths.mode)
        logger.info("node_resolve_reason=%s", node_paths.reason)

        if not node_exe or not node_exe.exists() or not node_root:
            self.nodeFailed.emit(f"{node_paths.reason}")
            return

        manifest_path = get_node_manifest_path()
        if manifest_path.exists():
            try:
                manifest = load_manifest(manifest_path)
                errors = verify_manifest(node_root, manifest)
                if errors:
                    self.nodeFailed.emit("Node bundle missing/broken. " + "; ".join(errors))
                    return
            except Exception as exc:
                self.nodeFailed.emit(f"Failed to verify node bundle: {exc}")
                return
        elif is_frozen():
            self.nodeFailed.emit("Node bundle missing/broken. Missing manifest.")
            return

        token_path = get_node_token_path()
        ensure_dirs(token_path.parent, get_node_data_dir(), get_node_logs_dir())
        token = token_path.read_text(encoding="utf-8").strip() if token_path.exists() else ""
        if not token:
            token = secrets.token_hex(32)
            token_path.write_text(token, encoding="utf-8")

        port = self._pick_port(8545)
        rpc_url = f"http://127.0.0.1:{port}/rpc"
        logger.info("selected_rpc_port=%s", port)
        logger.info("final_rpc_url=%s", rpc_url)

        env = os.environ.copy()
        env["ANIMICA_RPC_PORT"] = str(port)
        env["ANIMICA_RPC_ADMIN_TOKEN"] = token
        env["ANIMICA_DATA_DIR"] = str(get_node_data_dir())
        env["ANIMICA_LOGS_DIR"] = str(get_node_logs_dir())

        try:
            stdout_path = get_node_logs_dir() / "node-stdout.log"
            stderr_path = get_node_logs_dir() / "node-stderr.log"
            self._stdout_handle = open(stdout_path, "ab")
            self._stderr_handle = open(stderr_path, "ab")
            self._process = subprocess.Popen(
                [str(node_exe)],
                cwd=str(get_node_data_dir()),
                env=env,
                stdout=self._stdout_handle,
                stderr=self._stderr_handle,
            )
        except Exception as exc:
            self.nodeFailed.emit(f"Failed to start node: {exc}")
            return

        self._rpc_url = rpc_url
        self._token = token
        self._rpc_client = RPCClient(rpc_url, token=token)
        self._start_time = time.time()

        if not self._wait_for_rpc():
            self.stop()
            self.nodeFailed.emit("Node failed to respond on RPC")
            return

        self.rpcChanged.emit(rpc_url, token)
        self.nodeStarted.emit(rpc_url, token)
        self._status_timer.start()

    def stop(self) -> None:
        """Stop the node process."""
        self._status_timer.stop()
        if not self._process or self._process.poll() is not None:
            self._rpc_client = None
            self._rpc_url = None
            self._token = None
            self._start_time = None
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._process.kill()
        if self._stdout_handle:
            self._stdout_handle.close()
            self._stdout_handle = None
        if self._stderr_handle:
            self._stderr_handle.close()
            self._stderr_handle = None
        self._process = None
        self._rpc_client = None
        self._rpc_url = None
        self._token = None
        self._start_time = None

    def _pick_port(self, preferred: int) -> int:
        if not self._port_in_use(preferred):
            return preferred
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _port_in_use(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                return True
        return False

    def _wait_for_rpc(self) -> bool:
        deadline = time.time() + 30
        while time.time() < deadline:
            if not self._rpc_client:
                return False
            try:
                methods = self._rpc_client.get_rpc_methods()
                if methods is not None:
                    return True
            except Exception:
                time.sleep(1)
        return False

    def _poll_status(self) -> None:
        if not self._rpc_client:
            return
        status: Dict[str, Any] = {
            "rpc_url": self._rpc_url,
            "pid": self._process.pid if self._process else None,
            "port": int(self._rpc_url.split(":")[2].split("/")[0]) if self._rpc_url else None,
            "uptime": time.time() - self._start_time if self._start_time else 0,
            "logs_dir": str(get_node_logs_dir()),
        }
        try:
            head = self._rpc_client.get_chain_head()
            status["head"] = head
        except RPCError as exc:
            status["head_error"] = str(exc)

        try:
            status["sync"] = self._rpc_client.get_sync_status()
        except RPCError as exc:
            status["sync_error"] = str(exc)

        try:
            status["peers"] = self._rpc_client.get_peer_summary()
        except RPCError as exc:
            status["peers_error"] = str(exc)

        status["last_log_line"] = self._tail_node_log_line()

        self.statusUpdated.emit(status)

    def _tail_node_log_line(self) -> Optional[str]:
        log_dir = get_node_logs_dir()
        if not log_dir.exists():
            return None
        candidates = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            return None
        log_path = candidates[0]
        try:
            with log_path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                offset = max(size - 4096, 0)
                handle.seek(offset)
                chunk = handle.read().decode("utf-8", errors="replace")
                lines = [line for line in chunk.splitlines() if line.strip()]
                return lines[-1] if lines else None
        except Exception as exc:
            logger.debug("Failed to read node log line: %s", exc)
            return None
