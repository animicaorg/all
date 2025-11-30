"""
p2p.crypto
==========

Public crypto surface for the P2P stack.  This package intentionally
lazy-loads its submodules so importing `p2p.crypto` is cheap and does not
pull heavy dependencies until actually needed.

Submodules (loaded on first attribute access)
---------------------------------------------
- handshake : Kyber768 KEM + HKDF-SHA3-256 key schedule for P2P sessions
- aead     : ChaCha20-Poly1305 and AES-GCM wrappers (nonce schedule helpers)
- keys     : node identity key handling (Dilithium3 / SPHINCS+)
- peer_id  : peer-id derivation (sha3-256(pubkey) with alg_id tag)
- cert     : optional self-signed node certificate for QUIC ALPN

Utility helpers exported here
-----------------------------
- get_default_aead_name() -> str
- get_default_aead() -> object providing seal(open) APIs from aead.*
- make_peer_id(pubkey: bytes, alg_id: int) -> str
"""

from __future__ import annotations

import importlib
import os
from typing import Any, Dict

__all__ = [
    "handshake",
    "aead",
    "keys",
    "peer_id",
    "cert",
    "get_default_aead_name",
    "get_default_aead",
    "make_peer_id",
]

# Map public attribute -> submodule path (relative)
_LAZY: Dict[str, str] = {
    "handshake": ".handshake",
    "aead": ".aead",
    "keys": ".keys",
    "peer_id": ".peer_id",
    "cert": ".cert",
}


def __getattr__(name: str) -> Any:
    modpath = _LAZY.get(name)
    if not modpath:
        raise AttributeError(name)
    mod = importlib.import_module(__name__ + modpath)
    globals()[name] = mod
    return mod


# --------------------------------------------------------------------- #
# Small convenience helpers (lazy-importing internally)
# --------------------------------------------------------------------- #


def get_default_aead_name() -> str:
    """
    Return the configured AEAD name:
      - P2P_AEAD env var (case-insensitive) if set,
      - otherwise 'chacha20-poly1305' (fast & safe default).
    Accepted values: 'chacha20-poly1305', 'aes-gcm'.
    """
    v = os.getenv("P2P_AEAD", "chacha20-poly1305").strip().lower()
    if v in ("chacha20-poly1305", "aes-gcm"):
        return v
    # Be strict: misconfig should not silently change crypto.
    raise ValueError(
        f"Unsupported P2P_AEAD={v!r}; expected 'chacha20-poly1305' or 'aes-gcm'"
    )


def get_default_aead() -> Any:
    """
    Return the default AEAD implementation object from `aead`:
        aead.chacha20_poly1305  or  aead.aes_gcm
    The returned object exposes:
        - keygen() -> bytes
        - seal(key, nonce, aad, plaintext) -> ciphertext
        - open(key, nonce, aad, ciphertext) -> plaintext
        - NONCE_SIZE, KEY_SIZE, TAG_SIZE
    """
    mod = __getattr__("aead")
    name = get_default_aead_name()
    if name == "chacha20-poly1305":
        return mod.chacha20_poly1305
    else:
        return mod.aes_gcm


def make_peer_id(pubkey: bytes, alg_id: int) -> str:
    """
    Convenience wrapper around `peer_id.derive_peer_id`.
    """
    pid_mod = __getattr__("peer_id")
    return pid_mod.derive_peer_id(pubkey, alg_id)
