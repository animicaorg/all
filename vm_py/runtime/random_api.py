"""
vm_py.runtime.random_api — deterministic PRNG seeded from transaction hash.

Purpose
-------
Contracts sometimes want cheap pseudo-randomness for tests or local simulations.
This module provides a *deterministic* PRNG that derives bytes from a caller-
provided seed (typically a tx hash) using SHA3-256 in counter mode with strong
domain separation. It is **not** a source of consensus or cryptographic
randomness; hosts may replace it with a capability-based provider later.

Design
------
- Counter-mode DRBG over SHA3-256 with explicit domain tags.
- Pure, reproducible, and side-effect free (no OS randomness).
- Bytes-first API plus helpers for u64 and unbiased range sampling.

Typical usage
-------------
from vm_py.runtime import random_api as rnd

prng = rnd.from_tx_seed(tx_hash=b"...32-bytes...", caller=b"contract_addr", salt=b"demo")
nonce = prng.read(16)
i = prng.u64()
j = prng.randrange(10_000)   # unbiased 0..9999
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

try:
    from vm_py.errors import VmError
except Exception:  # pragma: no cover
    class VmError(Exception):  # type: ignore
        pass

from . import hash_api as _h


_DOMAIN_INIT = b"vm/random/init/v1"
_DOMAIN_BLOCK = b"vm/random/block/v1"
_MAX_REQUEST = 1 << 24  # 16 MiB per call guard for sanity


def _ensure_bytes(x: object, name: str) -> bytes:
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    raise VmError(f"{name} must be bytes-like, got {type(x).__name__}")


def _ensure_int(x: object, name: str, *, min_: int = 0, max_: Optional[int] = None) -> int:
    if not isinstance(x, int):
        raise VmError(f"{name} must be int, got {type(x).__name__}")
    if x < min_:
        raise VmError(f"{name} must be \u2265 {min_} (got {x})")
    if max_ is not None and x > max_:
        raise VmError(f"{name} must be \u2264 {max_} (got {x})")
    return x


@dataclass
class DRBG:
    """
    Deterministic PRNG over SHA3-256 in counter mode.

    state = SHA3-256(domain=_DOMAIN_INIT, seed || "|" || nonce || "|" || info)
    block_i = SHA3-256(domain=_DOMAIN_BLOCK, state || LE64(counter))
    """
    _state: bytes
    _counter: int = 0
    _buf: bytes = b""
    _pos: int = 0

    @staticmethod
    def new(seed: bytes, *, nonce: bytes = b"", info: bytes = b"") -> "DRBG":
        seed = _ensure_bytes(seed, "seed")
        nonce = _ensure_bytes(nonce, "nonce")
        info = _ensure_bytes(info, "info")
        state = _h.sha3_256(seed + b"|" + nonce + b"|" + info, domain=_DOMAIN_INIT)
        return DRBG(_state=state)

    # ----------------------------- Byte interface -----------------------------

    def _refill(self) -> None:
        # Produce the next 32-byte block
        block = _h.sha3_256(self._state + int.to_bytes(self._counter, 8, "little"), domain=_DOMAIN_BLOCK)
        self._counter += 1
        self._buf = block
        self._pos = 0

    def read(self, n: int) -> bytes:
        """Return exactly n bytes deterministically."""
        _ensure_int(n, "n", min_=0, max_=_MAX_REQUEST)
        if n == 0:
            return b""
        out = bytearray()
        while n > 0:
            if self._pos >= len(self._buf):
                self._refill()
            take = min(n, len(self._buf) - self._pos)
            out += self._buf[self._pos : self._pos + take]
            self._pos += take
            n -= take
        return bytes(out)

    # ----------------------------- Int conveniences ---------------------------

    def u64(self) -> int:
        """Return an unsigned 64-bit integer."""
        return int.from_bytes(self.read(8), "little")

    def randrange(self, n: int) -> int:
        """
        Unbiased integer in [0, n). Uses rejection sampling to avoid modulo bias.
        """
        n = _ensure_int(n, "n", min_=1)
        # Find the largest 64-bit value that can be reduced modulo n without bias.
        # We compute a threshold 't' such that values < t are accepted.
        # t = floor(2^64 / n) * n
        m = 1 << 64
        t = (m // n) * n
        while True:
            x = self.u64()
            if x < t:
                return x % n

    # ----------------------------- Derivation helper --------------------------

    def fork(self, *, label: bytes) -> "DRBG":
        """Derive a new DRBG instance from this one with a label."""
        label_b = _ensure_bytes(label, "label")
        child_state = _h.sha3_256(self._state + b"|fork|" + label_b, domain=_DOMAIN_INIT)
        return DRBG(_state=child_state)


# ------------------------------- Public helpers -------------------------------

def random_bytes(n: int, seed: bytes, *, nonce: bytes = b"", info: bytes = b"") -> bytes:
    """
    Convenience one-shot: derive from (seed, nonce, info) and read n bytes.
    """
    return DRBG.new(seed, nonce=nonce, info=info).read(_ensure_int(n, "n", min_=0, max_=_MAX_REQUEST))


def from_tx_seed(*, tx_hash: bytes, caller: bytes = b"", salt: bytes = b"") -> DRBG:
    """
    Build a DRBG seeded from a transaction hash and optional caller/salt.
    - tx_hash: typically the 32-byte hash of the tx that initiated execution
    - caller: contract/address bytes to further specialize sequences per-caller
    - salt:   free-form disambiguation label (e.g., method name)
    """
    tx_hash_b = _ensure_bytes(tx_hash, "tx_hash")
    if len(tx_hash_b) == 0:
        raise VmError("tx_hash must be non-empty")
    caller_b = _ensure_bytes(caller, "caller")
    salt_b = _ensure_bytes(salt, "salt")
    return DRBG.new(seed=tx_hash_b, nonce=caller_b, info=salt_b)


__all__ = [
    "DRBG",
    "random_bytes",
    "from_tx_seed",
]
