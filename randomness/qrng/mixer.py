"""
randomness.qrng.mixer
=====================

Extract-then-XOR mixer for combining the beacon output with QRNG bytes,
bound to a domain-separated transcript.

Intuition
---------
If *either* input is (close to) uniform and independent of the other, then
XOR of strong extractors yields an output that's (close to) uniform. We
derive per-source extractor outputs using SHA3-256, *bind them to a transcript*
to prevent cross-context reuse, XOR them, and then compress/bind again.

API
---
- mix_seed(beacon, qrng_bytes, *, context=None) -> bytes
    Returns a 32-byte mixed seed.

- mix(beacon, qrng_bytes, *, out_len=32, context=None) -> (bytes, MixReport)
    Returns `out_len` mixed bytes and a report with debug metadata.

Notes
-----
- This module is **non-consensus**. It provides operational mixing utilities.
- The transcript should carry enough context to prevent replay across rounds,
  heights, chains, or consumers. See `build_transcript(...)`.
- For deterministic tests, pass a stable `context`.

Security caveats
----------------
- Do not assume independence without analysis. Attested QRNGs can help, but
  correctness of mixing relies on at least one high-entropy, adversary-unknown
  source at the time of use.
- If `qrng_bytes` is empty, mixing degenerates to beacon-only extraction.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
from typing import Any, Mapping, Optional, Tuple, Union

try:
    # Prefer project domain-separated SHA3 helpers if available.
    from randomness.utils.hash import sha3_256 as _sha3_256  # type: ignore
except Exception:  # pragma: no cover - fallback for isolated usage
    def _sha3_256(data: bytes) -> bytes:
        return hashlib.sha3_256(data).digest()

# Try to import BeaconOut type for convenience; remain duck-typed if absent.
try:  # pragma: no cover - import convenience
    from randomness.types.core import BeaconOut  # type: ignore
except Exception:  # pragma: no cover
    class BeaconOut:  # type: ignore
        pass


# ------------------------------- Constants ------------------------------------

_MIX_DOMAIN = b"animica/qrng/mix/v1"
_EXTRACT_LABEL_BEACON = b"extract|beacon"
_EXTRACT_LABEL_QRNG = b"extract|qrng"
_FINAL_LABEL = b"final|sha3-256"


# ------------------------------- Data types -----------------------------------

@dataclasses.dataclass(frozen=True)
class MixReport:
    """
    Diagnostic information about a mix operation (non-security-critical).
    """
    out_len: int
    method: str
    transcript_sha3_256: str
    beacon_sha256: str
    qrng_sha256: str
    beacon_preview_b64: str
    qrng_preview_b64: str
    degenerate_beacon: bool
    degenerate_qrng: bool
    context_keys: Tuple[str, ...]


# ------------------------------- Utilities ------------------------------------

def _ensure_bytes_beacon(beacon: Union[bytes, "BeaconOut"]) -> bytes:
    """
    Accept either raw bytes or a BeaconOut-like object with an `output` attribute.
    """
    if isinstance(beacon, (bytes, bytearray, memoryview)):
        return bytes(beacon)
    # Try common attribute names
    for attr in ("output", "out", "bytes", "value"):
        if hasattr(beacon, attr):
            v = getattr(beacon, attr)
            if isinstance(v, (bytes, bytearray, memoryview)):
                return bytes(v)
    raise TypeError("beacon must be bytes or an object with a .output bytes attribute")


def _expand_sha3_256(seed: bytes, out_len: int) -> bytes:
    """
    Deterministic expander using SHA3-256 in counter mode (no external deps).
    Suitable for deriving up to a few kilobytes of keying material.
    """
    blocks = bytearray()
    ctr = 1
    while len(blocks) < out_len:
        blocks.extend(_sha3_256(seed + ctr.to_bytes(4, "big")))
        ctr += 1
    return bytes(blocks[:out_len])


def build_transcript(
    *,
    beacon_bytes: bytes,
    qrng_bytes: bytes,
    context: Optional[Mapping[str, Any]] = None,
) -> bytes:
    """
    Build a domain-separated transcript binding the mix to its usage context.

    Context suggestions (keys are optional):
      - "round_id": int or str
      - "height": int
      - "chain_id": int or str
      - "consumer": str   (who is asking, e.g., "vm_py.random", "miner.selector")
      - "purpose": str    (what for, e.g., "selection", "salt")
      - "label": str      (free-form)
    """
    ctx = context or {}
    parts = [ _MIX_DOMAIN, b"|v1" ]
    # Stable key ordering
    for k in sorted(ctx.keys()):
        v = ctx[k]
        # Serialize conservatively
        if isinstance(v, (bytes, bytearray, memoryview)):
            v_bytes = bytes(v)
        else:
            v_bytes = str(v).encode("utf-8", errors="replace")
        parts.extend([b"|", k.encode("utf-8"), b"=", v_bytes])
    # Include stable digests of the inputs (not raw inputs) to avoid large transcripts
    parts.extend([
        b"|beacon.sha256=", hashlib.sha256(beacon_bytes).digest(),
        b"|qrng.sha256=", hashlib.sha256(qrng_bytes).digest(),
    ])
    return _sha3_256(b"".join(parts))


def _extract(label: bytes, src: bytes, transcript: bytes, out_len: int) -> bytes:
    """
    Simple extractor: PRK = SHA3-256(domain || label || src || transcript)
    EXPAND(PRK, out_len) with counter-mode SHA3-256.
    """
    prk = _sha3_256(b"|".join((_MIX_DOMAIN, label, src, transcript)))
    return _expand_sha3_256(prk, out_len)


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


# --------------------------------- API ----------------------------------------

def mix(
    beacon: Union[bytes, "BeaconOut"],
    qrng_bytes: bytes,
    *,
    out_len: int = 32,
    context: Optional[Mapping[str, Any]] = None,
) -> Tuple[bytes, MixReport]:
    """
    Mix beacon output with QRNG bytes using extract-then-XOR.

    Steps:
      1) t = build_transcript(beacon, qrng, context)
      2) e_b = Extract(beacon, t)
      3) e_q = Extract(qrng, t)
      4) m  = e_b XOR e_q
      5) out = SHA3-256("final" || m || t) truncated/expanded to `out_len`

    Returns:
      (mixed_bytes, MixReport)
    """
    if out_len <= 0:
        raise ValueError("out_len must be > 0")

    b_bytes = _ensure_bytes_beacon(beacon)
    q_bytes = bytes(qrng_bytes or b"")

    t = build_transcript(beacon_bytes=b_bytes, qrng_bytes=q_bytes, context=context)

    e_b = _extract(_EXTRACT_LABEL_BEACON, b_bytes, t, out_len)
    e_q = _extract(_EXTRACT_LABEL_QRNG, q_bytes, t, out_len)

    mixed = _xor(e_b, e_q)
    # Bind and compress again; if out_len > 32, expand deterministically.
    final_seed = _sha3_256(b"|".join((_MIX_DOMAIN, _FINAL_LABEL, mixed, t)))
    out = final_seed if out_len <= 32 else _expand_sha3_256(final_seed, out_len)

    report = MixReport(
        out_len=out_len,
        method="extract-then-xor/sha3-256",
        transcript_sha3_256=t.hex(),
        beacon_sha256=hashlib.sha256(b_bytes).hexdigest(),
        qrng_sha256=hashlib.sha256(q_bytes).hexdigest(),
        beacon_preview_b64=base64.b64encode(b_bytes[:32]).decode("ascii"),
        qrng_preview_b64=base64.b64encode(q_bytes[:32]).decode("ascii"),
        degenerate_beacon=(len(b_bytes) == 0),
        degenerate_qrng=(len(q_bytes) == 0),
        context_keys=tuple(sorted((context or {}).keys())),
    )
    return out, report


def mix_seed(
    beacon: Union[bytes, "BeaconOut"],
    qrng_bytes: bytes,
    *,
    context: Optional[Mapping[str, Any]] = None,
) -> bytes:
    """
    Convenience wrapper returning a 32-byte seed.
    """
    out, _ = mix(beacon, qrng_bytes, out_len=32, context=context)
    return out


__all__ = [
    "MixReport",
    "build_transcript",
    "mix",
    "mix_seed",
]
