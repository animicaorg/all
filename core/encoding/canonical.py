from __future__ import annotations

"""
Canonical SignBytes encoder
===========================

Deterministic, domain-separated CBOR sign-bytes for Animica consensus objects.

We *never* sign raw objects directly. Instead, we sign a tiny CBOR map with
small integer keys so ordering is fully deterministic (RFC 8949 §4.2.1):

{
  1: domain,     # tstr, e.g. "animica/tx/sign/v1"
  2: chainId,    # uint (CAIP-2 numeric id)
  3: payload     # object encoded with canonical CBOR (no floats/indefinite)
  4: extra       # optional map for future-proofing (policy roots, etc.)
}

Using small integer keys guarantees strict key order (1<2<3<4) under canonical
CBOR. Domain separation prevents cross-type signature replays.

Public helpers:
- signbytes_tx(payload: object, chain_id: int, *, extra: dict|None) -> bytes
- signbytes_header(payload: object, chain_id: int, *, extra: dict|None) -> bytes
- signbytes(domain: str, payload: object, chain_id: int, *, extra: dict|None) -> bytes
- hash_signbytes(..., digest="sha3_256") -> bytes     (digest of the above)

This module only *encodes* sign-bytes. Key management & signature logic lives
in pq/ (Dilithium3, SPHINCS+), which must be fed the output of these functions.
"""

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping, Iterable, Union, Optional, Tuple

from core.encoding.cbor import dumps as cbor_dumps, EncodeError
from core.utils.hash import sha3_256, sha3_512

# --------------------------
# Domain strings (spec-bound)
# --------------------------

# Keep these in sync with spec/domains.yaml
DOM_TX_SIGN_V1: str = "animica/tx/sign/v1"
DOM_HEADER_SIGN_V1: str = "animica/header/sign/v1"

# Reserved for future use (not used here, but listed for completeness)
DOM_TX_HASH_V1: str = "animica/tx/hash/v1"
DOM_HEADER_HASH_V1: str = "animica/header/hash/v1"

_ALLOWED_DOMAINS = {
    DOM_TX_SIGN_V1,
    DOM_HEADER_SIGN_V1,
    DOM_TX_HASH_V1,
    DOM_HEADER_HASH_V1,
}

# --------------------------
# Type guards / sanitization
# --------------------------

Primitive = Union[None, bool, int, bytes, str]
Structured = Union[Primitive, Iterable["Structured"], Mapping[Union[int, bytes, str], "Structured"]]

class CanonicalTypeError(TypeError):
    pass

def _ensure_canonical_types(x: Any, *, _depth: int = 0) -> None:
    """
    Recursively ensure the payload only contains the subset supported by our canonical CBOR:
    - None, bool, int, bytes, str
    - list/tuple of allowed
    - dict with keys in {int, bytes, str} and values allowed
    Floats/Decimal/sets and custom classes are rejected pre-encode for clear errors.
    """
    if _depth > 1_000:
        raise CanonicalTypeError("maximum nesting exceeded")
    if x is None or isinstance(x, (bool, int, bytes, str)):
        return
    if isinstance(x, (list, tuple)):
        for i in x:
            _ensure_canonical_types(i, _depth=_depth + 1)
        return
    if isinstance(x, dict):
        for k, v in x.items():
            if not isinstance(k, (int, bytes, str)):
                raise CanonicalTypeError(f"unsupported map key type: {type(k).__name__}")
            _ensure_canonical_types(v, _depth=_depth + 1)
        return
    # dataclasses are allowed via CBOR encoder (it converts to dict), but we keep the check strict here
    from dataclasses import is_dataclass, asdict  # local import to avoid hard dep
    if is_dataclass(x):
        _ensure_canonical_types(asdict(x), _depth=_depth + 1)
        return
    raise CanonicalTypeError(f"unsupported value type: {type(x).__name__}")

# --------------------------
# Encoding
# --------------------------

def signbytes(domain: str, payload: Structured, chain_id: int, *, extra: Optional[Mapping[str, Structured]] = None) -> bytes:
    """
    Encode domain-separated sign-bytes for `payload` under `domain` and `chain_id`.
    """
    if domain not in _ALLOWED_DOMAINS:
        raise ValueError(f"unknown sign domain: {domain!r}")
    if not isinstance(chain_id, int) or chain_id < 0:
        raise ValueError("chain_id must be a non-negative integer")
    _ensure_canonical_types(payload)
    if extra is not None:
        _ensure_canonical_types(dict(extra))

    # Small-integer map keys for deterministic ordering.
    body = {1: domain, 2: chain_id, 3: payload}
    if extra:
        body[4] = dict(extra)

    try:
        return cbor_dumps(body)
    except EncodeError as e:
        # Rewrap to surface as canonical type error for callers higher up the stack
        raise CanonicalTypeError(str(e)) from e

def signbytes_tx(tx_payload: Structured, chain_id: int, *, extra: Optional[Mapping[str, Structured]] = None) -> bytes:
    """
    Domain = animica/tx/sign/v1
    `tx_payload` MUST match spec/tx_format.cddl (sans signature fields).
    """
    return signbytes(DOM_TX_SIGN_V1, tx_payload, chain_id, extra=extra)

def signbytes_header(header_payload: Structured, chain_id: int, *, extra: Optional[Mapping[str, Structured]] = None) -> bytes:
    """
    Domain = animica/header/sign/v1
    `header_payload` MUST match spec/header_format.cddl (sans proposer signature(s)).
    """
    return signbytes(DOM_HEADER_SIGN_V1, header_payload, chain_id, extra=extra)


def _as_mapping(x: Any) -> Mapping[str, Any]:
    """Best-effort conversion of dataclasses/objects to a Mapping for SignBytes."""
    if isinstance(x, Mapping):
        return dict(x)
    if hasattr(x, "to_obj"):
        return x.to_obj()  # type: ignore[no-any-return]
    if is_dataclass(x):
        return asdict(x)
    raise TypeError("expected mapping or dataclass-compatible object")


def _chain_id_from(obj: Any, mapping: Mapping[str, Any]) -> int:
    if hasattr(obj, "chain_id"):
        return int(getattr(obj, "chain_id"))
    if hasattr(obj, "chainId"):
        return int(getattr(obj, "chainId"))
    if "chain_id" in mapping:
        return int(mapping["chain_id"])
    if "chainId" in mapping:
        return int(mapping["chainId"])
    raise ValueError("header/tx missing chain id for signing")


def header_signing_bytes(header: Any) -> bytes:
    """
    Convenience: produce canonical SignBytes for a Header-like object or mapping.
    """
    payload = _as_mapping(header)
    chain_id = _chain_id_from(header, payload)
    return signbytes_header(payload, chain_id)

# --------------------------
# Hash helpers
# --------------------------

def hash_signbytes(domain: str, payload: Structured, chain_id: int, *, digest: str = "sha3_256", extra: Optional[Mapping[str, Structured]] = None) -> bytes:
    """
    Convenience helper: returns the digest of the canonical sign-bytes.
    digest ∈ {"sha3_256", "sha3_512"} (expandable later).
    """
    sb = signbytes(domain, payload, chain_id, extra=extra)
    if digest == "sha3_256":
        return sha3_256(sb)
    if digest == "sha3_512":
        return sha3_512(sb)
    raise ValueError("unsupported digest (expected 'sha3_256' or 'sha3_512')")

def tx_hash(domain: str, tx_payload: Structured, chain_id: int) -> bytes:
    """
    Deterministic transaction hash (sha3_256 of sign-bytes).
    Callers typically use `tx_hash(DOM_TX_SIGN_V1, ...)`.
    """
    return hash_signbytes(domain, tx_payload, chain_id, digest="sha3_256")

def header_hash(domain: str, header_payload: Structured, chain_id: int) -> bytes:
    """
    Deterministic header hash (sha3_256 of sign-bytes).
    Callers typically use `header_hash(DOM_HEADER_SIGN_V1, ...)`.
    """
    return hash_signbytes(domain, header_payload, chain_id, digest="sha3_256")


def tx_signing_bytes(tx: Any) -> bytes:
    """
    Convenience: produce canonical SignBytes for an UnsignedTx or Tx-like object.
    """
    # If a signed Tx is provided, sign the unsigned portion.
    if hasattr(tx, "unsigned"):
        tx = getattr(tx, "unsigned")

    payload = _as_mapping(tx)
    chain_id = _chain_id_from(tx, payload)
    return signbytes_tx(payload, chain_id)

__all__ = [
    "DOM_TX_SIGN_V1",
    "DOM_HEADER_SIGN_V1",
    "signbytes",
    "signbytes_tx",
    "signbytes_header",
    "header_signing_bytes",
    "tx_signing_bytes",
    "hash_signbytes",
    "tx_hash",
    "header_hash",
    "CanonicalTypeError",
]
