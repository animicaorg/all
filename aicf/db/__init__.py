from __future__ import annotations

"""
aicf.db
-------

Lightweight helpers and package marker for AICF storage backends.

This subpackage is intended to host concrete database adapters
(e.g., SQLite, RocksDB) used by the registry, queue, treasury,
and settlement modules. To keep imports tidy and test-friendly,
we centralize path resolution here.

Paths
-----
Base directory resolution follows:

1) AICF_DB_DIR               (if set)
2) XDG_DATA_HOME/animica/aicf
3) ~/.animica/aicf

Helpers exposed:
- default_base_dir() -> Path
- db_path(name, *, base_dir=None, create=False) -> Path

These are intentionally generic so callers can compose filenames
like "state.sqlite3", "queue.sqlite3", etc., without hard-coding
platform-specific locations.

Environment:
- AICF_DB_DIR: override the root directory for AICF databases.
"""

import os
from pathlib import Path
from typing import Optional, Union

Pathish = Union[str, os.PathLike[str]]


def default_base_dir() -> Path:
    """
    Resolve the default base directory for AICF DB files.

    Order:
      - $AICF_DB_DIR
      - $XDG_DATA_HOME/animica/aicf
      - ~/.animica/aicf
    """
    env = os.getenv("AICF_DB_DIR")
    if env:
        return Path(env).expanduser()

    xdg = os.getenv("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / "animica" / "aicf"

    return Path.home() / ".animica" / "aicf"


def db_path(name: str, *, base_dir: Optional[Pathish] = None, create: bool = False) -> Path:
    """
    Build a path under the AICF DB base directory.

    Args:
        name: Filename or relative path (e.g., "state.sqlite3", "queue/rocks").
        base_dir: Optional override for the base directory.
        create: If True, create parent directories (no-op if already exist).

    Returns:
        Path object pointing to the resolved location.
    """
    base = Path(base_dir).expanduser() if base_dir is not None else default_base_dir()
    p = base / name
    if create:
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


# Common convenience file names (not created automatically)
DEFAULT_SQLITE_FILE = default_base_dir() / "aicf.sqlite3"
DEFAULT_QUEUE_SQLITE_FILE = default_base_dir() / "queue.sqlite3"
DEFAULT_REGISTRY_SQLITE_FILE = default_base_dir() / "registry.sqlite3"

__all__ = [
    "default_base_dir",
    "db_path",
    "DEFAULT_SQLITE_FILE",
    "DEFAULT_QUEUE_SQLITE_FILE",
    "DEFAULT_REGISTRY_SQLITE_FILE",
]
