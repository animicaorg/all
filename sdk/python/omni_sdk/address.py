"""
omni_sdk.address
================

Address derivation and validation utilities for Animica.

Format
------
Addresses are Bech32m-encoded with HRP (default "anim"). The data payload is:

    payload = uvarint(alg_id) || sha3_256(pubkey)

Where:
- `alg_id` is the canonical integer algorithm id from the PQ registry
  (e.g., Dilithium3, SPHINCS+).
- `sha3_256(pubkey)` is the 32-byte hash of the raw public key bytes.

This module provides:
- from_pubkey(pubkey, alg_id, hrp="anim") -> str
- encode(payload_bytes, hrp="anim") -> str
- decode(address) -> (hrp, payload_bytes)
- parse(address) -> {"hrp", "alg_id", "pubkey_hash", "payload"}
- validate(address, expected_hrp=None) -> bool
- is_valid(address, expected_hrp=None) -> bool (alias)
- derive(...) -> str (alias for from_pubkey)

Dependencies
------------
We prefer the SDK's own bech32 and bytes helpers, falling back to the `pq` package
implementations if necessary. As a last resort we raise a clear error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

DEFAULT_HRP = "anim"

__all__ = [
    "DEFAULT_HRP",
    "from_pubkey",
    "derive",
    "encode",
    "decode",
    "parse",
    "validate",
    "is_valid",
    "AddressError",
]

# ---- Errors -----------------------------------------------------------------


class AddressError(ValueError):
    """Raised for malformed or invalid addresses/payloads."""


# ---- Hash helpers ------------------------------------------------------------


def _sha3_256(data: bytes) -> bytes:
    try:
        # Prefer SDK hash wrapper if available (consistent across codebase)
        from omni_sdk.utils.hash import \
            sha3_256 as _sdk_sha3_256  # type: ignore

        return _sdk_sha3_256(data)
    except Exception:
        import hashlib

        if not hasattr(hashlib, "sha3_256"):
            raise RuntimeError(
                "Python hashlib lacks sha3_256; please install pysha3 or use Python 3.8+."
            )
        return hashlib.sha3_256(data).digest()


# ---- UVarint helpers ---------------------------------------------------------


def _uvarint_encode(n: int) -> bytes:
    try:
        from omni_sdk.utils.bytes import \
            uvarint_encode as _sdk_uvarint_encode  # type: ignore

        return _sdk_uvarint_encode(n)
    except Exception:
        # Minimal local encoding (LEB128-like, 7 bits per byte)
        if n < 0:
            raise ValueError("uvarint cannot encode negative values")
        out = bytearray()
        while True:
            to_write = n & 0x7F
            n >>= 7
            if n:
                out.append(0x80 | to_write)
            else:
                out.append(to_write)
                break
        return bytes(out)


def _uvarint_decode(buf: bytes) -> Tuple[int, int]:
    """
    Decode uvarint from the beginning of buf.

    Returns (value, bytes_consumed).
    """
    try:
        from omni_sdk.utils.bytes import \
            uvarint_decode as _sdk_uvarint_decode  # type: ignore

        val, used = _sdk_uvarint_decode(buf)
        return int(val), int(used)
    except Exception:
        x = 0
        s = 0
        for i, b in enumerate(buf):
            x |= (b & 0x7F) << s
            if (b & 0x80) == 0:
                return x, i + 1
            s += 7
            if s > 63:
                raise AddressError("uvarint too large")
        raise AddressError("buffer ended before completing uvarint decode")


# ---- Bech32m helpers ---------------------------------------------------------


def _convertbits(data: bytes, from_bits: int, to_bits: int, pad: bool) -> bytes:
    """
    Convert a bytearray between bit group sizes. Used for Bech32 8<->5 conversion.
    """
    acc = 0
    bits = 0
    ret = bytearray()
    maxv = (1 << to_bits) - 1
    max_acc = (1 << (from_bits + to_bits - 1)) - 1
    for b in data:
        if b < 0 or (b >> from_bits):
            raise AddressError("invalid bit group source")
        acc = ((acc << from_bits) | b) & max_acc
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (to_bits - bits)) & maxv)
    elif bits >= from_bits or ((acc << (to_bits - bits)) & maxv):
        raise AddressError("non-zero padding")
    return bytes(ret)


def _get_bech32_impl():
    """
    Detect available bech32 helpers from SDK or PQ package, returning encode/decode callables.
    The callables operate on (hrp: str, data5: bytes) using Bech32m variant.
    """
    # SDK variant
    try:
        from omni_sdk.utils import bech32 as b32  # type: ignore

        # Common shapes to try:
        if (
            hasattr(b32, "Encoding")
            and hasattr(b32, "bech32_encode")
            and hasattr(b32, "bech32_decode")
        ):
            Encoding = b32.Encoding  # enum-like (must contain BECH32M)

            def enc(hrp: str, data5: bytes) -> str:
                return b32.bech32_encode(hrp, data5, Encoding.BECH32M)  # type: ignore[attr-defined]

            def dec(addr: str) -> Tuple[str, bytes]:
                hrp, data5, spec = b32.bech32_decode(addr)
                if (
                    getattr(Encoding, "BECH32M", None) is not None
                    and spec != Encoding.BECH32M
                ):
                    raise AddressError("address is not Bech32m")
                return hrp, bytes(data5 or ())

            return enc, dec
        # Some libs expose encode_bech32m/decode_bech32m
        if hasattr(b32, "encode_bech32m") and hasattr(b32, "decode_bech32m"):
            return b32.encode_bech32m, b32.decode_bech32m  # type: ignore[attr-defined]
    except Exception:
        pass

    # PQ variant
    try:
        from pq.py.utils import bech32 as b32  # type: ignore

        if (
            hasattr(b32, "Encoding")
            and hasattr(b32, "bech32_encode")
            and hasattr(b32, "bech32_decode")
        ):
            Encoding = b32.Encoding

            def enc(hrp: str, data5: bytes) -> str:
                return b32.bech32_encode(hrp, data5, Encoding.BECH32M)  # type: ignore[attr-defined]

            def dec(addr: str) -> Tuple[str, bytes]:
                hrp, data5, spec = b32.bech32_decode(addr)
                if (
                    getattr(Encoding, "BECH32M", None) is not None
                    and spec != Encoding.BECH32M
                ):
                    raise AddressError("address is not Bech32m")
                return hrp, bytes(data5 or ())

            return enc, dec
        if hasattr(b32, "encode_bech32m") and hasattr(b32, "decode_bech32m"):
            return b32.encode_bech32m, b32.decode_bech32m  # type: ignore[attr-defined]
    except Exception:
        pass

    raise RuntimeError(
        "Bech32 implementation not found. Ensure omni_sdk.utils.bech32 or pq.py.utils.bech32 is available."
    )


_B32_ENCODE, _B32_DECODE = _get_bech32_impl()


# ---- Core API ----------------------------------------------------------------


def from_pubkey(public_key: bytes, *, alg_id: int, hrp: str = DEFAULT_HRP) -> str:
    """
    Derive an address from a raw public key and algorithm id.

    Parameters
    ----------
    public_key : bytes
        Raw public key for the chosen PQ algorithm.
    alg_id : int
        Canonical algorithm id from the registry.
    hrp : str
        Human-Readable Part (network prefix). Defaults to "anim".

    Returns
    -------
    str
        Bech32m-encoded address string.
    """
    if not isinstance(public_key, (bytes, bytearray)) or len(public_key) == 0:
        raise AddressError("public_key must be non-empty bytes")
    if not isinstance(alg_id, int) or alg_id < 0:
        raise AddressError("alg_id must be a non-negative integer")

    pub_hash = _sha3_256(bytes(public_key))
    payload = _uvarint_encode(int(alg_id)) + pub_hash
    return encode(payload, hrp=hrp)


# Alias commonly used name
derive = from_pubkey


def encode(payload: bytes, *, hrp: str = DEFAULT_HRP) -> str:
    """
    Encode a binary payload to a Bech32m address with the given HRP.
    """
    if not isinstance(payload, (bytes, bytearray)) or len(payload) < 33:
        raise AddressError(
            "payload must be bytes of length >= 33 (uvarint(alg_id) + 32-byte hash)"
        )
    data5 = _convertbits(bytes(payload), 8, 5, True)
    return _B32_ENCODE(hrp, data5)


def decode(address: str) -> Tuple[str, bytes]:
    """
    Decode a Bech32m address into (hrp, payload_bytes).
    """
    if not isinstance(address, str) or not address:
        raise AddressError("address must be a non-empty string")
    hrp, data5 = _B32_DECODE(address)
    if not hrp:
        raise AddressError("invalid Bech32m hrp")
    payload = _convertbits(bytes(data5), 5, 8, False)
    if len(payload) < 33:
        raise AddressError("address payload too short")
    return hrp, payload


def parse(address: str) -> Dict[str, object]:
    """
    Parse an address into its components.

    Returns
    -------
    dict with keys:
      - hrp: str
      - alg_id: int
      - pubkey_hash: bytes (32 bytes)
      - payload: bytes (full payload)
    """
    hrp, payload = decode(address)
    alg_id, used = _uvarint_decode(payload)
    rest = payload[used:]
    if len(rest) != 32:
        raise AddressError("address payload has invalid pubkey hash length")
    return {
        "hrp": hrp,
        "alg_id": int(alg_id),
        "pubkey_hash": bytes(rest),
        "payload": bytes(payload),
    }


def validate(address: str, *, expected_hrp: Optional[str] = None) -> bool:
    """
    Validate an address format and, if provided, its HRP.

    Returns True if valid; False otherwise.
    """
    try:
        parts = parse(address)
        if expected_hrp is not None and parts["hrp"] != expected_hrp:
            return False
        return True
    except Exception:
        return False


# Friendly alias
is_valid = validate


# ---- Minimal CLI self-check --------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    # Quick smoke-test using deterministic bytes (NOT cryptographic keys).
    fake_pk = b"\x11" * 48
    for alg_id in (1, 2, 5, 42):
        addr = from_pubkey(fake_pk, alg_id=alg_id)
        ok = validate(addr, expected_hrp=DEFAULT_HRP)
        parsed = parse(addr)
        print(addr, ok, parsed["alg_id"], parsed["hrp"], len(parsed["pubkey_hash"]))
