"""
randomness.vdf.wesolowski
=========================

Reference (dev/testnet) implementation of a Wesolowski VDF prover and verifier.

This module is intentionally simple and dependency-free. It targets the RSA
group backend and is suitable for **devnet/testing**. Production deployments
must supply an MPC-generated RSA modulus and tune parameters via
:mod:`randomness.vdf.params`.

Key functions
-------------
- :func:`eval_y`:        compute y = x^(2^t) mod N by repeated squaring
- :func:`prove`:         produce a Wesolowski proof (ℓ, π) for (x, t, N)
- :func:`verify`:        verify that y ≟ π^ℓ · x^(2^t mod ℓ) (mod N)
- :class:`Prover`:       small wrapper that uses :class:`~randomness.vdf.params.VDFParams`

Notes
-----
- Challenge prime ℓ is derived from a domain-separated hash of (N, x, y, t).
  We sample a ~128-bit probable prime via Miller–Rabin with fixed bases.
- The implementation is straightforward and prioritizes clarity over speed.
- All operations are deterministic given the inputs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Tuple

from .params import VDFParams, get_params


# ---------------------------------------------------------------------------
# Basic math helpers
# ---------------------------------------------------------------------------

def _int_from_be(data: bytes) -> int:
    return int.from_bytes(data, "big", signed=False)


def _int_to_be(i: int) -> bytes:
    if i == 0:
        return b"\x00"
    l = (i.bit_length() + 7) // 8
    return i.to_bytes(l, "big", signed=False)


def _sha3_256(*chunks: bytes) -> bytes:
    h = hashlib.sha3_256()
    for c in chunks:
        h.update(c)
    return h.digest()


def _dom_hash(tag: bytes, *chunks: bytes) -> bytes:
    return _sha3_256(b"VDF\x01" + tag, *_len_prefix_chunks(chunks))


def _len_prefix_chunks(chunks: Tuple[bytes, ...]) -> bytes:
    """Deterministic framing to avoid ambiguity when hashing multiple chunks."""
    out = bytearray()
    for c in chunks:
        out += len(c).to_bytes(8, "big")
        out += c
    return bytes(out)


# ---------------------------------------------------------------------------
# Primes
# ---------------------------------------------------------------------------

_SMALL_PRIMES = (
    3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47,
    53, 59, 61, 67, 71, 73, 79, 83, 89, 97,
)


def _miller_rabin(n: int, bases: Tuple[int, ...]) -> bool:
    """Deterministic MR for fixed bases (probabilistic for 128-bit range)."""
    if n < 2:
        return False
    # small prime check
    for p in _SMALL_PRIMES:
        if n == p:
            return True
        if n % p == 0:
            return False

    # write n-1 = d * 2^s with d odd
    d = n - 1
    s = 0
    while d % 2 == 0:
        d //= 2
        s += 1

    def trial(a: int) -> bool:
        x = pow(a % n, d, n)
        if x == 1 or x == n - 1:
            return True
        for _ in range(s - 1):
            x = (x * x) % n
            if x == n - 1:
                return True
        return False

    for a in bases:
        if a % n == 0:
            continue
        if not trial(a):
            return False
    return True


# Fixed bases chosen to give a very low error rate around ~128–192 bits.
_MR_BASES_128 = (2, 3, 5, 7, 11, 13, 17, 19)


def _hash_to_prime(data: bytes, k_bits: int = 128) -> int:
    """
    Map bytes → probable prime of ~k_bits via domain-separated hashing.
    Deterministic; increments a counter on composite hits.
    """
    assert k_bits >= 64
    ctr = 0
    while True:
        seed = _dom_hash(b"challenge", data, ctr.to_bytes(8, "big"))
        # keep the top bit set to ensure size, force odd
        n = _int_from_be(seed) | (1 << (k_bits - 1)) | 1
        if _miller_rabin(n, _MR_BASES_128):
            return n
        ctr += 1


# ---------------------------------------------------------------------------
# Core VDF routines
# ---------------------------------------------------------------------------

def _normalize_x(x: int | bytes, N: int) -> int:
    """
    Map input into Z*_N (best-effort). For bytes, hash to integer in [2, N-1].
    For int, reduce modulo N and avoid the trivial classes {0,1}.
    """
    if isinstance(x, bytes):
        h = _dom_hash(b"x", x, _int_to_be(N))
        xi = 2 + (_int_from_be(h) % (N - 3)) if N > 3 else 2 % N
        return xi
    else:
        xi = x % N
        if xi in (0, 1):
            # rehash deterministically to escape trivial elements
            h = _dom_hash(b"x", _int_to_be(x), _int_to_be(N))
            xi = 2 + (_int_from_be(h) % (N - 3)) if N > 3 else 2 % N
        return xi


def eval_y(x: int | bytes, t: int, N: int) -> int:
    """
    Compute y = x^(2^t) mod N via repeated squaring. O(t) squarings.

    This function does not allocate an explicit exponent of size ~t bits.
    """
    base = _normalize_x(x, N)
    y = base
    for _ in range(t):
        y = (y * y) % N
    return y


def derive_challenge_prime(N: int, x: int, y: int, t: int, k_bits: int = 128) -> int:
    """Derive a challenge prime ℓ = H_to_prime(N, x, y, t)."""
    material = b"".join((_int_to_be(N), _int_to_be(x), _int_to_be(y), t.to_bytes(8, "big")))
    return _hash_to_prime(material, k_bits=k_bits)


def prove(x: int | bytes, t: int, N: int, k_bits: int = 128) -> Tuple[int, int, int]:
    """
    Produce (y, ℓ, π) for inputs (x, t, N) using the Wesolowski scheme.

    Steps:
      1) y = x^(2^t) mod N
      2) ℓ ← H_to_prime(N, x, y, t)
      3) q = ⌊2^t / ℓ⌋ and r = 2^t mod ℓ
      4) π = x^q mod N

    Returns:
        (y, l, pi)
    """
    xn = _normalize_x(x, N)
    y = eval_y(xn, t, N)
    l = derive_challenge_prime(N, xn, y, t, k_bits=k_bits)
    r = pow(2, t, l)  # r = 2^t mod l
    # q = (2^t - r) / l, constructed without floating point
    q = ((1 << t) - r) // l
    pi = pow(xn, q, N)
    return y, l, pi


def verify(x: int | bytes, y: int, t: int, N: int, l: int, pi: int) -> bool:
    """
    Verify a Wesolowski proof:

        Check: y ≟ π^ℓ · x^(2^t mod ℓ)  (mod N)

    This is O(log ℓ + log r) modular exponentiations, much faster than O(t).
    """
    xn = _normalize_x(x, N)
    r = pow(2, t, l)
    left = y % N
    right = (pow(pi, l, N) * pow(xn, r, N)) % N
    return left == right


# ---------------------------------------------------------------------------
# Prover wrapper bound to VDFParams
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Prover:
    params: VDFParams

    @classmethod
    def from_env(cls) -> "Prover":
        return cls(get_params())

    def prove(self, x: int | bytes) -> Tuple[int, int, int]:
        """Prove with parameters in :attr:`params`. Returns (y, l, pi)."""
        if self.params.backend != "rsa":
            raise NotImplementedError("Wesolowski prover currently supports RSA backend only")
        return prove(x, self.params.iterations, self.params.modulus_n)

    def verify(self, x: int | bytes, y: int, l: int, pi: int) -> bool:
        """Verify with parameters in :attr:`params`."""
        if self.params.backend != "rsa":
            raise NotImplementedError("Wesolowski verifier currently supports RSA backend only")
        return verify(x, y, self.params.iterations, self.params.modulus_n, l, pi)


__all__ = [
    "eval_y",
    "derive_challenge_prime",
    "prove",
    "verify",
    "Prover",
]
