from __future__ import annotations

"""
p2p.sync
========

Synchronization orchestrators for:
- headers (locators, getheaders/headers)
- blocks (parallel fetch with integrity checks)
- mempool (tx inv/fetch with rebroadcast suppression)
- shares (useful-work share relay)

This package uses *lazy exports* so importing `p2p.sync` does not eagerly import
submodules. Accessing `HeaderSync`, `BlockSync`, `MempoolSync`, or `ShareSync`
will import the corresponding module on first use.

Common lightweight types shared by submodules live here to avoid cycles.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

# --------------------------------------------------------------------------------------
# Shared types / knobs
# --------------------------------------------------------------------------------------

# Hash/Height aliases (kept simple to avoid importing core.* here)
Hash = bytes
Height = int

DEFAULT_MAX_IN_FLIGHT: int = 64
DEFAULT_REQUEST_TIMEOUT_SEC: float = 10.0
DEFAULT_MAX_REORG_DEPTH: int = 96


@dataclass(slots=True)
class SyncStats:
    """Minimal, transport-agnostic stats container updated by sync loops."""
    started_at: float
    last_progress_at: float
    headers_fetched: int = 0
    blocks_fetched: int = 0
    txs_fetched: int = 0
    shares_fetched: int = 0
    reorgs_observed: int = 0
    errors: int = 0


# --------------------------------------------------------------------------------------
# Lazy attribute export (PEP 562)
# --------------------------------------------------------------------------------------

__all__ = (
    "HeaderSync",
    "BlockSync",
    "MempoolSync",
    "ShareSync",
    "SyncStats",
    "Hash",
    "Height",
    "DEFAULT_MAX_IN_FLIGHT",
    "DEFAULT_REQUEST_TIMEOUT_SEC",
    "DEFAULT_MAX_REORG_DEPTH",
)

def __getattr__(name: str):
    """
    Lazily import heavy submodules only when their top-level symbols are accessed.
    """
    if name == "HeaderSync":
        from .headers import HeaderSync  # type: ignore
        return HeaderSync
    if name == "BlockSync":
        from .blocks import BlockSync  # type: ignore
        return BlockSync
    if name == "MempoolSync":
        from .mempool import MempoolSync  # type: ignore
        return MempoolSync
    if name == "ShareSync":
        from .shares import ShareSync  # type: ignore
        return ShareSync
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
