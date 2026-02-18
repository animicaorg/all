"""Tests for UWP CBOR encoding/decoding."""

import pytest
from core.usefulwork.types import UsefulWorkProof, Hash
from core.usefulwork.cbor_codec import (
    encode_proof,
    decode_proof,
    encode_proof_to_hex,
    decode_proof_from_hex,
    UWPDecodeError,
    MAX_RECEIPT_BYTES,
    MAX_METADATA_KEYS,
)


def test_encode_decode_round_trip():
    """Test encoding and decoding a proof."""
    proof = UsefulWorkProof(
        scheme_id="ena.eval.micro",
        plan_commitment=Hash(b'\x01' * 32),
        instance_id=Hash(b'\x02' * 32),
        input_commitment=Hash(b'\x03' * 32),
        output_commitment=Hash(b'\x04' * 32),
        receipt_bytes=b'test receipt data',
        metadata={"num_items": 100, "model": "test-model"},
    )
    
    # Encode
    cbor_bytes = encode_proof(proof)
    assert isinstance(cbor_bytes, bytes)
    assert len(cbor_bytes) > 0
    
    # Decode
    decoded = decode_proof(cbor_bytes)
    
    # Verify fields
    assert decoded.scheme_id == proof.scheme_id
    assert decoded.plan_commitment == proof.plan_commitment
    assert decoded.instance_id == proof.instance_id
    assert decoded.input_commitment == proof.input_commitment
    assert decoded.output_commitment == proof.output_commitment
    assert decoded.receipt_bytes == proof.receipt_bytes
    assert decoded.metadata == proof.metadata


def test_encode_decode_hex():
    """Test hex encoding/decoding."""
    proof = UsefulWorkProof(
        scheme_id="compute.receipt.v1",
        plan_commitment=Hash(b'\x05' * 32),
        instance_id=Hash(b'\x06' * 32),
        input_commitment=Hash(b'\x07' * 32),
        output_commitment=Hash(b'\x08' * 32),
        receipt_bytes=b'receipt',
    )
    
    # Encode to hex
    hex_str = encode_proof_to_hex(proof)
    assert isinstance(hex_str, str)
    assert all(c in '0123456789abcdef' for c in hex_str.lower())
    
    # Decode from hex
    decoded = decode_proof_from_hex(hex_str)
    assert decoded.scheme_id == proof.scheme_id
    assert decoded.receipt_bytes == proof.receipt_bytes


def test_decode_with_0x_prefix():
    """Test decoding hex with 0x prefix."""
    proof = UsefulWorkProof(
        scheme_id="test",
        plan_commitment=Hash(b'\x01' * 32),
        instance_id=Hash(b'\x02' * 32),
        input_commitment=Hash(b'\x03' * 32),
        output_commitment=Hash(b'\x04' * 32),
        receipt_bytes=b'data',
    )
    
    hex_str = "0x" + encode_proof_to_hex(proof)
    decoded = decode_proof_from_hex(hex_str)
    assert decoded.scheme_id == "test"


def test_oversized_receipt_rejected():
    """Test that oversized receipts are rejected."""
    proof = UsefulWorkProof(
        scheme_id="test",
        plan_commitment=Hash(b'\x01' * 32),
        instance_id=Hash(b'\x02' * 32),
        input_commitment=Hash(b'\x03' * 32),
        output_commitment=Hash(b'\x04' * 32),
        receipt_bytes=b'x' * (MAX_RECEIPT_BYTES + 1),
    )
    
    with pytest.raises(ValueError, match="receipt_bytes exceeds max"):
        encode_proof(proof)


def test_too_many_metadata_keys():
    """Test that too many metadata keys are rejected."""
    metadata = {f"key{i}": i for i in range(MAX_METADATA_KEYS + 1)}
    
    proof = UsefulWorkProof(
        scheme_id="test",
        plan_commitment=Hash(b'\x01' * 32),
        instance_id=Hash(b'\x02' * 32),
        input_commitment=Hash(b'\x03' * 32),
        output_commitment=Hash(b'\x04' * 32),
        receipt_bytes=b'data',
        metadata=metadata,
    )
    
    with pytest.raises(ValueError, match="metadata exceeds max"):
        encode_proof(proof)


def test_invalid_hash_length():
    """Test that invalid hash lengths are rejected."""
    with pytest.raises(ValueError, match="must be 32 bytes"):
        UsefulWorkProof(
            scheme_id="test",
            plan_commitment=b'\x01' * 16,  # Wrong length!
            instance_id=Hash(b'\x02' * 32),
            input_commitment=Hash(b'\x03' * 32),
            output_commitment=Hash(b'\x04' * 32),
            receipt_bytes=b'data',
        )


def test_missing_required_field():
    """Test that missing required fields are detected on decode."""
    # Create a minimal CBOR map missing required fields
    from core.encoding.cbor import dumps
    
    invalid_data = dumps({"scheme_id": "test"})
    
    with pytest.raises(UWPDecodeError, match="Missing required field"):
        decode_proof(invalid_data)


def test_invalid_scheme_id_too_long():
    """Test that excessively long scheme IDs are rejected on decode."""
    from core.encoding.cbor import dumps
    
    data = dumps({
        "scheme_id": "x" * 100,  # Too long
        "plan_commitment": b'\x01' * 32,
        "instance_id": b'\x02' * 32,
        "input_commitment": b'\x03' * 32,
        "output_commitment": b'\x04' * 32,
        "receipt_bytes": b'data',
    })
    
    with pytest.raises(UWPDecodeError, match="scheme_id must be"):
        decode_proof(data)


def test_invalid_cbor():
    """Test that invalid CBOR is rejected."""
    with pytest.raises(UWPDecodeError, match="CBOR decode failed"):
        decode_proof(b'\xff\xff\xff')
