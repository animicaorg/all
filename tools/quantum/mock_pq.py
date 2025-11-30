"""A small mock PQ-like signer for development/testing.

This uses HMAC-SHA256 as a stand-in for PQ signatures. The real implementation should
use actual post-quantum signature algorithms (e.g., Dilithium) and secure key storage.

API:
  gen_key() -> bytes (secret key hex)
  sign(message: bytes, sk_hex: str) -> str (hex signature)
  verify(message: bytes, sk_hex: str, sig_hex: str) -> bool

Note: this is intentionally simple and *not* cryptographically equivalent to PQ.
"""

from __future__ import annotations

import hashlib
import hmac
import os


def gen_key() -> str:
    return os.urandom(32).hex()


def sign(message: bytes, sk_hex: str) -> str:
    sk = bytes.fromhex(sk_hex)
    sig = hmac.new(sk, message, hashlib.sha256).digest()
    return sig.hex()


def verify(message: bytes, sk_hex: str, sig_hex: str) -> bool:
    expected = sign(message, sk_hex)
    return hmac.compare_digest(expected, sig_hex)
