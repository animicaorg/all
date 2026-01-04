"""
core.utils.address_codec — canonical account key derivation utilities.

Provides a single source of truth for converting bech32 addresses and public keys
into canonical 32-byte account keys used by StateDB.
"""

from __future__ import annotations

from typing import Any

from core.utils.address import address_to_bytes
from core.utils.hash import sha3_256
from pq.py.address import address_from_pubkey

ADDRESS_KEY_LEN = 32


class AccountKeyError(ValueError):
    """Raised when an account key cannot be derived."""


def account_key_from_bech32(addr: str) -> bytes:
    """
    Convert a bech32 (anim1...) address string into canonical account key bytes.
    """
    key = address_to_bytes(addr)
    _validate_account_key(key)
    return key


def account_key_from_pubkey(pubkey: bytes, alg_id: int | None) -> bytes:
    """
    Derive canonical account key bytes from a public key.

    If alg_id is provided, we derive a bech32 address and then decode it to
    canonical bytes. Otherwise, we fall back to sha3_256(pubkey) (digest only).
    """
    if not isinstance(pubkey, (bytes, bytearray, memoryview)):
        raise AccountKeyError("pubkey must be bytes-like")
    pub_bytes = bytes(pubkey)
    if alg_id is None:
        key = sha3_256(pub_bytes)
        _validate_account_key(key)
        return key

    addr = address_from_pubkey(pub_bytes, int(alg_id))
    key = address_to_bytes(addr)
    _validate_account_key(key)
    return key


def account_key_from_raw(value: bytes) -> bytes:
    """
    Normalize raw bytes into canonical account key bytes.

    Accepts 20/32/34 byte inputs. 34 bytes are treated as (alg_id || digest),
    returning the 32-byte digest. 20 bytes are left-padded to 32 bytes.
    """
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise AccountKeyError("raw value must be bytes-like")
    raw = bytes(value)
    if len(raw) == ADDRESS_KEY_LEN:
        return raw
    if len(raw) == 20:
        return raw.rjust(ADDRESS_KEY_LEN, b"\x00")
    if len(raw) == ADDRESS_KEY_LEN + 2:
        key = raw[-ADDRESS_KEY_LEN:]
        _validate_account_key(key)
        return key
    raise AccountKeyError(f"unexpected account key length: {len(raw)} bytes")


def account_key_from_any(value: Any) -> bytes:
    """
    Best-effort conversion from common address representations to account keys.
    """
    if value is None:
        raise AccountKeyError("missing address value")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return account_key_from_raw(value)
    if isinstance(value, str):
        return account_key_from_bech32(value)
    raise AccountKeyError(f"unsupported address type: {type(value).__name__}")


def _validate_account_key(key: bytes) -> None:
    if len(key) != ADDRESS_KEY_LEN:
        raise AccountKeyError(
            f"canonical account key must be {ADDRESS_KEY_LEN} bytes, got {len(key)}"
        )


__all__ = [
    "ADDRESS_KEY_LEN",
    "AccountKeyError",
    "account_key_from_bech32",
    "account_key_from_pubkey",
    "account_key_from_raw",
    "account_key_from_any",
]
