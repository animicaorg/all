from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

DEFAULT_SNAPSHOT_SUBDIR = "snapshots"


def _resolve_base_dir(data_dir: Optional[Path] = None) -> Path:
    if data_dir is not None:
        base = Path(data_dir).expanduser()
    else:
        base = Path(os.environ.get("ANIMICA_DATA_DIR", "~/.animica")).expanduser()
    if base.name.startswith("chain-"):
        base = base.parent
    return base


def get_snapshots_dir(data_dir: Optional[Path] = None) -> Path:
    base = _resolve_base_dir(data_dir)
    return base / DEFAULT_SNAPSHOT_SUBDIR


def get_snapshot_dir(
    chain_id: int,
    checkpoint_height: int,
    *,
    data_dir: Optional[Path] = None,
) -> Path:
    snapshots_dir = get_snapshots_dir(data_dir)
    return snapshots_dir / f"chain-{chain_id}-height-{checkpoint_height}"


def snapshot_path_display(path: Path, *, data_dir: Optional[Path] = None) -> str:
    path = Path(path)
    host_root = os.environ.get("ANIMICA_SNAPSHOT_HOST_DIR")
    base_dir = _resolve_base_dir(data_dir)
    if host_root:
        host_root_path = Path(host_root).expanduser()
        try:
            rel = path.relative_to(base_dir)
            return str(host_root_path / rel)
        except ValueError:
            return str(host_root_path / path.name)
    path_str = str(path)
    if path_str.startswith("/data/"):
        return f"container:{path_str}"
    return path_str


__all__ = ["get_snapshots_dir", "get_snapshot_dir", "snapshot_path_display"]
