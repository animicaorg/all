from __future__ import annotations

from pathlib import Path


NODE_PATH_UI_ERROR = (
    "Studio needs a host path for contributions. "
    "Choose a host path for Studio dir "
    "(e.g. ~/.animica/da_contrib) — not a node-only path like /data/."
)


def is_node_path(path: str, extra_node_paths: set[str] | None = None) -> bool:
    raw = str(path or "").strip()
    if not raw:
        return False
    if raw == "/data" or raw.startswith("/data/"):
        return True
    for candidate in set(extra_node_paths or set()):
        base = str(candidate or "").strip().rstrip("/")
        if not base:
            continue
        norm = raw.rstrip("/")
        if norm == base or norm.startswith(f"{base}/"):
            return True
    return False


def assert_host_writable_path(path: str, extra_node_paths: set[str] | None = None) -> Path:
    raw = str(path or "").strip()
    if is_node_path(raw, extra_node_paths=extra_node_paths):
        raise ValueError(NODE_PATH_UI_ERROR)
    return Path(raw).expanduser()
