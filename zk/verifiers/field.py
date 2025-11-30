# Copyright
# SPDX-License-Identifier: Apache-2.0
"""
BN254 prime field (a.k.a. alt_bn128) — minimal, pure-Python helpers.

This module provides a tiny `Fp` class with the essential arithmetic you need for
verifier-side math (e.g., parsing/validating coordinates, doing a few small-field
computations) without dragging in heavy dependencies.

It is **not** constant-time and is intended only for verification / testing
utilities, not for secret-bearing computations.

Features:
- Canonical modulus `P` and 32-byte big-endian (de)serialization.
- Basic ring ops: +, -, *, /, pow, neg, eq, int().
- Inversion via Fermat's little theorem.
- Square root via Tonelli–Shanks (returns None if non-residue).
- Legendre symbol and batch inversion utility.

References:
- EVM precompiles (alt_bn128) and BN254 curve used by Groth16 (snarkjs).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple, Union

# Local error type used across zk/verifiers
try:
    from . import ZKError
except Exception:  # pragma: no cover - fallback if imported standalone

    class ZKError(Exception):
        pass


# BN254 / alt_bn128 base field prime.
#   P = 21888242871839275222246405745257275088548364400416034343698204186575808495617
P: int = 21888242871839275222246405745257275088548364400416034343698204186575808495617
FP_BYTE_LEN = 32


def _to_int(x: Union[int, "Fp"]) -> int:
    return x.n if isinstance(x, Fp) else int(x)


def _red(x: int) -> int:
    """Reduce to canonical representative in [0, P)."""
    x %= P
    return x


@dataclass(frozen=True)
class Fp:
    """
    Small immutable wrapper for elements of F_p (BN254 prime field).

    Use like integers:
        a = Fp.from_int(5)
        b = Fp.from_int(7)
        c = a * b + 1
    """

    n: int  # canonical representative in [0, P)

    # --- Constructors -----------------------------------------------------

    @staticmethod
    def from_int(x: int) -> "Fp":
        return Fp(_red(x))

    @staticmethod
    def from_bytes(b: bytes, *, strict_len: bool = False) -> "Fp":
        """
        Parse big-endian bytes. If strict_len, require exactly 32 bytes.
        Shorter inputs are accepted by default (e.g., b'\\x01' -> 1).
        """
        if strict_len and len(b) != FP_BYTE_LEN:
            raise ZKError(f"Fp.from_bytes: expected {FP_BYTE_LEN} bytes, got {len(b)}")
        if len(b) > FP_BYTE_LEN:
            raise ZKError("Fp.from_bytes: too many bytes for field element")
        return Fp.from_int(int.from_bytes(b, "big"))

    @staticmethod
    def from_hex(s: str) -> "Fp":
        s = s.lower()
        if s.startswith("0x"):
            s = s[2:]
        if len(s) > FP_BYTE_LEN * 2:
            raise ZKError("Fp.from_hex: too many hex chars for field element")
        return Fp.from_int(int(s, 16) if s else 0)

    # --- Serialization ----------------------------------------------------

    def to_bytes(self) -> bytes:
        return int(self.n).to_bytes(FP_BYTE_LEN, "big")

    def to_hex(self, prefix: bool = True) -> str:
        h = self.to_bytes().hex()
        return ("0x" + h) if prefix else h

    # --- Basic number protocol -------------------------------------------

    def __int__(self) -> int:  # allows int(Fp(...))
        return self.n

    def __bool__(self) -> bool:
        return self.n != 0

    def __repr__(self) -> str:
        return f"Fp({self.to_hex()})"

    def __hash__(self) -> int:
        return hash(self.n)

    # --- Arithmetic -------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, (int, Fp)):
            return False
        return self.n == _red(_to_int(other))

    def __neg__(self) -> "Fp":
        return Fp(0 if self.n == 0 else P - self.n)

    def __add__(self, other: Union[int, "Fp"]) -> "Fp":
        return Fp(_red(self.n + _to_int(other)))

    def __radd__(self, other: Union[int, "Fp"]) -> "Fp":
        return self.__add__(other)

    def __sub__(self, other: Union[int, "Fp"]) -> "Fp":
        return Fp(_red(self.n - _to_int(other)))

    def __rsub__(self, other: Union[int, "Fp"]) -> "Fp":
        return Fp(_red(_to_int(other) - self.n))

    def __mul__(self, other: Union[int, "Fp"]) -> "Fp":
        return Fp(_red(self.n * _to_int(other)))

    def __rmul__(self, other: Union[int, "Fp"]) -> "Fp":
        return self.__mul__(other)

    def __truediv__(self, other: Union[int, "Fp"]) -> "Fp":
        o = _to_int(other)
        if o % P == 0:
            raise ZKError("Fp division by zero")
        return self * Fp.from_int(o).inv()

    def __rtruediv__(self, other: Union[int, "Fp"]) -> "Fp":
        return Fp.from_int(_to_int(other)) / self

    def __pow__(self, exponent: int, modulo=None) -> "Fp":
        if modulo is not None:
            raise ZKError("Fp.__pow__ does not support 3-arg pow")
        return Fp(pow(self.n, exponent, P))

    # --- Field-specific ops ----------------------------------------------

    def inv(self) -> "Fp":
        """Multiplicative inverse using Fermat's little theorem."""
        if self.n == 0:
            raise ZKError("Fp inverse of zero")
        return Fp(pow(self.n, P - 2, P))

    def legendre(self) -> int:
        """
        Legendre symbol (self | P) in { -1, 0, 1 }.
        0  => element is 0
        1  => quadratic residue mod P
        -1 => non-residue
        """
        if self.n == 0:
            return 0
        ls = pow(self.n, (P - 1) // 2, P)
        return -1 if ls == P - 1 else int(ls)

    def sqrt(self) -> Optional["Fp"]:
        """
        Tonelli–Shanks square root over F_p.
        Returns one of the two roots if it exists, else None.
        """
        if self.n == 0:
            return Fp(0)

        # Check if residue
        if self.legendre() != 1:
            return None

        # Factor p-1 = q * 2^s with q odd
        q = P - 1
        s = 0
        while q & 1 == 0:
            q >>= 1
            s += 1

        # Find a quadratic non-residue z
        z = 2
        while pow(z, (P - 1) // 2, P) != P - 1:
            z += 1

        m = s
        c = pow(z, q, P)
        t = pow(self.n, q, P)
        r = pow(self.n, (q + 1) // 2, P)

        while True:
            if t == 0:
                return Fp(0)
            if t == 1:
                return Fp(r)
            # Find lowest i in [1..m) such that t^(2^i) == 1
            i = 1
            t2i = (t * t) % P
            while i < m and t2i != 1:
                t2i = (t2i * t2i) % P
                i += 1
            # Update
            b = pow(c, 1 << (m - i - 1), P)
            r = (r * b) % P
            c = (b * b) % P
            t = (t * c) % P
            m = i

    # Convenience constants
    @staticmethod
    def zero() -> "Fp":
        return Fp(0)

    @staticmethod
    def one() -> "Fp":
        return Fp(1)


# --- Utilities ------------------------------------------------------------


def is_canonical_bytes(b: bytes) -> bool:
    """Check if bytes represent a canonical Fp element (0 <= x < P) and length == 32."""
    if len(b) != FP_BYTE_LEN:
        return False
    x = int.from_bytes(b, "big")
    return 0 <= x < P


def batch_inv(elems: Sequence[Fp]) -> List[Fp]:
    """
    Simultaneous inversion of many field elements in ~3 mults/elem (plus one inv).
    Zeros are preserved as zero (i.e., leave them as 0; they have no inverse).
    """
    n = len(elems)
    if n == 0:
        return []
    prefix = [Fp.one()] * n
    acc = Fp.one()
    for i, a in enumerate(elems):
        prefix[i] = acc
        acc = acc * a if a.n != 0 else acc

    inv_acc = acc.inv() if acc.n != 0 else Fp.zero()

    out = [Fp.zero()] * n
    for i in range(n - 1, -1, -1):
        a = elems[i]
        if a.n == 0:
            out[i] = Fp.zero()
        else:
            out[i] = prefix[i] * inv_acc
            inv_acc = inv_acc * a
    return out


# Commonly used constants
FP_ZERO = Fp.zero()
FP_ONE = Fp.one()
