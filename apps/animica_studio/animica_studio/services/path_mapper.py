from __future__ import annotations

from pathlib import Path


class NodeHostPathMapper:
    """Bidirectional path mapper between node/container and host roots."""

    def __init__(self, *, node_data_root: str, host_data_root: str, mapping_verified: bool) -> None:
        self.node_data_root = self._normalize_root(node_data_root, default="/data")
        self.host_data_root = self._normalize_root(host_data_root, default="")
        self.mapping_verified = bool(mapping_verified)

    @staticmethod
    def _normalize_root(path: str, *, default: str) -> str:
        raw = str(path or "").strip()
        if not raw:
            raw = default
        return str(Path(raw).expanduser()).rstrip("/") or "/"

    def _ensure_verified(self) -> None:
        if self.mapping_verified and self.host_data_root:
            return
        raise RuntimeError(
            "Host↔node ingest mapping is not verified. Fix Docker mounts so host data root maps to node data root."
        )

    def node_to_host(self, node_path: str) -> str:
        self._ensure_verified()
        node_raw = str(node_path or "").strip()
        prefix = f"{self.node_data_root}/"
        if not node_raw.startswith(prefix):
            return node_raw
        rel = node_raw[len(prefix) :]
        return str(Path(self.host_data_root) / rel)

    def host_to_node(self, host_path: str) -> str:
        self._ensure_verified()
        host_raw = str(Path(str(host_path or "").strip()).expanduser())
        prefix = f"{self.host_data_root}/"
        if not host_raw.startswith(prefix):
            return host_raw
        rel = host_raw[len(prefix) :]
        return str(Path(self.node_data_root) / rel)

