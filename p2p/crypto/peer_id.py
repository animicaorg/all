from __future__ import annotations

"""
Peer ID derivation
==================

Canonical Animica peer-id is:

    pid = SHA3-256( pubkey || alg_id_be32 )

where:
- `pubkey` is the raw public-key bytes of the node's *signature* identity
  (e.g., Dilithium3 or SPHINCS+ public key).
- `alg_id_be32` is the algorithm id as an unsigned 32-bit big-endian integer
  resolved via pq.registry (e.g., Dilithium3, SPHINCS+).

This file exposes helpers to compute the peer-id in bytes or hex and small
validators for formatting.

No extra domain tag is included (to stay interoperable with the wire-protocol
docs). If the registry changes ids in the future, the peer-id will change; ids
are expected to be stable and versioned via policy roots.

Example
-------
>>> from p2p.crypto.keys import generate
>>> ident = generate("dilithium3")
>>> from p2p.crypto.peer_id import peer_id_hex_from_identity
>>> peer_id_hex_from_identity(ident)[:16]
'c1a4…'

Security notes
--------------
Peer IDs are not secrets. They are stable identifiers derived from public data.
"""

from typing import Union, Optional, NewType
import binascii
import hashlib

# Resolve algorithm id from the pq registry
from pq.py import registry as pq_registry

PeerId = NewType("PeerId", bytes)

def _sha3_256(data: bytes) -> bytes:
    return hashlib.sha3_256(data).digest()

def _alg_id_u32_be(alg: Union[str, int]) -> bytes:
    """
    Resolve an algorithm identifier to a canonical 32-bit big-endian encoding.
    Accepts either a canonical name (e.g., 'dilithium3') or a numeric id.
    """
    if isinstance(alg, int):
        aid = alg
    else:
        # normalize name e.g. 'sphincs-shake-128s' → 'sphincs_shake_128s'
        name = alg.strip().lower().replace("-", "_")
        try:
            aid = int(pq_registry.id_of(name))  # type: ignore[attr-defined]
        except Exception as e:
            raise ValueError(f"unknown PQ algorithm name: {alg}") from e
    if aid < 0 or aid > 0xFFFFFFFF:
        raise ValueError("algorithm id outside 32-bit range")
    return aid.to_bytes(4, "big")

def peer_id(pubkey: bytes, alg: Union[str, int]) -> PeerId:
    """
    Compute the raw 32-byte peer id = sha3_256(pubkey || alg_id_be32).
    """
    if not isinstance(pubkey, (bytes, bytearray)) or len(pubkey) == 0:
        raise ValueError("pubkey must be non-empty bytes")
    return PeerId(_sha3_256(bytes(pubkey) + _alg_id_u32_be(alg)))

def peer_id_hex(pubkey: bytes, alg: Union[str, int]) -> str:
    """
    Return the peer id as a lowercase hex string (64 chars).
    """
    return peer_id(pubkey, alg).hex()

def peer_id_from_identity(ident: "NodeIdentity") -> PeerId:
    """
    Convenience: compute from p2p.crypto.keys.NodeIdentity without importing here at module top.
    """
    # Import locally to avoid circular imports
    try:
        from p2p.crypto.keys import NodeIdentity  # type: ignore
    except Exception:
        NodeIdentity = object  # type: ignore
    if not hasattr(ident, "pubkey") or not hasattr(ident, "alg"):
        raise TypeError("ident must be a NodeIdentity-like object with pubkey and alg")
    return peer_id(ident.pubkey, ident.alg)  # type: ignore[arg-type]

def peer_id_hex_from_identity(ident: "NodeIdentity") -> str:
    return peer_id_from_identity(ident).hex()

# --- Small utilities ---------------------------------------------------------

def is_valid_peer_id_bytes(b: bytes) -> bool:
    """Peer id bytes must be exactly 32 bytes."""
    return isinstance(b, (bytes, bytearray)) and len(b) == 32

def is_valid_peer_id_hex(s: str) -> bool:
    """Hex form must be 64 hex chars."""
    if not isinstance(s, str) or len(s) != 64:
        return False
    try:
        b = binascii.unhexlify(s)
    except Exception:
        return False
    return is_valid_peer_id_bytes(b)

def format_peer_id_short(pid: Union[PeerId, bytes, str], *, sep: str = "...", head: int = 6, tail: int = 6) -> str:
    """
    Render a compact string like 'c1a4ee...9f02d3' for UI/logs.
    Accepts raw bytes, PeerId, or hex string.
    """
    if isinstance(pid, (bytes, bytearray)):
        hx = bytes(pid).hex()
    else:
        hx = str(pid)
    if len(hx) < head + tail + len(sep):
        return hx
    return f"{hx[:head]}{sep}{hx[-tail:]}"

# --- Self-test (manual) ------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    from p2p.crypto.keys import generate
    ident = generate("dilithium3")
    hx = peer_id_hex_from_identity(ident)
    print(f"peer-id: {hx} ({format_peer_id_short(hx)})")
