"""
CBOR encoding/decoding for Useful Work Proofs with strict bounds enforcement.

This module implements deterministic CBOR encoding and defensive decoding for UWP.
"""

from __future__ import annotations

from typing import Any, Dict, List
from core.encoding.cbor import dumps as cbor_dumps, loads as cbor_loads
from .types import UsefulWorkProof, Hash

# Limits (policy-controlled, these are defaults)
MAX_RECEIPT_BYTES = 65536  # 64 KB
MAX_METADATA_KEYS = 32
MAX_METADATA_VALUE_BYTES = 1024
MAX_CBOR_DEPTH = 8


class UWPDecodeError(Exception):
    """Raised when CBOR decoding fails or bounds are violated."""
    pass


def encode_proof(proof: UsefulWorkProof) -> bytes:
    """
    Encode a UsefulWorkProof to canonical CBOR bytes.
    
    Args:
        proof: The proof to encode
        
    Returns:
        Canonical CBOR bytes
        
    Raises:
        ValueError: If proof violates size limits
    """
    if len(proof.receipt_bytes) > MAX_RECEIPT_BYTES:
        raise ValueError(f"receipt_bytes exceeds max {MAX_RECEIPT_BYTES} bytes")
    
    if len(proof.metadata) > MAX_METADATA_KEYS:
        raise ValueError(f"metadata exceeds max {MAX_METADATA_KEYS} keys")
    
    # Validate metadata values
    for key, value in proof.metadata.items():
        if isinstance(value, (str, bytes)):
            if len(str(value).encode() if isinstance(value, str) else value) > MAX_METADATA_VALUE_BYTES:
                raise ValueError(f"metadata[{key}] exceeds max {MAX_METADATA_VALUE_BYTES} bytes")
    
    # Build canonical map
    data = {
        "scheme_id": proof.scheme_id,
        "plan_commitment": bytes(proof.plan_commitment),
        "instance_id": bytes(proof.instance_id),
        "input_commitment": bytes(proof.input_commitment),
        "output_commitment": bytes(proof.output_commitment),
        "receipt_bytes": proof.receipt_bytes,
    }
    
    if proof.metadata:
        data["metadata"] = proof.metadata
    
    return cbor_dumps(data)


def decode_proof(cbor_bytes: bytes, *, max_size: int = MAX_RECEIPT_BYTES) -> UsefulWorkProof:
    """
    Decode CBOR bytes to UsefulWorkProof with strict bounds checking.
    
    Args:
        cbor_bytes: CBOR-encoded bytes
        max_size: Maximum allowed size for receipt_bytes
        
    Returns:
        Decoded UsefulWorkProof
        
    Raises:
        UWPDecodeError: If decoding fails or bounds are violated
    """
    # Check total size
    if len(cbor_bytes) > max_size + 4096:  # Allow overhead for structure
        raise UWPDecodeError(f"CBOR bytes exceed max {max_size + 4096} bytes")
    
    try:
        data = cbor_loads(cbor_bytes)
    except Exception as e:
        raise UWPDecodeError(f"CBOR decode failed: {e}")
    
    if not isinstance(data, dict):
        raise UWPDecodeError("Expected CBOR map at root")
    
    # Validate required fields
    required = ["scheme_id", "plan_commitment", "instance_id", 
                "input_commitment", "output_commitment", "receipt_bytes"]
    
    for field in required:
        if field not in data:
            raise UWPDecodeError(f"Missing required field: {field}")
    
    # Validate types and lengths
    scheme_id = data["scheme_id"]
    if not isinstance(scheme_id, str) or len(scheme_id) == 0 or len(scheme_id) > 64:
        raise UWPDecodeError("scheme_id must be 1-64 char string")
    
    # Validate hashes
    for field in ["plan_commitment", "instance_id", "input_commitment", "output_commitment"]:
        value = data[field]
        if not isinstance(value, bytes) or len(value) != 32:
            raise UWPDecodeError(f"{field} must be 32 bytes")
    
    # Validate receipt
    receipt_bytes = data["receipt_bytes"]
    if not isinstance(receipt_bytes, bytes):
        raise UWPDecodeError("receipt_bytes must be bytes")
    if len(receipt_bytes) > max_size:
        raise UWPDecodeError(f"receipt_bytes exceeds max {max_size} bytes")
    
    # Validate metadata (optional)
    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise UWPDecodeError("metadata must be a map")
    if len(metadata) > MAX_METADATA_KEYS:
        raise UWPDecodeError(f"metadata exceeds max {MAX_METADATA_KEYS} keys")
    
    # Construct proof
    return UsefulWorkProof(
        scheme_id=scheme_id,
        plan_commitment=Hash(data["plan_commitment"]),
        instance_id=Hash(data["instance_id"]),
        input_commitment=Hash(data["input_commitment"]),
        output_commitment=Hash(data["output_commitment"]),
        receipt_bytes=receipt_bytes,
        metadata=metadata,
    )


def encode_proof_to_hex(proof: UsefulWorkProof) -> str:
    """Encode proof to hex string (for RPC)."""
    return encode_proof(proof).hex()


def decode_proof_from_hex(hex_str: str, *, max_size: int = MAX_RECEIPT_BYTES) -> UsefulWorkProof:
    """Decode proof from hex string (for RPC)."""
    try:
        cbor_bytes = bytes.fromhex(hex_str.replace("0x", ""))
    except ValueError as e:
        raise UWPDecodeError(f"Invalid hex string: {e}")
    
    return decode_proof(cbor_bytes, max_size=max_size)


__all__ = [
    "MAX_RECEIPT_BYTES",
    "MAX_METADATA_KEYS",
    "MAX_METADATA_VALUE_BYTES",
    "UWPDecodeError",
    "encode_proof",
    "decode_proof",
    "encode_proof_to_hex",
    "decode_proof_from_hex",
]
