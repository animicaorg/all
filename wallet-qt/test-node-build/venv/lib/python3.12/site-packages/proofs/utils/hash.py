"""
Animica | proofs.utils.hash

Thin wrappers around core.utils.hash adding:
- Strong domain separation helpers (canonical "Animica|<name>" tags)
- Length-prefix concatenation to avoid ambiguity in multi-part hashing
- Tagged SHA3-256/512 and optional BLAKE3-256 helpers
- Small utilities: hex encoding, checksum32, Merkle-ish pair hashing

These helpers are used by proofs/* to compute nullifiers, receipts,
and other consensus-critical digests with explicit domains.

Design rules
- Never concatenate raw variable-length fields without length-prefix.
- Always domain-separate bytes with a canonical ASCII tag.
- Prefer SHA3-256 unless a larger digest or specific algorithm is required.
"""

from __future__ import annotations

from typing import Iterable, Optional, Tuple, Union

# Try to use the canonical core hashing primitives when available.
try:
    from core.utils.hash import sha3_256 as _sha3_256
    from core.utils.hash import sha3_512 as _sha3_512

    # core.utils.hash may or may not expose blake3; we feature-detect below.
    try:
        from core.utils.hash import blake3_256 as _blake3_256  # type: ignore

        _HAS_BLAKE3 = True
    except Exception:
        _blake3_256 = None  # type: ignore
        _HAS_BLAKE3 = False
except Exception:  # very early bootstraps/tests
    import hashlib

    def _sha3_256(data: bytes) -> bytes:
        return hashlib.sha3_256(data).digest()

    def _sha3_512(data: bytes) -> bytes:
        return hashlib.sha3_512(data).digest()

    _blake3_256 = None  # type: ignore
    _HAS_BLAKE3 = False

# Optional local BLAKE3 (if core didn't provide, try python-blake3)
if not _HAS_BLAKE3:
    try:
        import blake3  # type: ignore

        def _blake3_256(data: bytes) -> bytes:  # type: ignore
            return blake3.blake3(data).digest(length=32)

        _HAS_BLAKE3 = True
    except Exception:
        _blake3_256 = None  # type: ignore
        _HAS_BLAKE3 = False


# ------------------------------------------------------------------------------
# Domain separation
# ------------------------------------------------------------------------------

_ANIMICA_PREFIX = b"Animica|"

# Commonly used domain names (mirrors spec/domains.yaml for convenience).
DOM_PROOF_NULLIFIER = "proof:nullifier"
DOM_PROOF_HASHSHARE = "proof:hashshare"
DOM_PROOF_AI = "proof:ai"
DOM_PROOF_QUANTUM = "proof:quantum"
DOM_PROOF_STORAGE = "proof:storage"
DOM_PROOF_VDF = "proof:vdf"
DOM_PROOF_RECEIPT = "proof:receipt"
DOM_POLICY_ALG = "policy:alg"
DOM_HEADER_BIND = "header:bind"
DOM_ENVELOPE = "envelope"
DOM_SIGNBYTES_TX = "signbytes:tx"
DOM_SIGNBYTES_HEADER = "signbytes:header"


def domain_tag(name: str) -> bytes:
    """
    Return the canonical domain tag bytes for a given ASCII name.
    Example: "proof:ai" -> b"Animica|proof:ai"
    """
    try:
        name_bytes = name.encode("ascii")
    except UnicodeEncodeError as e:
        raise ValueError("domain name must be ASCII") from e
    return _ANIMICA_PREFIX + name_bytes


# ------------------------------------------------------------------------------
# Safe concatenation
# ------------------------------------------------------------------------------


def _to_bytes(x: Union[bytes, bytearray, memoryview, str, int]) -> bytes:
    """
    Normalize various input types into bytes:
    - bytes/bytearray/memoryview: copied to bytes
    - str: if startswith '0x' → hex, else UTF-8
    - int: big-endian unsigned, minimal length (0 => b'')
    """
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    if isinstance(x, str):
        if x.startswith("0x") or x.startswith("0X"):
            hx = x[2:]
            if len(hx) % 2 == 1:
                hx = "0" + hx
            return bytes.fromhex(hx)
        return x.encode("utf-8")
    if isinstance(x, int):
        if x < 0:
            raise ValueError("negative int not supported in _to_bytes")
        if x == 0:
            return b""
        length = (x.bit_length() + 7) // 8
        return x.to_bytes(length, "big")
    raise TypeError(f"unsupported type for _to_bytes: {type(x)!r}")


def _lp(part: Union[bytes, bytearray, memoryview, str, int]) -> bytes:
    """
    Length-prefix a single part with u64-be big-endian length.
    """
    b = _to_bytes(part)
    return len(b).to_bytes(8, "big") + b


def concat_lp(parts: Iterable[Union[bytes, bytearray, memoryview, str, int]]) -> bytes:
    """
    Concatenate a sequence of parts, each length-prefixed with a u64-be.
    """
    out = bytearray()
    for p in parts:
        out += _lp(p)
    return bytes(out)


def tag_bytes(
    tag: Union[str, bytes], *parts: Union[bytes, bytearray, memoryview, str, int]
) -> bytes:
    """
    Build a domain-separated, length-prefixed byte string:
        data = domain_tag(tag) || 0x00 || LP(part1) || LP(part2) || ...
    `tag` may be a domain-name str or raw bytes (already prefixed).
    """
    if isinstance(tag, str):
        t = domain_tag(tag)
    elif isinstance(tag, (bytes, bytearray, memoryview)):
        t = bytes(tag)
    else:
        raise TypeError("tag must be str or bytes")
    return t + b"\x00" + concat_lp(parts)


# ------------------------------------------------------------------------------
# Hash helpers (tagged)
# ------------------------------------------------------------------------------


def sha3_256(data: Union[bytes, bytearray, memoryview, str, int]) -> bytes:
    return _sha3_256(_to_bytes(data))


def sha3_512(data: Union[bytes, bytearray, memoryview, str, int]) -> bytes:
    return _sha3_512(_to_bytes(data))


def blake3_256(data: Union[bytes, bytearray, memoryview, str, int]) -> bytes:
    if _blake3_256 is None:
        # We intentionally do not raise—fall back to SHA3-256 for dev/test.
        # Callers that rely on BLAKE3 specificity can check has_blake3().
        return _sha3_256(_to_bytes(data))
    return _blake3_256(_to_bytes(data))


def has_blake3() -> bool:
    return _HAS_BLAKE3


def sha3_256_tag(
    tag: Union[str, bytes], *parts: Union[bytes, bytearray, memoryview, str, int]
) -> bytes:
    return _sha3_256(tag_bytes(tag, *parts))


def sha3_512_tag(
    tag: Union[str, bytes], *parts: Union[bytes, bytearray, memoryview, str, int]
) -> bytes:
    return _sha3_512(tag_bytes(tag, *parts))


def blake3_256_tag(
    tag: Union[str, bytes], *parts: Union[bytes, bytearray, memoryview, str, int]
) -> bytes:
    data = tag_bytes(tag, *parts)
    if _blake3_256 is None:
        return _sha3_256(data)
    return _blake3_256(data)


# ------------------------------------------------------------------------------
# Small utilities
# ------------------------------------------------------------------------------


def to_hex(data: Union[bytes, bytearray, memoryview], prefix: str = "0x") -> str:
    return prefix + bytes(data).hex()


def checksum32(data: Union[bytes, bytearray, memoryview, str, int]) -> bytes:
    """
    4-byte checksum = first 4 bytes of sha3_256(data). Useful for short ids.
    """
    return sha3_256(data)[:4]


def merkle_pair(
    tag_name: str, left: Union[bytes, str], right: Union[bytes, str]
) -> bytes:
    """
    Simple pair hash used in compact receipt leaves:
        H = SHA3-256( Tag || 0x00 || LP(left) || LP(right) )
    The `tag_name` should indicate the tree (e.g., "receipt:pair").
    """
    return sha3_256_tag(tag_name, left, right)


# ------------------------------------------------------------------------------
# High-level patterns used by proofs/* modules
# ------------------------------------------------------------------------------


def nullifier_from_body(body_cbor: bytes, subdomain: str) -> bytes:
    """
    Compute a nullifier for a proof body with a specific subdomain:
        N = sha3_256_tag("proof:nullifier", subdomain, body_cbor)
    `subdomain` is typically one of: "hashshare", "ai", "quantum", "storage", "vdf".
    """
    if not isinstance(body_cbor, (bytes, bytearray, memoryview)):
        raise TypeError("body_cbor must be bytes-like")
    sd = subdomain.encode("ascii")
    return sha3_256_tag(DOM_PROOF_NULLIFIER, sd, bytes(body_cbor))


def receipt_leaf_hash(kind: str, payload: bytes) -> bytes:
    """
    Hash material destined for the proofsRoot / receipts tree leaves:
        H = sha3_256_tag("proof:receipt", kind, payload)
    """
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise TypeError("payload must be bytes-like")
    return sha3_256_tag(DOM_PROOF_RECEIPT, kind.encode("ascii"), bytes(payload))


# ------------------------------------------------------------------------------
# Self-test (very light)
# ------------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    # Basic sanity checks
    assert domain_tag("x") == b"Animica|x"
    a = sha3_256_tag("test:domain", b"hello")
    b = sha3_256_tag("test:domain", b"hello")
    c = sha3_256_tag("test:domain", b"hello", b"")  # different due to LP
    assert a == b and a != c

    # Nullifier determinism
    n1 = nullifier_from_body(b"\xa1\x01\x02", "ai")
    n2 = nullifier_from_body(b"\xa1\x01\x02", "ai")
    assert n1 == n2

    # Blake3 presence won't fail; just prints status.
    print("has_blake3:", has_blake3())
    print("ok:", to_hex(a)[:18], to_hex(n1)[:18])
