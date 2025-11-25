"""
Animica | proofs.cbor

Canonical CBOR encode/decode helpers + light schema checks for proof envelopes
and bodies that mirror spec/*.cddl. This module is deliberately strict about:

- Deterministic (canonical) map ordering for signing/merkle hashing.
- Minimal, fast structural validation before calling the heavy verifiers.

It does **not** aim to be a full CDDL engine; it enforces the stable surface
we depend on for hashing and tx/header binding. For full conformance tests,
see spec/test_vectors and proofs/tests.

External deps
-------------
We prefer cbor2 (pure-Python w/ C accelerator) if present, otherwise try the
older 'cbor' package. If neither is available, we raise a clear ImportError.

Canonical ordering
------------------
CBOR canonical ordering (RFC 7049 §3.9 / RFC 8949 §4.2.1) sorts map keys by
their encoded bytewise representation. Because our proof maps use **ASCII
text keys only**, we can implement a correct specialization:

  sort by (len(utf8(key)), utf8(key))

which matches the canonical CBOR rule for text-keys.

Public API
----------
- dumps_canonical(obj: Any) -> bytes
- loads(data: bytes) -> Any

- encode_envelope(env: ProofEnvelope) -> bytes
- decode_envelope(data: bytes) -> ProofEnvelope

- validate_envelope_dict(d: dict) -> None
- validate_body(pt: ProofType, body: dict) -> None

If you change field names or add required keys, update _BODY_RULES below and
regenerate vectors.

"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, is_dataclass
from io import BytesIO
from typing import Any, Dict, Iterable, Optional, Tuple, Union

# Local types/errors
from .errors import SchemaError, ProofError
from .types import ProofEnvelope, ProofType

# Try CBOR backends
_CBOR_BACKEND = None  # "cbor2" | "cbor"
try:
    import cbor2  # type: ignore

    _CBOR_BACKEND = "cbor2"
except Exception:
    try:
        import cbor as _cbor_legacy  # type: ignore

        _CBOR_BACKEND = "cbor"
    except Exception:  # pragma: no cover
        _CBOR_BACKEND = None

if _CBOR_BACKEND is None:  # pragma: no cover
    raise ImportError(
        "No CBOR backend found. Install 'cbor2' (preferred) or 'cbor'. "
        "e.g. pip install cbor2"
    )

# --------------------------------------------------------------------------------------
# Canonicalization helpers
# --------------------------------------------------------------------------------------

def _utf8(b: Union[str, bytes]) -> bytes:
    if isinstance(b, str):
        return b.encode("utf-8")
    return b

def _is_text_key(k: Any) -> bool:
    return isinstance(k, str)

def _canon_sort_items(items: Iterable[Tuple[Any, Any]]) -> Iterable[Tuple[Any, Any]]:
    """
    Sort (key, value) pairs according to CBOR canonical map ordering specialized
    to ASCII/UTF-8 text keys: by (len(utf8(key)), utf8(key)).
    """
    try:
        return sorted(items, key=lambda kv: (len(_utf8(kv[0])), _utf8(kv[0])))
    except Exception as e:
        raise SchemaError(f"map contains non-text key; keys must be str: {e}")

def _canon_obj(obj: Any) -> Any:
    """
    Recursively produce a structure where all dicts are OrderedDicts with canonical
    key ordering, lists/tuples are normalized element-wise, and dataclasses are
    converted to dicts first. This guarantees stable CBOR encoding across runs.
    """
    # dataclasses → dict
    if is_dataclass(obj):
        obj = asdict(obj)

    # dict → OrderedDict with canonical key order, validate keys are text
    if isinstance(obj, dict):
        for k in obj.keys():
            if not _is_text_key(k):
                raise SchemaError(f"non-text map key encountered (type={type(k).__name__}); keys must be str")
        canon_items = [(k, _canon_obj(v)) for k, v in _canon_sort_items(obj.items())]
        return OrderedDict(canon_items)

    # list/tuple → list with canonicalized elements
    if isinstance(obj, (list, tuple)):
        return [ _canon_obj(x) for x in obj ]

    # bytes/str/int/bool/None → as-is
    return obj

def dumps_canonical(obj: Any) -> bytes:
    """
    Canonical CBOR encoding:
    - deterministic ordering for all maps (ASCII text keys only)
    - minimal integer/byte major types are handled by backend.
    """
    canon = _canon_obj(obj)

    if _CBOR_BACKEND == "cbor2":
        # Use the lower-level encoder with canonical=True if available (cbor2>=5),
        # otherwise rely on our OrderedDict pre-sorting.
        bio = BytesIO()
        try:
            enc = cbor2.CBOREncoder(bio, canonical=True)  # type: ignore[arg-type]
        except TypeError:  # old cbor2 without canonical kwarg
            return cbor2.dumps(canon)  # type: ignore[attr-defined]
        enc.encode(canon)
        return bio.getvalue()

    # Legacy 'cbor' backend (no explicit canonical flag; OrderedDict suffices).
    return _cbor_legacy.dumps(canon)  # type: ignore[name-defined]

def loads(data: bytes) -> Any:
    if _CBOR_BACKEND == "cbor2":
        return cbor2.loads(data)  # type: ignore[attr-defined]
    return _cbor_legacy.loads(data)  # type: ignore[name-defined]

# --------------------------------------------------------------------------------------
# Surface schemas (specialized, minimal)
# --------------------------------------------------------------------------------------

# Utility tiny type predicates
def _is_uint(x: Any) -> bool:
    return isinstance(x, int) and x >= 0

def _is_bstr(x: Any, n: Optional[int] = None) -> bool:
    ok = isinstance(x, (bytes, bytearray))
    if not ok:
        return False
    if n is None:
        return True
    return len(x) == n

def _is_text(x: Any) -> bool:
    return isinstance(x, str)

def _is_array(x: Any) -> bool:
    return isinstance(x, list)

def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise SchemaError(msg)

# Per-proof body field rules (lightweight; keep in sync with verifiers)
# Keys are intentionally short & stable to minimize CBOR.
_BODY_RULES: Dict[ProofType, Dict[str, Any]] = {
    # HashShare proof (link to header + u-draw + nonce/mix). See proofs/hashshare.py
    ProofType.HASH_SHARE: {
        "required": {
            "headerHash": ("bstr", 32),
            "u": ("bstr", None),          # raw u-draw bytes (domain-separated)
            "nonce": ("uint", None),
        },
        "optional": {
            "mixSeed": ("bstr", 32),
            "target": ("bstr", None),     # packed target or micro-target (future-proof)
            "dRatio": ("uint", None),     # difficulty ratio * 1e6 (optional hint)
        },
    },
    # AI v1: TEE attestation + traps receipts + output digest. See proofs/ai.py
    ProofType.AI: {
        "required": {
            "attestation": ("bstr", None),   # COSE/JWS or vendor quote bytes (envelope)
            "traps": ("array", None),        # array of small receipts (opaque here)
            "outputDigest": ("bstr", 32),    # SHA3-256 of model output
        },
        "optional": {
            "qos": ("uint", None),           # millis or abstract units
            "redundancy": ("uint", None),    # how many replicas ran (for metrics)
        },
    },
    # Quantum v1: provider cert + trap circuit results + circuit digest. See proofs/quantum.py
    ProofType.QUANTUM: {
        "required": {
            "providerCert": ("bstr", None),  # X.509/EdDSA/PQ-hybrid bundle (opaque here)
            "trapResults": ("array", None),  # array of small (trap_id, ok?) pairs (opaque)
            "circuitDigest": ("bstr", 32),   # SHA3-256 of circuit JSON
            "shots": ("uint", None),
        },
        "optional": {
            "depth": ("uint", None),
            "width": ("uint", None),
        },
    },
    # Storage v0: heartbeat proof-of-space-time
    ProofType.STORAGE: {
        "required": {
            "sector": ("uint", None),
            "timestamp": ("uint", None),
            "ticket": ("bstr", None),        # digest for retrieval ticket (optional bonus in verifiers)
        },
        "optional": {
            "size": ("uint", None),          # bytes pledged
        },
    },
    # VDF Wesolowski
    ProofType.VDF: {
        "required": {
            "input": ("bstr", None),
            "y": ("bstr", None),
            "proof": ("bstr", None),
            "iterations": ("uint", None),
        },
        "optional": {},
    },
}

# --------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------

def _check_field(name: str, val: Any, kind: Tuple[str, Optional[int]]) -> None:
    t, n = kind
    if t == "uint":
        _require(_is_uint(val), f"field {name!r} must be unsigned int")
    elif t == "bstr":
        _require(_is_bstr(val, n), f"field {name!r} must be bytes{'' if n is None else f' of len {n}'}")
    elif t == "text":
        _require(_is_text(val), f"field {name!r} must be text")
    elif t == "array":
        _require(_is_array(val), f"field {name!r} must be array")
    else:
        raise SchemaError(f"unknown rule kind {t!r} for field {name!r}")

def validate_body(pt: ProofType, body: Dict[str, Any]) -> None:
    """
    Minimal structural validation for a proof body map. Raises SchemaError on failure.
    """
    if not isinstance(body, dict):
        raise SchemaError("proof body must be a map")
    rules = _BODY_RULES.get(pt)
    if not rules:
        raise SchemaError(f"no schema rules registered for proof type {int(pt)}")
    required: Dict[str, Tuple[str, Optional[int]]] = rules["required"]
    optional: Dict[str, Tuple[str, Optional[int]]] = rules["optional"]

    # required fields present and typed
    for k, kind in required.items():
        if k not in body:
            raise SchemaError(f"missing required field {k!r} for proof type {int(pt)}")
        _check_field(k, body[k], kind)

    # optional fields typed if present
    for k, kind in optional.items():
        if k in body:
            _check_field(k, body[k], kind)

    # Unknown keys are tolerated (forward-compatible), but must be text
    for k in body.keys():
        _require(_is_text_key(k), "non-text key in proof body not allowed")

def validate_envelope_dict(d: Dict[str, Any]) -> None:
    """
    Envelope must be a map with:
      - "type_id": uint (matches ProofType)
      - "nullifier": bytes (non-empty)
      - "body": map (validated per proof type)
    """
    if not isinstance(d, dict):
        raise SchemaError("envelope must be a map")
    for k in ("type_id", "nullifier", "body"):
        if k not in d:
            raise SchemaError(f"envelope missing required key {k!r}")

    t = d["type_id"]
    n = d["nullifier"]
    b = d["body"]

    _require(_is_uint(t), "type_id must be unsigned int")
    try:
        pt = ProofType(int(t))
    except Exception as e:
        raise SchemaError(f"unknown type_id: {t}") from e

    _require(_is_bstr(n) and len(n) > 0, "nullifier must be bytes (non-empty)")
    validate_body(pt, b)

# --------------------------------------------------------------------------------------
# Envelope encode/decode
# --------------------------------------------------------------------------------------

def encode_envelope(env: ProofEnvelope) -> bytes:
    """
    Encode a ProofEnvelope to canonical CBOR. Validates before encoding.
    """
    if not isinstance(env, ProofEnvelope):
        raise SchemaError("encode_envelope expects a ProofEnvelope")

    d = {
        "type_id": int(env.type_id),
        "nullifier": bytes(env.nullifier),
        "body": env.body,
    }
    validate_envelope_dict(d)
    return dumps_canonical(d)

def decode_envelope(data: bytes) -> ProofEnvelope:
    """
    Decode CBOR bytes into a ProofEnvelope, then run schema checks.
    """
    try:
        obj = loads(data)
    except Exception as e:
        raise ProofError(f"invalid CBOR envelope: {e}") from e

    if not isinstance(obj, dict):
        raise SchemaError("CBOR did not decode to a map for envelope")

    validate_envelope_dict(obj)

    return ProofEnvelope(
        type_id=ProofType(int(obj["type_id"])),
        nullifier=bytes(obj["nullifier"]),
        body=obj["body"],
    )

# --------------------------------------------------------------------------------------
# Debug/introspection helpers
# --------------------------------------------------------------------------------------

def canonical_hex(obj: Any) -> str:
    """Convenience: canonical CBOR hex string of an object (for tests/tools)."""
    return encode_bytes_hex(dumps_canonical(obj))

def encode_bytes_hex(b: bytes) -> str:
    return "0x" + b.hex()

__all__ = [
    "dumps_canonical",
    "loads",
    "encode_envelope",
    "decode_envelope",
    "validate_envelope_dict",
    "validate_body",
    "canonical_hex",
    "encode_bytes_hex",
]
