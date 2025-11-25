"""
Animica | proofs.vdf

Reference verifier for Wesolowski VDF over an RSA group of unknown order.
This module verifies a VDF proof and emits ProofMetrics usable by PoIES.

Verification (RSA group):
- Input: modulus N (odd, composite, e.g., RSA-2048), generator g ∈ (Z/NZ)*,
         output y = g^{2^T} mod N, iterations T, proof π,
         where challenge ℓ is a (deterministically) hashed prime.
- Check: π^ℓ * g^{r}  ≡  y  (mod N)  where r = 2^T mod ℓ.

We deterministically derive ℓ from (N,g,y) using SHA3-256 and a Miller–Rabin
probable-prime search with fixed bases. No trapdoor is required for verification.

Seconds-equivalent estimation:
- If body.calibration.iters_per_sec is provided, use it directly.
- Otherwise estimate from modulus size with a conservative heuristic.

Body shape (checked via proofs/schemas/vdf.cddl; summarized here):
{
  "group": { "kind": "RSA", "N": bstr },   # RSA modulus bytes (big-endian)
  "g": bstr,                                # generator bytes (big-endian)
  "y": bstr,                                # output bytes (big-endian)
  "T": uint,                                # number of squarings (>= 1)
  "proof": { "pi": bstr },                  # Wesolowski proof element (big-endian)
  ? "calibration": { "iters_per_sec": number }  # optional verifier speed hint
}

Returned metrics (subset):
- vdf_seconds: float  → estimated verification time-equivalent for T squarings.
Other fields in ProofMetrics remain None for this proof type.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from .errors import ProofError, SchemaError
from .types import ProofEnvelope, ProofType
from .metrics import ProofMetrics
from .cbor import validate_body
from .utils.hash import sha3_256


# ─────────────────────────────── utilities ───────────────────────────────

def _int_from_bytes(b: bytes) -> int:
    if not isinstance(b, (bytes, bytearray)):
        raise SchemaError("expected bytes")
    return int.from_bytes(b, "big", signed=False)

def _int_to_bytes(x: int, size: int | None = None) -> bytes:
    if x < 0:
        raise ValueError("negative int")
    if size is None:
        size = (x.bit_length() + 7) // 8 or 1
    return x.to_bytes(size, "big")


# ───────────────────── hash-to-prime (deterministic) ─────────────────────

_CHAL_DOMAIN = b"Animica/VDF/Wesolowski/challenge/v1"

def _hash_to_prime(seed: bytes, bits: int = 128, max_iter: int = 10_000) -> int:
    """
    Deterministically map seed → probable prime of 'bits' bits using SHA3-256
    and Miller–Rabin with fixed bases suitable up to 2^256.
    The candidate is taken from the hash stream, forced odd, and incremented by 2
    until a probable prime is found or max_iter is exceeded.
    """
    if bits < 64 or bits > 256:
        raise SchemaError("challenge prime size must be in [64,256] bits")
    # Expand a stream of hashes deterministically.
    ctr = 0
    while ctr < max_iter:
        h = sha3_256(_CHAL_DOMAIN + seed + ctr.to_bytes(8, "big"))
        # Mask to requested bit width and force top/low bits to ensure size & odd.
        m = int.from_bytes(h, "big")
        cand = m & ((1 << bits) - 1)
        cand |= (1 << (bits - 1))  # set MSB
        cand |= 1                  # force odd
        # Increment by 2 k times to explore nearby candidates deterministically.
        for k in range(0, 257):  # small stride before moving ctr
            c = cand + 2 * k
            if _is_probable_prime(c):
                return c
        ctr += 1
    raise ProofError("failed to derive a challenge prime within iteration budget")

def _is_probable_prime(n: int) -> bool:
    """Deterministic MR for 64..256-bit candidates with fixed bases."""
    if n < 2:
        return False
    # small primes
    small = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]
    for p in small:
        if n % p == 0:
            return n == p
    # write n-1 = d * 2^s
    d = n - 1
    s = (d & -d).bit_length() - 1  # count trailing zeros
    d >>= s
    # bases sufficient for 64..256 bits
    # (see research on deterministic bases; this set is conservative)
    bases = [2, 3, 5, 7, 11, 13, 17]
    for a in bases:
        if not _mr_check(a, s, d, n):
            return False
    return True

def _mr_check(a: int, s: int, d: int, n: int) -> bool:
    x = pow(a % n, d, n)
    if x == 1 or x == n - 1:
        return True
    for _ in range(s - 1):
        x = (x * x) % n
        if x == n - 1:
            return True
    return False


# ─────────────────────────── verification core ───────────────────────────

def _validate_group_rsa(group: Dict[str, Any]) -> int:
    if group.get("kind") != "RSA":
        raise SchemaError("only RSA group is supported in v1 verifier")
    N_b = group.get("N", None)
    if not isinstance(N_b, (bytes, bytearray)):
        raise SchemaError("group.N must be bytes")
    N = _int_from_bytes(N_b)
    if N < 3 or N % 2 == 0:
        raise SchemaError("RSA modulus must be an odd integer ≥ 3")
    return N

def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return abs(a)

def _derive_challenge_prime(N: int, g: int, y: int, bits: int = 128) -> int:
    seed = _int_to_bytes(N) + _int_to_bytes(g) + _int_to_bytes(y)
    return _hash_to_prime(seed, bits=bits)

def _estimate_seconds(T: int, mod_bits: int, hint_iters_per_sec: float | None) -> float:
    """
    Convert iterations to a crude time-equivalent for 'verification difficulty'.
    For Wesolowski verify, the cost is dominated by:
      - one modular exponentiation with exponent ℓ (≈ 2^bits), small vs N,
      - one exponentiation g^r with r < ℓ,
    and a few multiplications, *not* by T directly.
    In practice, verifier cost ~ O(log ℓ) exponentiations, independent of T.
    However, to express fairness vs. a time-delay target, chains often map T →
    "seconds-equivalent" via a calibrated iters/sec figure from the prover side.
    We support both paths: use hint if provided; otherwise fall back to a heuristic
    per modulus size (very conservative).
    """
    if hint_iters_per_sec and hint_iters_per_sec > 0:
        return float(T) / float(hint_iters_per_sec)

    # Heuristic: typical CPU squaring throughput in prover mode (very rough).
    # 2048-bit: ~3.0e6/s, 3072-bit: ~1.6e6/s, 4096-bit: ~0.9e6/s
    if mod_bits <= 2048:
        ips = 3.0e6
    elif mod_bits <= 3072:
        ips = 1.6e6
    else:
        ips = 0.9e6
    return float(T) / ips


def verify_vdf_body(body: Dict[str, Any]) -> Tuple[ProofMetrics, Dict[str, Any]]:
    """
    Verify a Wesolowski VDF proof body and return (ProofMetrics, details).
    """
    # 1) Schema (shape) validation against CDDL/JSON-Schema.
    validate_body(ProofType.VDF, body)

    # 2) Pull fields & basic checks.
    N = _validate_group_rsa(body["group"])
    g = _int_from_bytes(body["g"])
    y = _int_from_bytes(body["y"])
    proof = body.get("proof", {})
    pi = _int_from_bytes(proof.get("pi", b""))
    T = int(body["T"])

    if T < 1:
        raise SchemaError("T must be ≥ 1")
    if not (1 < g < N) or _gcd(g, N) != 1:
        raise ProofError("generator g not in multiplicative group modulo N")
    if not (1 < y < N) or _gcd(y, N) != 1:
        raise ProofError("output y not in multiplicative group modulo N")
    if not (1 < pi < N) or _gcd(pi, N) != 1:
        raise ProofError("proof π not in multiplicative group modulo N")

    mod_bits = N.bit_length()

    # 3) Derive the challenge prime ℓ deterministically from (N, g, y).
    # Bitsize of ℓ can be tuned; 128 bits is common.
    ell = _derive_challenge_prime(N, g, y, bits=128)

    # 4) Compute r = 2^T mod ℓ without materializing 2^T.
    r = pow(2, T, ell)  # Python's pow uses modular exponentiation efficiently.

    # 5) Verify Wesolowski equation: π^ℓ * g^r == y (mod N)
    left = (pow(pi, ell, N) * pow(g, r, N)) % N
    if left != y % N:
        raise ProofError("VDF equation does not hold for provided (π, ℓ, r)")

    # 6) Seconds-equivalent estimation (optional calibration).
    cal = body.get("calibration") or {}
    iters_per_sec = float(cal["iters_per_sec"]) if "iters_per_sec" in cal else None
    seconds_equiv = _estimate_seconds(T, mod_bits, iters_per_sec)

    # 7) Build metrics & details.
    metrics = ProofMetrics(
        vdf_seconds=float(seconds_equiv),
    )

    details: Dict[str, Any] = {
        "group": {"kind": "RSA", "mod_bits": mod_bits},
        "T": int(T),
        "ell_bits": int(ell.bit_length()),
        "ell": hex(ell),
        "r": int(r),
        "equation_ok": True,
        "calibration_used": (iters_per_sec is not None),
        "seconds_equiv": float(seconds_equiv),
    }
    return metrics, details


def verify_envelope(env: ProofEnvelope) -> Tuple[ProofMetrics, Dict[str, Any]]:
    """
    Envelope-aware wrapper; validates type, then delegates to verify_vdf_body.
    """
    if env.type_id != ProofType.VDF:
        raise SchemaError(f"wrong proof type for VDF verifier: {int(env.type_id)}")
    return verify_vdf_body(env.body)


__all__ = [
    "verify_vdf_body",
    "verify_envelope",
]
