"""
P2P pre-admission adapter for relayed transactions
==================================================

Goal
----
Provide a *very fast* pre-admission gate that runs **before** the full mempool
validation path when txs arrive over P2P. It does three things:

1) Cheap stateless checks on the raw CBOR envelope (size, minimal decode, optional chainId).
2) Rate limiting (global and per-peer) using token buckets.
3) Duplicate suppression via a TTL cache keyed by tx-hash(raw_cbor).

If a tx passes these gates, higher layers (mempool.validate → mempool.pool) do
the full, expensive checks (PQ signature, state-accounting, etc.).

Design notes
------------
- Zero hard dependency on the rest of the codebase: optional imports are guarded.
- Uses SHA3-256(raw) as the fast hash for dedupe (stable across encoders).
- Minimal CBOR decode: best-effort using `cbor2` (preferred) or falls back to a
  no-decode path that only enforces byte-size limits.
- The adapter is intentionally *tolerant* and errs on DEFER when unsure.

Typical usage
-------------
    gate = P2PAdmission(max_tx_bytes=131072, per_peer_tps=50, global_tps=500, chain_id_expected=1)
    verdict = gate.check(peer_id=b"...", raw_cbor=payload_bytes)
    if verdict.accept:
        # forward into mempool.validate/mempool.pool
    elif verdict.drop_reason == "duplicate":
        # ignore silently
    else:
        # optionally score the peer / apply back-pressure

"""

from __future__ import annotations

import hashlib
import heapq
import logging
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger(__name__)

# Optional CBOR decoder
try:  # preferred
    import cbor2  # type: ignore
except Exception:  # pragma: no cover
    cbor2 = None  # type: ignore

# Optional shared hash helper (not required)
try:
    from core.utils.hash import sha3_256 as _sha3_256  # type: ignore
except Exception:  # pragma: no cover
    _sha3_256 = None  # type: ignore


# --------------------------------------------------------------------------------------
# Token bucket
# --------------------------------------------------------------------------------------


class TokenBucket:
    """
    Simple token bucket with wall-clock refill.
    - capacity: maximum burst.
    - refill_rate: tokens per second.
    """

    __slots__ = ("capacity", "refill_rate", "_tokens", "_last")

    def __init__(self, capacity: float, refill_rate: float) -> None:
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate)
        self._tokens = float(capacity)
        self._last = time.monotonic()

    def allow(self, now: Optional[float] = None) -> bool:
        t = time.monotonic() if now is None else now
        # Refill
        elapsed = max(0.0, t - self._last)
        if elapsed:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
            self._last = t
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


# --------------------------------------------------------------------------------------
# TTL set for dedupe with O(log n) expiration
# --------------------------------------------------------------------------------------


class TTLSet:
    """
    TTL-backed membership set with O(1) check/insert and O(log n) eviction of stale entries.
    """

    def __init__(self, ttl_seconds: int = 300) -> None:
        self.ttl = int(ttl_seconds)
        self._entries: Dict[bytes, float] = {}
        self._heap: list[Tuple[float, bytes]] = []

    def add(self, key: bytes, now: Optional[float] = None) -> None:
        t = time.monotonic() if now is None else now
        exp = t + self.ttl
        self._entries[key] = exp
        heapq.heappush(self._heap, (exp, key))
        self._evict(t)

    def __contains__(self, key: bytes) -> bool:
        t = time.monotonic()
        self._evict(t)
        exp = self._entries.get(key)
        return bool(exp and exp > t)

    def _evict(self, now: float) -> None:
        H = self._heap
        E = self._entries
        while H and H[0][0] <= now:
            exp, k = heapq.heappop(H)
            if E.get(k, 0.0) <= now:
                E.pop(k, None)


# --------------------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------------------


class DropReason(str, Enum):
    RATELIMIT_GLOBAL = "ratelimit_global"
    RATELIMIT_PEER = "ratelimit_peer"
    DUPLICATE = "duplicate"
    OVERSIZE = "oversize"
    MALFORMED = "malformed"
    WRONG_CHAIN = "wrong_chain"
    DECODE_ERROR = "decode_error"
    UNKNOWN = "unknown"


@dataclass
class Verdict:
    accept: bool
    tx_hash: Optional[bytes] = None
    drop_reason: Optional[DropReason] = None
    info: Optional[str] = None  # human-friendly (debug logs)

    @property
    def tx_hex(self) -> Optional[str]:
        return None if self.tx_hash is None else "0x" + self.tx_hash.hex()


# --------------------------------------------------------------------------------------
# Admission gate
# --------------------------------------------------------------------------------------


class P2PAdmission:
    """
    Fast gate for relayed txs:
      - size caps
      - per-peer & global token buckets (TPS)
      - duplicate suppression (TTL set)
      - cheap CBOR parse to read `chainId` (if configured)

    All parameters are adjustable at runtime if desired.
    """

    def __init__(
        self,
        *,
        max_tx_bytes: int = 128 * 1024,
        per_peer_tps: float = 40.0,
        global_tps: float = 400.0,
        per_peer_burst: float = 2.0,
        global_burst: float = 50.0,
        seen_ttl_seconds: int = 300,
        chain_id_expected: Optional[int] = None,
    ) -> None:
        self.max_tx_bytes = int(max_tx_bytes)
        self.chain_id_expected = chain_id_expected

        self._global_bucket = TokenBucket(capacity=global_burst, refill_rate=global_tps)
        self._peer_buckets: Dict[bytes, TokenBucket] = {}

        self._seen = TTLSet(ttl_seconds=seen_ttl_seconds)
        self._per_peer_capacity = float(per_peer_burst)
        self._per_peer_rate = float(per_peer_tps)

    # Public API -----------------------------------------------------------------------

    def check(
        self, *, peer_id: bytes, raw_cbor: bytes, now: Optional[float] = None
    ) -> Verdict:
        """
        Perform pre-admission checks. Returns a Verdict. On success, includes tx_hash.
        """
        # Size guard
        if len(raw_cbor) <= 0 or len(raw_cbor) > self.max_tx_bytes:
            return Verdict(
                False,
                drop_reason=DropReason.OVERSIZE,
                info=f"bytes={len(raw_cbor)} > cap={self.max_tx_bytes}",
            )

        # Hash early (for dedupe and tracking); ultra-cheap
        h = self._hash_raw(raw_cbor)

        # Dedupe (before ratelimit to avoid burning tokens on repeats)
        if h in self._seen:
            return Verdict(
                False, drop_reason=DropReason.DUPLICATE, info="seen_recently"
            )

        # Global ratelimit
        if not self._global_bucket.allow(now):
            return Verdict(
                False, drop_reason=DropReason.RATELIMIT_GLOBAL, info="global_bucket"
            )

        # Peer ratelimit
        if not self._peer_bucket(peer_id).allow(now):
            return Verdict(
                False, drop_reason=DropReason.RATELIMIT_PEER, info="peer_bucket"
            )

        # Minimal decode / chainId sanity (best-effort)
        if self.chain_id_expected is not None:
            ok, err = self._cheap_chain_id_check(raw_cbor, self.chain_id_expected)
            if not ok:
                return Verdict(
                    False,
                    drop_reason=(
                        DropReason.WRONG_CHAIN
                        if err == "mismatch"
                        else DropReason.DECODE_ERROR
                    ),
                    info=err,
                )

        # Mark as seen *after* success path so that downstream replays from the same
        # peer or others are naturally suppressed for TTL.
        self._seen.add(h, now)

        return Verdict(True, tx_hash=h)

    # Internals ------------------------------------------------------------------------

    def _peer_bucket(self, peer_id: bytes) -> TokenBucket:
        b = self._peer_buckets.get(peer_id)
        if b is None:
            b = TokenBucket(
                capacity=self._per_peer_capacity, refill_rate=self._per_peer_rate
            )
            self._peer_buckets[peer_id] = b
        return b

    @staticmethod
    def _hash_raw(raw: bytes) -> bytes:
        if _sha3_256 is not None:  # prefer shared impl for consistency
            return _sha3_256(raw)
        # portable fallback
        return hashlib.sha3_256(raw).digest()

    @staticmethod
    def _cheap_chain_id_check(raw_cbor: bytes, expected: int) -> Tuple[bool, str]:
        """
        Best-effort extraction of `chainId` from a CBOR-encoded Tx.
        We accommodate both map-keys being small ints or text strings.

        Returns (ok, reason) where reason is "mismatch" or "decode_error" on failure.
        """
        if cbor2 is None:
            return False, "decode_error"
        try:
            obj = cbor2.loads(raw_cbor)
        except Exception:
            return False, "decode_error"

        cid = None
        if isinstance(obj, dict):
            # Most canonical schemas use either key 1 or "chainId"
            for k in (1, "chainId", "cid"):
                if k in obj:
                    cid = obj[k]
                    break
        # Some txs are arrays/tuples: [kind, sender, nonce, ..., chainId, ...]
        if cid is None and isinstance(obj, (list, tuple)) and len(obj) >= 5:
            # Heuristic: search for a small int near the end that looks like a chain id
            tail = obj[-3:]
            for v in tail:
                if isinstance(v, int) and 0 <= v < (1 << 31):
                    cid = v
                    break

        if cid is None:
            return False, "decode_error"
        if cid != expected:
            return False, "mismatch"
        return True, ""


# --------------------------------------------------------------------------------------
# Convenience factory with sensible defaults drawn from mempool.config (if present)
# --------------------------------------------------------------------------------------


def from_config(
    *,
    chain_id_expected: Optional[int] = None,
    fallback_max_bytes: int = 128 * 1024,
    fallback_per_peer_tps: float = 40.0,
    fallback_global_tps: float = 400.0,
    fallback_seen_ttl: int = 300,
) -> P2PAdmission:
    """
    Create a P2PAdmission using values from `mempool.config` when available,
    otherwise fall back to safe defaults.
    """
    max_bytes = fallback_max_bytes
    per_peer_tps = fallback_per_peer_tps
    global_tps = fallback_global_tps
    seen_ttl = fallback_seen_ttl

    try:
        from mempool.config import MAX_TX_BYTES  # type: ignore
        from mempool.config import (P2P_GLOBAL_TPS, P2P_PER_PEER_TPS,
                                    RELAY_SEEN_TTL_S)

        max_bytes = int(
            getattr(MAX_TX_BYTES, "value", MAX_TX_BYTES)
        )  # allow simple constants or enums
        per_peer_tps = float(P2P_PER_PEER_TPS)
        global_tps = float(P2P_GLOBAL_TPS)
        seen_ttl = int(RELAY_SEEN_TTL_S)
    except Exception:  # pragma: no cover
        pass

    return P2PAdmission(
        max_tx_bytes=max_bytes,
        per_peer_tps=per_peer_tps,
        global_tps=global_tps,
        seen_ttl_seconds=seen_ttl,
        chain_id_expected=chain_id_expected,
    )


__all__ = [
    "P2PAdmission",
    "Verdict",
    "DropReason",
    "from_config",
]
