"""
core.utils.address — Canonical address-to-bytes conversion for StateDB keys

This module provides a unified way to convert Animica address strings into
canonical byte representations suitable for use as StateDB account keys.

Address Formats
---------------
1. Bech32m addresses (e.g., 'anim1…'): Decoded to payload bytes (alg_id || digest)
2. System addresses (e.g., 'system:treasury'): UTF-8 encoded
3. Hex addresses (e.g., '0x…'): Raw hex bytes

The canonical encoding ensures that:
- Genesis loader and RPC queries use the same key representation
- State lookups succeed regardless of entry point
- Address format is normalized consistently across the codebase
"""

from __future__ import annotations

from typing import Optional


class AddressError(ValueError):
    """Raised when an address cannot be parsed or validated."""


def address_to_bytes(addr: str, *, allow_system: bool = True) -> bytes:
    """
    Convert an address string to canonical bytes for StateDB keys.

    Args:
        addr: Address string (bech32m 'anim1…', system 'system:…', or hex '0x…')
        allow_system: Whether to allow system:* addresses (default True)

    Returns:
        Canonical byte representation suitable for StateDB keys

    Raises:
        AddressError: If address format is invalid or not supported

    Examples:
        >>> # Bech32m address → payload bytes (alg_id || sha3_256(pubkey))
        >>> address_to_bytes("anim1...")  # 34 bytes
        
        >>> # System address → UTF-8 encoded
        >>> address_to_bytes("system:treasury")  # b'system:treasury'
        
        >>> # Hex address → raw bytes
        >>> address_to_bytes("0x1234...")  # bytes.fromhex("1234...")
    """
    if not isinstance(addr, str) or not addr:
        raise AddressError("address must be a non-empty string")

    addr = addr.strip()

    # 1. System addresses: UTF-8 encoding (for backward compatibility with existing genesis)
    if addr.startswith("system:"):
        if not allow_system:
            raise AddressError("system addresses not allowed in this context")
        # Normalize to lowercase for consistency
        return addr.lower().encode("utf-8")

    # 2. Bech32m addresses: decode to payload
    if addr.lower().startswith("anim"):
        try:
            # Import here to avoid circular dependencies during early bootstrap
            from pq.py.address import decode_address

            rec = decode_address(addr)
            # Return the full payload: alg_id (2 bytes) || digest (32 bytes)
            digest_bytes = bytes(rec.digest) if not isinstance(rec.digest, bytes) else rec.digest
            return rec.alg_id.to_bytes(2, "big") + digest_bytes
        except Exception as e:
            raise AddressError(f"failed to decode bech32m address: {e}") from e

    # 3. Hex addresses: raw bytes
    if addr.startswith("0x") or _is_hex(addr):
        hex_str = addr[2:] if addr.startswith("0x") else addr
        try:
            return bytes.fromhex(hex_str)
        except Exception as e:
            raise AddressError(f"invalid hex address: {e}") from e

    # Unknown format
    raise AddressError(
        f"address format not recognized: {addr!r} "
        "(expected bech32m 'anim1…', system 'system:…', or hex '0x…')"
    )


def _is_hex(s: str) -> bool:
    """Check if a string is valid hex (without 0x prefix)."""
    if not s:
        return False
    try:
        int(s, 16)
        return True
    except ValueError:
        return False


def normalize_address(addr: str) -> str:
    """
    Normalize an address string to canonical form (lowercase).

    This is useful for deduplication and comparison, but does NOT
    perform bech32m checksum validation.

    Args:
        addr: Address string

    Returns:
        Normalized address string (lowercase)

    Examples:
        >>> normalize_address("SYSTEM:TREASURY")
        'system:treasury'
        >>> normalize_address("Anim1ABC...")
        'anim1abc...'
    """
    return addr.strip().lower()


__all__ = [
    "address_to_bytes",
    "normalize_address",
    "AddressError",
]
