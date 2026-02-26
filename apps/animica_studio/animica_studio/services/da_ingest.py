from __future__ import annotations

from pathlib import Path

from .da_client import DaClient


class DaIngestService:
    def __init__(self, da: DaClient, host_ingest_fallback: Path | None = None) -> None:
        self.da = da
        self.host_ingest_fallback = host_ingest_fallback or (Path.home() / ".animica" / "da_ingest")

    def ingest_local_file(self, node_path: str, namespace: int = 0) -> dict:
        return self.da.ingest_local(node_path=node_path, namespace=namespace)

    def suggested_host_ingest_dir(self) -> Path:
        info = self.da.get_ingest_dir()
        raw = str(info.get("dir") or "").strip()
        if raw.startswith("/data/"):
            return self.host_ingest_fallback
        return Path(raw) if raw else self.host_ingest_fallback
