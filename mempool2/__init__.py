"""
mempool2 - Next-Generation Mempool System
==========================================

Production-ready mempool implementation with:
- Never-throw admission engine
- Pure policy functions
- Persistent SQLite storage
- Deterministic eviction
- Block template selection

Key components:
- types: Data models (MempoolEntry, MempoolStats)
- policy: Pure validation functions returning Optional[TxReject]
- storage: Crash-safe SQLite backend
- admission: Exception-safe admission engine
- evict: Deterministic eviction logic
- template: Block template selection with nonce ordering
"""

__version__ = "2.0.0"

from .types import MempoolEntry, MempoolStats, TxSource
from .admission import admit_tx
from .storage import MempoolStorage
from .template import select_txs

__all__ = [
    "MempoolEntry",
    "MempoolStats",
    "TxSource",
    "admit_tx",
    "MempoolStorage",
    "select_txs",
]
