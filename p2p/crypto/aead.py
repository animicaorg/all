from __future__ import annotations

"""
AEAD wrappers for P2P transport
===============================

This module provides a small, uniform wrapper over two AEAD constructions:

  • ChaCha20-Poly1305  (preferred on most platforms)
  • AES-256-GCM        (fallback/alternative)

Both use a 12-byte nonce. We derive per-record nonces from a 12-byte
`nonce_base` combined with a monotonically increasing 64-bit sequence number:

    nonce = nonce_base[0:4] || (nonce_base[4:12] XOR be64(seq))

This avoids nonce reuse as long as `seq` never repeats and stays < 2^64.
Each direction (send/recv) uses an *independent* `nonce_base`, so peers
derive different nonces for the same seq value.

Associated Data (AAD)
---------------------
We prepend a constant domain tag to any caller-provided AAD:

    AAD_effective = b"animica/p2p/aead/v1" || user_aad

API
---
- AEADContext: stateful encryptor/decryptor with an internal sequence counter.
- create_aead(name, key, nonce_base, ...): factory.
- available_aeads(): list alg names available on this platform.

Security notes
--------------
- KEY must be 32 bytes (256-bit) for both algorithms.
- NONCE_BASE must be 12 bytes.
- Reuse of (key, nonce) pair catastrophically breaks security. This schedule,
  combined with a monotonic seq per direction, prevents reuse.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

try:
    from cryptography.hazmat.primitives.ciphers.aead import (AESGCM,
                                                             ChaCha20Poly1305)
except Exception as e:  # pragma: no cover - environment without cryptography
    AESGCM = None  # type: ignore
    ChaCha20Poly1305 = None  # type: ignore
    _cryptography_import_error = e
else:
    _cryptography_import_error = None

AEAD_DOMAIN_TAG = b"animica/p2p/aead/v1"
NONCE_SIZE = 12
KEY_SIZE = 32


def _xor_bytes(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def _be64(n: int) -> bytes:
    if n < 0 or n > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("sequence number out of range [0, 2^64-1]")
    return n.to_bytes(8, "big")


def derive_nonce(nonce_base: bytes, seq: int) -> bytes:
    """
    Build a 12-byte nonce:
        prefix = nonce_base[:4]
        tail   = nonce_base[4:] XOR be64(seq)
        return prefix || tail
    """
    if len(nonce_base) != NONCE_SIZE:
        raise ValueError("nonce_base must be 12 bytes")
    return nonce_base[:4] + _xor_bytes(nonce_base[4:], _be64(seq))


@dataclass
class AEADContext:
    """
    Stateful AEAD context with a per-direction sequence counter.

    encrypt(plaintext, aad=...) -> (ciphertext, seq_used)
    decrypt(ciphertext, seq, aad=...) -> plaintext
    """

    name: str
    key: bytes
    nonce_base: bytes
    _seq: int = 0
    _impl: object = None  # underlying cryptography object
    _aad_prefix: bytes = AEAD_DOMAIN_TAG

    def __post_init__(self) -> None:
        if len(self.key) != KEY_SIZE:
            raise ValueError("key must be 32 bytes")
        if len(self.nonce_base) != NONCE_SIZE:
            raise ValueError("nonce_base must be 12 bytes")

        if self.name.lower() in ("chacha20-poly1305", "chacha20_poly1305", "chacha20"):
            if ChaCha20Poly1305 is None:  # pragma: no cover
                raise RuntimeError(
                    f"ChaCha20-Poly1305 not available: {_cryptography_import_error}"
                )
            self._impl = ChaCha20Poly1305(self.key)
            self.name = "chacha20-poly1305"
        elif self.name.lower() in ("aes-256-gcm", "aes_gcm", "aes-gcm", "aes"):
            if AESGCM is None:  # pragma: no cover
                raise RuntimeError(
                    f"AES-GCM not available: {_cryptography_import_error}"
                )
            self._impl = AESGCM(self.key)
            self.name = "aes-256-gcm"
        else:
            raise ValueError(f"unknown AEAD algorithm: {self.name}")

    @property
    def seq(self) -> int:
        return self._seq

    def set_seq(self, n: int) -> None:
        if n < 0 or n > 0xFFFFFFFFFFFFFFFF:
            raise ValueError("sequence out of range")
        self._seq = n

    def _aad(self, user_aad: Optional[bytes]) -> bytes:
        if user_aad:
            return self._aad_prefix + user_aad
        return self._aad_prefix

    def encrypt(
        self, plaintext: bytes, *, aad: Optional[bytes] = None
    ) -> Tuple[bytes, int]:
        """
        Encrypt and increment sequence counter.
        Returns (ciphertext, seq_used).
        """
        if self._seq == 0xFFFFFFFFFFFFFFFF:
            raise OverflowError("sequence counter exhausted")
        seq_used = self._seq
        nonce = derive_nonce(self.nonce_base, seq_used)
        aad_eff = self._aad(aad)

        if self.name == "chacha20-poly1305":
            ct = self._impl.encrypt(nonce, plaintext, aad_eff)  # type: ignore[attr-defined]
        else:  # aes-256-gcm
            ct = self._impl.encrypt(nonce, plaintext, aad_eff)  # type: ignore[attr-defined]

        self._seq += 1
        return ct, seq_used

    def decrypt(
        self, ciphertext: bytes, seq: int, *, aad: Optional[bytes] = None
    ) -> bytes:
        """
        Decrypt a record that used the given sequence number.
        Does *not* mutate the internal sequence counter.
        """
        nonce = derive_nonce(self.nonce_base, seq)
        aad_eff = self._aad(aad)

        if self.name == "chacha20-poly1305":
            return self._impl.decrypt(nonce, ciphertext, aad_eff)  # type: ignore[attr-defined]
        else:
            return self._impl.decrypt(nonce, ciphertext, aad_eff)  # type: ignore[attr-defined]


def available_aeads() -> list[str]:
    """
    Return algorithm names available on this runtime.
    """
    algs: list[str] = []
    if ChaCha20Poly1305 is not None:
        algs.append("chacha20-poly1305")
    if AESGCM is not None:
        algs.append("aes-256-gcm")
    return algs


def create_aead(
    name: str,
    key: bytes,
    nonce_base: bytes,
    *,
    aad_prefix: Optional[bytes] = None,
    initial_seq: int = 0,
) -> AEADContext:
    """
    Factory to build an AEADContext.
    """
    ctx = AEADContext(
        name=name,
        key=key,
        nonce_base=nonce_base,
        _aad_prefix=aad_prefix if aad_prefix is not None else AEAD_DOMAIN_TAG,
    )
    ctx.set_seq(initial_seq)
    return ctx
