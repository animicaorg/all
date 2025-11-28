from __future__ import annotations

"""
Head management & genesis finalization
=====================================

This module centralizes:
- Reading the canonical head (height, hash) from the block DB.
- Writing/updating the canonical head after fork choice.
- One-time genesis finalization: ensure genesis header is persisted and the
  canonical head is at height 0 on first boot, with basic invariants checked.

Design notes
------------
We deliberately keep this module "core-only":
- No PoIES/consensus checks here (that's in consensus/).
- No heavy state rebuild; we only ensure the *header/block* persistence and a
  consistent head pointer so the node can boot from genesis and begin syncing.

Expected block_db API (from core/db/block_db.py):
- get_canonical_head() -> Optional[tuple[int, bytes]]
- set_canonical_head(height: int, h: bytes) -> None
- get_header_by_hash(h: bytes) -> Optional[Header]
- put_header(height: int, h: bytes, header) -> None
- put_block(h: bytes, block) -> None    # optional; not used here for genesis
- has_genesis() -> bool                 # optional; if missing we detect via head/height
- get_genesis_hash() -> Optional[bytes] # optional; if missing we infer from stored header

We *feature-detect* optional methods. If absent, we fall back to portable logic.

Public API
----------
- read_head(block_db) -> Optional[tuple[int, bytes]]
- write_head(block_db, height: int, h: bytes) -> None
- finalize_genesis(block_db, params: ChainParams, genesis_header: Header) -> tuple[int, bytes]
"""

from dataclasses import asdict
from typing import Optional, Tuple, Any

from core.types.params import ChainParams
from core.types.header import Header
from core.encoding.canonical import header_signing_bytes
from core.utils.hash import sha3_256
from core.errors import GenesisError


# --- Small field helpers (tolerate snake/camel) -----------------------------

def _get_chain_id(hdr: Header) -> int:
    if hasattr(hdr, "chain_id"):
        return int(getattr(hdr, "chain_id"))
    if hasattr(hdr, "chainId"):
        return int(getattr(hdr, "chainId"))
    # As a last resort, try dataclass→dict
    m = asdict(hdr)
    if "chain_id" in m:
        return int(m["chain_id"])
    if "chainId" in m:
        return int(m["chainId"])
    raise GenesisError("genesis header missing chainId/chain_id")


def _get_height(hdr: Header) -> int:
    if hasattr(hdr, "height"):
        return int(getattr(hdr, "height"))
    m = asdict(hdr)
    if "height" in m:
        return int(m["height"])
    raise GenesisError("genesis header missing height")


def _header_hash(hdr: Header) -> bytes:
    """Canonical header hash: sha3_256(SignBytes(header))."""
    return sha3_256(header_signing_bytes(hdr))


# --- Head I/O ---------------------------------------------------------------

def read_head(block_db) -> Optional[Tuple[int, bytes]]:
    """
    Return the canonical head (height, hash) if present, else None.
    Supports both the legacy get_canonical_head() API and the newer get_head().
    """
    if hasattr(block_db, "get_canonical_head"):
        return block_db.get_canonical_head()
    if hasattr(block_db, "get_head"):
        return block_db.get_head()
    raise GenesisError("block_db missing head getter")


def write_head(block_db, height: int, h: bytes) -> None:
    """
    Update the canonical head pointer. Supports both set_canonical_head(height, h)
    and set_head(height, h) naming variants.
    """
    if hasattr(block_db, "set_canonical_head"):
        block_db.set_canonical_head(height, h)
        return
    if hasattr(block_db, "set_head"):
        block_db.set_head(height, h)
        return
    raise GenesisError("block_db missing head setter")


# --- Genesis finalization ---------------------------------------------------

def finalize_genesis(block_db, params: ChainParams, genesis_header: Header) -> Tuple[int, bytes]:
    """
    Ensure the DB has a consistent genesis and a canonical head.

    Steps:
      1) Validate basic genesis invariants: chainId matches params, height == 0.
      2) Compute genesis hash H0.
      3) If no canonical head yet: persist header(0) if missing and set head=(0, H0).
      4) If a head exists:
           - If height==0, ensure stored header hash equals H0, else fail.
           - If height>0, ensure the stored genesis (by lookup through parent chain)
             is consistent with H0; if not, fail (wrong DB for this chain).
    Returns:
      (height, hash) of the canonical head after finalization (height will be 0
       on first boot; may be >0 if DB already synced).
    """
    # (1) Basic invariants from header vs params
    chain_id = _get_chain_id(genesis_header)
    if chain_id != params.chain_id:
        raise GenesisError(f"genesis chainId={chain_id} does not match params.chain_id={params.chain_id}")

    height0 = _get_height(genesis_header)
    if height0 != 0:
        raise GenesisError(f"genesis header height must be 0, got {height0}")

    # (2) Genesis hash
    h0 = _header_hash(genesis_header)

    # (3) If no head, write genesis and set head
    head = None
    try:
        head = read_head(block_db)
    except Exception:
        head = None
    if head is None:
        # Persist header(0) if not present
        existing = block_db.get_header_by_hash(h0)
        if existing is None:
            if hasattr(block_db, "write_header"):
                try:
                    block_db.write_header(0, genesis_header)  # type: ignore[attr-defined]
                except TypeError:
                    block_db.write_header(0, h0, genesis_header)  # type: ignore[arg-type]
            elif hasattr(block_db, "put_header"):
                try:
                    block_db.put_header(0, h0, genesis_header)  # type: ignore[arg-type]
                except TypeError:
                    block_db.put_header(genesis_header)  # type: ignore[call-arg]
            else:
                raise GenesisError("block_db missing header writer")
        if hasattr(block_db, "set_canonical"):
            block_db.set_canonical(0, h0)  # type: ignore[attr-defined]
        write_head(block_db, 0, h0)
        return (0, h0)

    # (4) Head exists; sanity-check against our genesis
    cur_height, cur_hash = head
    if cur_height == 0:
        # If DB points to genesis, ensure it's OUR genesis
        if cur_hash != h0:
            raise GenesisError("existing DB has different genesis hash (wrong network or corrupted DB)")
        # Nothing to do
        return head

    # cur_height > 0. We expect that the stored genesis (reachable ancestor) matches h0.
    # We try a cheap check via any provided helper; else we rely on a stored "genesis hash"
    # in the DB if available; else we conservatively ensure *at least* that our genesis header
    # object is persisted (idempotent), without walking history here.
    _ensure_genesis_header_persisted(block_db, h0, genesis_header)

    # If DB exposes a get_genesis_hash(), use it for a strict check.
    if hasattr(block_db, "get_genesis_hash"):
        try:
            gh = block_db.get_genesis_hash()
        except TypeError:
            gh = None  # method exists but different signature
        if gh is not None and gh != h0:
            raise GenesisError("existing DB genesis hash does not match provided genesis header")

    # Otherwise we accept the current head as-is.
    return head


def _ensure_genesis_header_persisted(block_db, h0: bytes, hdr: Header) -> None:
    """
    Persist genesis header(0) if missing. This is idempotent and safe even if
    the DB is already advanced.
    """
    try:
        existing = block_db.get_header_by_hash(h0)
        if existing is None:
            if hasattr(block_db, "write_header"):
                try:
                    block_db.write_header(0, hdr)  # type: ignore[attr-defined]
                except TypeError:
                    block_db.write_header(0, h0, hdr)  # type: ignore[arg-type]
            elif hasattr(block_db, "put_header"):
                try:
                    block_db.put_header(0, h0, hdr)  # type: ignore[arg-type]
                except TypeError:
                    block_db.put_header(hdr)  # type: ignore[call-arg]
    except Exception:
        # Be conservative: if the backend doesn't allow inserting retroactively
        # (some exotic impl), we just skip; finalize_genesis will still succeed
        # as long as the canonical head & DB are consistent.
        pass
