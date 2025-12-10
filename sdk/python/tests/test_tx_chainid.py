"""
Unit tests for SDK transaction chainId handling.

Tests that chainId is:
1. Required when building transactions
2. Properly encoded in CBOR
3. Properly decoded from CBOR/RPC dicts
4. Validated to be > 0
"""

from __future__ import annotations

import pytest


def test_tx_builder_requires_chainid():
    """Test that SDK transaction builders require chainId parameter."""
    from omni_sdk.tx.build import transfer
    
    # Build a transaction with chainId
    tx = transfer(
        from_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
        to_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
        amount=1000,
        nonce=0,
        gas_limit=21000,
        max_fee=1000000000,
        chain_id=1,  # Required parameter
    )
    
    assert tx.chain_id == 1


def test_tx_builder_rejects_invalid_chainid():
    """Test that transaction builders reject invalid chainId values."""
    from omni_sdk.tx.build import transfer
    
    # ChainId must be positive
    with pytest.raises(ValueError, match="chain_id must be non-negative"):
        transfer(
            from_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
            to_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
            amount=1000,
            nonce=0,
            gas_limit=21000,
            max_fee=1000000000,
            chain_id=-1,  # Invalid
        )


def test_canonical_body_dict_includes_chainid():
    """Test that canonical_body_dict includes chainId."""
    from omni_sdk.tx.build import transfer
    from omni_sdk.tx.encode import canonical_body_dict
    
    tx = transfer(
        from_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
        to_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
        amount=1000,
        nonce=0,
        gas_limit=21000,
        max_fee=1000000000,
        chain_id=42,
    )
    
    body = canonical_body_dict(tx)
    
    assert "chainId" in body, f"chainId missing from body: {list(body.keys())}"
    assert body["chainId"] == 42


def test_pack_signed_includes_chainid_in_body():
    """Test that pack_signed encodes chainId in the body field."""
    from omni_sdk.tx.build import transfer
    from omni_sdk.tx.encode import pack_signed
    from omni_sdk.utils.cbor import loads as cbor_loads
    
    tx = transfer(
        from_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
        to_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
        amount=1000,
        nonce=0,
        gas_limit=21000,
        max_fee=1000000000,
        chain_id=123,
    )
    
    # Pack with dummy signature
    raw_cbor = pack_signed(
        tx,
        signature=b"dummy_sig",
        alg_id=1,
        public_key=b"dummy_pubkey",
    )
    
    # Decode and verify structure
    decoded = cbor_loads(raw_cbor)
    
    assert "body" in decoded, f"Missing 'body' field: {list(decoded.keys())}"
    assert "chainId" in decoded["body"], f"Missing 'chainId' in body: {list(decoded['body'].keys())}"
    assert decoded["body"]["chainId"] == 123


def test_tx_from_rpc_dict_requires_chainid():
    """Test that Tx.from_rpc_dict requires chainId and rejects missing/invalid values."""
    from omni_sdk.types.core import Tx
    
    # Valid case
    tx_dict = {
        "from": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
        "to": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
        "nonce": 0,
        "value": 1000,
        "data": "0x",
        "gasLimit": 21000,
        "maxFee": 1000000000,
        "chainId": 1,
    }
    
    tx = Tx.from_rpc_dict(tx_dict)
    assert tx.chain_id == 1
    
    # Missing chainId should raise error
    bad_dict = tx_dict.copy()
    del bad_dict["chainId"]
    
    with pytest.raises(ValueError, match="missing required field 'chainId'"):
        Tx.from_rpc_dict(bad_dict)
    
    # ChainId = 0 should raise error
    bad_dict = tx_dict.copy()
    bad_dict["chainId"] = 0
    
    with pytest.raises(ValueError, match="must be a positive integer"):
        Tx.from_rpc_dict(bad_dict)
    
    # ChainId = -1 should raise error
    bad_dict = tx_dict.copy()
    bad_dict["chainId"] = -1
    
    with pytest.raises(ValueError, match="must be a positive integer"):
        Tx.from_rpc_dict(bad_dict)
    
    # Invalid type should raise error
    bad_dict = tx_dict.copy()
    bad_dict["chainId"] = "not_a_number"
    
    with pytest.raises(ValueError, match="invalid chainId"):
        Tx.from_rpc_dict(bad_dict)


def test_sign_bytes_deterministic_with_chainid():
    """Test that sign_bytes is deterministic and includes chainId."""
    from omni_sdk.tx.build import transfer
    from omni_sdk.tx.encode import sign_bytes
    
    # Build same transaction twice
    tx1 = transfer(
        from_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
        to_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
        amount=1000,
        nonce=0,
        gas_limit=21000,
        max_fee=1000000000,
        chain_id=99,
    )
    
    tx2 = transfer(
        from_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
        to_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
        amount=1000,
        nonce=0,
        gas_limit=21000,
        max_fee=1000000000,
        chain_id=99,
    )
    
    # Sign bytes should be identical
    sb1 = sign_bytes(tx1)
    sb2 = sign_bytes(tx2)
    assert sb1 == sb2
    
    # Different chainId should produce different sign bytes
    tx3 = transfer(
        from_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
        to_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
        amount=1000,
        nonce=0,
        gas_limit=21000,
        max_fee=1000000000,
        chain_id=100,  # Different chainId
    )
    
    sb3 = sign_bytes(tx3)
    assert sb3 != sb1, "Different chainId should produce different sign bytes"
