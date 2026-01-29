"""
Test that _chain_id_from handles signed envelope structure with body field.

This test verifies the fix for the issue where tx.sendRawTransaction was failing
with "header/tx missing chain id for signing" because _chain_id_from didn't check
the nested body field in signed envelopes.
"""
import pytest
from core.encoding.canonical import _chain_id_from


def test_chain_id_from_flat_structure():
    """Test that _chain_id_from works with flat chainId."""
    obj = {"chainId": 42}
    result = _chain_id_from(obj, obj)
    assert result == 42


def test_chain_id_from_flat_structure_snake_case():
    """Test that _chain_id_from works with flat chain_id."""
    obj = {"chain_id": 99}
    result = _chain_id_from(obj, obj)
    assert result == 99


def test_chain_id_from_nested_body():
    """Test that _chain_id_from works with nested body.chainId (signed envelope)."""
    obj = {
        "body": {
            "chainId": 1,
            "from": "anim1test",
            "to": "anim1dest",
            "nonce": 0,
            "value": 1000,
            "gasLimit": 21000,
            "maxFee": 1000000000,
            "data": b""
        },
        "sig": {
            "algId": 1,
            "pubkey": b"dummy",
            "sig": b"dummy"
        }
    }
    result = _chain_id_from(obj, obj)
    assert result == 1


def test_chain_id_from_nested_body_snake_case():
    """Test that _chain_id_from works with nested body.chain_id."""
    obj = {
        "body": {
            "chain_id": 123,
            "from": "anim1test",
        },
        "sig": {}
    }
    result = _chain_id_from(obj, obj)
    assert result == 123


def test_chain_id_from_missing_raises():
    """Test that _chain_id_from raises when chainId is missing."""
    obj = {"from": "anim1test", "to": "anim1dest"}
    with pytest.raises(ValueError, match="header/tx missing chain id for signing"):
        _chain_id_from(obj, obj)


def test_chain_id_from_nested_body_missing_raises():
    """Test that _chain_id_from raises when body exists but chainId is missing."""
    obj = {
        "body": {
            "from": "anim1test",
            "to": "anim1dest"
        },
        "sig": {}
    }
    with pytest.raises(ValueError, match="header/tx missing chain id for signing"):
        _chain_id_from(obj, obj)


def test_chain_id_from_attribute():
    """Test that _chain_id_from works with object attributes."""
    class MockTx:
        chain_id = 555
    
    obj = MockTx()
    result = _chain_id_from(obj, {})
    assert result == 555


def test_chain_id_from_attribute_camel_case():
    """Test that _chain_id_from works with chainId attribute."""
    class MockTx:
        chainId = 777
    
    obj = MockTx()
    result = _chain_id_from(obj, {})
    assert result == 777
