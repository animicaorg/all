from __future__ import annotations

"""
Compact block announce protocol.

Goal
----
Announce new blocks quickly with enough information for peers to decide whether to
fetch, and (optionally) allow a "compact" path where most transactions are mapped
by short-ids and only the missing bodies are requested.

This module only defines *payload* shapes (msgspec structs) and helpers for:
  • CompactAnnounce       (ANNOUNCE)
  • RequestBlockPieces    (GET_BLOCK with want-mask)
  • RequestMissingTxs     (ASK_MISSING by short-ids)
  • RespondMissingTxs     (MISSING_TXS returns tx bodies for requested short-ids)

Wire framing (message id, AEAD, checksums) lives in p2p.wire.*  The small "t" tag
field below disambiguates the payload variant inside this sub-protocol.

Conventions
-----------
- Hashes are 32 bytes (sha3-256) unless stated otherwise.
- Heights are unsigned 64-bit integers.
- Short-ids are 6 bytes derived deterministically from header_hash||tx_hash via SHA3-256,
  truncated to 6 bytes (48 bits). This is collision-resistant enough for compact usage
  with bounded fanout; ties will be handled by the responder (return conflicting set).
- The "want" bitmask in RequestBlockPieces:
    0x01 = header
    0x02 = transactions (full bodies)
    0x04 = proofs (PoIES envelopes)
    0x08 = receipts (if available)
  Peers are free to ignore unknown bits.

Limits import from p2p.constants when available and fall back to sane defaults.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

import msgspec

try:
    from p2p.constants import (HASH_LEN, MAX_BLOCK_TXS, MAX_PROOFS,
                               MAX_TXIDS_PER_COMPACT, SHORT_ID_LEN)
except Exception:  # pragma: no cover
    HASH_LEN = 32
    SHORT_ID_LEN = 6
    MAX_TXIDS_PER_COMPACT = 8192
    MAX_BLOCK_TXS = 1_000_000  # upper bound guard; real value comes from chain params
    MAX_PROOFS = 4096

# -- Local payload tags (not wire msg_ids) --
TAG_ANNOUNCE = 10
TAG_GET_BLOCK = 11
TAG_ASK_MISSING = 12
TAG_MISSING_TXS = 13


class ProtocolError(Exception):
    pass


# ---------------------- Structs ----------------------


class _CompactAnnounceS(msgspec.Struct, omit_defaults=True):
    """
    CompactAnnounce:
      t   : TAG_ANNOUNCE
      hh  : block hash (32B)
      ph  : parent hash (32B)
      ht  : height (u64)
      sc  : scalar 'score' (e.g., µ-nats or any monotone weight) as int
      tc  : tx count (uint)
      pc  : proofs count (uint)
      sid : list[bytes(SHORT_ID_LEN)] optional (short-ids of txs in canonical order)
    """

    t: int
    hh: bytes
    ph: bytes
    ht: int
    sc: int
    tc: int
    pc: int
    sid: List[bytes] = msgspec.field(default_factory=list)


class _RequestBlockPiecesS(msgspec.Struct, omit_defaults=True):
    """
    RequestBlockPieces:
      t   : TAG_GET_BLOCK
      hh  : block hash (32B)
      want: bitmask (header=1, txs=2, proofs=4, receipts=8)
    """

    t: int
    hh: bytes
    want: int


class _RequestMissingTxsS(msgspec.Struct, omit_defaults=True):
    """
    RequestMissingTxs:
      t   : TAG_ASK_MISSING
      hh  : block hash (32B)
      sid : list[bytes(SHORT_ID_LEN)] — short-ids to fetch
    """

    t: int
    hh: bytes
    sid: List[bytes]


class _RespondMissingTxsS(msgspec.Struct, omit_defaults=True):
    """
    RespondMissingTxs:
      t   : TAG_MISSING_TXS
      hh  : block hash (32B)
      tx  : list[bytes] — raw CBOR-encoded tx bodies matching request order
      dup : list[int]   — indices of entries that collided (optional; default empty)
    """

    t: int
    hh: bytes
    tx: List[bytes]
    dup: List[int] = msgspec.field(default_factory=list)


ENC = msgspec.msgpack.Encoder()
DEC_ANN = msgspec.msgpack.Decoder(type=_CompactAnnounceS)
DEC_GET = msgspec.msgpack.Decoder(type=_RequestBlockPiecesS)
DEC_ASK = msgspec.msgpack.Decoder(type=_RequestMissingTxsS)
DEC_MTX = msgspec.msgpack.Decoder(type=_RespondMissingTxsS)


# ---------------------- Helpers ----------------------


def short_id(header_hash: bytes, tx_hash: bytes) -> bytes:
    """
    Compute a 6-byte short-id deterministically from header-hash and tx-hash.
    SID = SHA3-256( b"sid" || header_hash || tx_hash )[0:6]
    """
    if len(header_hash) != HASH_LEN or len(tx_hash) != HASH_LEN:
        raise ProtocolError("bad hash length for short-id")
    import hashlib

    h = hashlib.sha3_256(b"sid" + header_hash + tx_hash).digest()
    return h[:SHORT_ID_LEN]


def _check_hash(tag: str, h: bytes) -> None:
    if not isinstance(h, (bytes, bytearray)) or len(h) != HASH_LEN:
        raise ProtocolError(
            f"{tag}: invalid hash length {len(h) if isinstance(h,(bytes,bytearray)) else 'NA'}"
        )


def _check_sid_list(sids: Sequence[bytes]) -> None:
    if len(sids) > MAX_TXIDS_PER_COMPACT:
        raise ProtocolError(f"too many short-ids (max {MAX_TXIDS_PER_COMPACT})")
    for s in sids:
        if not isinstance(s, (bytes, bytearray)) or len(s) != SHORT_ID_LEN:
            raise ProtocolError("short-id must be bytes of length SHORT_ID_LEN")


def _check_counts(txc: int, pc: int) -> None:
    if txc < 0 or txc > MAX_BLOCK_TXS:
        raise ProtocolError("tx count out of range")
    if pc < 0 or pc > MAX_PROOFS:
        raise ProtocolError("proof count out of range")


# ---------------------- Builders ----------------------


@dataclass(frozen=True)
class CompactAnnounce:
    header_hash: bytes
    parent_hash: bytes
    height: int
    score: int
    tx_count: int
    proofs_count: int
    short_ids: List[bytes]


def build_announce(
    header_hash: bytes,
    parent_hash: bytes,
    height: int,
    score: int,
    tx_count: int,
    proofs_count: int,
    short_ids: Iterable[bytes] | None = None,
) -> bytes:
    """Encode a CompactAnnounce payload."""
    _check_hash("header", header_hash)
    _check_hash("parent", parent_hash)
    _check_counts(tx_count, proofs_count)
    sids = list(short_ids or [])
    _check_sid_list(sids)
    msg = _CompactAnnounceS(
        t=TAG_ANNOUNCE,
        hh=bytes(header_hash),
        ph=bytes(parent_hash),
        ht=int(height),
        sc=int(score),
        tc=int(tx_count),
        pc=int(proofs_count),
        sid=[bytes(s) for s in sids],
    )
    return ENC.encode(msg)


def build_get_block(header_hash: bytes, want_mask: int) -> bytes:
    """Encode RequestBlockPieces for a given block hash and 'want' bitmask."""
    _check_hash("header", header_hash)
    msg = _RequestBlockPiecesS(
        t=TAG_GET_BLOCK, hh=bytes(header_hash), want=int(want_mask)
    )
    return ENC.encode(msg)


def build_ask_missing(header_hash: bytes, short_ids: Iterable[bytes]) -> bytes:
    """Encode RequestMissingTxs for a given block hash."""
    _check_hash("header", header_hash)
    sids = list(short_ids)
    _check_sid_list(sids)
    msg = _RequestMissingTxsS(
        t=TAG_ASK_MISSING, hh=bytes(header_hash), sid=[bytes(s) for s in sids]
    )
    return ENC.encode(msg)


def build_missing_txs(
    header_hash: bytes, tx_bodies: Iterable[bytes], dup_indices: Iterable[int] = ()
) -> bytes:
    """Encode RespondMissingTxs with raw CBOR-encoded tx bodies and optional duplicate indices."""
    _check_hash("header", header_hash)
    txs = [bytes(b) for b in tx_bodies]
    if len(txs) > MAX_TXIDS_PER_COMPACT:
        raise ProtocolError("too many tx bodies in response")
    dups = [int(i) for i in dup_indices]
    msg = _RespondMissingTxsS(
        t=TAG_MISSING_TXS, hh=bytes(header_hash), tx=txs, dup=dups
    )
    return ENC.encode(msg)


# ---------------------- Parsers ----------------------


def parse_announce(data: bytes) -> CompactAnnounce:
    m = DEC_ANN.decode(data)
    if m.t != TAG_ANNOUNCE:
        raise ProtocolError("ANNOUNCE tag mismatch")
    _check_hash("header", m.hh)
    _check_hash("parent", m.ph)
    _check_counts(m.tc, m.pc)
    _check_sid_list(m.sid or [])
    return CompactAnnounce(
        header_hash=bytes(m.hh),
        parent_hash=bytes(m.ph),
        height=int(m.ht),
        score=int(m.sc),
        tx_count=int(m.tc),
        proofs_count=int(m.pc),
        short_ids=[bytes(s) for s in (m.sid or [])],
    )


@dataclass(frozen=True)
class RequestBlockPieces:
    header_hash: bytes
    want_mask: int


def parse_get_block(data: bytes) -> RequestBlockPieces:
    m = DEC_GET.decode(data)
    if m.t != TAG_GET_BLOCK:
        raise ProtocolError("GET_BLOCK tag mismatch")
    _check_hash("header", m.hh)
    return RequestBlockPieces(header_hash=bytes(m.hh), want_mask=int(m.want))


@dataclass(frozen=True)
class RequestMissingTxs:
    header_hash: bytes
    short_ids: List[bytes]


def parse_ask_missing(data: bytes) -> RequestMissingTxs:
    m = DEC_ASK.decode(data)
    if m.t != TAG_ASK_MISSING:
        raise ProtocolError("ASK_MISSING tag mismatch")
    _check_hash("header", m.hh)
    _check_sid_list(m.sid)
    return RequestMissingTxs(
        header_hash=bytes(m.hh), short_ids=[bytes(s) for s in m.sid]
    )


@dataclass(frozen=True)
class RespondMissingTxs:
    header_hash: bytes
    tx_bodies: List[bytes]
    dup_indices: List[int]


def parse_missing_txs(data: bytes) -> RespondMissingTxs:
    m = DEC_MTX.decode(data)
    if m.t != TAG_MISSING_TXS:
        raise ProtocolError("MISSING_TXS tag mismatch")
    _check_hash("header", m.hh)
    txs = [bytes(b) for b in m.tx]
    if len(txs) > MAX_TXIDS_PER_COMPACT:
        raise ProtocolError("too many tx bodies")
    dups = [int(i) for i in (m.dup or [])]
    return RespondMissingTxs(header_hash=bytes(m.hh), tx_bodies=txs, dup_indices=dups)


# ---------------------- Utilities ----------------------


def make_short_ids(header_hash: bytes, tx_hashes: Iterable[bytes]) -> List[bytes]:
    """Compute short-ids for a list of tx hashes for this header."""
    return [short_id(header_hash, h) for h in tx_hashes]


def missing_from_sid_set(
    announced: Sequence[bytes], have_txhash_by_sid: Dict[bytes, bytes]
) -> List[bytes]:
    """
    Given the announced short-id sequence and a mapping sid->txhash for items we already
    have, return the subset of short-ids we *still* need to request.
    """
    out: List[bytes] = []
    for sid in announced:
        if sid not in have_txhash_by_sid:
            out.append(sid)
    return out


__all__ = [
    # tags
    "TAG_ANNOUNCE",
    "TAG_GET_BLOCK",
    "TAG_ASK_MISSING",
    "TAG_MISSING_TXS",
    # builders
    "build_announce",
    "build_get_block",
    "build_ask_missing",
    "build_missing_txs",
    # parsers
    "parse_announce",
    "parse_get_block",
    "parse_ask_missing",
    "parse_missing_txs",
    # dataclasses
    "CompactAnnounce",
    "RequestBlockPieces",
    "RequestMissingTxs",
    "RespondMissingTxs",
    # helpers
    "short_id",
    "make_short_ids",
    "missing_from_sid_set",
    # constants (re-export fallbacks ok)
    "HASH_LEN",
    "SHORT_ID_LEN",
    "MAX_TXIDS_PER_COMPACT",
    "MAX_BLOCK_TXS",
    "MAX_PROOFS",
]
