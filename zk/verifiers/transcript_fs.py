"""
Animica zk.verifiers.transcript_fs
==================================

Fiat–Shamir transcript helpers for BN254-based proofs.

Design goals
------------
- Deterministic, production-ready, minimal dependencies.
- Works over the BN254 scalar field Fr (same field as circuits).
- Uses a Poseidon sponge (params loaded at startup; see zk.verifiers.poseidon).
- Strong domain separation (Merlin-style): label- and type-tagged absorbs.
- Safe encoding for bytes & group elements (no silent reduction collisions).

Core ideas
----------
- The transcript maintains a Poseidon sponge state with rate = t-1 and capacity = 1.
- Inputs are *field elements*. We convert various types into Fr elements safely:
  * integers -> reduced mod r (Fr)
  * bytes -> split into 31-byte BE limbs (each < r), with length delimiters
  * G1/G2 points -> canonical affine bytes (big-endian) with an explicit infinity tag,
                   then chunked the same as generic bytes
- Domain separation:
  For each append:
    absorb(TAG(kind,label)), absorb(LEN), absorb(data limbs...)
  For a challenge:
    absorb(TAG("challenge",label)), then PERMUTE, output state[0].
  TAG(kind,label) = H = sha3_256("animica.fs/{kind}/" || label) mod r.

Public API
----------
- Transcript(protocol_label: str, *, params_name="bn254_t3")
- t.append_message(label: str, data: bytes)
- t.append_scalar(label: str, x: int)
- t.append_u64(label: str, x: int)
- t.append_g1(label: str, P)
- t.append_g2(label: str, Q)
- t.challenge_scalar(label: str, n: int = 1) -> int | list[int]

Notes
-----
- Poseidon parameters are NOT hard-coded; you must register/load the same params
  the circuits use (see zk.verifiers.poseidon.load_params_json / register_params).
- Endianness for byte chunking is **big-endian** (BE) and chunk size is 31 bytes.
- Canonical G1/G2 encoding here is uncompressed (affine) with leading 0x00 for
  finite points and 0x01 for infinity. Coordinates are 32-byte BE each (Fp / Fp2 limbs).

License: MIT
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple, Union

import hashlib

# Poseidon permutation & params registry
from .poseidon import PoseidonParams, get_params, poseidon_permute
# BN254 helpers (for canonicalizing group elements)
from .pairing_bn254 import normalize_g1, normalize_g2, curve_order


# ---------------------------
# Field arithmetic (Fr)
# ---------------------------

_FR = int(curve_order())  # BN254 scalar field order (r)

def _fadd(a: int, b: int) -> int:
    return (int(a) + int(b)) % _FR

def _fred(a: int) -> int:
    return int(a) % _FR


# ---------------------------
# Domain tags & encoding
# ---------------------------

_CHUNK_BYTES = 31  # 248 bits per limb < 254-bit modulus, avoids reduction collisions


def _tag(kind: str, label: str) -> int:
    """
    Domain tag for (kind,label). Returns an Fr element via sha3-256.
    """
    h = hashlib.sha3_256(f"animica.fs/{kind}/{label}".encode("utf-8")).digest()
    return int.from_bytes(h, "big") % _FR


def _u64_to_fr(x: int) -> int:
    if not (0 <= int(x) < (1 << 64)):
        raise ValueError("u64 out of range")
    return int(x)  # < 2^64 ⊂ Fr


def _int_to_be_bytes(x: int, width: int) -> bytes:
    return int(x).to_bytes(width, "big")


def _chunk_bytes_to_fr_limbs(data: bytes, *, chunk: int = _CHUNK_BYTES) -> List[int]:
    """
    Split bytes into big-endian limbs of size `chunk`, returning each limb as an Fr element
    *without further reduction collisions* because chunk*8 < log2(r).
    """
    limbs: List[int] = []
    n = len(data)
    off = 0
    while off < n:
        blk = data[off : off + chunk]
        limbs.append(int.from_bytes(blk, "big"))
        off += chunk
    if n == 0:
        # Represent empty as a single zero limb for unambiguous delimiting
        limbs.append(0)
    return limbs


def _encode_g1_bytes(P) -> bytes:
    """
    Canonical uncompressed G1 encoding for transcript:
      - infinity: b'\x01'
      - finite:   b'\x00' || x(32) || y(32)  (Fp big-endian)
    """
    aff = normalize_g1(P)
    if aff is None:
        return b"\x01"
    x, y = aff
    return b"\x00" + _int_to_be_bytes(x, 32) + _int_to_be_bytes(y, 32)


def _encode_g2_bytes(Q) -> bytes:
    """
    Canonical uncompressed G2 encoding for transcript:
      - infinity: b'\x01'
      - finite:   b'\x00' || xc0(32) || xc1(32) || yc0(32) || yc1(32)  (Fq2 big-endian limbs)
    """
    aff = normalize_g2(Q)
    if aff is None:
        return b"\x01"
    (xc0, xc1), (yc0, yc1) = aff
    return (
        b"\x00"
        + _int_to_be_bytes(xc0, 32)
        + _int_to_be_bytes(xc1, 32)
        + _int_to_be_bytes(yc0, 32)
        + _int_to_be_bytes(yc1, 32)
    )


# ---------------------------
# Poseidon sponge
# ---------------------------

@dataclass
class _Sponge:
    params: PoseidonParams

    def __post_init__(self) -> None:
        self.t = self.params.t
        self.rate = self.t - 1
        if self.rate <= 0:
            raise ValueError("Poseidon width t must be >= 2")
        self.state: List[int] = [0] * self.t
        self._pos = 0  # next absorb index in [0 .. rate-1]

    def absorb_elems(self, elems: Iterable[int]) -> None:
        for v in elems:
            v = _fred(v)
            self.state[self._pos] = _fadd(self.state[self._pos], v)
            self._pos += 1
            if self._pos == self.rate:
                self._permute()

    def absorb_domain(self, tag_elem: int, length_elem: int) -> None:
        """
        Absorb a (tag, length) header to prevent cross-type/cross-label collisions.
        """
        self.absorb_elems((tag_elem, length_elem))

    def squeeze(self) -> int:
        """
        One squeeze word: permute then return state[0].
        """
        self._permute()
        return int(self.state[0])

    def _permute(self) -> None:
        self.state = poseidon_permute(self.state, self.params)
        self._pos = 0


# ---------------------------
# Transcript
# ---------------------------

class Transcript:
    """
    Fiat–Shamir transcript using Poseidon over Fr.

    Example:
        t = Transcript("animica:plonk_v1", params_name="bn254_t3")
        t.append_message("circuit_hash", circuit_root_bytes)
        t.append_g1("commit_A", A)
        t.append_scalar("alpha", 123)
        r = t.challenge_scalar("beta")
        k1, k2 = t.challenge_scalar("kappa", n=2)
    """

    def __init__(self, protocol_label: str, *, params_name: str = "bn254_t3") -> None:
        self.params = get_params(params_name)
        self.sponge = _Sponge(self.params)
        # Initialize with a protocol-level domain tag
        proto_tag = _tag("init", protocol_label)
        self.sponge.absorb_domain(proto_tag, 1)
        self.sponge.absorb_elems([proto_tag])

    # --- append methods ---

    def append_message(self, label: str, data: Union[bytes, bytearray, memoryview]) -> None:
        """
        Append arbitrary bytes with length delimiting and label domain tag.
        """
        b = bytes(data)
        limbs = _chunk_bytes_to_fr_limbs(b)
        self._absorb_typed("msg", label, limbs)

    def append_scalar(self, label: str, x: int) -> None:
        """
        Append a scalar in Fr (reduced modulo r).
        """
        limb = _fred(int(x))
        self._absorb_typed("scalar", label, [limb])

    def append_u64(self, label: str, x: int) -> None:
        """
        Append a 64-bit unsigned integer (no reduction ambiguity).
        """
        limb = _u64_to_fr(int(x))
        self._absorb_typed("u64", label, [limb])

    def append_g1(self, label: str, P) -> None:
        """
        Append a G1 point (canonical BE encoding → Fr limbs).
        """
        limbs = _chunk_bytes_to_fr_limbs(_encode_g1_bytes(P))
        self._absorb_typed("g1", label, limbs)

    def append_g2(self, label: str, Q) -> None:
        """
        Append a G2 point (canonical BE encoding → Fr limbs).
        """
        limbs = _chunk_bytes_to_fr_limbs(_encode_g2_bytes(Q))
        self._absorb_typed("g2", label, limbs)

    # --- challenges ---

    def challenge_scalar(self, label: str, n: int = 1) -> Union[int, List[int]]:
        """
        Derive one or more Fr challenges, domain-separated by `label`.
        """
        if n <= 0:
            raise ValueError("n must be >= 1")
        ch_tag = _tag("challenge", label)
        # absorb domain header with zero-length payload (pure label separation)
        self.sponge.absorb_domain(ch_tag, 0)
        out = [self.sponge.squeeze() for _ in range(n)]
        return out[0] if n == 1 else out

    # --- internal ---

    def _absorb_typed(self, kind: str, label: str, limbs: Sequence[int]) -> None:
        tag = _tag(kind, label)
        self.sponge.absorb_domain(tag, len(limbs))
        self.sponge.absorb_elems(limbs)


__all__ = [
    "Transcript",
]
