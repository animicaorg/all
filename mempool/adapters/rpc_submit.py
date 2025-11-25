"""
RPC ↔ Mempool bridge
====================

This adapter is the single place the JSON-RPC layer uses to talk to *a real*
mempool. It’s intentionally forgiving: it works with the full mempool (mempool.pool.TxPool),
and gracefully falls back to the lightweight in-memory pending pool
(rpc.pending_pool.InMemoryPendingPool) when the real mempool is not wired.

Typical usage from rpc/methods/tx.py:
    adapter = RpcSubmitAdapter(tx_pool, pending_pool)  # one or both can be None
    res = await adapter.submit_decoded(tx=tx, raw_cbor=raw, tx_hash=tx_hash)
    view = await adapter.get_transaction(tx_hash)

The adapter tries multiple method names/signatures on the pool to avoid tight
coupling (duck typing):
- add(tx) / add_tx(tx) / submit(tx) for decoded Tx objects
- get(tx_hash) / by_hash(tx_hash) to read
- stats() (optional) for pool stats
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Optional, Protocol, Union, Mapping, MutableMapping

# Best-effort imports of rich mempool types/errors. If unavailable, we fallback.
try:
    from mempool.errors import (
        AdmissionError,
        ReplacementError,
        DoSError,
        FeeTooLow,
        NonceGap,
        Oversize,
    )
except Exception:  # pragma: no cover - optional dependency
    class _BaseErr(Exception): ...
    class AdmissionError(_BaseErr): ...
    class ReplacementError(_BaseErr): ...
    class DoSError(_BaseErr): ...
    class FeeTooLow(_BaseErr): ...
    class NonceGap(_BaseErr): ...
    class Oversize(_BaseErr): ...

# Pending pool (json-rpc fast path) is optional too.
try:
    from rpc.pending_pool import InMemoryPendingPool  # type: ignore
except Exception:  # pragma: no cover
    InMemoryPendingPool = None  # type: ignore


log = logging.getLogger(__name__)


class TxLike(Protocol):
    """Minimal surface we rely on from core.types.tx.Tx."""
    def hash(self) -> bytes: ...
    sender: bytes
    nonce: int
    maxFeePerGas: int  # or tip fields, used only for optional views


class PoolLike(Protocol):
    """Protocol we *try* to satisfy against mempool.pool.TxPool implementations."""
    async def add(self, tx: TxLike) -> Any: ...
    def get(self, tx_hash: bytes) -> Optional[Any]: ...
    def stats(self) -> Any: ...


class SubmitStatus(Enum):
    ADDED = auto()
    REPLACED = auto()
    DUPLICATE = auto()
    REJECTED = auto()
    QUEUED = auto()  # used by pending-pool fallback


@dataclass
class SubmitResult:
    status: SubmitStatus
    tx_hash: bytes
    reason: Optional[str] = None
    meta: Optional[Any] = None  # implementation-defined (e.g., TxMeta)


@dataclass
class TxView:
    """Lightweight, JSON-serializable view returned to RPC layer."""
    hash: str
    sender: Optional[str] = None
    nonce: Optional[int] = None
    max_fee_per_gas: Optional[int] = None
    raw_cbor_hex: Optional[str] = None  # present only from pending fallback
    origin: str = "pool"  # "pool" | "pending" | "unknown"


def _hex(b: Optional[bytes]) -> Optional[str]:
    return None if b is None else "0x" + b.hex()


class RpcSubmitAdapter:
    """
    Bridge that prefers the real mempool if available; otherwise, it enqueues
    into the in-memory pending pool so txs are at least visible via RPC.
    """

    def __init__(
        self,
        tx_pool: Optional[PoolLike] = None,
        pending_pool: Optional["InMemoryPendingPool"] = None,
        pending_ttl_seconds: int = 600,
    ) -> None:
        self._pool = tx_pool
        self._pending = pending_pool
        self._pending_ttl = pending_ttl_seconds

    # ----------------------------------------------------------------------------------
    # Submission
    # ----------------------------------------------------------------------------------

    async def submit_decoded(self, tx: TxLike, raw_cbor: bytes, tx_hash: bytes) -> SubmitResult:
        """
        Submit a *decoded* Tx to the real mempool if present; otherwise queue it
        in the pending pool. Returns a structured SubmitResult suitable for RPC.
        """
        # Prefer full mempool
        if self._pool is not None:
            log.debug("Submitting tx to mempool: %s", _hex(tx_hash))
            try:
                add_coro = self._try_pool_add(self._pool, tx)
                meta = await add_coro if asyncio.iscoroutine(add_coro) else add_coro
                return SubmitResult(status=SubmitStatus.ADDED, tx_hash=tx_hash, meta=meta)
            except ReplacementError as e:
                return SubmitResult(status=SubmitStatus.REPLACED, tx_hash=tx_hash, reason=str(e))
            except FeeTooLow as e:
                return SubmitResult(status=SubmitStatus.REJECTED, tx_hash=tx_hash, reason=f"fee_too_low: {e}")
            except NonceGap as e:
                return SubmitResult(status=SubmitStatus.REJECTED, tx_hash=tx_hash, reason=f"nonce_gap: {e}")
            except Oversize as e:
                return SubmitResult(status=SubmitStatus.REJECTED, tx_hash=tx_hash, reason=f"oversize: {e}")
            except AdmissionError as e:
                return SubmitResult(status=SubmitStatus.REJECTED, tx_hash=tx_hash, reason=f"admission_error: {e}")
            except DoSError as e:
                return SubmitResult(status=SubmitStatus.REJECTED, tx_hash=tx_hash, reason=f"dos_reject: {e}")
            except Exception as e:  # pragma: no cover - safety net
                log.exception("Unexpected mempool error during submit")
                return SubmitResult(status=SubmitStatus.REJECTED, tx_hash=tx_hash, reason=f"unknown: {e}")

        # Fallback: pending pool (if available)
        if self._pending is not None:
            try:
                self._pending_put(tx_hash, raw_cbor, ttl=self._pending_ttl)
                return SubmitResult(status=SubmitStatus.QUEUED, tx_hash=tx_hash)
            except Exception as e:  # pragma: no cover
                log.exception("Failed to enqueue into pending pool")
                return SubmitResult(status=SubmitStatus.REJECTED, tx_hash=tx_hash, reason=f"pending_enqueue_failed: {e}")

        # No pool at all
        return SubmitResult(status=SubmitStatus.REJECTED, tx_hash=tx_hash, reason="no_pool_available")

    # ----------------------------------------------------------------------------------
    # Reads
    # ----------------------------------------------------------------------------------

    async def get_transaction(self, tx_hash: bytes) -> Optional[TxView]:
        """
        Fetch a transaction from the mempool, or from the pending fallback.
        Returns a thin TxView for RPC serialization, or None if not found.
        """
        # Prefer real mempool view
        if self._pool is not None:
            item = self._try_pool_get(self._pool, tx_hash)
            if asyncio.iscoroutine(item):
                item = await item
            if item is not None:
                return self._view_from_pool_item(item, tx_hash)

        # Pending fallback
        if self._pending is not None:
            raw = self._pending_get(tx_hash)
            if raw is not None:
                return TxView(hash=_hex(tx_hash) or "0x", raw_cbor_hex=_hex(raw), origin="pending")

        return None

    async def pool_stats(self) -> Mapping[str, int]:
        """
        Return a minimal set of stats the RPC layer may expose:
        - size: number of txs in the real pool (if present)
        - pending_size: number of txs in pending fallback (if present)
        """
        out: dict[str, int] = {}
        if self._pool is not None and hasattr(self._pool, "stats"):
            try:
                st = self._pool.stats()
                if asyncio.iscoroutine(st):
                    st = await st
                # Try common shapes
                if isinstance(st, Mapping) and "size" in st:
                    out["size"] = int(st["size"])  # type: ignore[arg-type]
                elif hasattr(st, "size"):
                    out["size"] = int(st.size)  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover
                log.exception("pool.stats() failed")

        if self._pending is not None:
            try:
                n = self._pending_len()
                out["pending_size"] = n
            except Exception:  # pragma: no cover
                pass

        return out

    # ----------------------------------------------------------------------------------
    # Internals — tolerant duck-typing against various pool shapes
    # ----------------------------------------------------------------------------------

    def _try_pool_add(self, pool: Any, tx: TxLike) -> Union[Any, "asyncio.Future[Any]"]:
        """Try common method names for adding a decoded transaction."""
        for name in ("add", "add_tx", "submit"):
            if hasattr(pool, name):
                fn = getattr(pool, name)
                # sync or async; 1-arg (tx) expected
                return fn(tx)
        raise AdmissionError("pool does not expose an add/add_tx/submit method")

    def _try_pool_get(self, pool: Any, tx_hash: bytes) -> Union[Any, "asyncio.Future[Any]", None]:
        """Try common method names for fetching by hash."""
        for name in ("get", "by_hash", "get_tx", "get_by_hash"):
            if hasattr(pool, name):
                fn = getattr(pool, name)
                return fn(tx_hash)
        return None

    def _view_from_pool_item(self, item: Any, tx_hash: bytes) -> TxView:
        """
        Normalize a mempool item (PoolTx or Tx) to a TxView. We avoid importing
        concrete types here; instead, pluck a few well-known attributes if present.
        """
        sender = getattr(item, "sender", None)
        nonce = getattr(item, "nonce", None)
        max_fee = getattr(item, "maxFeePerGas", None)
        # Some pools wrap the Tx inside `.tx`
        inner = getattr(item, "tx", None)
        if inner is not None:
            sender = getattr(inner, "sender", sender)
            nonce = getattr(inner, "nonce", nonce)
            max_fee = getattr(inner, "maxFeePerGas", max_fee)

        sender_hex = _hex(sender) if isinstance(sender, (bytes, bytearray)) else None
        return TxView(
            hash=_hex(tx_hash) or "0x",
            sender=sender_hex,
            nonce=int(nonce) if isinstance(nonce, int) else None,
            max_fee_per_gas=int(max_fee) if isinstance(max_fee, int) else None,
            origin="pool",
        )

    # Pending fallback helpers — duck-type against the simple in-memory pool

    def _pending_put(self, tx_hash: bytes, raw_cbor: bytes, ttl: int) -> None:
        """
        Try common method names on the pending pool. We support:
        - put(tx_hash, raw, ttl_s=?)
        - add(tx_hash, raw, ttl=?)
        - enqueue(tx_hash, raw, ttl=?)
        """
        p = self._pending
        assert p is not None
        # Prefer put(..., ttl_s=?)
        if hasattr(p, "put"):
            sig = inspect.signature(p.put)  # type: ignore[attr-defined]
            kwargs = {}
            if "ttl_s" in sig.parameters:
                kwargs["ttl_s"] = ttl
            elif "ttl" in sig.parameters:
                kwargs["ttl"] = ttl
            return p.put(tx_hash, raw_cbor, **kwargs)  # type: ignore[attr-defined]

        for name in ("add", "enqueue"):
            if hasattr(p, name):
                fn = getattr(p, name)
                try:
                    return fn(tx_hash, raw_cbor, ttl=ttl)
                except TypeError:
                    return fn(tx_hash, raw_cbor)

        raise RuntimeError("pending pool has no put/add/enqueue method")

    def _pending_get(self, tx_hash: bytes) -> Optional[bytes]:
        p = self._pending
        assert p is not None
        for name in ("get", "by_hash", "get_tx"):
            if hasattr(p, name):
                return getattr(p, name)(tx_hash)
        # Sometimes pending pool is a simple dict-like
        if isinstance(p, MutableMapping):  # type: ignore[redundant-cast]
            return p.get(tx_hash)  # type: ignore[call-arg]
        return None

    def _pending_len(self) -> int:
        p = self._pending
        assert p is not None
        if hasattr(p, "__len__"):
            try:
                return int(len(p))  # type: ignore[arg-type]
            except Exception:
                pass
        if hasattr(p, "size"):
            try:
                return int(getattr(p, "size"))
            except Exception:
                pass
        return 0


__all__ = ["RpcSubmitAdapter", "SubmitResult", "SubmitStatus", "TxView"]
