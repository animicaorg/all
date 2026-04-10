"""
coretx.canonical - Canonical Transaction Encoding
==================================================

Deterministic encoding and hashing for transactions.
All encoding is CBOR-based with canonical (sorted) keys.

Domain separation ensures:
- Sign bytes include chain_id and domain marker
- TxId is computed from complete envelope
- Replay attacks across chains/contexts are prevented
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict

try:
    from core.encoding.cbor import dumps as cbor_dumps, loads as cbor_loads
except ImportError:
    # Fallback to cbor2 if core encoding not available
    # Note: cbor2.dumps doesn't have sort_keys, but it's deterministic by default
    import cbor2
    def cbor_dumps(obj):  # type: ignore
        return cbor2.dumps(obj)
    cbor_loads = cbor2.loads  # type: ignore

from .types import TxBody, TxAuth, TxEnvelope, TxId, TxKind

__all__ = [
    "DOMAIN_TX_SIGN",
    "PREHASH_NONE",
    "PREHASH_SHA3_256",
    "PREHASH_SHA3_512",
    "encode_tx_body",
    "encode_tx_auth",
    "encode_tx_envelope",
    "compute_sign_bytes",
    "compute_sign_hash",
    "compute_txid",
    "decode_tx_envelope",
]

# Domain separation constant for transaction signing
DOMAIN_TX_SIGN = "animica.tx.v1"

# Prehash algorithm identifiers
PREHASH_NONE = 0
PREHASH_SHA3_256 = 1
PREHASH_SHA3_512 = 2


def _sha3_256(data: bytes) -> bytes:
    """SHA3-256 hash"""
    return hashlib.sha3_256(data).digest()


def _sha3_512(data: bytes) -> bytes:
    """SHA3-512 hash"""
    return hashlib.sha3_512(data).digest()


def encode_tx_body(body: TxBody) -> bytes:
    """
    Encode TxBody to canonical CBOR.
    
    Keys are sorted alphabetically for determinism.
    """
    data: Dict[str, Any] = {
        "chain_id": body.chain_id,
        "data": body.data,
        "fee": body.fee,
        "from_addr": body.from_addr,
        "gas_limit": body.gas_limit,
        "kind": int(body.kind),
        "memo": body.memo,
        "nonce": body.nonce,
        "timestamp": body.timestamp,
        "to_addr": body.to_addr,
        "value": body.value,
        "version": body.version,
    }
    # CBOR with canonical encoding (keys are automatically sorted by core.encoding.cbor)
    return cbor_dumps(data)


def encode_tx_auth(auth: TxAuth) -> bytes:
    """
    Encode TxAuth to canonical CBOR.
    """
    data: Dict[str, Any] = {
        "prehash_id": auth.prehash_id,
        "pubkey_bytes": auth.pubkey_bytes,
        "scheme_id": auth.scheme_id,
        "signature_bytes": auth.signature_bytes,
    }
    return cbor_dumps(data)


def encode_tx_envelope(envelope: TxEnvelope) -> bytes:
    """
    Encode complete TxEnvelope to canonical CBOR.
    
    The envelope contains both body and auth.
    TxId is NOT included in the encoding (it's derived from this encoding).
    """
    data: Dict[str, Any] = {
        "auth": {
            "prehash_id": envelope.auth.prehash_id,
            "pubkey_bytes": envelope.auth.pubkey_bytes,
            "scheme_id": envelope.auth.scheme_id,
            "signature_bytes": envelope.auth.signature_bytes,
        },
        "body": {
            "chain_id": envelope.body.chain_id,
            "data": envelope.body.data,
            "fee": envelope.body.fee,
            "from_addr": envelope.body.from_addr,
            "gas_limit": envelope.body.gas_limit,
            "kind": int(envelope.body.kind),
            "memo": envelope.body.memo,
            "nonce": envelope.body.nonce,
            "timestamp": envelope.body.timestamp,
            "to_addr": envelope.body.to_addr,
            "value": envelope.body.value,
            "version": envelope.body.version,
        },
    }
    return cbor_dumps(data)


def compute_sign_bytes(body: TxBody, domain: str = DOMAIN_TX_SIGN) -> bytes:
    """
    Compute the canonical bytes to be signed for a transaction.
    
    Includes:
    - Domain separation marker
    - Chain ID (for replay protection)
    - All body fields in canonical order
    
    This is what gets fed to the PQ signing algorithm.
    """
    # Encode body first
    body_cbor = encode_tx_body(body)
    
    # Wrap with domain separation
    sign_data: Dict[str, Any] = {
        "domain": domain,
        "chain_id": body.chain_id,
        "body": body_cbor,
    }
    
    return cbor_dumps(sign_data)


def compute_sign_hash(body: TxBody, prehash_id: int = PREHASH_SHA3_512) -> bytes:
    """
    Compute the hash of sign_bytes for prehashed PQ signatures.
    
    Args:
        body: Transaction body
        prehash_id: Prehash algorithm (PREHASH_SHA3_256 or PREHASH_SHA3_512)
    
    Returns:
        Hash of sign_bytes
    """
    sign_bytes = compute_sign_bytes(body)
    
    if prehash_id == PREHASH_NONE:
        return sign_bytes
    elif prehash_id == PREHASH_SHA3_256:
        return _sha3_256(sign_bytes)
    elif prehash_id == PREHASH_SHA3_512:
        return _sha3_512(sign_bytes)
    else:
        raise ValueError(f"Unknown prehash_id: {prehash_id}")


def compute_txid(envelope: TxEnvelope) -> TxId:
    """
    Compute the transaction ID from a complete envelope.
    
    TxId = SHA3-256(canonical_encoding(envelope))
    
    This includes the signature, making the txid unique even if the same
    transaction is signed multiple times (malleability protection).
    """
    envelope_cbor = encode_tx_envelope(envelope)
    txid_bytes = _sha3_256(envelope_cbor)
    return TxId(bytes32=txid_bytes)


def _extract_int_field(data: dict, field_name: str, context: str) -> int:
    """
    Extract and convert a field to int, with clear error messages.
    
    Args:
        data: Dictionary containing the field
        field_name: Name of the field to extract
        context: Context for error messages (e.g., "transaction body", "auth")
    
    Returns:
        Field value as int
    
    Raises:
        TypeError: If field is missing or cannot be converted to int
    """
    try:
        value = data[field_name]
    except KeyError as e:
        raise TypeError(f"Missing required field '{field_name}' in {context}") from e
    
    try:
        return int(value)
    except (ValueError, TypeError) as e:
        raise TypeError(f"Invalid {field_name} in {context}: {e}") from e


def decode_tx_envelope(data: bytes) -> TxEnvelope:
    """
    Decode a CBOR-encoded transaction envelope.
    
    Args:
        data: CBOR-encoded envelope bytes
    
    Returns:
        TxEnvelope instance
    
    Raises:
        ValueError: If decoding fails or data is malformed
        TypeError: If decoded data has wrong types
    """
    try:
        raw = cbor_loads(data)
    except Exception as e:
        raise ValueError(f"CBOR decode failed: {e}") from e
    
    if not isinstance(raw, dict):
        raise TypeError(f"Expected dict, got {type(raw)}")
    
    # Extract body
    body_dict = raw.get("body")
    if not isinstance(body_dict, dict):
        raise TypeError(f"body must be dict, got {type(body_dict)}")
    
    # Convert numeric fields to int to prevent TypeError
    # CBOR may decode numbers as different types (float, etc)
    version = _extract_int_field(body_dict, "version", "transaction body")
    chain_id = _extract_int_field(body_dict, "chain_id", "transaction body")
    nonce = _extract_int_field(body_dict, "nonce", "transaction body")
    value = _extract_int_field(body_dict, "value", "transaction body")
    fee = _extract_int_field(body_dict, "fee", "transaction body")
    gas_limit = _extract_int_field(body_dict, "gas_limit", "transaction body")
    timestamp = _extract_int_field(body_dict, "timestamp", "transaction body")
    
    # Ensure bytes fields are bytes
    from_addr = body_dict["from_addr"]
    to_addr = body_dict["to_addr"]
    data = body_dict["data"]
    if not isinstance(from_addr, bytes):
        raise TypeError(f"from_addr must be bytes, got {type(from_addr)}")
    if not isinstance(to_addr, bytes):
        raise TypeError(f"to_addr must be bytes, got {type(to_addr)}")
    if not isinstance(data, bytes):
        raise TypeError(f"data must be bytes, got {type(data)}")
    
    # Ensure memo is string
    memo = body_dict["memo"]
    if not isinstance(memo, str):
        raise TypeError(f"memo must be str, got {type(memo)}")
    
    body = TxBody(
        version=version,
        chain_id=chain_id,
        nonce=nonce,
        from_addr=from_addr,
        to_addr=to_addr,
        value=value,
        fee=fee,
        gas_limit=gas_limit,
        data=data,
        memo=memo,
        timestamp=timestamp,
        kind=TxKind(body_dict.get("kind", 0)),
    )
    
    # Extract auth
    auth_dict = raw.get("auth")
    if not isinstance(auth_dict, dict):
        raise TypeError(f"auth must be dict, got {type(auth_dict)}")
    
    # Convert numeric fields to int
    scheme_id = _extract_int_field(auth_dict, "scheme_id", "auth")
    prehash_id = _extract_int_field(auth_dict, "prehash_id", "auth")
    
    # Ensure bytes fields are bytes
    pubkey_bytes = auth_dict["pubkey_bytes"]
    signature_bytes = auth_dict["signature_bytes"]
    if not isinstance(pubkey_bytes, bytes):
        raise TypeError(f"pubkey_bytes must be bytes, got {type(pubkey_bytes)}")
    if not isinstance(signature_bytes, bytes):
        raise TypeError(f"signature_bytes must be bytes, got {type(signature_bytes)}")
    
    auth = TxAuth(
        scheme_id=scheme_id,
        pubkey_bytes=pubkey_bytes,
        signature_bytes=signature_bytes,
        prehash_id=prehash_id,
    )
    
    # Compute txid
    envelope = TxEnvelope(body=body, auth=auth, txid=TxId(bytes32=b"\x00" * 32))
    txid = compute_txid(envelope)
    
    # Return envelope with correct txid
    return TxEnvelope(body=body, auth=auth, txid=txid)
