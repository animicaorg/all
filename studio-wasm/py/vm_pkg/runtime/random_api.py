from __future__ import annotations

"""
random_api — deterministic PRNG seeded from call (Pyodide/browser-safe)

Design
------
- No OS entropy, no `secrets`, no `random` module; purely hash-based and
  repeatable across platforms.
- Stream generator built from SHA3-512 over (seed || domain || counter_be).
- Convenience helpers for bytes, integers, choices, and shuffles.
- Seed derivation helper to combine multiple call-scoped parts safely.

Typical use
-----------
    seed = derive_seed(call_tag, tx_hash, abi_args_digest)
    rng = DeterministicRandom(seed)
    nonce = rng.random_bytes(32)
    idx = rng.randint(10)

Determinism contract
--------------------
Given identical seed and the same sequence of method calls, all outputs
are byte-for-byte identical across runs and environments.
"""

from typing import Any, Iterable, List, Optional, Sequence, Tuple

from ..errors import ValidationError
from . import hash_api

# ---------------- Utilities ----------------


def _ensure_bytes(name: str, v: Any) -> bytes:
    if not isinstance(v, (bytes, bytearray)):
        raise ValidationError(f"{name} must be bytes")
    return bytes(v)


def _u32be(n: int) -> bytes:
    if n < 0 or n > 0xFFFFFFFF:
        raise ValidationError("length out of range for u32")
    return n.to_bytes(4, "big")


# ---------------- Seed Derivation ----------------


def derive_seed(*parts: bytes) -> bytes:
    """
    Derive a 32-byte seed from concatenated length-prefixed parts with a
    clear domain tag. Each part must be bytes.

    seed = SHA3-256("animica|rand|v1" || Σ (u32(len(part)) || part))
    """
    dom = b"animica|rand|v1"
    acc = bytearray(dom)
    for p in parts:
        p = _ensure_bytes("seed_part", p)
        acc += _u32be(len(p))
        acc += p
    return hash_api.sha3_256(bytes(acc))


# ---------------- PRNG ----------------


class DeterministicRandom:
    """
    Hash-driven deterministic PRNG.

    Internal state:
      - seed: 32 bytes (already domain-separated/derived by caller)
      - counter: 64-bit incrementing block index
    Block function:
      B(i) = SHA3-512(seed || b"|R|" || u64be(i))
    """

    __slots__ = ("_seed", "_ctr", "_buf", "_buf_idx")

    def __init__(self, seed: bytes) -> None:
        s = _ensure_bytes("seed", seed)
        if len(s) == 0:
            raise ValidationError("seed must be non-empty")
        # Normalize seed to fixed size
        self._seed = hash_api.sha3_256(s)
        self._ctr = 0
        self._buf = b""
        self._buf_idx = 0

    # ---- Core stream ----

    def _next_block(self) -> bytes:
        c = self._ctr.to_bytes(8, "big")
        self._ctr += 1
        return hash_api.sha3_512(self._seed + b"|R|" + c)

    def _ensure_bytes_available(self, n: int) -> None:
        if n <= 0:
            return
        remaining = len(self._buf) - self._buf_idx
        while remaining < n:
            blk = self._next_block()
            if self._buf_idx == 0 and not self._buf:
                # Fast-path: just append
                self._buf = blk
            else:
                # Compact: drop consumed prefix and append new block
                self._buf = self._buf[self._buf_idx :] + blk
                self._buf_idx = 0
            remaining = len(self._buf) - self._buf_idx

    # ---- Public API ----

    def random_bytes(self, n: int) -> bytes:
        """Return n bytes from the stream."""
        if not isinstance(n, int) or n < 0:
            raise ValidationError("n must be a non-negative int")
        if n == 0:
            return b""
        self._ensure_bytes_available(n)
        out = self._buf[self._buf_idx : self._buf_idx + n]
        self._buf_idx += n
        # If buffer fully consumed, reset to avoid growth
        if self._buf_idx >= len(self._buf):
            self._buf = b""
            self._buf_idx = 0
        return out

    def randbits(self, k: int) -> int:
        """Return a non-negative Python int with exactly k random bits (k>=0)."""
        if not isinstance(k, int) or k < 0:
            raise ValidationError("k must be a non-negative int")
        if k == 0:
            return 0
        nbytes = (k + 7) // 8
        raw = bytearray(self.random_bytes(nbytes))
        # Mask extraneous high bits
        excess = (8 * nbytes) - k
        if excess:
            raw[0] &= 0xFF >> excess
        out = 0
        for b in raw:
            out = (out << 8) | b
        return out

    def randint(self, upper: int) -> int:
        """Uniform integer in [0, upper-1]. Raises if upper <= 0."""
        if not isinstance(upper, int) or upper <= 0:
            raise ValidationError("upper must be a positive int")
        # Rejection sampling
        k = (upper - 1).bit_length()
        if k == 0:
            return 0
        while True:
            x = self.randbits(k)
            if x < upper:
                return x

    def choice(self, seq: Sequence[Any]) -> Any:
        if not isinstance(seq, Sequence) or len(seq) == 0:
            raise ValidationError("choice() requires a non-empty sequence")
        return seq[self.randint(len(seq))]

    def shuffle(self, seq: Sequence[Any]) -> List[Any]:
        """Return a shuffled copy of seq (Fisher–Yates using this RNG)."""
        arr = list(seq)
        # Fisher–Yates: for i from n-1 downto 1, swap i with j in [0, i]
        for i in range(len(arr) - 1, 0, -1):
            j = self.randint(i + 1)
            arr[i], arr[j] = arr[j], arr[i]
        return arr


# ---------------- Convenience wrapper ----------------


def random(
    n: int, *, seed: Optional[bytes] = None, rng: Optional[DeterministicRandom] = None
) -> bytes:
    """
    One-shot helper to get n bytes:
      - if rng is provided, use it
      - else if seed provided, construct a temporary RNG
      - else error
    """
    if rng is not None:
        return rng.random_bytes(n)
    if seed is None:
        raise ValidationError("random() requires either rng= or seed=")
    return DeterministicRandom(seed).random_bytes(n)


__all__ = [
    "derive_seed",
    "DeterministicRandom",
    "random",
]
