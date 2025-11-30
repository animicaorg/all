"""
mempool.adapters
================

Integration points the mempool uses to talk to the rest of the system without
taking hard dependencies. These lightweight Protocols are intentionally small
to keep tests fast and adapters easy to mock.

Typical implementers:
- core.db.state_db.StateDB (for balances/nonces)
- rpc/state_service.py (for read-only state from a node)
- core/chain/head.py (for current head info)
- mempool/deps.py (default wiring for a node process)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, MutableMapping, Optional, Protocol

# --------------------------------------------------------------------------------------
# State & Head Views
# --------------------------------------------------------------------------------------


class StateView(Protocol):
    """Read-only account state access used by mempool accounting/validation."""

    def get_balance(self, address: bytes) -> int: ...
    def get_nonce(self, address: bytes) -> int: ...


class HeadView(Protocol):
    """Light view of the canonical head used for TTLs, fee windows, and reorg handling."""

    def head_number(self) -> int: ...
    def head_hash(self) -> bytes: ...
    def chain_id(self) -> int: ...


# --------------------------------------------------------------------------------------
# Fee statistics used by fee_market and priority calculators
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FeeWindow:
    """
    Aggregated fee statistics observed over a recent rolling window.

    base_fee: moving floor (can be 0 for non-EIP-1559-style regimes)
    tip_percentiles: mapping like {10: x, 25: y, 50: z, 75: w, 90: v}
    """

    base_fee: int
    tip_percentiles: Mapping[int, int]


class FeeStatsView(Protocol):
    """Provider of recent base fee and tip distribution percentiles."""

    def current(self) -> FeeWindow: ...
    def min_acceptable_fee(self) -> int:
        """Return the current dynamic floor enforced for admission."""
        ...


# --------------------------------------------------------------------------------------
# Optional write-through hooks (used by pool→drain→builder)
# --------------------------------------------------------------------------------------


class NonceCache(Protocol):
    """
    Optional local nonce cache that the mempool can consult/update on admission
    to reduce DB round-trips. Implementations should be best-effort only.
    """

    def get(self, address: bytes) -> Optional[int]: ...
    def put(self, address: bytes, nonce: int) -> None: ...
    def invalidate(self, address: bytes) -> None: ...


__all__ = [
    "StateView",
    "HeadView",
    "FeeWindow",
    "FeeStatsView",
    "NonceCache",
]
