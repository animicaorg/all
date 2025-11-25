from __future__ import annotations

"""
Inventory protocol (INV / GETDATA / NOTFOUND) for txs, blocks, and useful-work shares.

This module only defines the payload shapes (encoded with msgpack via msgspec) and
helpers to build/parse/validate inventory messages. The wire envelope (msg_id,
framing, encryption) is handled by p2p.wire.*.

Message shapes (canonical field order):
- InvMessage:      {"t": 1, "items": [ {"k": <kind>, "h": <32B>}, ... ]}
- GetDataMessage:  {"t": 2, "items": [ {"k": <kind>, "h": <32B>}, ... ]}
- NotFoundMessage: {"t": 3, "items": [ {"k": <kind>, "h": <32B>}, ... ]}

where kind ∈ InvKind = { TX=1, BLOCK=2, SHARE=3 } and h is a 32-byte digest.

Flow:
  • A peer announces availability with InvMessage (bounded list).
  • The receiver filters unknown hashes and replies with GetDataMessage.
  • The sender responds on the corresponding topic with the full object(s) or
    replies with NotFoundMessage if something just became unavailable.

Limits & validation live here to provide a single source of truth for producers
and consumers (gossip, sync, miner share relay).
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable, List, Sequence, Tuple

import msgspec

# ---- Constants (import from p2p.constants if available) ----
try:
    from p2p.constants import MAX_INV_PER_MSG, HASH_LEN
except Exception:  # pragma: no cover
    MAX_INV_PER_MSG = 2048          # sane upper bound for batched mempool announcements
    HASH_LEN = 32                    # SHA3-256 digest length


class ProtocolError(Exception):
    """Lightweight protocol error for local validation failures."""


# ---- Inventory kinds ----

class InvKind(IntEnum):
    TX = 1
    BLOCK = 2
    SHARE = 3  # useful-work share (HashShare/AI/Quantum envelopes or ids)


@dataclass(frozen=True)
class InvItem:
    kind: InvKind
    hash: bytes  # 32 bytes


# ---- Wire structs (msgspec Structs keep canonical order) ----

class _InvItemS(msgspec.Struct, omit_defaults=True):
    k: int
    h: bytes


class _InvS(msgspec.Struct, omit_defaults=True):
    t: int
    items: List[_InvItemS]


class _GetDataS(msgspec.Struct, omit_defaults=True):
    t: int
    items: List[_InvItemS]


class _NotFoundS(msgspec.Struct, omit_defaults=True):
    t: int
    items: List[_InvItemS]


ENC = msgspec.msgpack.Encoder()
DEC_INV = msgspec.msgpack.Decoder(type=_InvS)
DEC_GET = msgspec.msgpack.Decoder(type=_GetDataS)
DEC_NF = msgspec.msgpack.Decoder(type=_NotFoundS)

# Type tags for this tiny payload family
TAG_INV = 1
TAG_GETDATA = 2
TAG_NOTFOUND = 3


# ---- Validation helpers ----

def _validate_item(item: InvItem) -> None:
    if not isinstance(item.kind, InvKind):
        raise ProtocolError("invalid inventory kind")
    if not isinstance(item.hash, (bytes, bytearray)) or len(item.hash) != HASH_LEN:
        raise ProtocolError(f"invalid hash length, expected {HASH_LEN} bytes")


def _dedupe_and_bound(items: Sequence[InvItem]) -> List[InvItem]:
    if len(items) > MAX_INV_PER_MSG:
        raise ProtocolError(f"too many inventory items (max {MAX_INV_PER_MSG})")
    seen: set[Tuple[int, bytes]] = set()
    out: List[InvItem] = []
    for it in items:
        _validate_item(it)
        key = (int(it.kind), bytes(it.hash))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _pack_items(items: Sequence[InvItem]) -> List[_InvItemS]:
    return [_InvItemS(k=int(i.kind), h=bytes(i.hash)) for i in items]


def _unpack_items(items: List[_InvItemS]) -> List[InvItem]:
    out: List[InvItem] = []
    for it in items:
        try:
            kind = InvKind(int(it.k))
        except ValueError as e:  # pragma: no cover
            raise ProtocolError(f"unknown inventory kind {it.k}") from e
        if not isinstance(it.h, (bytes, bytearray)) or len(it.h) != HASH_LEN:
            raise ProtocolError(f"invalid hash length in payload (got {len(it.h)})")
        out.append(InvItem(kind=kind, hash=bytes(it.h)))
    return _dedupe_and_bound(out)


# ---- Builders ----

def build_inv(items: Iterable[InvItem]) -> bytes:
    """Encode an InvMessage (INV)."""
    deduped = _dedupe_and_bound(list(items))
    payload = _InvS(t=TAG_INV, items=_pack_items(deduped))
    return ENC.encode(payload)


def build_getdata(items: Iterable[InvItem]) -> bytes:
    """Encode a GetDataMessage (GETDATA)."""
    deduped = _dedupe_and_bound(list(items))
    payload = _GetDataS(t=TAG_GETDATA, items=_pack_items(deduped))
    return ENC.encode(payload)


def build_notfound(items: Iterable[InvItem]) -> bytes:
    """Encode a NotFoundMessage (NOTFOUND)."""
    deduped = _dedupe_and_bound(list(items))
    payload = _NotFoundS(t=TAG_NOTFOUND, items=_pack_items(deduped))
    return ENC.encode(payload)


# ---- Parsers ----

def parse_inv(data: bytes) -> List[InvItem]:
    """Parse & validate an INV payload."""
    msg = DEC_INV.decode(data)
    if msg.t != TAG_INV:
        raise ProtocolError("INV tag mismatch")
    return _unpack_items(msg.items)


def parse_getdata(data: bytes) -> List[InvItem]:
    """Parse & validate a GETDATA payload."""
    msg = DEC_GET.decode(data)
    if msg.t != TAG_GETDATA:
        raise ProtocolError("GETDATA tag mismatch")
    return _unpack_items(msg.items)


def parse_notfound(data: bytes) -> List[InvItem]:
    """Parse & validate a NOTFOUND payload."""
    msg = DEC_NF.decode(data)
    if msg.t != TAG_NOTFOUND:
        raise ProtocolError("NOTFOUND tag mismatch")
    return _unpack_items(msg.items)


# ---- Conveniences ----

def inv_for_hashes(kind: InvKind, hashes: Iterable[bytes]) -> bytes:
    """Convenience to build an INV from a single kind and an iterable of 32B hashes."""
    return build_inv(InvItem(kind, h) for h in hashes)


def getdata_for_missing(kind: InvKind, hashes: Iterable[bytes]) -> bytes:
    """Convenience to build GETDATA from a list of desired hashes of one kind."""
    return build_getdata(InvItem(kind, h) for h in hashes)


def partition_by_kind(items: Sequence[InvItem]) -> dict[InvKind, List[bytes]]:
    """Group inventory items by kind → [hash]."""
    out: dict[InvKind, List[bytes]] = {InvKind.TX: [], InvKind.BLOCK: [], InvKind.SHARE: []}
    for it in items:
        out.setdefault(it.kind, []).append(it.hash)
    return out


__all__ = [
    "InvKind",
    "InvItem",
    "ProtocolError",
    "build_inv",
    "build_getdata",
    "build_notfound",
    "parse_inv",
    "parse_getdata",
    "parse_notfound",
    "inv_for_hashes",
    "getdata_for_missing",
    "partition_by_kind",
    "TAG_INV",
    "TAG_GETDATA",
    "TAG_NOTFOUND",
    "MAX_INV_PER_MSG",
    "HASH_LEN",
]
