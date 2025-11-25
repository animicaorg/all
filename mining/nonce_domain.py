from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Domain tags (must match spec/domains.yaml)
# ─────────────────────────────────────────────────────────────────────────────
DOMAIN_U_DRAW = b"animica/u-draw/v1"
DOMAIN_MIXSEED = b"animica/mixseed/v1"
DOMAIN_HEADER_NONCE = b"animica/header-nonce/v1"


def sha3_256(data: bytes) -> bytes:
    """Shortcut to hashlib.sha3_256 for clarity."""
    return hashlib.sha3_256(data).digest()


# ─────────────────────────────────────────────────────────────────────────────
# mixSeed derivation
# ─────────────────────────────────────────────────────────────────────────────
def derive_mix_seed(parent_mix_seed: bytes, beacon: bytes, extra: bytes = b"") -> bytes:
    """
    Derive the next mixSeed bound to the previous one and the randomness beacon.

    mixSeed := SHA3-256( DOMAIN_MIXSEED || parent_mix_seed || beacon || extra )

    - parent_mix_seed: 32 bytes from parent header
    - beacon: randomness/beacon output bytes (length flexible)
    - extra: optional bytes to bind chain- or network-specific salts
    """
    return sha3_256(DOMAIN_MIXSEED + parent_mix_seed + beacon + extra)


# ─────────────────────────────────────────────────────────────────────────────
# Header+nonce preimage construction
# ─────────────────────────────────────────────────────────────────────────────
def build_nonce_preimage(
    header_sign_bytes: bytes,
    mix_seed: bytes,
    nonce: bytes,
) -> bytes:
    """
    Construct the AE/PoW preimage used for the u-draw.

    preimage := DOMAIN_HEADER_NONCE || header_sign_bytes || mix_seed || nonce

    - header_sign_bytes: canonical "SignBytes" of the header template (see core/encoding/canonical.py)
    - mix_seed: 32-byte seed derived via derive_mix_seed or supplied by template
    - nonce: arbitrary-length nonce bytes (commonly 8 or 16 bytes, big-endian)
    """
    return DOMAIN_HEADER_NONCE + header_sign_bytes + mix_seed + nonce


# ─────────────────────────────────────────────────────────────────────────────
# u-draw & H(u) helpers
# ─────────────────────────────────────────────────────────────────────────────
_U256 = 1 << 256  # 2^256
_U256_PLUS_1 = _U256 + 1


def u_from_digest(digest: bytes) -> float:
    """
    Map a 256-bit digest to a uniform u in (0,1), inclusive of neither endpoint.

    We compute: u = (x + 1) / (2^256 + 1) where x = int(digest).
    This avoids u=0 exactly and keeps u<1.
    """
    if len(digest) != 32:
        raise ValueError("digest must be 32 bytes (SHA3-256)")
    x = int.from_bytes(digest, "big", signed=False)
    return (x + 1) / _U256_PLUS_1


def H_from_u(u: float) -> float:
    """
    Compute H(u) = -ln(u) in natural units (nats).
    Uses math.log (double precision) which is more than adequate given u is ~256 bits.
    """
    if not (0.0 < u < 1.0):
        raise ValueError("u must be in (0,1)")
    return -math.log(u)


def micro_nats(h_nats: float) -> int:
    """
    Convert nats → micro-nats (µ-nats) with rounding to nearest integer.
    """
    return int(round(h_nats * 1_000_000))


def h_from_preimage_micro(
    header_sign_bytes: bytes,
    mix_seed: bytes,
    nonce: bytes,
) -> Tuple[bytes, float, int]:
    """
    Convenience: build preimage, hash to digest, map → u, compute H(u) and µ-nats.

    Returns: (digest, u, h_micro_nats)
    """
    pre = build_nonce_preimage(header_sign_bytes, mix_seed, nonce)
    digest = sha3_256(DOMAIN_U_DRAW + pre)
    u = u_from_digest(digest)
    h = H_from_u(u)
    return digest, u, micro_nats(h)


# ─────────────────────────────────────────────────────────────────────────────
# Structured result for scanners
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class UDrawResult:
    digest: bytes
    u: float
    h_nats: float
    h_micro: int

    @classmethod
    def from_preimage(cls, header_sign_bytes: bytes, mix_seed: bytes, nonce: bytes) -> "UDrawResult":
        pre = build_nonce_preimage(header_sign_bytes, mix_seed, nonce)
        digest = sha3_256(DOMAIN_U_DRAW + pre)
        u = u_from_digest(digest)
        h = H_from_u(u)
        return cls(digest=digest, u=u, h_nats=h, h_micro=micro_nats(h))


# ─────────────────────────────────────────────────────────────────────────────
# Nonce utilities
# ─────────────────────────────────────────────────────────────────────────────
def int_to_nonce(value: int, length: int = 8) -> bytes:
    """
    Encode an integer nonce as big-endian bytes of fixed length.
    """
    if value < 0:
        raise ValueError("nonce must be non-negative")
    return value.to_bytes(length, "big", signed=False)


def bump_nonce(nonce: bytes) -> bytes:
    """
    Increment a fixed-length big-endian nonce with wrap-around.
    """
    n = (int.from_bytes(nonce, "big") + 1) % (1 << (8 * len(nonce)))
    return n.to_bytes(len(nonce), "big")


# ─────────────────────────────────────────────────────────────────────────────
# Example (manual test)
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":  # pragma: no cover
    # Fake header SignBytes and seeds for a quick smoke test.
    header_sign_bytes = b"\x01" * 64  # placeholder canonical header bytes
    parent_mix = b"\x00" * 32
    beacon = b"beacon-seed"
    mix_seed = derive_mix_seed(parent_mix, beacon)

    nonce = int_to_nonce(42, length=8)
    res = UDrawResult.from_preimage(header_sign_bytes, mix_seed, nonce)
    print("digest =", res.digest.hex())
    print("u      =", f"{res.u:.18f}")
    print("H(u)   =", f"{res.h_nats:.9f}", "nats")
    print("Hµ     =", res.h_micro, "µ-nats")
