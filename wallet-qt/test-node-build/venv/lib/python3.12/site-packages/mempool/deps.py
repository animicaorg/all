"""
mempool.deps
============

Thin integration layer that gives the mempool convenient, *stable* hooks into:

  1) **State view** backed by `core/db/state_db.py` for balance/nonce reads.
  2) **Head tracker** to observe canonical head changes (hash/height).
  3) **Recent fee statistics** derived from recent blocks to feed fee-market logic.

This module is deliberately defensive and will gracefully degrade if some optional
fields are not present (e.g. no `baseFee` in headers). It introspects objects
to find the attributes it needs and falls back to best-effort computations.

Typical usage
-------------

```python
from mempool.deps import open_state_db, open_block_db, StateView, HeadTracker, RecentFeeStats

state_db  = open_state_db("sqlite:///animica.db")       # or RocksDB if available
block_db  = open_block_db("sqlite:///animica.db")

state     = StateView(state_db)
headtrack = HeadTracker(block_db)
fees      = RecentFeeStats(block_db, window=64)

# On new head:
blk = block_db.get_block_by_hash(new_hash)
headtrack.notify_new_head(blk)
fees.add_block(blk)

# In mempool admission:
balance = state.get_balance(sender)
nonce   = state.get_nonce(sender)
tip_med = fees.median_tip() or 0

“””

from future import annotations

from collections import deque
from dataclasses import dataclass
from statistics import median
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence, Tuple, Union
import logging

logger = logging.getLogger(name)

——————————————————————————

Light imports from core DBs (optional / guarded)

——————————————————————————

def open_kv(db_uri: str):
“””
Open the KV backend based on the URI. Prefers SQLite; gracefully degrades
if RocksDB is requested but not available.
“””
try:
# SQLite path
from core.db.sqlite import SqliteKV  # type: ignore
return SqliteKV(db_uri)
except Exception as e:
logger.debug(“SqliteKV unavailable: %s”, e)

try:
    # Optional RocksDB
    from core.db.rocksdb import RocksKV  # type: ignore
    return RocksKV(db_uri)
except Exception as e:
    logger.error("No KV backend available for %s: %s", db_uri, e)
    raise

def open_state_db(db_uri: str):
“”“Construct a StateDB on top of the configured KV store.”””
kv = open_kv(db_uri)
from core.db.state_db import StateDB  # type: ignore
return StateDB(kv)

def open_block_db(db_uri: str):
“”“Construct a BlockDB on top of the configured KV store.”””
kv = open_kv(db_uri)
from core.db.block_db import BlockDB  # type: ignore
return BlockDB(kv)

——————————————————————————

Helpers

——————————————————————————

def _first_attr(obj: Any, names: Sequence[str], default: Any = None) -> Any:
for n in names:
if hasattr(obj, n):
return getattr(obj, n)
# mapping fallback
try:
for n in names:
if n in obj:
return obj[n]
except Exception:
pass
return default

def _as_int(v: Any) -> Optional[int]:
try:
if v is None:
return None
return int(v)
except Exception:
return None

def _tx_iter(block: Any) -> Iterable[Any]:
txs = _first_attr(block, (“txs”, “transactions”, “tx_list”), None)
return txs or ()

def _receipts_iter(block: Any) -> Iterable[Any]:
recs = _first_attr(block, (“receipts”, “tx_receipts”), None)
return recs or ()

def _header(block_or_header: Any) -> Any:
return _first_attr(block_or_header, (“header”,), block_or_header)

——————————————————————————

State view

——————————————————————————

class StateView:
“””
Small facade over StateDB providing a stable API for the mempool regardless
of the underlying storage schema.

Required StateDB capabilities (directly or via duck typing):
  - get_balance(address: bytes) -> int
  - get_nonce(address: bytes) -> int
"""

def __init__(self, state_db: Any):
    self._db = state_db

def get_balance(self, address: bytes) -> int:
    if hasattr(self._db, "get_balance"):
        return int(self._db.get_balance(address))
    # Fallbacks: try generic get("acct:<addr_hex>:balance")
    try:
        key = ("acct", address.hex(), "balance")
        return int(self._db.get(key) or 0)
    except Exception:
        logger.debug("StateView.get_balance fallback path used")
        return 0

def get_nonce(self, address: bytes) -> int:
    if hasattr(self._db, "get_nonce"):
        return int(self._db.get_nonce(address))
    try:
        key = ("acct", address.hex(), "nonce")
        return int(self._db.get(key) or 0)
    except Exception:
        logger.debug("StateView.get_nonce fallback path used")
        return 0

——————————————————————————

Head tracker

——————————————————————————

@dataclass
class HeadInfo:
hash: Optional[bytes]
height: int

class HeadTracker:
“””
Minimal canonical-head tracker. Consumers may either:
- call refresh() to poll the DB, or
- call notify_new_head(block) when they observe a new head.

The tracker only stores (hash, height). It does not emit events on its own.
"""

def __init__(self, block_db: Any):
    self._db = block_db
    self._head: HeadInfo = HeadInfo(hash=None, height=-1)
    # Try initial refresh
    try:
        self.refresh()
    except Exception as e:
        logger.debug("HeadTracker initial refresh failed: %s", e)

@property
def hash(self) -> Optional[bytes]:
    return self._head.hash

@property
def height(self) -> int:
    return self._head.height

def refresh(self) -> HeadInfo:
    """
    Attempts to read the canonical head from BlockDB or core.chain.head helpers.
    """
    # Prefer BlockDB helpers if available
    try:
        if hasattr(self._db, "get_canonical_head"):
            hdr = self._db.get_canonical_head()
            h = _first_attr(hdr, ("hash", "header_hash", "id"), None)
            num = _first_attr(hdr, ("height", "number"), None)
            self._head = HeadInfo(
                hash=h if isinstance(h, (bytes, bytearray)) else None,
                height=int(num) if num is not None else self._head.height,
            )
            return self._head
    except Exception as e:
        logger.debug("BlockDB.get_canonical_head failed: %s", e)

    # Try core.chain.head module
    try:
        from core.chain.head import read_head  # type: ignore
        head = read_head(self._db)
        self._head = HeadInfo(
            hash=_first_attr(head, ("hash",), None),
            height=int(_first_attr(head, ("height", "number"), -1)),
        )
        return self._head
    except Exception as e:
        logger.debug("core.chain.head.read_head failed: %s", e)

    return self._head

def notify_new_head(self, block: Any) -> HeadInfo:
    hdr = _header(block)
    h = _first_attr(hdr, ("hash", "header_hash", "id"), None)
    num = _first_attr(hdr, ("height", "number"), None)
    if isinstance(h, str) and h.startswith("0x"):
        try:
            h = bytes.fromhex(h[2:])
        except Exception:
            h = None
    if isinstance(h, str):
        h = None  # unknown encoding
    self._head = HeadInfo(hash=h if isinstance(h, (bytes, bytearray)) else None,
                          height=int(num) if num is not None else self._head.height)
    return self._head

——————————————————————————

Recent fee statistics

——————————————————————————

@dataclass
class FeeSample:
gas_used: int
gas_limit: int
base_fee: Optional[int]          # None if not present in headers
tip_median: Optional[int]        # median of per-tx (gas_price - base_fee) if computable
tx_count: int

def _extract_fee_sample(block: Any) -> FeeSample:
hdr = _header(block)

gas_used  = _as_int(_first_attr(hdr, ("gas_used", "gasUsed"))) or 0
gas_limit = _as_int(_first_attr(hdr, ("gas_limit", "gasLimit"))) or max(gas_used, 1)
base_fee  = _as_int(_first_attr(hdr, ("base_fee_per_gas", "baseFeePerGas", "baseFee")))

# Per-tx tip estimation:
tips: List[int] = []
for tx in _tx_iter(block):
    gp  = _as_int(_first_attr(tx, ("gas_price", "gasPrice")))
    tip = _as_int(_first_attr(tx, ("max_priority_fee_per_gas", "maxPriorityFeePerGas")))
    maxf= _as_int(_first_attr(tx, ("max_fee_per_gas", "maxFeePerGas")))

    # Try EIP-1559 style first
    if tip is not None:
        tips.append(max(0, tip))
    elif gp is not None and base_fee is not None:
        # Legacy: treat (gas_price - base_fee) as a tip lower bound
        tips.append(max(0, gp - base_fee))
    elif gp is not None:
        # No base fee available → consider entire gas price as tip proxy
        tips.append(max(0, gp))
    elif maxf is not None and base_fee is not None:
        tips.append(max(0, maxf - base_fee))

tip_med = median(tips) if tips else None
tx_count = len(list(_tx_iter(block)))
return FeeSample(
    gas_used=gas_used,
    gas_limit=gas_limit,
    base_fee=base_fee,
    tip_median=tip_med,
    tx_count=tx_count,
)

class RecentFeeStats:
“””
Maintains a sliding window of recent fee samples, exposed via simple
statistics helpers used by fee-market & admission policy.

If constructed with a BlockDB, the window can be primed from the current
canonical chain using `prime_from_chain(window)` and then maintained via
`add_block(block)` on every new head.
"""

def __init__(self, block_db: Optional[Any] = None, window: int = 64):
    self._db = block_db
    self._window: int = max(1, int(window))
    self._samples: Deque[FeeSample] = deque(maxlen=self._window)

# -- Priming / updates -----------------------------------------------------

def clear(self) -> None:
    self._samples.clear()

def prime_from_chain(self, count: Optional[int] = None) -> int:
    """
    Populate the window from the last `count` canonical blocks.
    If `count` is None, use the current window size.
    Returns the number of blocks ingested.
    """
    if self._db is None:
        return 0

    want = min(self._window, int(count or self._window))
    got = 0

    # Attempt to walk backwards using BlockDB helpers.
    try:
        if hasattr(self._db, "iter_canonical_back"):
            for blk in self._db.iter_canonical_back(limit=want):  # type: ignore
                self._samples.appendleft(_extract_fee_sample(blk))
                got += 1
            return got
    except Exception as e:
        logger.debug("BlockDB.iter_canonical_back failed: %s", e)

    # Fallback: try (head -> get_block_by_hash -> parentHash ...)
    try:
        head = None
        if hasattr(self._db, "get_canonical_head"):
            head = self._db.get_canonical_head()
        elif hasattr(self._db, "get_head"):
            head = self._db.get_head()

        get_by_hash = getattr(self._db, "get_block_by_hash", None)
        parent_attr = ("parent_hash", "parentHash", "prev", "prev_hash")

        blk = head
        while blk is not None and got < want and callable(get_by_hash):
            self._samples.appendleft(_extract_fee_sample(blk))
            got += 1
            ph = _first_attr(_header(blk), parent_attr, None)
            if ph is None:
                break
            blk = get_by_hash(ph)
    except Exception as e:
        logger.debug("fallback chain walk failed: %s", e)

    return got

def add_block(self, block: Any) -> None:
    self._samples.append(_extract_fee_sample(block))

# -- Query helpers ---------------------------------------------------------

def median_tip(self) -> Optional[int]:
    vals = [s.tip_median for s in self._samples if s.tip_median is not None]
    return int(median(vals)) if vals else None

def ema_base_fee(self, alpha: float = 0.3) -> Optional[int]:
    """
    Exponentially-weighted moving average of base fee if available.
    """
    if not 0.0 < alpha <= 1.0:
        alpha = 0.3
    vals = [s.base_fee for s in self._samples if s.base_fee is not None]
    if not vals:
        return None
    ema = vals[0]
    for v in vals[1:]:
        ema = int(round(alpha * v + (1 - alpha) * ema))
    return int(ema)

def ema_utilization(self, alpha: float = 0.3) -> float:
    """
    EWMA of gas utilization ratio (gas_used / gas_limit).
    Returns a value in [0, +inf) though typical values are [0, 1.0+].
    """
    if not self._samples:
        return 0.0
    # Seed with the first sample
    s0 = self._samples[0]
    z0 = (s0.gas_used / max(1, s0.gas_limit))
    z = z0
    for s in list(self._samples)[1:]:
        r = (s.gas_used / max(1, s.gas_limit))
        z = alpha * r + (1 - alpha) * z
    return float(z)

def tx_count_avg(self) -> float:
    if not self._samples:
        return 0.0
    return sum(s.tx_count for s in self._samples) / float(len(self._samples))

def snapshot(self) -> Dict[str, Union[int, float, None]]:
    """
    Compact dictionary used by fee-market to pick floors/suggestions.
    Keys:
      - base_fee_ema: Optional[int]
      - utilization_ema: float
      - median_tip: Optional[int]
      - tx_count_avg: float
    """
    return {
        "base_fee_ema": self.ema_base_fee(),
        "utilization_ema": self.ema_utilization(),
        "median_tip": self.median_tip(),
        "tx_count_avg": self.tx_count_avg(),
    }

all = [
“open_kv”,
“open_state_db”,
“open_block_db”,
“StateView”,
“HeadInfo”,
“HeadTracker”,
“FeeSample”,
“RecentFeeStats”,
]
