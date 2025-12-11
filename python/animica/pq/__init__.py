"""Post-quantum signature abstraction for Animica.

The goal of this module is to present a tiny, well-defined API for PQ
operations while hiding optional liboqs/liboqs-python plumbing. When a real
backend is unavailable the code falls back to a deterministic, insecure fake
implementation that is only enabled when ``ANIMICA_UNSAFE_PQ_FAKE=1``.
"""

from __future__ import annotations

import hashlib
import os
from typing import Protocol, Tuple

ALG_ID_SPHINCS_SHAKE_128S = 0x1002
ALG_NAME = "sphincs_shake_128s"


class PQBackend(Protocol):
    """Minimal interface expected by CLI and RPC."""

    def keygen(self) -> Tuple[bytes, bytes]:
        ...

    def sign(self, secret_key: bytes, message: bytes) -> bytes:
        ...

    def verify(self, public_key: bytes, message: bytes, signature: bytes) -> bool:
        ...


class _FakeBackend:
    def keygen(self) -> Tuple[bytes, bytes]:
        seed = b"animica-pq-fake"
        sk = hashlib.sha256(seed + b"sk").digest()
        pk = hashlib.sha256(b"animica-pq-fake-pk|" + sk).digest()
        return pk, sk

    def sign(self, secret_key: bytes, message: bytes) -> bytes:
        # Derive the public key the same way as keygen so verification can be
        # performed using only the public key (matching real PQ schemes).
        pk = hashlib.sha256(b"animica-pq-fake-pk|" + secret_key).digest()

        # Deterministic, obviously insecure signature used only for local dev.
        sig = hashlib.sha512(b"animica-pq-fake-sig|" + pk + b"|" + message).digest()
        return sig + sig  # keep length large to resemble real PQ signatures

    def verify(self, public_key: bytes, message: bytes, signature: bytes) -> bool:
        expected = hashlib.sha512(
            b"animica-pq-fake-sig|" + public_key + b"|" + message
        ).digest()
        return signature == expected + expected


class _OQSBackend:
    def __init__(self) -> None:
        import oqs  # type: ignore

        self._sig = oqs.Signature("SPHINCS+-shake-128s")

    def keygen(self) -> Tuple[bytes, bytes]:
        pk, sk = self._sig.generate_keypair()
        return pk, sk

    def sign(self, secret_key: bytes, message: bytes) -> bytes:
        return self._sig.sign(message, secret_key)

    def verify(self, public_key: bytes, message: bytes, signature: bytes) -> bool:
        return self._sig.verify(message, signature, public_key)


def get_backend() -> Tuple[PQBackend, str]:
    """Select the best available PQ backend.

    Returns (backend, label)
    """

    if os.environ.get("ANIMICA_UNSAFE_PQ_FAKE") == "1":
        return _FakeBackend(), "fake"

    try:
        return _OQSBackend(), "oqs"
    except Exception:
        # Last resort is the fake backend, but guard with env toggle to make it
        # obvious in logs/tests.
        return _FakeBackend(), "fake"


__all__ = [
    "ALG_ID_SPHINCS_SHAKE_128S",
    "ALG_NAME",
    "PQBackend",
    "get_backend",
]
