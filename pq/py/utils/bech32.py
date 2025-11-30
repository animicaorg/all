from __future__ import annotations

"""
Bech32m encoder/decoder for Animica addresses (anim1…)
======================================================

This module implements BIP-0173/0350 Bech32/Bech32m primitives and a thin,
opinionated address wrapper for Animica:

- HRP (Human Readable Part): "anim"
- Encoding: **Bech32m** (constant 0x2bc830a3)
- Data: raw payload bytes, converted 8→5 bits (no version byte),
        typically: alg_id || sha3_256(pubkey)  (length validated elsewhere)

Usage
-----
    addr = encode_address(payload_bytes)              # "anim1..."
    payload = decode_address(addr)                    # original bytes
    hrp, data5, spec = bech32_decode(addr)           # low-level

Notes
-----
* We keep the primitives generic, but the `encode_address` / `decode_address`
  helpers always enforce Bech32m (per BIP-350).
* No consensus-critical logic lives here; exact payload shape is defined in
  `pq/py/address.py` and the repo `spec/domains.yaml`.
* This file is dependency-free and safe for browser bundling (via transpilers).

References
----------
BIP-0173: https://github.com/bitcoin/bips/blob/master/bip-0173.mediawiki
BIP-0350: https://github.com/bitcoin/bips/blob/master/bip-0350.mediawiki
"""

from typing import Iterable, List, Sequence, Tuple

# 32-character alphabet per BIP-0173.
CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
CHARSET_REV = {c: i for i, c in enumerate(CHARSET)}

# Bech32 constants
_BECH32_CONST = 1
_BECH32M_CONST = 0x2BC830A3


class Bech32Error(ValueError):
    pass


# ---------------------------------------------------------------------------
# Core Bech32/Bech32m primitives
# ---------------------------------------------------------------------------


def _polymod(values: Sequence[int]) -> int:
    """Internal: Compute Bech32 checksum polymod."""
    # generator coefficients
    GENERATORS = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for v in values:
        if v < 0 or v > 31:
            raise Bech32Error("polymod values must be 5-bit")
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ v
        for i in range(5):
            if (top >> i) & 1:
                chk ^= GENERATORS[i]
    return chk


def _hrp_expand(hrp: str) -> List[int]:
    """Expand HRP for checksum computation."""
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _create_checksum(hrp: str, data: Sequence[int], bech32m: bool) -> List[int]:
    const = _BECH32M_CONST if bech32m else _BECH32_CONST
    values = _hrp_expand(hrp) + list(data)
    polymod = _polymod(values + [0, 0, 0, 0, 0, 0]) ^ const
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]


def _verify_checksum(hrp: str, data: Sequence[int]) -> Tuple[bool, str]:
    """
    Verify checksum and detect spec ("bech32" or "bech32m").
    Returns (ok, spec). If ok is False, spec is "".
    """
    pm = _polymod(_hrp_expand(hrp) + list(data))
    if pm == _BECH32_CONST:
        return True, "bech32"
    if pm == _BECH32M_CONST:
        return True, "bech32m"
    return False, ""


def bech32_encode(hrp: str, data: Sequence[int], spec: str = "bech32m") -> str:
    """
    Encode HRP + 5-bit data words into a Bech32/Bech32m string.
    `spec` must be "bech32" or "bech32m".
    """
    if not hrp or any((ord(c) < 33 or ord(c) > 126) for c in hrp):
        raise Bech32Error("invalid HRP characters")
    if any(d < 0 or d > 31 for d in data):
        raise Bech32Error("data values must be 5-bit (0..31)")
    if spec not in ("bech32", "bech32m"):
        raise Bech32Error("spec must be 'bech32' or 'bech32m'")

    # Enforce lowercase output.
    hrp = hrp.lower()
    bech32m = spec == "bech32m"
    checksum = _create_checksum(hrp, data, bech32m)
    combined = list(data) + checksum
    return hrp + "1" + "".join(CHARSET[d] for d in combined)


def bech32_decode(bech: str) -> Tuple[str, List[int], str]:
    """
    Decode a Bech32/Bech32m string into (hrp, data, spec).
    Raises Bech32Error on failure.
    """
    if not bech or len(bech) < 8:
        raise Bech32Error("string too short for bech32")

    # Enforce single-case.
    if any(c.isupper() for c in bech) and any(c.islower() for c in bech):
        raise Bech32Error("mixed-case bech32 is invalid")
    bech = bech.lower()

    pos = bech.rfind("1")
    if pos < 1 or pos + 7 > len(bech):
        raise Bech32Error("invalid position of separator '1'")

    hrp = bech[:pos]
    data_part = bech[pos + 1 :]
    if any((ord(c) < 33 or ord(c) > 126) for c in hrp):
        raise Bech32Error("invalid HRP characters")

    try:
        data = [CHARSET_REV[c] for c in data_part]
    except KeyError:
        raise Bech32Error("invalid data character in bech32 string")

    ok, spec = _verify_checksum(hrp, data)
    if not ok:
        raise Bech32Error("checksum mismatch")

    # strip checksum (last 6 words)
    return hrp, data[:-6], spec


# ---------------------------------------------------------------------------
# 8↔5 bit conversion (BIP-0173 "convertbits")
# ---------------------------------------------------------------------------


def convertbits(
    data: Iterable[int], from_bits: int, to_bits: int, pad: bool = True
) -> List[int]:
    """
    General power-of-2 base conversion.
    E.g. convertbits(bytes, 8, 5) to make Bech32 data words.

    If pad=False, leftover bits must be zero (strict mode).
    """
    acc = 0
    bits = 0
    ret: List[int] = []
    maxv = (1 << to_bits) - 1
    max_acc = (1 << (from_bits + to_bits - 1)) - 1

    for value in data:
        if value < 0 or (value >> from_bits):
            raise Bech32Error("invalid value for convertbits")
        acc = ((acc << from_bits) | value) & max_acc
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            ret.append((acc >> bits) & maxv)

    if pad:
        if bits:
            ret.append((acc << (to_bits - bits)) & maxv)
    else:
        if bits >= from_bits:
            raise Bech32Error("illegal zero-padding")
        if ((acc << (to_bits - bits)) & maxv) != 0:
            raise Bech32Error("non-zero padding")

    return ret


def bytes_to_5bit(data: bytes) -> List[int]:
    return convertbits(data, 8, 5, pad=True)


def fivebit_to_bytes(words: Sequence[int]) -> bytes:
    return bytes(convertbits(words, 5, 8, pad=False))


# ---------------------------------------------------------------------------
# Animica address helpers (Bech32m, HRP "anim")
# ---------------------------------------------------------------------------

DEFAULT_HRP = "anim"


def encode_address(payload: bytes, hrp: str = DEFAULT_HRP) -> str:
    """
    Encode raw payload bytes as an Animica address using Bech32m and hrp="anim".
    Payload length is not constrained here (validated by higher layers).
    """
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise Bech32Error("payload must be bytes-like")
    data5 = bytes_to_5bit(bytes(payload))
    return bech32_encode(hrp, data5, spec="bech32m")


def decode_address(addr: str, expected_hrp: str = DEFAULT_HRP) -> bytes:
    """
    Decode an Animica bech32(m) address back to payload bytes.

    * Enforces Bech32m (BIP-350).
    * Enforces the expected HRP (default "anim").
    """
    hrp, data5, spec = bech32_decode(addr)
    if spec != "bech32m":
        raise Bech32Error("Animica addresses must use bech32m")
    if expected_hrp and hrp != expected_hrp:
        raise Bech32Error(f"unexpected HRP: {hrp} (expected {expected_hrp})")
    return fivebit_to_bytes(data5)


# ---------------------------------------------------------------------------
# Tiny self-test (optional)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Round-trip check with a dummy payload.
    example = b"\x01" + bytes.fromhex("11" * 32)  # alg_id=1 + 32 bytes
    addr = encode_address(example)
    back = decode_address(addr)
    assert back == example, "round-trip failed"
    print("OK:", addr)
