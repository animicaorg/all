"""
omni_sdk.proofs.hashshare
=========================

Developer helpers to *locally* build and sanity-verify HashShare-style proofs.
These are **non-consensus** tools intended for devnets, demos, and diagnostics.
Full nodes (the `proofs/` module in the node repo) remain the source of truth.

What this module does
---------------------
- Derives a share hash from a header hash + nonce (+ optional mixSeed) with a
  clear domain tag.
- Computes a uniform-in-[0,1) "share ratio" u from the share hash:
      u = int(shareHash) / 2**256
  and exposes convenience metrics (u, H(u) = -ln(u)).
- Produces an *envelope-like* dict with `type_id`, `body`, and `nullifier`
  (shapes chosen for interop with tools; consensus shapes may differ).
- Verifies a locally-built envelope by re-deriving `shareHash` and metrics,
  and (optionally) checks against a caller-provided `target_ratio` threshold.

What this module does *not* do
------------------------------
- It does not replicate the node's exact CBOR schemas nor policy checks.
- It does not enforce Θ schedules, Γ caps, escort rules, or nullifier TTLs.

Usage
-----
    from omni_sdk.proofs.hashshare import build_hashshare, verify_hashshare

    hdr = {"hash": "0x..."}               # header hash or {"raw": "0xCBOR..."} (sha3 over raw)
    nonce = bytes.fromhex("0011223344556677")
    proof = build_hashshare(header=hdr, nonce=nonce, mix_seed=None)
    ok = verify_hashshare(proof, target_ratio=0.10)

"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple, Union

# --- Utilities ---------------------------------------------------------------

try:
    from omni_sdk.utils.bytes import from_hex as _from_hex
    from omni_sdk.utils.bytes import to_hex as _to_hex  # type: ignore
except Exception:  # pragma: no cover

    def _to_hex(b: bytes) -> str:
        return "0x" + bytes(b).hex()

    def _from_hex(s: str) -> bytes:
        s = s[2:] if isinstance(s, str) and s.startswith("0x") else s
        return bytes.fromhex(s)


try:
    from omni_sdk.utils.hash import sha3_256  # type: ignore
except Exception:  # pragma: no cover
    import hashlib as _hashlib

    def sha3_256(data: bytes) -> bytes:
        return _hashlib.sha3_256(data).digest()


# Try to use SDK's canonical CBOR; fall back to a minimal, stable encoding.
try:
    from omni_sdk.utils.cbor import dumps as cbor_dumps  # type: ignore
except Exception:  # pragma: no cover
    # Deterministic encoding fallback: key-sorted JSON-like bytes.
    import json as _json

    def cbor_dumps(obj: Any) -> bytes:
        return _json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


Json = Dict[str, Any]


class HashShareError(Exception):
    """Raised on build/verify errors."""


# Domain tags (byte strings) for clarity and collision resistance.
DOMAIN_MATERIAL = b"animica:hashshare.material.v1"
DOMAIN_NULLIFIER = b"animica:proof.nullifier.hashshare.v1"


def _norm_bytes(x: Union[str, bytes, bytearray, memoryview], *, field: str) -> bytes:
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    if isinstance(x, str):
        try:
            return _from_hex(x)
        except Exception as e:
            raise HashShareError(f"{field} must be bytes or 0x-hex") from e
    raise HashShareError(f"{field} must be bytes or 0x-hex")


def _extract_header_hash(header: Mapping[str, Any]) -> bytes:
    """
    Accepts:
      - header['hash'] or header['headerHash'] (0x-hex)
      - header['raw'] CBOR bytes (0x-hex), hashed via sha3_256(raw)
      - direct bytes via header['hash_bytes'] (escape hatch for tests)
    """
    v = header.get("hash") or header.get("headerHash")
    if isinstance(v, str) and v.startswith("0x"):
        return _from_hex(v)
    rb = header.get("raw")
    if isinstance(rb, str) and rb.startswith("0x"):
        return sha3_256(_from_hex(rb))
    hb = header.get("hash_bytes")
    if isinstance(hb, (bytes, bytearray, memoryview)):
        bb = bytes(hb)
        if len(bb) != 32:
            raise HashShareError("header.hash_bytes must be 32 bytes")
        return bb
    raise HashShareError(
        "header must include 'hash' (0x...), 'headerHash', or 'raw' (CBOR hex)"
    )


def _material(header_hash: bytes, nonce: bytes, mix_seed: Optional[bytes]) -> bytes:
    """
    Deterministic material used to derive the share hash.

    shareHash = sha3_256(DOMAIN_MATERIAL || header_hash || nonce || mix_seed?)
    """
    parts = [DOMAIN_MATERIAL, header_hash, nonce]
    if mix_seed is not None:
        parts.append(mix_seed)
    return b"".join(parts)


def _share_hash(header_hash: bytes, nonce: bytes, mix_seed: Optional[bytes]) -> bytes:
    return sha3_256(_material(header_hash, nonce, mix_seed))


def _share_value(h: bytes) -> int:
    """Interpret the 32-byte hash as a big-endian integer."""
    return int.from_bytes(h, "big", signed=False)


def _share_ratio(h: bytes) -> float:
    # Avoid returning exact 0.0 (extremely unlikely), clamp to (0,1)
    v = _share_value(h)
    denom = 1 << 256
    u = v / denom
    # clamp into (0, 1) exclusive for -ln(u)
    if u <= 0.0:
        u = 1.0 / denom
    elif u >= 1.0:
        u = (denom - 1) / denom
    return u


def _nullifier(header_hash: bytes, nonce: bytes) -> bytes:
    """
    Deterministic per-proof nullifier used by the dev envelope to prevent trivial
    replay/duplication in demos. Real chains may bind more fields.

    nullifier = sha3_256(DOMAIN_NULLIFIER || header_hash || nonce)
    """
    return sha3_256(DOMAIN_NULLIFIER + header_hash + nonce)


# --- Public shapes -----------------------------------------------------------


@dataclass(frozen=True)
class HashShareBody:
    """
    A minimal, envelope-embeddable body for a HashShare-style proof.

    Fields are 0x-hex strings for easy JSON/CBOR transport.
    """

    headerHash: str
    nonce: str
    mixSeed: Optional[str]
    shareHash: str
    # The raw "work" value and the user-friendly ratio (both redundant with shareHash).
    valueHex: str
    ratio: float


@dataclass(frozen=True)
class ProofEnvelope:
    """
    Non-consensus envelope shape for dev tools. Eases interop with node CLIs.
    """

    type_id: str  # "hashshare.v1" (string to avoid guessing numeric ids)
    body: HashShareBody
    nullifier: str  # 0x-hex


# --- Builders & Verifiers ----------------------------------------------------


def build_hashshare(
    *,
    header: Mapping[str, Any],
    nonce: Union[bytes, bytearray, memoryview, str],
    mix_seed: Optional[Union[bytes, bytearray, memoryview, str]] = None,
    type_id: str = "hashshare.v1",
) -> Dict[str, Any]:
    """
    Build a local HashShare envelope-like object.

    Parameters
    ----------
    header : Mapping
        Must include 'hash' (0x-hex) or 'raw' (0x-hex CBOR).
    nonce : bytes | 0x-hex
        Miner/searcher nonce used for the u-draw.
    mix_seed : Optional[bytes | 0x-hex]
        Optional domain personalization/mix bytes (e.g., from prior beacon).
    type_id : str
        Envelope type identifier for downstream tools; default "hashshare.v1".

    Returns
    -------
    dict with keys:
        - "type_id": str
        - "body": {headerHash, nonce, mixSeed, shareHash, valueHex, ratio}
        - "nullifier": 0x-hex
        - "metrics": {"u": float, "H_u": float}
    """
    hh = _extract_header_hash(header)
    nn = _norm_bytes(nonce, field="nonce")
    ms = _norm_bytes(mix_seed, field="mix_seed") if mix_seed is not None else None

    sh = _share_hash(hh, nn, ms)
    u = _share_ratio(sh)
    H_u = -math.log(
        u
    )  # informational; node consensus uses fixed-point µ-nats internally

    body = HashShareBody(
        headerHash=_to_hex(hh),
        nonce=_to_hex(nn),
        mixSeed=_to_hex(ms) if ms is not None else None,
        shareHash=_to_hex(sh),
        valueHex=_to_hex(sh),  # same bytes as shareHash; readable alias
        ratio=u,
    )

    nul = _nullifier(hh, nn)

    # Prepare JSON-friendly dict (avoid dataclass dependency downstream)
    out: Dict[str, Any] = {
        "type_id": type_id,
        "body": {
            "headerHash": body.headerHash,
            "nonce": body.nonce,
            "mixSeed": body.mixSeed,
            "shareHash": body.shareHash,
            "valueHex": body.valueHex,
            "ratio": body.ratio,
        },
        "nullifier": _to_hex(nul),
        "metrics": {"u": u, "H_u": H_u},
    }

    return out


def verify_hashshare(
    proof_or_envelope: Mapping[str, Any],
    *,
    target_ratio: Optional[float] = None,
    expect_type_id: Optional[str] = None,
) -> bool:
    """
    Sanity-verify a locally built HashShare envelope or body object.

    Parameters
    ----------
    proof_or_envelope : Mapping
        Either the dict returned by `build_hashshare` or a compatible object
        with "body" (and optionally "type_id", "nullifier").
    target_ratio : Optional[float]
        If provided, require u <= target_ratio (i.e., meets difficulty target).
    expect_type_id : Optional[str]
        If provided, require envelope["type_id"] == expect_type_id.

    Returns
    -------
    True if verification passes; False otherwise.
    """
    # Accept both envelope-like and body-like inputs
    if "body" in proof_or_envelope:
        body = proof_or_envelope["body"]
        type_id = proof_or_envelope.get("type_id")
        if expect_type_id is not None and type_id != expect_type_id:
            return False
    else:
        body = proof_or_envelope

    try:
        hh = _norm_bytes(body.get("headerHash"), field="body.headerHash")
        nn = _norm_bytes(body.get("nonce"), field="body.nonce")
        ms_hex = body.get("mixSeed")
        ms = _norm_bytes(ms_hex, field="body.mixSeed") if ms_hex is not None else None

        stated_sh = _norm_bytes(body.get("shareHash"), field="body.shareHash")
        recomputed = _share_hash(hh, nn, ms)
        if recomputed != stated_sh:
            return False

        # Optional: check nullifier if present
        nul_hex = (
            proof_or_envelope.get("nullifier") if "body" in proof_or_envelope else None
        )
        if isinstance(nul_hex, str) and nul_hex.startswith("0x"):
            if _nullifier(hh, nn) != _norm_bytes(nul_hex, field="nullifier"):
                return False

        u = _share_ratio(stated_sh)
        if target_ratio is not None and not (0.0 < target_ratio <= 1.0):
            return False
        if target_ratio is not None and u > float(target_ratio):
            return False

    except Exception:
        return False

    return True


# Convenience exports for callers that want the raw primitives
compute_share_value = _share_value
compute_share_ratio = _share_ratio

__all__ = [
    "HashShareError",
    "HashShareBody",
    "ProofEnvelope",
    "build_hashshare",
    "verify_hashshare",
    "compute_share_value",
    "compute_share_ratio",
]
