from __future__ import annotations

"""
core.genesis
============

Helpers and conveniences around the project's bundled genesis artifacts.

This package intentionally avoids importing heavy modules at import time.
When you call `load_default_genesis(...)` it will import `core.genesis.loader`
lazily so that light tools (e.g., `--help` CLI) don't pay the cost.

Exports
-------
- GENESIS_DIR: Path to this package directory.
- GENESIS_JSON: Path to the bundled genesis.json.
- load_default_genesis(db, *, params_override=None): convenience wrapper that
  loads/validates the bundled genesis and initializes the DB using the loader.
"""

from pathlib import Path
from typing import Any, Optional, Mapping

GENESIS_DIR: Path = Path(__file__).resolve().parent
GENESIS_JSON: Path = GENESIS_DIR / "genesis.json"


def get_default_genesis_path() -> Path:
    """
    Returns the absolute path to the bundled genesis.json.
    """
    return GENESIS_JSON


def load_default_genesis(
    db: Any,
    *,
    params_override: Optional[Mapping[str, Any]] = None,
) -> Any:
    """
    Lazy-import wrapper around `core.genesis.loader.load_genesis`.

    Parameters
    ----------
    db : Any
        A KV/DB handle compatible with `core.db` backends.
    params_override : Optional[Mapping[str, Any]]
        Optional partial overrides for chain parameters when loading genesis
        (useful for devnet/tests).

    Returns
    -------
    Any
        Whatever `loader.load_genesis` returns (typically a struct/tuple with
        {params, state_root, header}).

    Notes
    -----
    - Import is done here to avoid hard import cycles during bring-up.
    - Raises the same exceptions as the underlying loader.
    """
    # Local import to keep package import cheap and avoid early dependency edges.
    from .loader import load_genesis  # type: ignore

    return load_genesis(GENESIS_JSON, db, params_override=params_override)


__all__ = [
    "GENESIS_DIR",
    "GENESIS_JSON",
    "get_default_genesis_path",
    "load_default_genesis",
]
