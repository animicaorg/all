"""NodeService — wraps ProcessManager + RpcClient to provide node status model for UI."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from animica_studio.services.process_manager import ProcessManager
from animica_studio.services.rpc_client import RpcClient
from animica_studio.storage.config import Config

log = logging.getLogger(__name__)


@dataclass
class NodeStatus:
    """Snapshot of local node state."""

    running: bool = False
    pid: int | None = None
    rpc_reachable: bool = False
    head_number: int | None = None
    head_hash: str | None = None
    chain_id: int | None = None
    uptime_s: float | None = None
    error: str | None = None
    started_by_app: bool = False
    timestamp: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "pid": self.pid,
            "rpc_reachable": self.rpc_reachable,
            "head_number": self.head_number,
            "head_hash": self.head_hash,
            "chain_id": self.chain_id,
            "uptime_s": self.uptime_s,
            "error": self.error,
            "started_by_app": self.started_by_app,
            "timestamp": self.timestamp,
        }


class NodeService:
    """High-level node operations: start/stop/restart/status.

    Wraps :class:`~animica_studio.services.process_manager.ProcessManager` and
    :class:`~animica_studio.services.rpc_client.RpcClient`.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        profile = config.get_active_profile()
        self._pm = ProcessManager(
            start_cmd=list(profile.node.start_cmd),
            rpc_url=profile.node.rpc_local_url,
            data_dir=None,
            config=config,
        )
        self._rpc_url = profile.node.rpc_local_url

    # ------------------------------------------------------------------
    # Core controls
    # ------------------------------------------------------------------

    def start(self) -> dict[str, Any]:
        """Start the local node. Returns status dict."""
        try:
            self._pm.start()
            return {"ok": True, "message": "Node start requested."}
        except Exception as exc:  # noqa: BLE001
            log.error("NodeService.start failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def stop(self) -> dict[str, Any]:
        """Stop the local node."""
        try:
            self._pm.stop()
            return {"ok": True, "message": "Node stopped."}
        except Exception as exc:  # noqa: BLE001
            log.error("NodeService.stop failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def restart(self) -> dict[str, Any]:
        """Restart the local node."""
        try:
            self._pm.restart()
            return {"ok": True, "message": "Node restarted."}
        except Exception as exc:  # noqa: BLE001
            log.error("NodeService.restart failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def status(self) -> NodeStatus:
        """Return a :class:`NodeStatus` snapshot."""
        pm_status = self._pm.status()
        running = bool(pm_status.get("running", False))
        pid = pm_status.get("pid")
        started_by_app = bool(pm_status.get("started_by_app", False))
        uptime_s = pm_status.get("uptime_s")

        rpc_reachable = False
        head_number: int | None = None
        head_hash: str | None = None
        chain_id: int | None = None
        error: str | None = None

        try:
            client = RpcClient(self._rpc_url, connect_timeout=2.0, read_timeout=5.0, max_retries=1)
            try:
                head = client.get_head()
                rpc_reachable = True
                head_number = head.number
                head_hash = head.hash
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
            try:
                chain_id = client.get_chain_id()
            except Exception:  # noqa: BLE001
                pass
            client.close()
        except Exception as exc:  # noqa: BLE001
            error = str(exc)

        return NodeStatus(
            running=running,
            pid=pid,
            rpc_reachable=rpc_reachable,
            head_number=head_number,
            head_hash=head_hash,
            chain_id=chain_id,
            uptime_s=uptime_s,
            error=error,
            started_by_app=started_by_app,
        )

    def tail_logs(self, n: int = 100) -> list[str]:
        """Return last *n* lines from the node log file."""
        try:
            return self._pm.tail_log(n)
        except Exception as exc:  # noqa: BLE001
            log.warning("NodeService.tail_logs: %s", exc)
            return []
