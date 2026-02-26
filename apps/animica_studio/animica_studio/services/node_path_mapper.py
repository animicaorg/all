from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from animica_studio.services.da_path_guard import NODE_PATH_UI_ERROR, assert_host_writable_path
from animica_studio.services.rpc_client import RpcClient


class NodePathMapper:
    """Resolve node<->host DA path mapping and probe mount visibility."""

    def __init__(self, host_chain_dir: str | None) -> None:
        self._host_chain_dir = str(host_chain_dir or "").strip()

    @staticmethod
    def infer_node_data_root(*candidates: str) -> str:
        for candidate in candidates:
            path = str(candidate or "").strip()
            if not path.startswith("/"):
                continue
            parts = Path(path).parts
            if len(parts) >= 2:
                return f"/{parts[1]}"
        return "/data"

    def host_data_root(self) -> str:
        if not self._host_chain_dir:
            return ""
        return str(Path(self._host_chain_dir).expanduser().parent)

    def map_host_path(self, node_path: str, node_data_root: str) -> str:
        if not self._host_chain_dir:
            return ""
        node_raw = str(node_path or "").strip()
        data_root = str(node_data_root or "").strip() or "/data"
        if not node_raw.startswith("/") or not node_raw.startswith(data_root):
            return ""
        rel = Path(node_raw).relative_to(Path(data_root))
        return str(Path(self.host_data_root()) / rel)

    def probe_visibility(self, client: RpcClient, node_pending_dir: str, host_pending_dir: str) -> tuple[bool, str]:
        reg = client.registry()
        stat_method = reg.resolve_any(["da.statPath", "da_statPath"])
        if not stat_method:
            return False, "Node does not expose da.statPath required for mount probe"
        try:
            host_pending = assert_host_writable_path(host_pending_dir)
        except ValueError as exc:
            if str(exc) == NODE_PATH_UI_ERROR:
                return False, NODE_PATH_UI_ERROR
            return False, str(exc)
        host_pending.mkdir(parents=True, exist_ok=True)
        probe_name = ".studio_probe"
        host_probe = host_pending / probe_name
        node_probe = os.path.join(str(node_pending_dir or "").rstrip("/"), probe_name)
        host_probe.write_bytes(b"studio-probe")
        try:
            out = client.call_with_schema(stat_method, {"path": node_probe})
            exists = bool(out.get("exists", False)) if isinstance(out, dict) else bool(out)
            if exists:
                return True, ""
            return False, (
                f"Node cannot see host ingest directory. I wrote to host path {host_probe}, "
                f"node expected {node_probe}; mapping missing."
            )
        finally:
            host_probe.unlink(missing_ok=True)

