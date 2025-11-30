"""
omni_sdk.tx.encode
==================

Deterministic CBOR encoding for Animica transactions.

This module provides:
- `canonical_body_dict(tx)` → a canonical, signable dictionary view of the Tx
- `sign_bytes(tx)` → bytes to sign (CBOR of the canonical body)
- `pack_signed(tx, signature, alg_id, public_key)` → raw signed CBOR blob ready for RPC
- `unpack_signed(raw)` → parsed envelope {body, sig, algId, pubKey}
- `tx_hash(raw_or_envelope)` → sha3_256 hash of the signed transaction bytes

Design notes
------------
* We produce *deterministic* CBOR using the SDK's `utils.cbor.dumps`, which
  implements canonical (a.k.a. "deterministic") map ordering.
* The canonical "SignBytes" is the CBOR encoding of the **body** only—no
  signature fields are included. Upstream layers may apply domain separation
  when calling the signer (see `wallet.signer.PQSigner.sign(domain=...)`).
* The wire envelope contains:
    {
      "body":   { ... canonical body ... },
      "algId":  <int>,            # PQ alg id (from pq registry)
      "pubKey": <bytes>,          # raw public key bytes
      "sig":    <bytes>,          # raw signature bytes
    }
  This mirrors the node's expected schema and is forward-compatible with
  additional optional fields (which the node will ignore if unknown).

Compatibility
-------------
The `Tx` object can be a dataclass from `omni_sdk.types.core` or a mapping
with the same keys. We read via attribute access with mapping fallback.

"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Tuple, Union

# Deterministic CBOR helpers (required)
try:
    from omni_sdk.utils.cbor import dumps as cbor_dumps  # type: ignore
    from omni_sdk.utils.cbor import loads as cbor_loads
except Exception as _e:  # pragma: no cover
    raise RuntimeError(
        "omni_sdk.utils.cbor is required for deterministic CBOR encoding"
    ) from _e

# Hash helpers (for tx_hash)
try:
    from omni_sdk.utils.hash import sha3_256 as _sha3_256  # type: ignore
except Exception:
    import hashlib

    def _sha3_256(b: bytes) -> bytes:  # type: ignore
        if not hasattr(hashlib, "sha3_256"):
            raise RuntimeError(
                "Python hashlib lacks sha3_256; install pysha3 or upgrade Python."
            )
        return hashlib.sha3_256(b).digest()


# Small hex helper (for debugging)
try:
    from omni_sdk.utils.bytes import to_hex as _to_hex  # type: ignore
except Exception:

    def _to_hex(b: bytes) -> str:
        return "0x" + bytes(b).hex()


TxLike = Any  # dataclass or mapping with required fields


# -----------------------------------------------------------------------------
# Canonical body (SignBytes source)
# -----------------------------------------------------------------------------

_BODY_KEY_ORDER = (
    "chainId",
    "from",
    "to",
    "nonce",
    "value",
    "gasLimit",
    "maxFee",
    "data",
)


def _get(tx: TxLike, key: str) -> Any:
    if hasattr(tx, key):
        return getattr(tx, key)
    if isinstance(tx, Mapping) and key in tx:
        return tx[key]
    # Legacy alias for 'from' sometimes is 'from_addr'
    if key == "from":
        if hasattr(tx, "from_addr"):
            return getattr(tx, "from_addr")
        if isinstance(tx, Mapping) and "from_addr" in tx:
            return tx["from_addr"]
    raise KeyError(f"Tx field '{key}' not found")


def canonical_body_dict(tx: TxLike) -> Dict[str, Any]:
    """
    Build the canonical, signable body dictionary for a transaction.

    Fields (and types):
      - chainId  : int
      - from     : str (bech32m address)
      - to       : str | None
      - nonce    : int
      - value    : int
      - gasLimit : int
      - maxFee   : int
      - data     : bytes
    """
    body = {
        "chainId": int(_get(tx, "chainId")),
        "from": str(_get(tx, "from")),
        "to": _get(tx, "to"),
        "nonce": int(_get(tx, "nonce")),
        "value": int(_get(tx, "value")),
        "gasLimit": int(
            _get(tx, "gas_limit") if hasattr(tx, "gas_limit") else _get(tx, "gasLimit")
        ),
        "maxFee": int(
            _get(tx, "max_fee") if hasattr(tx, "max_fee") else _get(tx, "maxFee")
        ),
        "data": bytes(_get(tx, "data") or b""),
    }

    # Normalize "to": accept "", but encode as None for creations
    if body["to"] in ("", None):
        body["to"] = None
    else:
        body["to"] = str(body["to"])

    return body


def sign_bytes(tx: TxLike) -> bytes:
    """
    Return the deterministic CBOR-encoded SignBytes for the given Tx.

    This is the exact byte string that should be signed by the PQ signer.
    """
    return cbor_dumps(canonical_body_dict(tx))


# -----------------------------------------------------------------------------
# Signed envelope (wire format)
# -----------------------------------------------------------------------------


def pack_signed(
    tx: TxLike,
    *,
    signature: bytes,
    alg_id: int,
    public_key: bytes,
    extra_fields: Dict[str, Any] | None = None,
) -> bytes:
    """
    Produce a raw, signed CBOR transaction.

    Parameters
    ----------
    tx : Tx-like
        Transaction object (dataclass or mapping).
    signature : bytes
        Raw signature bytes returned by the PQ signer.
    alg_id : int
        PQ algorithm id corresponding to the public key and signature.
    public_key : bytes
        Raw public key bytes for the signer.
    extra_fields : Optional[Dict]
        Optional additional fields to include at top-level (e.g. accessList).

    Returns
    -------
    bytes
        Canonical CBOR encoding of the envelope.
    """
    body = canonical_body_dict(tx)
    env: Dict[str, Any] = {
        "body": body,
        "algId": int(alg_id),
        "pubKey": bytes(public_key),
        "sig": bytes(signature),
    }
    if extra_fields:
        # Copy only simple JSON/CBOR-friendly values; callers must ensure validity.
        env.update(extra_fields)
    return cbor_dumps(env)


def unpack_signed(raw: bytes) -> Dict[str, Any]:
    """
    Parse a raw signed CBOR transaction into a Python dictionary.

    Returns a dict with (at least) keys: body, algId, pubKey, sig.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError("raw must be bytes")
    obj = cbor_loads(bytes(raw))
    if not isinstance(obj, dict):
        raise ValueError("signed tx must decode to a CBOR map")
    # Minimal shape validation
    for k in ("body", "algId", "pubKey", "sig"):
        if k not in obj:
            raise ValueError(f"signed tx missing field '{k}'")
    return obj


# -----------------------------------------------------------------------------
# Hash helpers
# -----------------------------------------------------------------------------


def tx_hash(data: Union[bytes, Dict[str, Any]]) -> bytes:
    """
    Compute the sha3_256 hash of a signed transaction.

    Accepts either a raw CBOR-encoded transaction or the decoded envelope dict.
    """
    if isinstance(data, (bytes, bytearray)):
        raw = bytes(data)
    elif isinstance(data, dict):
        raw = cbor_dumps(data)
    else:
        raise TypeError("tx_hash expects raw bytes or a dict envelope")
    return _sha3_256(raw)


def tx_hash_hex(data: Union[bytes, Dict[str, Any]]) -> str:
    return _to_hex(tx_hash(data))


__all__ = [
    "canonical_body_dict",
    "sign_bytes",
    "pack_signed",
    "unpack_signed",
    "tx_hash",
    "tx_hash_hex",
]
