from __future__ import annotations

"""
Animica PQ utils — hashing helpers
==================================

Portable wrappers around common cryptographic hash functions used in tooling,
SDKs, and non-consensus utilities:

- SHA3-256 / SHA3-512  (stdlib `hashlib`)
- BLAKE3-256           (optional: `pip install blake3`; graceful fallback)
- Keccak-256           (optional: `pip install pysha3` OR `pip install pycryptodome`)
- Hex helpers          (`to_hex`, `from_hex`) with 0x-prefix handling

Notes
-----
* These helpers are **not** consensus-critical by themselves; consensus
  domains and exact encodings live in `spec/` and in the core modules.
* We avoid importing optional dependencies at module import time; instead we
  resolve them lazily when a function is first called, so environments
  missing an optional algo still import this module successfully.

"""

from typing import Iterable, Optional
import hashlib
import binascii
import logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hex helpers
# ---------------------------------------------------------------------------

def to_hex(b: bytes, prefix: str = "0x") -> str:
    """
    Convert bytes to lower-case hex string with optional prefix (default `0x`).
    """
    if not isinstance(b, (bytes, bytearray, memoryview)):
        raise TypeError("to_hex expects bytes-like input")
    return (prefix or "") + binascii.hexlify(bytes(b)).decode("ascii")


def from_hex(s: str | bytes | bytearray | memoryview) -> bytes:
    """
    Parse hex into bytes. Accepts strings with/without 0x prefix and ignores
    leading/trailing whitespace and underscores (for readability).
    """
    if isinstance(s, (bytes, bytearray, memoryview)):
        s = bytes(s).decode("ascii")
    if not isinstance(s, str):
        raise TypeError("from_hex expects str or bytes-like input")

    s = s.strip().lower().replace("_", "")
    if s.startswith("0x"):
        s = s[2:]
    if len(s) % 2:
        # Support odd-length hex by left-padding a zero nybble
        s = "0" + s
    try:
        return binascii.unhexlify(s)
    except binascii.Error as e:
        raise ValueError(f"invalid hex string: {e}") from e


# ---------------------------------------------------------------------------
# Core SHA3 (always available via hashlib in Python 3.6+)
# ---------------------------------------------------------------------------

def sha3_256(data: bytes | bytearray | memoryview) -> bytes:
    """
    SHA3-256 digest of `data`.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("sha3_256 expects bytes-like input")
    return hashlib.sha3_256(data).digest()


def sha3_512(data: bytes | bytearray | memoryview) -> bytes:
    """
    SHA3-512 digest of `data`.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("sha3_512 expects bytes-like input")
    return hashlib.sha3_512(data).digest()


# ---------------------------------------------------------------------------
# Optional BLAKE3
# ---------------------------------------------------------------------------

def _blake3_impl():
    try:
        # Lazy import to keep this module importable without blake3 installed.
        import blake3  # type: ignore
        return blake3
    except Exception:
        return None


def blake3_256(data: bytes | bytearray | memoryview) -> bytes:
    """
    BLAKE3-256 digest of `data` if the `blake3` package is available.
    If not installed, gracefully falls back to SHA3-256 and logs once.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("blake3_256 expects bytes-like input")

    impl = _blake3_impl()
    if impl is None:
        if not getattr(blake3_256, "_warned", False):
            log.warning("blake3 package not installed; blake3_256() falling back to sha3_256.")
            setattr(blake3_256, "_warned", True)
        return sha3_256(data)

    return impl.blake3(data).digest(length=32)


# ---------------------------------------------------------------------------
# Optional Keccak-256 (pre-standard SHA-3 — used in some ecosystems)
# ---------------------------------------------------------------------------

def _keccak_factory():
    """
    Try multiple providers for Keccak-256:
    - pysha3 (module name 'sha3') → keccak_256()
    - pycryptodome (Crypto.Hash.keccak) → new(digest_bits=256)
    Returns a callable `f(data: bytes) -> bytes`, or None if unavailable.
    """
    # pysha3
    try:
        import sha3  # type: ignore
        def _k(data: bytes) -> bytes:
            h = sha3.keccak_256()
            h.update(data)
            return h.digest()
        return _k
    except Exception:
        pass

    # pycryptodome
    try:
        from Crypto.Hash import keccak as _keccak  # type: ignore
        def _k2(data: bytes) -> bytes:
            h = _keccak.new(digest_bits=256)
            h.update(data)
            return h.digest()
        return _k2
    except Exception:
        pass

    return None


def keccak_256(data: bytes | bytearray | memoryview) -> bytes:
    """
    Keccak-256 digest of `data` if a provider is available.
    If no provider is available, raises NotImplementedError with guidance.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("keccak_256 expects bytes-like input")

    impl = getattr(keccak_256, "_impl", None)
    if impl is None:
        impl = _keccak_factory()
        setattr(keccak_256, "_impl", impl)

    if impl is None:
        raise NotImplementedError(
            "Keccak-256 provider not found. Install either 'pysha3' or 'pycryptodome'."
        )
    return impl(bytes(data))


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def hash_concat(parts: Iterable[bytes], algo: str = "sha3_256") -> bytes:
    """
    Hash the concatenation of `parts` using the selected `algo`.
    `algo` ∈ {'sha3_256','sha3_512','blake3_256','keccak_256'}.
    """
    try:
        if algo == "sha3_256":
            h = hashlib.sha3_256()
            for p in parts:
                if not isinstance(p, (bytes, bytearray, memoryview)):
                    raise TypeError("all parts must be bytes-like")
                h.update(p)
            return h.digest()
        elif algo == "sha3_512":
            h = hashlib.sha3_512()
            for p in parts:
                if not isinstance(p, (bytes, bytearray, memoryview)):
                    raise TypeError("all parts must be bytes-like")
                h.update(p)
            return h.digest()
        elif algo == "blake3_256":
            # Use single-shot wrapper above for simplicity.
            return blake3_256(b"".join(bytes(p) for p in parts))
        elif algo == "keccak_256":
            return keccak_256(b"".join(bytes(p) for p in parts))
        else:
            raise ValueError(f"unsupported algo: {algo}")
    except Exception:
        # Re-raise with a shorter stack retaining the original message.
        raise


def hash_tagged(tag: bytes, msg: bytes, algo: str = "sha3_256") -> bytes:
    """
    Simple domain separation helper: H( len(tag)||tag || msg ).
    **Do not** use for consensus unless the exact construction is specified in `spec/domains.yaml`.
    """
    if not isinstance(tag, (bytes, bytearray)) or not isinstance(msg, (bytes, bytearray)):
        raise TypeError("hash_tagged expects bytes for tag and msg")
    if len(tag) > 255:
        raise ValueError("tag too long; must fit in one byte length for this helper")

    prefix = bytes([len(tag)]) + bytes(tag)
    return hash_concat((prefix, bytes(msg)), algo=algo)
