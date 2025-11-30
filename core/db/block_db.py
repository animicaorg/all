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
from typing import Callable, Iterator, Optional, Tuple

from ..encoding.cbor import cbor_dumps, cbor_loads
from ..types.block import Block  # type: ignore
from ..types.header import Header  # type: ignore
from ..utils.bytes import to_hex
from ..utils.hash import sha3_256
from .kv import KV, Batch, ReadOnlyKV

# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

PFX_HDR = b"\x10"
PFX_BLK = b"\x11"
PFX_HIX = b"\x12"
PFX_META = b"\x1f"

META_HEAD_HASH = PFX_META + b"head_hash"
META_HEAD_HEIGHT = PFX_META + b"head_height"
META_GENESIS = PFX_META + b"genesis_hash"
META_CHAIN_ID = PFX_META + b"chain_id"


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
        self, height: int, block_hash: bytes, batch: Optional[Batch] = None
    ) -> None:
        """
        Set the canonical block at `height` to `block_hash`. Does not verify that the hash
        corresponds to a stored header—callers should ensure existence earlier.
        """
        if batch is None:
            self.kv.put(k_hix(height), block_hash)
        else:
            batch.put(k_hix(height), block_hash)

    def get_canonical_hash(self, height: int) -> Optional[bytes]:
        return self.kv.get(k_hix(height))

    # --- Head pointers ---

    def set_head(
        self, height: int, block_hash: bytes, batch: Optional[Batch] = None
    ) -> None:
        """
        Update the canonical head pointers. Usually called after writing the height index.
        """
        if batch is None:
            self.kv.put(META_HEAD_HEIGHT, _u64be(height))
            self.kv.put(META_HEAD_HASH, block_hash)
        else:
            batch.put(META_HEAD_HEIGHT, _u64be(height))
            batch.put(META_HEAD_HASH, block_hash)

    def get_head(self) -> Optional[Tuple[int, bytes]]:
        h_raw = self.kv.get(META_HEAD_HEIGHT)
        if h_raw is None:
            return None
        n = _from_u64be(h_raw)
        hh = self.kv.get(META_HEAD_HASH)
        if hh is None:
            return None
        return (n, hh)

    def set_genesis_hash(
        self, block_hash: bytes, batch: Optional[Batch] = None
    ) -> None:
        if batch is None:
            self.kv.put(META_GENESIS, block_hash)
        else:
            batch.put(META_GENESIS, block_hash)

    def get_genesis_hash(self) -> Optional[bytes]:
        return self.kv.get(META_GENESIS)

    def set_chain_id(self, chain_id: int, batch: Optional[Batch] = None) -> None:
        if batch is None:
            self.kv.put(META_CHAIN_ID, _u64be(chain_id))
        else:
            batch.put(META_CHAIN_ID, _u64be(chain_id))

    def get_chain_id(self) -> Optional[int]:
        v = self.kv.get(META_CHAIN_ID)
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
        Intended for linear devnet import or after fork-choice has selected this block.

        Returns the block hash.
        """
        hh = header_hash(block.header)  # type: ignore[attr-defined]
        with self.kv.batch() as b:
            self.put_block(block, batch=b)
            self.set_canonical(height, hh, batch=b)
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
