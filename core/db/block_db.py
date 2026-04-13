from __future__ import annotations

"""
Block DB (headers, blocks, canonical head pointers)
===================================================

This module stores canonical headers/blocks and provides fast lookup by hash
or height. It also tracks the canonical head (height, hash). Fork-choice is
handled in consensus/; this DB accepts any block and lets callers set which
hash is canonical at a given height by writing the height→hash index and
updating the head pointer.

Key layout
----------
- HDR := 0x10 | hash(32)                       -> cbor(Header)
- BLK := 0x11 | hash(32)                       -> cbor(Block)
- HIX := 0x12 | height:u64be                   -> hash(32)      (canonical index: height → hash)
- META:
    * HEAD_H := 0x1F | b"head_hash"            -> hash(32)
    * HEAD_N := 0x1F | b"head_height"          -> u64be
    * GENESIS := 0x1F | b"genesis_hash"        -> hash(32)      (optional helper)
    * CHAINID := 0x1F | b"chain_id"            -> u64be         (optional helper)

Notes
-----
- We do not maintain a TX index here (see core/db/tx_index.py).
- Heights are stored big-endian to preserve lexicographic ordering.
- The "block hash" used here is the hash of the *header*'s canonical encoding.
  Blocks are keyed by that same hash for convenience.

"""

from dataclasses import asdict, is_dataclass
import logging
from typing import Callable, Iterator, Optional, Tuple

from ..encoding.cbor import cbor_dumps, cbor_loads
from ..types.block import Block  # type: ignore
from ..types.header import Header  # type: ignore
from ..utils.bytes import to_hex
from ..utils.hash import sha3_256
from .kv import KV, Batch, ReadOnlyKV

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

PFX_HDR = b"\x10"
PFX_BLK = b"\x11"
PFX_HIX = b"\x12"
PFX_RXI = b"\x22"  # Receipt index: tx_hash → (height, index, receipt)
PFX_DMI = b"\x23"  # Deploy metadata index: tx_hash → deploy metadata map
PFX_META = b"\x1f"

META_HEAD_HASH = PFX_META + b"head_hash"
META_HEAD_HEIGHT = PFX_META + b"head_height"
META_CANONICAL_HEIGHT = PFX_META + b"canonical_height"  # Height excluding instant blocks
META_GENESIS = PFX_META + b"genesis_hash"
META_CHAIN_ID = PFX_META + b"chain_id"
META_GENESIS_SHA256 = PFX_META + b"genesis_sha256"
META_GENESIS_CREATED_AT = PFX_META + b"genesis_created_at"


def _u64be(n: int) -> bytes:
    if n < 0 or n > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("u64 out of range")
    return n.to_bytes(8, "big")


def _from_u64be(b: bytes) -> int:
    if len(b) != 8:
        raise ValueError("expected 8 bytes for u64")
    return int.from_bytes(b, "big")


def k_hdr(h: bytes) -> bytes:
    return PFX_HDR + h


def k_blk(h: bytes) -> bytes:
    return PFX_BLK + h


def k_hix(height: int) -> bytes:
    return PFX_HIX + _u64be(height)


def k_rxi(tx_hash: bytes) -> bytes:
    return PFX_RXI + tx_hash


def k_dmi(tx_hash: bytes) -> bytes:
    return PFX_DMI + tx_hash


# ---------------------------------------------------------------------------
# Encoding helpers (tolerant of dataclass with/without to_cbor)
# ---------------------------------------------------------------------------


def _to_cbor(obj) -> bytes:
    # Prefer object-provided to_cbor for canonical layout.
    if hasattr(obj, "to_cbor") and callable(getattr(obj, "to_cbor")):
        return obj.to_cbor()  # type: ignore[attr-defined]
    # Fallback: dataclass → dict → CBOR
    if is_dataclass(obj):
        return cbor_dumps(asdict(obj))
    # Last resort: trust it's already a json-like structure
    return cbor_dumps(obj)


def _from_cbor_header(b: bytes) -> Header:
    if hasattr(Header, "from_cbor"):
        return Header.from_cbor(b)  # type: ignore[attr-defined]
    d = cbor_loads(b)
    return Header(**d)  # type: ignore[arg-type]


def _from_cbor_block(b: bytes) -> Block:
    if hasattr(Block, "from_cbor"):
        return Block.from_cbor(b)  # type: ignore[attr-defined]
    d = cbor_loads(b)
    return Block(**d)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def header_hash(header: Header) -> bytes:
    """
    Compute the canonical header hash. We prefer a dedicated method if present;
    otherwise hash the CBOR bytes of the header value.
    """
    if hasattr(header, "hash") and callable(getattr(header, "hash")):
        h = header.hash()  # type: ignore[attr-defined]
        return bytes(h)
    return sha3_256(_to_cbor(header))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class BlockDB:
    """
    Block/Header store with canonical height index & head pointers.
    """

    def __init__(self, kv: KV):
        self.kv = kv

    # --- Store ---

    def put_header(self, header: Header, batch: Optional[Batch] = None) -> bytes:
        hh = header_hash(header)
        data = _to_cbor(header)
        if batch is None:
            self.kv.put(k_hdr(hh), data)
        else:
            batch.put(k_hdr(hh), data)
        return hh

    def put_block(self, block: Block, batch: Optional[Batch] = None) -> bytes:
        """
        Store a full block. The block hash is derived from its header.
        This does *not* set the canonical index; call set_canonical(height, hash).
        """
        hh = header_hash(block.header)  # type: ignore[attr-defined]
        bdata = _to_cbor(block)
        hdata = _to_cbor(block.header)  # store header too for completeness
        if batch is None:
            self.kv.put(k_blk(hh), bdata)
            self.kv.put(k_hdr(hh), hdata)
        else:
            batch.put(k_blk(hh), bdata)
            batch.put(k_hdr(hh), hdata)
        return hh

    # --- Canonical index ---

    def set_canonical(
        self,
        height: int,
        block_hash: bytes,
        batch: Optional[Batch] = None,
        *,
        allow_overwrite: bool = False,
    ) -> None:
        """
        Set the canonical block at `height` to `block_hash`. Does not verify that the hash
        corresponds to a stored header—callers should ensure existence earlier.
        """
        existing = self.kv.get(k_hix(height))
        if existing is not None and existing != block_hash and not allow_overwrite:
            log.error(
                "refusing to overwrite canonical block at height %d",
                height,
                extra={"existing": to_hex(existing), "incoming": to_hex(block_hash)},
            )
            raise ValueError(
                "refusing to overwrite canonical block at height "
                f"{height}: {to_hex(existing)} != {to_hex(block_hash)}"
            )
        if batch is None:
            self.kv.put(k_hix(height), block_hash)
        else:
            batch.put(k_hix(height), block_hash)

    def get_canonical_hash(self, height: int) -> Optional[bytes]:
        return self.kv.get(k_hix(height))

    # --- Head pointers ---

    def set_head(
        self,
        height: int,
        block_hash: bytes,
        batch: Optional[Batch] = None,
        *,
        allow_reorg: bool = False,
    ) -> None:
        """
        Update the canonical head pointers. Usually called after writing the height index.
        """
        cur = self.get_head()
        if cur is not None:
            cur_height, cur_hash = cur
            if height <= cur_height and block_hash != cur_hash and not allow_reorg:
                log.error(
                    "refusing to move head without explicit reorg",
                    extra={
                        "current_height": cur_height,
                        "current_hash": to_hex(cur_hash),
                        "next_height": height,
                        "next_hash": to_hex(block_hash),
                    },
                )
                raise ValueError(
                    "refusing to move head without explicit reorg: "
                    f"{cur_height}->{height}"
                )
        if batch is None:
            self.kv.put(META_HEAD_HEIGHT, _u64be(height))
            self.kv.put(META_HEAD_HASH, block_hash)
        else:
            batch.put(META_HEAD_HEIGHT, _u64be(height))
            batch.put(META_HEAD_HASH, block_hash)

    def set_canonical_head(
        self,
        height: int,
        block_hash: bytes,
        batch: Optional[Batch] = None,
        *,
        allow_overwrite: bool = False,
        allow_reorg: bool = False,
    ) -> None:
        """
        Convenience helper that updates both the canonical height index and
        the head pointers.
        """
        self.set_canonical(height, block_hash, batch=batch, allow_overwrite=allow_overwrite)
        self.set_head(height, block_hash, batch=batch, allow_reorg=allow_reorg)

    def get_head(self) -> Optional[Tuple[int, bytes]]:
        h_raw = self.kv.get(META_HEAD_HEIGHT)
        if h_raw is None:
            return None
        n = _from_u64be(h_raw)
        hh = self.kv.get(META_HEAD_HASH)
        if hh is None:
            return None
        return (n, hh)

    def get_canonical_head(self) -> Optional[Tuple[int, bytes]]:
        """
        Return the canonical head (height, hash) if available.

        Prefers the explicit head pointers; falls back to the canonical height
        index if needed.
        """
        head = self.get_head()
        if head is not None:
            return head
        iterator = getattr(self.kv, "iter_prefix", None)
        if not callable(iterator):
            return None
        try:
            max_height = -1
            max_hash: Optional[bytes] = None
            for key, value in iterator(PFX_HIX):
                if len(key) < len(PFX_HIX) + 8:
                    continue
                height = _from_u64be(key[-8:])
                if height >= max_height:
                    max_height = height
                    max_hash = bytes(value)
            if max_hash is None:
                return None
            return (max_height, max_hash)
        except Exception:
            return None

    def set_genesis_hash(
        self, block_hash: bytes, batch: Optional[Batch] = None
    ) -> None:
        if batch is None:
            self.kv.put(META_GENESIS, block_hash)
        else:
            batch.put(META_GENESIS, block_hash)

    def get_genesis_hash(self) -> Optional[bytes]:
        return self.kv.get(META_GENESIS)

    def set_genesis_sha256(
        self, sha256: bytes, batch: Optional[Batch] = None
    ) -> None:
        if batch is None:
            self.kv.put(META_GENESIS_SHA256, sha256)
        else:
            batch.put(META_GENESIS_SHA256, sha256)

    def get_genesis_sha256(self) -> Optional[bytes]:
        return self.kv.get(META_GENESIS_SHA256)

    def set_genesis_created_at(
        self, created_at: int, batch: Optional[Batch] = None
    ) -> None:
        if batch is None:
            self.kv.put(META_GENESIS_CREATED_AT, _u64be(created_at))
        else:
            batch.put(META_GENESIS_CREATED_AT, _u64be(created_at))

    def get_genesis_created_at(self) -> Optional[int]:
        v = self.kv.get(META_GENESIS_CREATED_AT)
        return None if v is None else _from_u64be(v)

    def set_chain_id(self, chain_id: int, batch: Optional[Batch] = None) -> None:
        if batch is None:
            self.kv.put(META_CHAIN_ID, _u64be(chain_id))
        else:
            batch.put(META_CHAIN_ID, _u64be(chain_id))

    def get_chain_id(self) -> Optional[int]:
        v = self.kv.get(META_CHAIN_ID)
        return None if v is None else _from_u64be(v)

    def set_canonical_height(self, height: int, batch: Optional[Batch] = None) -> None:
        """
        Set the canonical height (excluding instant blocks).
        This is used for halving schedule calculations.
        """
        if batch is None:
            self.kv.put(META_CANONICAL_HEIGHT, _u64be(height))
        else:
            batch.put(META_CANONICAL_HEIGHT, _u64be(height))

    def get_canonical_height(self) -> Optional[int]:
        """
        Get the canonical height (excluding instant blocks).
        Returns None if not set (e.g., at genesis).
        """
        v = self.kv.get(META_CANONICAL_HEIGHT)
        return None if v is None else _from_u64be(v)

    # --- Lookups by hash/height ---

    def get_header_by_hash(self, block_hash: bytes) -> Optional[Header]:
        v = self.kv.get(k_hdr(block_hash))
        return None if v is None else _from_cbor_header(v)

    def get_block_by_hash(self, block_hash: bytes) -> Optional[Block]:
        v = self.kv.get(k_blk(block_hash))
        if v is None:
            # If full block wasn't stored, fall back to header-only presence.
            hv = self.kv.get(k_hdr(block_hash))
            if hv is None:
                return None
            # synthesize a Block with header only if Block type allows it
            header = _from_cbor_header(hv)
            try:
                return Block(header=header, txs=[], proofs=[], receipts=None)  # type: ignore[call-arg]
            except TypeError:
                # Fallback: return None to avoid fabricating incorrect structure
                return None
        return _from_cbor_block(v)

    def get_header_by_height(self, height: int) -> Optional[Header]:
        hh = self.get_canonical_hash(height)
        return None if hh is None else self.get_header_by_hash(hh)

    def get_block_by_height(self, height: int) -> Optional[Block]:
        hh = self.get_canonical_hash(height)
        return None if hh is None else self.get_block_by_hash(hh)

    # --- Batch lookups for sync performance ---

    def has_blocks_batch(self, block_hashes: list[bytes]) -> set[bytes]:
        """
        Check which blocks exist in the database from a list of block hashes.
        
        Returns a set of hashes that exist in the DB. This is much faster than
        calling has_block/get_block_by_hash individually for each hash when
        syncing thousands of blocks.
        
        This implementation uses batch reads where possible for optimal performance.
        
        Args:
            block_hashes: List of block hashes to check
            
        Returns:
            Set of block hashes that exist in the database
        """
        if not block_hashes:
            return set()
        
        existing = set()
        # Check if KV supports batch operations for even better performance
        if hasattr(self.kv, 'get_batch'):
            # Build keys to check
            block_keys = [k_blk(h) for h in block_hashes]
            header_keys = [k_hdr(h) for h in block_hashes]
            all_keys = block_keys + header_keys
            
            # Batch fetch all keys at once
            results = self.kv.get_batch(all_keys)
            
            # Check which hashes had either block or header
            for i, h in enumerate(block_hashes):
                # Check if block or header exists for this hash
                if results[i] is not None or results[i + len(block_hashes)] is not None:
                    existing.add(h)
        else:
            # Fallback to individual checks (still faster than async individual calls)
            for h in block_hashes:
                # Check if either full block or header exists
                if self.kv.get(k_blk(h)) is not None or self.kv.get(k_hdr(h)) is not None:
                    existing.add(h)
        return existing

    def has_headers_batch(self, header_hashes: list[bytes]) -> set[bytes]:
        """
        Check which headers exist in the database from a list of header hashes.
        
        Returns a set of hashes that exist in the DB. This is much faster than
        calling has_header/get_header_by_hash individually for each hash when
        syncing thousands of headers.
        
        This implementation uses batch reads where possible for optimal performance.
        
        Args:
            header_hashes: List of header hashes to check
            
        Returns:
            Set of header hashes that exist in the database
        """
        if not header_hashes:
            return set()
        
        existing = set()
        # Check if KV supports batch operations for even better performance
        if hasattr(self.kv, 'get_batch'):
            # Build keys to check
            keys = [k_hdr(h) for h in header_hashes]
            
            # Batch fetch all keys at once
            results = self.kv.get_batch(keys)
            
            # Check which hashes exist
            for i, h in enumerate(header_hashes):
                if results[i] is not None:
                    existing.add(h)
        else:
            # Fallback to individual checks (still faster than async individual calls)
            for h in header_hashes:
                if self.kv.get(k_hdr(h)) is not None:
                    existing.add(h)
        return existing

    # --- Receipt lookup by tx_hash ---

    def get_receipt_loc_by_hash(self, tx_hash: bytes) -> Optional[dict]:
        """
        Look up receipt location (height, index, block_hash) by transaction hash.
        
        This is used by the RPC layer to find where a receipt is stored.
        The actual receipt can then be fetched via get_block_by_height(height).receipts[index].
        
        Returns:
            Dict with keys {"height": int, "index": int, "block_hash": bytes} if found, None otherwise.
        """
        ptr_data = self.kv.get(k_rxi(tx_hash))
        if ptr_data is None:
            return None
        
        # Decode pointer: {h: height, i: index, b: block_hash}
        ptr = cbor_loads(ptr_data)
        return {
            "height": int(ptr["h"]),
            "index": int(ptr["i"]),
            "block_hash": bytes(ptr["b"]),
        }

    def get_receipt_by_tx_hash(self, tx_hash: bytes) -> Optional[Tuple[int, int, bytes, Any]]:
        """
        Look up a receipt by transaction hash.
        
        Returns:
            Tuple of (height, tx_index, block_hash, receipt_obj) if found, None otherwise.
            The receipt_obj is the Receipt instance from the block.
        """
        loc = self.get_receipt_loc_by_hash(tx_hash)
        if loc is None:
            return None
        
        height = loc["height"]
        idx = loc["index"]
        block_hash = loc["block_hash"]
        
        # Fetch the block to get the receipt
        block = self.get_block_by_hash(block_hash)
        if block is None or block.receipts is None:
            return None
        
        if idx >= len(block.receipts):
            return None
        
        receipt = block.receipts[idx]
        return (height, idx, block_hash, receipt)

    def get_transaction_by_hash(self, tx_hash: bytes) -> Optional[Tuple[int, int, bytes, Any]]:
        """
        Look up a transaction by hash.
        
        Uses the receipt index (PFX_RXI) to find the transaction location, then
        fetches the block to get the actual transaction object.
        
        Returns:
            Tuple of (height, tx_index, block_hash, tx_obj) if found, None otherwise.
            The tx_obj is the Tx instance from the block.
        """
        loc = self.get_receipt_loc_by_hash(tx_hash)
        if loc is None:
            return None
        
        height = loc["height"]
        idx = loc["index"]
        block_hash = loc["block_hash"]
        
        # Fetch the block to get the transaction
        block = self.get_block_by_hash(block_hash)
        if block is None or block.txs is None:
            return None
        
        if idx >= len(block.txs):
            return None
        
        tx = block.txs[idx]
        return (height, idx, block_hash, tx)

    # --- Deploy metadata lookup by tx_hash ---

    def put_deploy_metadata_by_tx_hash(
        self,
        tx_hash: bytes,
        metadata: dict,
        *,
        batch: Optional[Batch] = None,
    ) -> None:
        """
        Persist deployment metadata for a transaction hash.
        """
        payload = cbor_dumps(metadata)
        if batch is None:
            self.kv.put(k_dmi(tx_hash), payload)
        else:
            batch.put(k_dmi(tx_hash), payload)

    def get_deploy_metadata_by_tx_hash(self, tx_hash: bytes) -> Optional[dict]:
        """
        Fetch deployment metadata by transaction hash.
        """
        payload = self.kv.get(k_dmi(tx_hash))
        if payload is None:
            return None
        decoded = cbor_loads(payload)
        if isinstance(decoded, dict):
            return decoded
        return None

    # --- Iteration over canonical chain ---

    def iter_canonical_headers(
        self, start: int = 0, end_inclusive: Optional[int] = None
    ) -> Iterator[Tuple[int, bytes, Header]]:
        """
        Iterate canonical headers from `start` to `end_inclusive` (or to head if None).
        Yields (height, hash, Header).
        """
        if end_inclusive is None:
            head = self.get_head()
            if head is None:
                return
            end_inclusive = head[0]

        for h in range(start, end_inclusive + 1):
            hh = self.get_canonical_hash(h)
            if hh is None:
                continue
            hv = self.kv.get(k_hdr(hh))
            if hv is None:
                continue
            yield (h, hh, _from_cbor_header(hv))

    # --- Convenience: atomic write of block + canonical + head ---

    def append_canonical_block(self, height: int, block: Block) -> bytes:
        """
        Atomically store a block, mark it canonical at `height`, and advance head if higher.
        Also index transactions and receipts for fast lookup by tx_hash.
        Intended for linear devnet import or after fork-choice has selected this block.

        Returns the block hash.
        """
        hh = header_hash(block.header)  # type: ignore[attr-defined]
        existing = self.get_canonical_hash(height)
        if existing is not None:
            if existing != hh:
                raise ValueError(
                    "refusing to overwrite canonical block at height "
                    f"{height}: {to_hex(existing)} != {to_hex(hh)}"
                )
            return hh
        with self.kv.batch() as b:
            self.put_block(block, batch=b)
            self.set_canonical(height, hh, batch=b)
            
            # Index transactions and receipts by tx_hash for fast RPC lookup
            # This allows tx.getReceipt to find receipts by tx_hash efficiently
            if block.txs:
                for idx, tx in enumerate(block.txs):
                    # Get tx hash - prefer dedicated hash() method
                    if hasattr(tx, 'hash') and callable(tx.hash):
                        tx_hash = tx.hash()
                    else:
                        # Fallback: compute hash from CBOR encoding
                        # Note: This should match the canonical tx hash computation
                        tx_hash = sha3_256(_to_cbor(tx))
                    
                    # Store pointer: tx_hash → (height, idx, block_hash)
                    # Receipt can be retrieved via get_block_by_height then indexing receipts[idx]
                    receipt_ptr = cbor_dumps({"h": height, "i": idx, "b": hh})
                    b.put(k_rxi(tx_hash), receipt_ptr)
            
            cur = self.get_head()
            if cur is None or height >= cur[0]:
                self.set_head(height, hh, batch=b)
            b.commit()
        return hh

    # --- Debug helpers ---

    def __repr__(self) -> str:
        head = self.get_head()
        if head is None:
            return "<BlockDB head=None>"
        return f"<BlockDB head=({head[0]}, {to_hex(head[1])})>"


__all__ = [
    "BlockDB",
    "header_hash",
]
