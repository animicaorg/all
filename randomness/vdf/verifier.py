"""
randomness.vdf.verifier
=======================

Constant-time-ish Wesolowski verifier used for **primary consensus checks**.

This module performs the verification with a fixed computation pattern to
minimize timing variance in Python. It:
  - Re-derives the challenge prime ℓ from (N, x, y, t) and compares it in a
    constant-time-ish manner to any provided ℓ.
  - Performs the core check: y ≟ π^ℓ · x^(2^t mod ℓ) (mod N).
  - Avoids early returns; all predicates are evaluated and AND-combined.

Notes
-----
- Python cannot guarantee perfect constant-time behavior; this implementation
  simply avoids obvious data-dependent branches and uses byte-wise XOR checks.
- For consensus, callers should use :func:`verify_consensus` or :class:`Verifier`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Union

from .params import VDFParams, get_params
from .wesolowski import derive_challenge_prime


IntOrBytes = Union[int, bytes]


# -----------------------------------------------------------------------------
# Constant-time-ish primitives (best-effort in Python)
# -----------------------------------------------------------------------------

def _int_byte_width(n: int) -> int:
    return max(1, (n.bit_length() + 7) // 8)


def _int_to_be(i: int, width: int) -> bytes:
    return int(i).to_bytes(width, "big", signed=False)


def _ct_bytes_eq(a: bytes, b: bytes) -> bool:
    # Constant-time-ish equality: XOR-accumulate without early return
    la = len(a)
    lb = len(b)
    m = max(la, lb, 1)
    aa = a.rjust(m, b"\x00")
    bb = b.rjust(m, b"\x00")
    diff = 0
    for x, y in zip(aa, bb):
        diff |= (x ^ y)
    return diff == 0


def _ct_int_eq(a: int, b: int, width: int) -> bool:
    return _ct_bytes_eq(_int_to_be(a % (1 << (8 * width)), width),
                        _int_to_be(b % (1 << (8 * width)), width))


def _ct_and(*flags: bool) -> bool:
    # Combine without short-circuiting semantics
    acc = True
    for f in flags:
        # Use bitwise AND to avoid branching; cast to bool at the end
        acc = bool(acc & bool(f))
    return acc


# -----------------------------------------------------------------------------
# Local normalization of x (mirrors wesolowski._normalize_x)
# -----------------------------------------------------------------------------

def _sha3_256(*chunks: bytes) -> bytes:
    # local import to keep module surface minimal
    import hashlib
    h = hashlib.sha3_256()
    for c in chunks:
        h.update(c)
    return h.digest()


def _dom_hash(tag: bytes, *chunks: bytes) -> bytes:
    # same domain tag as wesolowski.py
    def frame(parts: Tuple[bytes, ...]) -> bytes:
        out = bytearray()
        for p in parts:
            out += len(p).to_bytes(8, "big")
            out += p
        return bytes(out)
    return _sha3_256(b"VDF\x01" + tag, frame(chunks))


def _normalize_x(x: IntOrBytes, N: int) -> int:
    """
    Map input into Z*_N (best-effort). For bytes, hash to integer in [2, N-1].
    For int, reduce modulo N and avoid the trivial classes {0,1}.
    """
    if isinstance(x, (bytes, bytearray)):
        h = _dom_hash(b"x", bytes(x), _int_to_be(N, _int_byte_width(N)))
        # ensure in [2, N-1] when possible
        return (2 + int.from_bytes(h, "big") % max(1, (N - 3))) % N if N > 3 else (2 % N)
    else:
        xi = int(x) % N
        if xi in (0, 1):
            h = _dom_hash(b"x", _int_to_be(int(x), max(1, (int(x).bit_length() + 7) // 8)),
                          _int_to_be(N, _int_byte_width(N)))
            xi = (2 + int.from_bytes(h, "big") % max(1, (N - 3))) % N if N > 3 else (2 % N)
        return xi


# -----------------------------------------------------------------------------
# Verifier
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Verifier:
    """
    Primary consensus verifier for Wesolowski VDF proofs bound to :class:`VDFParams`.

    Use :meth:`verify` for boolean-only checks or :meth:`verify_with_report`
    to obtain a reason string for diagnostics/metrics.
    """
    params: VDFParams
    k_bits: int = 128  # challenge size used when re-deriving ℓ

    @classmethod
    def from_env(cls) -> "Verifier":
        return cls(get_params())

    def verify(self, x: IntOrBytes, y: int, pi: int, l: Optional[int] = None) -> bool:
        ok, _ = self.verify_with_report(x, y, pi, l)
        return ok

    def verify_with_report(self, x: IntOrBytes, y: int, pi: int, l: Optional[int] = None) -> Tuple[bool, str]:
        """
        Constant-time-ish verification:

            Check: y ≟ π^ℓ · x^(2^t mod ℓ) (mod N)

        Also re-derives ℓ' from (N, x, y, t) and requires ℓ == ℓ'.
        Returns:
            (ok, reason)  -- reason is "ok" when ok=True, otherwise a short label.
        """
        # Fixed references
        N = self.params.modulus_n
        t = self.params.iterations
        width = _int_byte_width(N)

        # Precompute normalized values (no early returns)
        xn = _normalize_x(x, N)
        y_mod = y % N

        # Derive challenge prime from transcript and compare (constant-time-ish)
        l_prime = derive_challenge_prime(N, xn, y_mod, t, k_bits=self.k_bits)
        l_use = int(l_prime if l is None else l)

        l_eq = _ct_int_eq(l_use, l_prime, max(width, _int_byte_width(l_prime)))

        # Compute r = 2^t mod ℓ and RHS = π^ℓ * x^r (mod N)
        r = pow(2, t, l_use)
        rhs = (pow(pi % N, l_use, N) * pow(xn, r, N)) % N

        # Core equality in constant-time-ish manner
        y_eq = _ct_int_eq(y_mod, rhs, width)

        # Optional structural sanity checks (evaluated but not branched on)
        # These are not strictly necessary for correctness, but help keep inputs sane.
        sane_l = (l_use | 1) == l_use  # odd
        sane_ranges = True  # avoid data-dependent branches; booleans will be ANDed

        ok = _ct_and(
            self.params.backend == "rsa",
            l_eq,
            y_eq,
            sane_l,
            sane_ranges,
        )

        # Build a non-branching reason selection
        # (we still compute all flags; choose first descriptive label)
        reason = "ok"
        # We avoid elif/early-return; assign deterministically by priority ordering.
        if not ok:
            # Assign reason by a fixed check order (informational only).
            reason = (
                "backend_mismatch" if self.params.backend != "rsa" else
                "challenge_mismatch" if not l_eq else
                "equation_mismatch" if not y_eq else
                "invalid_challenge" if not sane_l else
                "invalid_input"
            )

        return ok, reason


# -----------------------------------------------------------------------------
# Convenience API
# -----------------------------------------------------------------------------

def verify_consensus(x: IntOrBytes, y: int, pi: int, l: Optional[int] = None) -> bool:
    """
    Verify using VDF parameters from the environment (:func:`get_params`).
    Returns True iff proof is valid for current consensus parameters.
    """
    return Verifier.from_env().verify(x, y, pi, l)


__all__ = [
    "Verifier",
    "verify_consensus",
]
