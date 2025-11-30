from __future__ import annotations

"""
address.py — Animica bech32m addresses

Format
------
Address = bech32m( HRP="anim", data = convertbits(payload, 8->5) )
payload = alg_id(2 bytes, big-endian) || sha3_256(pubkey)(32 bytes)

- `alg_id` identifies the signing algorithm of the account key (see pq/alg_ids.yaml).
- `sha3_256(pubkey)` binds the long public key down to a fixed 32-byte digest.

Examples
--------
>>> from pq.py.registry import ALG_ID
>>> from pq.py.utils.hash import sha3_256
>>> # toy pubkey bytes for demo (DO NOT USE IN PROD)
>>> pub = b"\x02" + b"\x11"*32
>>> addr = address_from_pubkey(pub, ALG_ID["dilithium3"])
>>> rec = decode_address(addr)
>>> rec.hrp, rec.alg_id, rec.digest.hex()[:8]
('anim', ALG_ID['dilithium3'], sha3_256(pub).hex()[:8])

This module is self-contained and only relies on:
- pq.py.utils.hash       (sha3_256)
- pq.py.utils.bech32     (bech32m encode/decode + convertbits)
- pq.py.registry         (optional) for validating alg_id is known
"""

from dataclasses import dataclass
from typing import Optional, Tuple

# We depend on the utility bech32 module you added earlier.
# It should export: bech32_encode(hrp: str, data: bytes-like, spec="bech32m") -> str
#                   bech32_decode(addr: str) -> Tuple[str, bytes, str]
#                   convertbits(data: bytes, from_bits: int, to_bits: int, pad: bool) -> bytes
from pq.py.utils import bech32 as _b32
from pq.py.utils.hash import sha3_256

HRP_DEFAULT = "anim"
_PAYLOAD_LEN = 2 + 32  # alg_id (2) + digest (32)
_BECH32_MAX_LEN = 90


class AddressError(ValueError):
    """Raised when an address fails parsing or validation."""


@dataclass(frozen=True)
class AddressRecord:
    hrp: str
    alg_id: int
    digest: bytes  # 32-byte sha3(pubkey)

    def to_string(self, hrp_override: Optional[str] = None) -> str:
        """Re-encode this record as a bech32m string."""
        hrp = hrp_override or self.hrp
        payload = self.alg_id.to_bytes(2, "big") + self.digest
        data5 = _b32.convertbits(payload, 8, 5, True)
        return _b32.bech32_encode(hrp, data5, spec="bech32m")


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def payload_from_pubkey(pubkey: bytes, alg_id: int) -> bytes:
    """
    Compute the 34-byte payload (alg_id_be16 || sha3_256(pubkey)).
    """
    if not isinstance(pubkey, (bytes, bytearray, memoryview)):
        raise TypeError("pubkey must be bytes-like")
    if not (0 <= alg_id <= 0xFFFF):
        raise ValueError("alg_id must fit in two bytes (0..65535)")
    digest = sha3_256(bytes(pubkey))
    return alg_id.to_bytes(2, "big") + digest


def address_from_pubkey(pubkey: bytes, alg_id: int, *, hrp: str = HRP_DEFAULT) -> str:
    """
    Build an Animica address string from a raw public key & algorithm id.
    """
    payload = payload_from_pubkey(pubkey, alg_id)
    data5 = _b32.convertbits(payload, 8, 5, True)
    addr = _b32.bech32_encode(hrp, data5, spec="bech32m")
    if len(addr) > _BECH32_MAX_LEN:
        # Should not happen for our payload size; defensive check.
        raise AddressError("encoded address exceeds bech32 length limits")
    return addr


# ---------------------------------------------------------------------------
# Decoding / Validation
# ---------------------------------------------------------------------------


def decode_address(addr: str, *, expect_hrp: Optional[str] = None) -> AddressRecord:
    """
    Parse a bech32m address back to components. Raises AddressError on failure.

    Args:
      addr: bech32m string, e.g., 'anim1...'
      expect_hrp: if provided, must match the address HRP.
    """
    try:
        hrp, data5, spec = _b32.bech32_decode(addr)
    except Exception as e:
        raise AddressError(f"bech32m decode failed: {e}") from e

    if spec != "bech32m":
        raise AddressError("Animica addresses must use bech32m")

    if expect_hrp is not None and hrp != expect_hrp:
        raise AddressError(f"HRP mismatch: expected {expect_hrp!r}, got {hrp!r}")

    try:
        payload = _b32.convertbits(data5, 5, 8, False)
    except Exception as e:
        raise AddressError(f"5-bit to 8-bit conversion failed: {e}") from e

    if len(payload) != _PAYLOAD_LEN:
        raise AddressError(f"payload length invalid: {len(payload)} != {_PAYLOAD_LEN}")

    alg_id = int.from_bytes(payload[0:2], "big")
    digest = payload[2:]

    # Optional: ensure alg_id is recognized by our registry
    try:
        from pq.py.registry import is_known_alg_id

        if not is_known_alg_id(alg_id):
            raise AddressError(f"unknown alg_id: 0x{alg_id:02x}")
    except Exception:
        # If registry import fails (early bootstrap), we still return the record.
        # Upstream callers can decide whether to treat unknown alg_ids as fatal.
        pass

    return AddressRecord(hrp=hrp, alg_id=alg_id, digest=digest)


def validate_address(
    addr: str,
    *,
    expect_hrp: Optional[str] = None,
    allowed_alg_ids: Optional[set[int]] = None,
) -> bool:
    """
    Lightweight validator. Returns True if valid; otherwise raises AddressError.

    - Checks bech32m checksum/spec, HRP (if provided), payload length,
      known/allowed algorithm id (if provided).
    """
    rec = decode_address(addr, expect_hrp=expect_hrp)
    if allowed_alg_ids is not None and rec.alg_id not in allowed_alg_ids:
        raise AddressError(f"alg_id not allowed: 0x{rec.alg_id:02x}")
    return True


# ---------------------------------------------------------------------------
# Pretty helpers
# ---------------------------------------------------------------------------


def short(addr: str, *, keep: int = 6) -> str:
    """
    Render a short address like anim1qq..abcd (useful in logs/UI).
    """
    if len(addr) <= 2 * keep + 3:
        return addr
    return f"{addr[:keep]}…{addr[-keep:]}"


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Tiny smoke test with deterministic pubkey material (not cryptographic!)
    pubkey_demo = b"\x01" * 48  # dummy bytes; real pubkeys come from the PQ libs
    try:
        from pq.py.registry import ALG_ID

        alg = ALG_ID.get("dilithium3", 0x30)
    except Exception:
        alg = 0x30  # fallback default

    addr = address_from_pubkey(pubkey_demo, alg)
    print("[address] demo:", addr, short(addr))

    rec = decode_address(addr, expect_hrp=HRP_DEFAULT)
    assert rec.alg_id == alg, "alg_id round-trip mismatch"
    # Re-encode and compare
    addr2 = rec.to_string()
    assert addr2 == addr, "address re-encode mismatch"
    print(
        "[address] decode/encode OK; alg_id=0x%02x digest=%s"
        % (rec.alg_id, rec.digest.hex()[:16])
    )
