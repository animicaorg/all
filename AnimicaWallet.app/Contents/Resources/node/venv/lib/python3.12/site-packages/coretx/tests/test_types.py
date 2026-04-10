"""
Tests for coretx.types module
"""

import pytest

from coretx.types import TxBody, TxAuth, TxEnvelope, TxId, TxKind


def test_tx_body_creation():
    """Test creating a valid TxBody"""
    body = TxBody(
        version=1,
        chain_id=1,
        nonce=0,
        from_addr=b"\x01" * 32,
        to_addr=b"\x02" * 32,
        value=1000,
        fee=21,
        gas_limit=21000,
        data=b"",
        memo="test transaction",
        timestamp=1234567890,
        kind=TxKind.TRANSFER,
    )
    assert body.version == 1
    assert body.chain_id == 1
    assert body.nonce == 0
    assert body.value == 1000


def test_tx_body_validation_negative_values():
    """Test that negative values are rejected"""
    with pytest.raises(ValueError, match="version must be >= 0"):
        TxBody(
            version=-1,
            chain_id=1,
            nonce=0,
            from_addr=b"\x01" * 32,
            to_addr=b"\x02" * 32,
            value=1000,
            fee=21,
            gas_limit=21000,
            data=b"",
            memo="",
            timestamp=1234567890,
        )


def test_tx_body_validation_wrong_address_length():
    """Test that wrong address lengths are rejected"""
    with pytest.raises(ValueError, match="from_addr must be 32 bytes"):
        TxBody(
            version=1,
            chain_id=1,
            nonce=0,
            from_addr=b"\x01" * 20,  # Wrong length
            to_addr=b"\x02" * 32,
            value=1000,
            fee=21,
            gas_limit=21000,
            data=b"",
            memo="",
            timestamp=1234567890,
        )


def test_tx_body_validation_memo_too_long():
    """Test that oversized memos are rejected"""
    with pytest.raises(ValueError, match="memo must be <= 256 bytes"):
        TxBody(
            version=1,
            chain_id=1,
            nonce=0,
            from_addr=b"\x01" * 32,
            to_addr=b"\x02" * 32,
            value=1000,
            fee=21,
            gas_limit=21000,
            data=b"",
            memo="x" * 300,  # Too long
            timestamp=1234567890,
        )


def test_tx_auth_creation():
    """Test creating a valid TxAuth"""
    auth = TxAuth(
        scheme_id=1,
        pubkey_bytes=b"pubkey" * 10,
        signature_bytes=b"signature" * 20,
        prehash_id=2,
    )
    assert auth.scheme_id == 1
    assert auth.prehash_id == 2


def test_tx_auth_validation():
    """Test TxAuth validation"""
    with pytest.raises(ValueError, match="pubkey_bytes cannot be empty"):
        TxAuth(
            scheme_id=1,
            pubkey_bytes=b"",
            signature_bytes=b"sig",
            prehash_id=2,
        )
    
    with pytest.raises(ValueError, match="signature_bytes cannot be empty"):
        TxAuth(
            scheme_id=1,
            pubkey_bytes=b"pub",
            signature_bytes=b"",
            prehash_id=2,
        )


def test_txid_creation():
    """Test creating a TxId"""
    txid = TxId(bytes32=b"\xaa" * 32)
    assert len(txid.bytes32) == 32
    assert txid.hex() == "0x" + "aa" * 32


def test_txid_from_hex():
    """Test creating TxId from hex string"""
    hex_str = "0x" + "bb" * 32
    txid = TxId.from_hex(hex_str)
    assert txid.hex() == hex_str
    
    # Test without 0x prefix
    hex_str2 = "cc" * 32
    txid2 = TxId.from_hex(hex_str2)
    assert txid2.hex() == "0x" + hex_str2


def test_txid_validation():
    """Test TxId validation"""
    with pytest.raises(ValueError, match="TxId must be exactly 32 bytes"):
        TxId(bytes32=b"\x01" * 20)


def test_tx_envelope_creation():
    """Test creating a complete TxEnvelope"""
    body = TxBody(
        version=1,
        chain_id=1,
        nonce=0,
        from_addr=b"\x01" * 32,
        to_addr=b"\x02" * 32,
        value=1000,
        fee=21,
        gas_limit=21000,
        data=b"",
        memo="",
        timestamp=1234567890,
    )
    
    auth = TxAuth(
        scheme_id=1,
        pubkey_bytes=b"pubkey" * 10,
        signature_bytes=b"signature" * 20,
        prehash_id=2,
    )
    
    txid = TxId(bytes32=b"\xaa" * 32)
    
    envelope = TxEnvelope(body=body, auth=auth, txid=txid)
    assert envelope.body == body
    assert envelope.auth == auth
    assert envelope.txid == txid


def test_tx_envelope_type_validation():
    """Test that TxEnvelope rejects wrong types"""
    body = TxBody(
        version=1,
        chain_id=1,
        nonce=0,
        from_addr=b"\x01" * 32,
        to_addr=b"\x02" * 32,
        value=1000,
        fee=21,
        gas_limit=21000,
        data=b"",
        memo="",
        timestamp=1234567890,
    )
    
    auth = TxAuth(
        scheme_id=1,
        pubkey_bytes=b"pubkey" * 10,
        signature_bytes=b"signature" * 20,
        prehash_id=2,
    )
    
    txid = TxId(bytes32=b"\xaa" * 32)
    
    # Wrong body type
    with pytest.raises(TypeError, match="body must be TxBody"):
        TxEnvelope(body="not a body", auth=auth, txid=txid)  # type: ignore
    
    # Wrong auth type
    with pytest.raises(TypeError, match="auth must be TxAuth"):
        TxEnvelope(body=body, auth="not auth", txid=txid)  # type: ignore
    
    # Wrong txid type
    with pytest.raises(TypeError, match="txid must be TxId"):
        TxEnvelope(body=body, auth=auth, txid="not txid")  # type: ignore
