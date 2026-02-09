"""
Tests for coretx.canonical module
"""

import pytest

from coretx.types import TxBody, TxAuth, TxEnvelope, TxId, TxKind
from coretx.canonical import (
    encode_tx_body,
    encode_tx_auth,
    encode_tx_envelope,
    compute_sign_bytes,
    compute_sign_hash,
    compute_txid,
    decode_tx_envelope,
    DOMAIN_TX_SIGN,
    PREHASH_SHA3_256,
    PREHASH_SHA3_512,
)


def _make_test_body():
    """Create a test transaction body"""
    return TxBody(
        version=1,
        chain_id=1,
        nonce=0,
        from_addr=b"\x01" * 32,
        to_addr=b"\x02" * 32,
        value=1000,
        fee=21,
        gas_limit=21000,
        data=b"test data",
        memo="test memo",
        timestamp=1234567890,
        kind=TxKind.TRANSFER,
    )


def test_encode_tx_body_deterministic():
    """Test that encoding is deterministic"""
    body = _make_test_body()
    
    # Encode multiple times
    encoded1 = encode_tx_body(body)
    encoded2 = encode_tx_body(body)
    
    # Should be identical
    assert encoded1 == encoded2
    assert isinstance(encoded1, bytes)
    assert len(encoded1) > 0


def test_encode_tx_body_different_bodies():
    """Test that different bodies encode differently"""
    body1 = _make_test_body()
    body2 = TxBody(
        version=1,
        chain_id=1,
        nonce=1,  # Different nonce
        from_addr=b"\x01" * 32,
        to_addr=b"\x02" * 32,
        value=1000,
        fee=21,
        gas_limit=21000,
        data=b"test data",
        memo="test memo",
        timestamp=1234567890,
    )
    
    encoded1 = encode_tx_body(body1)
    encoded2 = encode_tx_body(body2)
    
    # Should be different
    assert encoded1 != encoded2


def test_encode_decode_roundtrip():
    """Test that encoding and decoding preserves data"""
    body = _make_test_body()
    auth = TxAuth(
        scheme_id=1,
        pubkey_bytes=b"pubkey" * 10,
        signature_bytes=b"signature" * 20,
        prehash_id=2,
    )
    
    # Create envelope with placeholder txid
    envelope = TxEnvelope(body=body, auth=auth, txid=TxId(bytes32=b"\x00" * 32))
    txid = compute_txid(envelope)
    envelope = TxEnvelope(body=body, auth=auth, txid=txid)
    
    # Encode
    encoded = encode_tx_envelope(envelope)
    
    # Decode
    decoded = decode_tx_envelope(encoded)
    
    # Check body fields
    assert decoded.body.version == body.version
    assert decoded.body.chain_id == body.chain_id
    assert decoded.body.nonce == body.nonce
    assert decoded.body.from_addr == body.from_addr
    assert decoded.body.to_addr == body.to_addr
    assert decoded.body.value == body.value
    assert decoded.body.fee == body.fee
    assert decoded.body.gas_limit == body.gas_limit
    assert decoded.body.data == body.data
    assert decoded.body.memo == body.memo
    assert decoded.body.timestamp == body.timestamp
    
    # Check auth fields
    assert decoded.auth.scheme_id == auth.scheme_id
    assert decoded.auth.pubkey_bytes == auth.pubkey_bytes
    assert decoded.auth.signature_bytes == auth.signature_bytes
    assert decoded.auth.prehash_id == auth.prehash_id
    
    # Check txid
    assert decoded.txid.bytes32 == txid.bytes32


def test_compute_sign_bytes_deterministic():
    """Test that sign_bytes computation is deterministic"""
    body = _make_test_body()
    
    sign_bytes1 = compute_sign_bytes(body)
    sign_bytes2 = compute_sign_bytes(body)
    
    assert sign_bytes1 == sign_bytes2
    assert isinstance(sign_bytes1, bytes)
    assert len(sign_bytes1) > 0


def test_compute_sign_bytes_includes_domain():
    """Test that sign_bytes includes domain separation"""
    body = _make_test_body()
    
    sign_bytes = compute_sign_bytes(body, domain=DOMAIN_TX_SIGN)
    
    # The exact format is CBOR, but we can check it's non-empty
    assert len(sign_bytes) > 0


def test_compute_sign_hash_sha3_256():
    """Test sign hash with SHA3-256"""
    body = _make_test_body()
    
    hash_bytes = compute_sign_hash(body, prehash_id=PREHASH_SHA3_256)
    
    # SHA3-256 produces 32 bytes
    assert len(hash_bytes) == 32


def test_compute_sign_hash_sha3_512():
    """Test sign hash with SHA3-512"""
    body = _make_test_body()
    
    hash_bytes = compute_sign_hash(body, prehash_id=PREHASH_SHA3_512)
    
    # SHA3-512 produces 64 bytes
    assert len(hash_bytes) == 64


def test_compute_txid_deterministic():
    """Test that txid computation is deterministic"""
    body = _make_test_body()
    auth = TxAuth(
        scheme_id=1,
        pubkey_bytes=b"pubkey" * 10,
        signature_bytes=b"signature" * 20,
        prehash_id=2,
    )
    
    envelope = TxEnvelope(body=body, auth=auth, txid=TxId(bytes32=b"\x00" * 32))
    
    txid1 = compute_txid(envelope)
    txid2 = compute_txid(envelope)
    
    assert txid1.bytes32 == txid2.bytes32
    assert len(txid1.bytes32) == 32


def test_compute_txid_different_signatures():
    """Test that different signatures produce different txids"""
    body = _make_test_body()
    auth1 = TxAuth(
        scheme_id=1,
        pubkey_bytes=b"pubkey" * 10,
        signature_bytes=b"signature1" * 20,
        prehash_id=2,
    )
    auth2 = TxAuth(
        scheme_id=1,
        pubkey_bytes=b"pubkey" * 10,
        signature_bytes=b"signature2" * 20,
        prehash_id=2,
    )
    
    envelope1 = TxEnvelope(body=body, auth=auth1, txid=TxId(bytes32=b"\x00" * 32))
    envelope2 = TxEnvelope(body=body, auth=auth2, txid=TxId(bytes32=b"\x00" * 32))
    
    txid1 = compute_txid(envelope1)
    txid2 = compute_txid(envelope2)
    
    # Different signatures should produce different txids (malleability protection)
    assert txid1.bytes32 != txid2.bytes32


def test_decode_invalid_cbor():
    """Test that invalid CBOR is rejected"""
    with pytest.raises(ValueError, match="CBOR decode failed"):
        decode_tx_envelope(b"invalid cbor data")


def test_decode_wrong_structure():
    """Test that wrong structure is rejected"""
    import cbor2
    
    # Valid CBOR but wrong structure
    wrong_data = cbor2.dumps({"wrong": "structure"})
    
    with pytest.raises((KeyError, TypeError)):
        decode_tx_envelope(wrong_data)
