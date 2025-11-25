from __future__ import annotations
"""
Share relay protocol: HashShare / AI / Quantum (and friends) with caps precheck.

This module mirrors tx_relay but for *useful-work shares* (proof envelopes).
It provides compact payloads for:
  • ShareInv        (announce share ids)
  • ShareGetData    (request bodies by id)
  • ShareBodies     (deliver CBOR-encoded proof envelopes)

Pre-admission ("caps precheck"):
--------------------------------
Hosts supply a `precheck` callback that:
  1) Parses and *light-verifies* a share body enough to extract its nullifier
     and type_id (Hash/AI/Quantum/Storage/VDF) and optionally perform
     policy/caps checks (Γ, per-type caps) using local consensus view.
  2) Returns a SharePrecheckResult indicating accept/reject and the canonical
     *share_id* = pack_share_id(type_id, nullifier).

The relay gate performs:
  • size bounds checks (DoS guard)
  • dedupe via RollingBloom, keyed by share_id (nullifier-based)
  • optional pre-dedupe body-hash cache to avoid repeated heavy prechecks
  • delegation to precheck() for policy enforcement

Wire framing (message ids, AEAD) is handled by p2p.wire.*. Here we only encode/
decode payloads.

ID format
---------
share_id := u8 type_id || 32-byte nullifier  -> total 33 bytes

This allows peers to reconstruct per-type handling while deduping by nullifier.

"""

from dataclasses import dataclass
from typing import Iterable, List, Optional, Callable, Tuple

import hashlib
import struct
import time

import msgspec

# ---- Constants (fallbacks; prefer p2p.constants if available) ----------------
try:
    from p2p.constants import (
        MAX_SHARE_INV,
        MAX_SHARE_BODIES,
        MAX_SHARE_BYTES,
        NULLIFIER_LEN,
        HASH_LEN,
        TYPE_ID_HASH,
        TYPE_ID_AI,
        TYPE_ID_QUANTUM,
        TYPE_ID_STORAGE,
        TYPE_ID_VDF,
    )
except Exception:  # pragma: no cover
    MAX_SHARE_INV = 4096
    MAX_SHARE_BODIES = 512
    MAX_SHARE_BYTES = 256 * 1024  # 256 KiB per share body hard cap
    NULLIFIER_LEN = 32
    HASH_LEN = 32
    TYPE_ID_HASH = 0
    TYPE_ID_AI = 1
    TYPE_ID_QUANTUM = 2
    TYPE_ID_STORAGE = 3
    TYPE_ID_VDF = 4

ALLOWED_TYPE_IDS = {TYPE_ID_HASH, TYPE_ID_AI, TYPE_ID_QUANTUM, TYPE_ID_STORAGE, TYPE_ID_VDF}

# ---- Try to reuse RollingBloom from tx_relay; else fallback ------------------
try:
    from p2p.protocol.tx_relay import RollingBloom  # type: ignore
except Exception:  # pragma: no cover
    def _mix64(x: int) -> int:
        x = (x + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9 & 0xFFFFFFFFFFFFFFFF
        x = (x ^ (x >> 27)) * 0x94D049BB133111EB & 0xFFFFFFFFFFFFFFFF
        return x ^ (x >> 31)

    class Bloom:
        __slots__ = ("m_bits", "k", "_arr")
        def __init__(self, m_bits: int, k: int) -> None:
            self.m_bits, self.k = int(m_bits), int(k)
            self._arr = bytearray((m_bits + 7) // 8)
        def _indexes(self, h: bytes):
            d = hashlib.sha3_256(h).digest()
            s1 = struct.unpack_from(">Q", d, 0)[0]
            s2 = struct.unpack_from(">Q", d, 8)[0] ^ struct.unpack_from(">Q", d, 16)[0]
            for i in range(self.k):
                v = _mix64(s1 + i * 0x9E3779B97F4A7C15) ^ _mix64(s2 + i * 0xBF58476D1CE4E5B9)
                yield v % self.m_bits
        def add(self, h: bytes) -> None:
            for idx in self._indexes(h):
                self._arr[idx >> 3] |= (1 << (idx & 7))
        def contains(self, h: bytes) -> bool:
            return all(self._arr[idx >> 3] & (1 << (idx & 7)) for idx in self._indexes(h))

    class RollingBloom:
        def __init__(self, m_bits: int, k: int, generations: int = 3) -> None:
            self._gens = [Bloom(m_bits, k) for _ in range(max(1, generations))]
            self._head = 0
        def add(self, h: bytes) -> None:
            self._gens[self._head].add(h)
        def contains(self, h: bytes) -> bool:
            return any(g.contains(h) for g in self._gens)
        def rotate(self) -> None:
            self._head = (self._head + 1) % len(self._gens)
            m_bits = self._gens[self._head].m_bits
            k = self._gens[self._head].k
            self._gens[self._head] = Bloom(m_bits, k)

# ---- Payload tags (local to this payload codec) ------------------------------
TAG_SHARE_INV = 40
TAG_SHARE_GET = 41
TAG_SHARE_BODIES = 42

# ---- Errors ------------------------------------------------------------------
class ProtocolError(Exception):
    pass

class AdmissionError(Exception):
    pass

# ---- ID helpers --------------------------------------------------------------
SHARE_ID_LEN = 1 + NULLIFIER_LEN  # 33 bytes

def pack_share_id(type_id: int, nullifier: bytes) -> bytes:
    if type_id not in ALLOWED_TYPE_IDS:
        raise ProtocolError(f"pack_share_id: unknown type_id {type_id}")
    if not isinstance(nullifier, (bytes, bytearray)) or len(nullifier) != NULLIFIER_LEN:
        raise ProtocolError("pack_share_id: nullifier must be 32 bytes")
    return bytes([type_id]) + bytes(nullifier)

def unpack_share_id(share_id: bytes) -> Tuple[int, bytes]:
    if not isinstance(share_id, (bytes, bytearray)) or len(share_id) != SHARE_ID_LEN:
        raise ProtocolError("unpack_share_id: bad id length")
    return share_id[0], bytes(share_id[1:])

# ---- Wire payload structs ----------------------------------------------------
class _ShareInvS(msgspec.Struct, omit_defaults=True):
    t: int
    ids: List[bytes]  # each 33 bytes (type_id||nullifier)

class _ShareGetS(msgspec.Struct, omit_defaults=True):
    t: int
    ids: List[bytes]

class _ShareBodiesS(msgspec.Struct, omit_defaults=True):
    t: int
    bodies: List[bytes]  # CBOR-encoded proof envelopes

ENC = msgspec.msgpack.Encoder()
DEC_INV = msgspec.msgpack.Decoder(type=_ShareInvS)
DEC_GET = msgspec.msgpack.Decoder(type=_ShareGetS)
DEC_BOD = msgspec.msgpack.Decoder(type=_ShareBodiesS)

def _check_ids(ids: Iterable[bytes], limit: int, tag: str) -> List[bytes]:
    out: List[bytes] = []
    for sid in ids:
        if not isinstance(sid, (bytes, bytearray)) or len(sid) != SHARE_ID_LEN:
            raise ProtocolError(f"{tag}: each id must be {SHARE_ID_LEN} bytes")
        tid, _nul = unpack_share_id(sid)
        if tid not in ALLOWED_TYPE_IDS:
            raise ProtocolError(f"{tag}: unknown type_id {tid}")
        out.append(bytes(sid))
        if len(out) > limit:
            raise ProtocolError(f"{tag}: too many ids (>{limit})")
    return out

# ---- Builders / Parsers ------------------------------------------------------
def build_share_inv(ids: Iterable[bytes]) -> bytes:
    return ENC.encode(_ShareInvS(t=TAG_SHARE_INV, ids=_check_ids(ids, MAX_SHARE_INV, "ShareInv")))

def parse_share_inv(data: bytes) -> List[bytes]:
    m = DEC_INV.decode(data)
    if m.t != TAG_SHARE_INV:
        raise ProtocolError("ShareInv tag mismatch")
    return _check_ids(m.ids, MAX_SHARE_INV, "ShareInv")

def build_share_get(ids: Iterable[bytes]) -> bytes:
    return ENC.encode(_ShareGetS(t=TAG_SHARE_GET, ids=_check_ids(ids, MAX_SHARE_INV, "ShareGetData")))

def parse_share_get(data: bytes) -> List[bytes]:
    m = DEC_GET.decode(data)
    if m.t != TAG_SHARE_GET:
        raise ProtocolError("ShareGetData tag mismatch")
    return _check_ids(m.ids, MAX_SHARE_INV, "ShareGetData")

def build_share_bodies(bodies: Iterable[bytes]) -> bytes:
    bs = [bytes(b) for b in bodies]
    if len(bs) > MAX_SHARE_BODIES:
        raise ProtocolError("ShareBodies has too many items")
    for b in bs:
        if len(b) == 0 or len(b) > MAX_SHARE_BYTES:
            raise ProtocolError("ShareBodies includes empty/oversized body")
    return ENC.encode(_ShareBodiesS(t=TAG_SHARE_BODIES, bodies=bs))

def parse_share_bodies(data: bytes) -> List[bytes]:
    m = DEC_BOD.decode(data)
    if m.t != TAG_SHARE_BODIES:
        raise ProtocolError("ShareBodies tag mismatch")
    out: List[bytes] = []
    for b in m.bodies:
        if len(b) == 0 or len(b) > MAX_SHARE_BYTES:
            raise ProtocolError("ShareBodies body size out of range")
        out.append(bytes(b))
        if len(out) > MAX_SHARE_BODIES:
            raise ProtocolError("ShareBodies item limit exceeded")
    return out

# ---- Relay gate --------------------------------------------------------------
@dataclass
class SharePrecheckResult:
    """
    Result from host-provided precheck():
      - accepted: True iff body passes quick verification and caps precheck
      - reason: short machine-readable reason if rejected
      - share_id: 33B id = pack_share_id(type_id, nullifier) (required on accept)
    """
    accepted: bool
    reason: Optional[str]
    share_id: Optional[bytes]

@dataclass
class AdmitShareResult:
    accepted: bool
    reason: Optional[str]
    share_id: Optional[bytes]

def body_hash(data: bytes) -> bytes:
    """sha3-256 of the raw body (used for cheap precheck-dedupe)."""
    return hashlib.sha3_256(data).digest()

class ShareRelayGate:
    """
    Guard for share relay.

    Typical usage:
        gate = ShareRelayGate(precheck=my_fast_precheck)
        new_ids = gate.admit_inv_ids(ids_from_peer)
        res = gate.admit_share_body(raw_cbor_envelope)

    The `precheck` should:
      - parse type_id & nullifier from the envelope
      - (optionally) verify cheap invariants and policy roots
      - perform per-type / total-Γ caps *precheck* (approximate is OK)
      - return SharePrecheckResult with share_id if acceptable
    """
    def __init__(
        self,
        precheck: Callable[[bytes], SharePrecheckResult],
        *,
        body_bloom_m_bits: int = 524288,   # 64 KiB
        id_bloom_m_bits: int = 1048576,    # 128 KiB
        bloom_k: int = 7,
        generations: int = 3,
        rotate_interval_s: float = 60.0,
    ) -> None:
        self.precheck = precheck
        self.seen_bodies = RollingBloom(body_bloom_m_bits, bloom_k, generations)
        self.seen_ids = RollingBloom(id_bloom_m_bits, bloom_k, generations)
        self._last_rotate_at = time.monotonic()
        self._rotate_interval_s = rotate_interval_s

    # maintenance
    def rotate(self) -> None:
        self.seen_bodies.rotate(); self.seen_ids.rotate()
        self._last_rotate_at = time.monotonic()

    def maybe_rotate(self) -> None:
        if (time.monotonic() - self._last_rotate_at) >= self._rotate_interval_s:
            self.rotate()

    # admission
    def admit_inv_ids(self, ids: Iterable[bytes]) -> List[bytes]:
        """Return the subset of ids we haven't seen; mark them to dampen echo."""
        new: List[bytes] = []
        for sid in _check_ids(ids, MAX_SHARE_INV, "admit_inv"):
            if not self.seen_ids.contains(sid):
                new.append(sid)
                self.seen_ids.add(sid)
        return new

    def admit_share_body(self, body: bytes) -> AdmitShareResult:
        """Quick screen + caps precheck via host callback."""
        blen = len(body)
        if blen == 0:
            return AdmitShareResult(False, "empty", None)
        if blen > MAX_SHARE_BYTES:
            return AdmitShareResult(False, f"oversize>{MAX_SHARE_BYTES}", None)

        # Cheap dedupe by body hash to avoid repeated heavy prechecks.
        bh = body_hash(body)
        if self.seen_bodies.contains(bh):
            return AdmitShareResult(False, "body-dup", None)

        # Delegate to host precheck (should parse nullifier/type and check caps/policy).
        try:
            pc = self.precheck(body)
        except Exception as e:  # pragma: no cover
            return AdmitShareResult(False, f"precheck-exc:{type(e).__name__}", None)

        if not isinstance(pc, SharePrecheckResult):
            return AdmitShareResult(False, "precheck-bad-result", None)

        if not pc.accepted:
            # mark the body hash (even on reject) to dampen repeated attempts
            self.seen_bodies.add(bh)
            return AdmitShareResult(False, pc.reason or "precheck-reject", None)

        sid = pc.share_id
        if not sid or len(sid) != SHARE_ID_LEN:
            return AdmitShareResult(False, "precheck-missing-id", None)

        # Nullifier-based dedupe (authoritative).
        if self.seen_ids.contains(sid):
            self.seen_bodies.add(bh)
            return AdmitShareResult(False, "duplicate", sid)

        # Accept: mark both filters.
        self.seen_ids.add(sid)
        self.seen_bodies.add(bh)
        return AdmitShareResult(True, None, sid)

# ---- Public exports ----------------------------------------------------------
__all__ = [
    # tags
    "TAG_SHARE_INV", "TAG_SHARE_GET", "TAG_SHARE_BODIES",
    # id helpers
    "SHARE_ID_LEN", "pack_share_id", "unpack_share_id",
    "ALLOWED_TYPE_IDS",
    # payload builders/parsers
    "build_share_inv", "parse_share_inv",
    "build_share_get", "parse_share_get",
    "build_share_bodies", "parse_share_bodies",
    # relay gate & results
    "ShareRelayGate", "SharePrecheckResult", "AdmitShareResult",
    # errors
    "ProtocolError", "AdmissionError",
    # util
    "body_hash",
    # type ids (re-export fallbacks for convenience)
    "TYPE_ID_HASH", "TYPE_ID_AI", "TYPE_ID_QUANTUM", "TYPE_ID_STORAGE", "TYPE_ID_VDF",
]
