"""
execution.adapters.block_db — bridge to core.db.block_db

A thin, defensive adapter that persists and retrieves blocks together with their
receipts/logs, without hard-coding the underlying database implementation.

Goals
-----
• Provide a small, typed facade used by the execution engine and RPC layer.
• Support multiple core.db.block_db shapes via duck-typing (put_block +
  set_receipts, or put_block_with_receipts, etc.).
• Optionally verify that the receipts root matches the block header before
  persisting (caller can supply a receipts-root function).

This module intentionally does NOT compute block/receipt hashes itself; callers
may pass explicit callbacks if the underlying core DB doesn't expose them.

Typical usage
-------------
    db = BlockStore(core_block_db, compute_block_hash=hash_block, compute_receipts_root=receipts_root)

    # Persist a freshly executed block atomically
    db.persist_block(block, receipts, set_head=True)

    # Read back
    b = db.get_block_by_number(42)
    recs = db.get_receipts_by_hash(b.header.hash)

"""

from __future__ import annotations

from typing import (Any, Callable, List, Literal, Optional, Protocol, Sequence,
                    Tuple, runtime_checkable)

# ----------------------------- Public exceptions ------------------------------


class BlockAdapterError(Exception):
    """Base error for block-db adapter."""


class TxReceiptCountMismatch(BlockAdapterError):
    """Raised when len(receipts) != len(block.txs)."""


class ReceiptsRootMismatch(BlockAdapterError):
    """Raised when the provided receipts root does not match block.header."""


# ---------------------------- Underlying DB Protocol --------------------------


@runtime_checkable
class _CoreBlockDB(Protocol):
    # puts --------------------------------------------------------------------
    def put_block(self, block: Any) -> bytes: ...

    # Alternative legacy naming:
    def set_block(self, block: Any) -> None: ...

    # Combined variant (optional):
    def put_block_with_receipts(self, block: Any, receipts: Sequence[Any]) -> bytes: ...

    # receipts ----------------------------------------------------------------
    def set_receipts(self, block_hash: bytes, receipts: Sequence[Any]) -> None: ...
    def get_receipts(self, block_hash: bytes) -> Sequence[Any]: ...

    # gets --------------------------------------------------------------------
    def get_block_by_hash(self, block_hash: bytes) -> Any: ...
    def get_block_by_number(self, height: int) -> Any: ...

    # head pointers ------------------------------------------------------------
    def set_head(self, block_hash: bytes) -> None: ...
    def get_head(self) -> Any: ...

    # optional txn-ish APIs ---------------------------------------------------
    def snapshot(self) -> object: ...
    def revert(self, snap_id: object) -> None: ...
    def commit(self) -> None: ...


# ------------------------------ Helper utilities ------------------------------


def _maybe(db: Any, *names: str):
    """Return the first callable attribute that exists on db, else None."""
    for n in names:
        fn = getattr(db, n, None)
        if callable(fn):
            return fn
    return None


# ------------------------------- Main Facade ----------------------------------


class BlockStore:
    """
    Facade for persisting and retrieving blocks and their receipts.

    Parameters
    ----------
    core_db:
        An object implementing (duck-typed) the _CoreBlockDB protocol.
    compute_block_hash:
        Optional callable that returns the canonical block hash given a block.
        Used if core_db.put_block does not return a hash and the block doesn't
        expose a `.hash` attribute (bytes).
    compute_receipts_root:
        Optional callable that returns the receipts root (bytes) for a sequence
        of receipts; used for preflight verification against block.header.
    verify_receipts_root:
        If True (default) and compute_receipts_root is provided, verify that
        block.header.receiptsRoot == compute_receipts_root(receipts) before
        persisting.
    """

    def __init__(
        self,
        core_db: _CoreBlockDB,
        *,
        compute_block_hash: Optional[Callable[[Any], bytes]] = None,
        compute_receipts_root: Optional[Callable[[Sequence[Any]], bytes]] = None,
        verify_receipts_root: bool = True,
    ):
        self._db = core_db
        self._hash_block = compute_block_hash
        self._root_receipts = compute_receipts_root
        self._verify_root = verify_receipts_root

    # --------------------------------- puts ----------------------------------

    def persist_block(
        self,
        block: Any,
        receipts: Sequence[Any],
        *,
        set_head: bool = False,
    ) -> bytes:
        """
        Persist `block` and its `receipts` atomically when possible.

        Returns the block hash (bytes).

        Raises
        ------
        TxReceiptCountMismatch
            if len(receipts) != len(block.txs) (when block.txs available).
        ReceiptsRootMismatch
            if verify_receipts_root is enabled and the root differs.
        BlockAdapterError
            on unsupported underlying DB API or other runtime failures.
        """
        # Basic length sanity (best-effort; tolerate blocks that don't expose txs)
        txs = getattr(block, "txs", None)
        if txs is not None and len(receipts) != len(txs):
            raise TxReceiptCountMismatch(
                f"receipts length {len(receipts)} != txs length {len(txs)}"
            )

        # Optional receipts-root verification
        if self._verify_root and self._root_receipts is not None:
            want = self._root_receipts(receipts)
            got = getattr(getattr(block, "header", None), "receiptsRoot", None)
            if isinstance(got, (bytes, bytearray)):
                if got != want:
                    raise ReceiptsRootMismatch("receiptsRoot mismatch")
            # If header doesn't expose receiptsRoot, we skip verification.

        snap = _maybe(self._db, "snapshot")
        revert = _maybe(self._db, "revert")
        commit = _maybe(self._db, "commit")
        snap_id = None
        if snap and revert:
            snap_id = snap()

        try:
            # Attempt combined put first
            put_combined = _maybe(self._db, "put_block_with_receipts")
            if put_combined:
                block_hash = put_combined(block, receipts)
                if not isinstance(block_hash, (bytes, bytearray)):
                    # Some DBs may return None; derive if possible
                    block_hash = self._derive_block_hash(block)
            else:
                # Separate calls: put block, then receipts
                put_block = _maybe(self._db, "put_block", "set_block")
                if not put_block:
                    raise BlockAdapterError("underlying DB lacks put_block/set_block")
                res = put_block(block)
                block_hash = (
                    res
                    if isinstance(res, (bytes, bytearray))
                    else self._derive_block_hash(block)
                )

                set_receipts = _maybe(self._db, "set_receipts")
                if not set_receipts:
                    raise BlockAdapterError("underlying DB lacks set_receipts")
                set_receipts(block_hash, receipts)

            if set_head:
                set_head_fn = _maybe(self._db, "set_head")
                if not set_head_fn:
                    raise BlockAdapterError("underlying DB lacks set_head")
                set_head_fn(block_hash)

        except Exception:
            if snap_id is not None and revert:
                revert(snap_id)
            raise
        else:
            if commit:
                commit()
            return bytes(block_hash)

    # --------------------------------- gets ----------------------------------

    def get_block_by_hash(self, block_hash: bytes) -> Any:
        fn = _maybe(self._db, "get_block_by_hash")
        if not fn:
            raise BlockAdapterError("underlying DB lacks get_block_by_hash")
        return fn(block_hash)

    def get_block_by_number(self, height: int) -> Any:
        fn = _maybe(self._db, "get_block_by_number")
        if not fn:
            raise BlockAdapterError("underlying DB lacks get_block_by_number")
        return fn(height)

    def get_receipts_by_hash(self, block_hash: bytes) -> Sequence[Any]:
        fn = _maybe(self._db, "get_receipts")
        if not fn:
            raise BlockAdapterError("underlying DB lacks get_receipts")
        return fn(block_hash)

    def get_block_and_receipts_by_hash(
        self, block_hash: bytes
    ) -> Tuple[Any, Sequence[Any]]:
        return self.get_block_by_hash(block_hash), self.get_receipts_by_hash(block_hash)

    # --------------------------------- head ----------------------------------

    def get_head(self) -> Any:
        fn = _maybe(self._db, "get_head")
        if not fn:
            raise BlockAdapterError("underlying DB lacks get_head")
        return fn()

    def set_head(self, block_hash: bytes) -> None:
        fn = _maybe(self._db, "set_head")
        if not fn:
            raise BlockAdapterError("underlying DB lacks set_head")
        fn(block_hash)

    # ------------------------------- internals --------------------------------

    def _derive_block_hash(self, block: Any) -> bytes:
        """Derive block hash from block.hash or via callback; else error."""
        h = getattr(getattr(block, "header", None), "hash", None)
        if isinstance(h, (bytes, bytearray)):
            return bytes(h)
        h2 = getattr(block, "hash", None)
        if isinstance(h2, (bytes, bytearray)):
            return bytes(h2)
        if self._hash_block is not None:
            return bytes(self._hash_block(block))
        raise BlockAdapterError(
            "cannot determine block hash; provide compute_block_hash"
        )


# Public API
__all__ = [
    "BlockStore",
    "BlockAdapterError",
    "TxReceiptCountMismatch",
    "ReceiptsRootMismatch",
]
