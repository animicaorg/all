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

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

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

#: Qt marshals a Python int in a signal payload as a signed 64-bit qlonglong.
_INT64_MIN, _INT64_MAX = -(2 ** 63), 2 ** 63 - 1

#: How long to wait before retrying after failing to obtain any RPC endpoint.
_RETRY_SECONDS = 30


def qt_safe(value: Any) -> Any:
    """Make an RPC payload safe to send through ``Signal(dict)``.

    Chain data is full of uint64 quantities — chainwork, cumulative difficulty,
    hashes rendered as integers — that exceed the *signed* 64-bit range Qt
    uses. Emitting a dict containing even one of them raises::

        OverflowError: int too big to convert
        libshiboken: Overflow: Value 15081591973463719831 exceeds limits of
        type [signed] "x" (8bytes)

    and the emit fails **entirely**, so every connected slot receives nothing.
    That is a silent, total loss of the status feed — the dashboard simply
    never updates — rather than one bad field. Out-of-range integers become
    strings; everything else passes through untouched.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if _INT64_MIN <= value <= _INT64_MAX else str(value)
    if isinstance(value, dict):
        return {k: qt_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [qt_safe(v) for v in value]
    return value


class _StatusPollTask(QRunnable):
    """Runs one status refresh off the GUI thread."""

    def __init__(self, controller: "NodeController") -> None:
        super().__init__()
        self._controller = controller

    def run(self) -> None:  # pragma: no cover - thread body
        try:
            self._controller._collect_status()
        except RuntimeError as exc:
            # Controller destroyed mid-poll while the app was closing.
            logger.debug("status poll abandoned: %s", exc)
        except Exception:
            logger.exception("status poll failed")
        finally:
            try:
                self._controller._poll_in_flight = False
            except RuntimeError:
                pass


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
        #: True when serving a remote endpoint instead of a local node process.
        self._remote = False
        self._retry_timer: Optional[QTimer] = None
        #: Guards against piling up polls when the endpoint is slower than 5s.
        self._poll_in_flight = False
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(5000)
        self._status_timer.timeout.connect(self._poll_status)

    def _use_remote_rpc(self, reason: str) -> bool:
        """Fall back to the network's public RPC endpoint.

        Returns True if a usable remote endpoint was published. Verifies it
        actually answers before announcing it, so the UI is never told an
        endpoint is live when it is not.
        """
        from animica_miner_gui.backend.config import (
            DEFAULT_RPC_ENDPOINTS,
            NetworkType,
            load_config,
        )

        explicit: Optional[str] = None
        try:
            network = load_config().network
            explicit = network.rpc_url
            resolved = network.resolved_rpc_url()
            is_devnet = network.network_type == NetworkType.DEVNET
        except Exception:
            resolved = None
            is_devnet = False

        # Candidates in priority order. A devnet default points at localhost —
        # exactly the node we just failed to start — so it is not a candidate.
        candidates: list[str] = []
        if resolved and not is_devnet:
            candidates.append(resolved)
        # Last resort: the mainnet endpoint. This covers network types with no
        # default of their own (testnet and custom both resolve to None, which
        # otherwise reproduced the original "no endpoint at all" bug for anyone
        # who picked them) and devnet installs with no local node. Skipped when
        # the user set an explicit rpc_url — if they named an endpoint, silently
        # querying a different chain would be worse than failing.
        if not explicit:
            fallback = DEFAULT_RPC_ENDPOINTS.get(NetworkType.MAINNET)
            if fallback and fallback not in candidates:
                candidates.append(fallback)

        for rpc_url in candidates:
            client = RPCClient(rpc_url, timeout=10.0)
            try:
                status = client.get_sync_status()
            except Exception as exc:
                logger.warning("remote RPC %s did not respond: %s", rpc_url, exc)
                continue
            if not status:
                logger.warning("remote RPC %s returned no sync status", rpc_url)
                continue

            logger.info("no local node (%s); using remote RPC %s", reason, rpc_url)
            self._rpc_url = rpc_url
            self._token = ""
            self._rpc_client = client
            self._remote = True
            self.rpcChanged.emit(rpc_url, "")
            self.nodeStarted.emit(rpc_url, "")
            self._status_timer.start()
            return True

        return False

    def connect_remote_now(self) -> bool:
        """Publish a remote endpoint immediately, before any node start.

        Lets the UI render live data on first paint instead of waiting out a
        local-node startup that may never succeed.
        """
        return self._use_remote_rpc("startup")

    def _schedule_retry(self) -> None:
        """Retry connecting later instead of giving up for the session.

        A transient failure at launch — resuming from sleep, VPN still
        connecting, captive portal — used to leave every statistic at "--"
        permanently, with no way to recover short of restarting the app.
        """
        if getattr(self, "_retry_timer", None) is None:
            self._retry_timer = QTimer(self)
            self._retry_timer.setSingleShot(True)
            self._retry_timer.timeout.connect(self.start)
        if not self._retry_timer.isActive():
            logger.info("no RPC endpoint yet; retrying in %ss", _RETRY_SECONDS)
            self._retry_timer.start(_RETRY_SECONDS * 1000)

    def _fail_or_remote(self, reason: str) -> None:
        """Any local-node failure degrades to the remote endpoint if possible.

        A packaged build has no node payload, but a node can also fail to
        start, fail to respond, or have a broken bundle. In every one of those
        cases the user still wants their balance and chain stats, so try the
        public endpoint before declaring failure.
        """
        if self._use_remote_rpc(reason):
            return
        self.nodeFailed.emit(reason)
        self._schedule_retry()

    def start(self, force: bool = False) -> None:
        """Start the bundled node.

        `force=True` is for an explicit user action. Called without it, an
        already-working remote endpoint wins: probing for a local node tears
        the remote connection down for the whole _wait_for_rpc deadline, and
        the dashboard blanked to "--" for 15 seconds every launch. A packaged
        build has no node payload anyway, so nothing is lost by not looking.
        """
        if self._process and self._process.poll() is None:
            return
        if not force and self._remote and self._rpc_client:
            logger.info(
                "already connected to %s; not probing for a local node",
                self._rpc_url,
            )
            return

        self.nodeStarting.emit()
        node_paths = resolve_node_executable()
        node_exe = node_paths.exe_path
        node_root = node_paths.base_dir
        logger.info("node_resolve_mode=%s", node_paths.mode)
        logger.info("node_resolve_reason=%s", node_paths.reason)

        if not node_exe or not node_exe.exists() or not node_root:
            # No local node payload — the normal case for a packaged build.
            # Publish the remote endpoint anyway so the wallet, dashboard,
            # console and IDE all get a working RPC client. Previously
            # rpcChanged only ever fired after a local node started, so in a
            # release build it never fired at all and every tab sat empty
            # showing "RPC unavailable".
            self._fail_or_remote(f"{node_paths.reason}")
            return

        manifest_path = get_node_manifest_path()
        if manifest_path.exists():
            try:
                manifest = load_manifest(manifest_path)
                errors = verify_manifest(node_root, manifest)
                if errors:
                    self._fail_or_remote("Node bundle missing/broken. " + "; ".join(errors))
                    return
            except Exception as exc:
                self._fail_or_remote(f"Failed to verify node bundle: {exc}")
                return
        elif is_frozen():
            self._fail_or_remote("Node bundle missing/broken. Missing manifest.")
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
            self._fail_or_remote(f"Failed to start node: {exc}")
            return

        self._rpc_url = rpc_url
        self._token = token
        self._rpc_client = RPCClient(rpc_url, token=token)
        self._start_time = time.time()

        if not self._wait_for_rpc():
            self.stop()
            self._fail_or_remote("Node failed to respond on RPC")
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
            # Clear the handle too, or the dashboard keeps showing the PID of a
            # node that is no longer running.
            self._process = None
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
        # 15s, not 30s. This runs on the GUI thread; a local node that has not
        # answered in 15 seconds is not about to, and we already have a remote
        # endpoint serving data in the meantime.
        deadline = time.time() + 15
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

    def _rpc_port(self) -> Optional[int]:
        """Port of the active endpoint, or the scheme default when implicit."""
        if not self._rpc_url:
            return None
        from urllib.parse import urlparse

        try:
            parsed = urlparse(self._rpc_url)
            if parsed.port:
                return parsed.port
            return {"https": 443, "http": 80}.get(parsed.scheme)
        except Exception:
            return None

    def _poll_status(self) -> None:
        """Kick off a status refresh on a worker thread.

        This used to do three blocking HTTP calls inline. On the Qt GUI thread
        that froze the whole window — buttons dead, nothing repainting — for up
        to 3x the RPC timeout, every 5 seconds, whenever the endpoint was slow
        or half-open. Qt marshals a signal emitted from a worker thread back to
        the GUI thread automatically, so the UI only ever sees the finished
        payload.
        """
        if not self._rpc_client or self._poll_in_flight:
            return
        self._poll_in_flight = True
        self._pool.start(_StatusPollTask(self))

    def _collect_status(self) -> None:
        if not self._rpc_client:
            return
        status: Dict[str, Any] = {
            "rpc_url": self._rpc_url,
            "pid": self._process.pid if self._process else None,
            # urlparse, not split(":")[2] — that indexed past the end on any URL
            # without an explicit port ("https://rpc.animica.org/rpc" splits
            # into 2 parts) and raised IndexError on every poll tick.
            "port": self._rpc_port(),
            "remote": self._remote,
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

        # get_mempool_stats existed but nothing ever called it, so the Mempool
        # figures were blank even though the node answers instantly.
        try:
            status["mempool"] = self._rpc_client.get_mempool_stats()
        except RPCError as exc:
            status["mempool_error"] = str(exc)

        status["last_log_line"] = self._tail_node_log_line()

        try:
            self.statusUpdated.emit(qt_safe(status))
        except Exception:
            # Never let a malformed status payload kill the poll timer.
            logger.exception("failed to emit node status")

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
