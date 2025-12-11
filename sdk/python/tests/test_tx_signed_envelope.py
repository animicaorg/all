"""
Tests for signed transaction envelope structure.

Validates that pack_signed produces envelopes with the correct structure
expected by the node (sig as dict, not raw bytes).
"""

import pytest
from omni_sdk.tx.encode import pack_signed, unpack_signed
from omni_sdk.utils.cbor import dumps as cbor_dumps, loads as cbor_loads


def test_pack_signed_produces_correct_envelope_structure():
    """
    Test that pack_signed creates an envelope with sig as a dict.
    
    Per requirements:
    - SDK test should ensure serialized JSON sent to node includes sig 
      and matches server schema.
    """
    # Create a minimal transaction
    tx = {
        "chainId": 1,
        "from": "anim1test",
        "to": "anim1dest",
        "nonce": 0,
        "value": 1000,
        "gasLimit": 21000,
        "maxFee": 1000000000,
        "data": b"",
    }
    
    # Pack with signature
    signature = b"fake_signature_bytes_" * 5  # 105 bytes
    alg_id = 4098  # sphincs_shake_128s
    public_key = b"fake_public_key_bytes" * 2  # 42 bytes
    
    raw_tx = pack_signed(
        tx,
        signature=signature,
        alg_id=alg_id,
        public_key=public_key,
    )
    
    # Verify it's bytes
    assert isinstance(raw_tx, bytes)
    
    # Unpack and verify structure
    envelope = unpack_signed(raw_tx)
    
    # Verify envelope has body and sig
    assert "body" in envelope, "Envelope must have 'body' field"
    assert "sig" in envelope, "Envelope must have 'sig' field"
    
    # Verify sig is a dict (not raw bytes) - KEY REQUIREMENT
    sig = envelope["sig"]
    assert isinstance(sig, dict), "sig field must be a dict, not raw bytes"
    
    # Verify sig dict has required fields
    assert "algId" in sig, "sig dict must have 'algId' field"
    assert "pubkey" in sig, "sig dict must have 'pubkey' field"
    assert "sig" in sig, "sig dict must have 'sig' field (signature bytes)"
    
    # Verify the fields have the correct values
    assert sig["algId"] == alg_id
    assert sig["pubkey"] == public_key
    assert sig["sig"] == signature
    
    # Verify body has expected structure
    body = envelope["body"]
    assert isinstance(body, dict)
    assert body["chainId"] == 1
    assert body["from"] == "anim1test"
    assert body["to"] == "anim1dest"
    assert body["nonce"] == 0
    assert body["value"] == 1000
    assert body["gasLimit"] == 21000
    assert body["maxFee"] == 1000000000


def test_pack_signed_with_extra_fields():
    """Test that pack_signed preserves extra fields at top level."""
    from omni_sdk.tx.encode import pack_signed, unpack_signed
    
    tx = {
        "chainId": 1,
        "from": "anim1test",
        "to": None,  # Contract creation
        "nonce": 5,
        "value": 0,
        "gasLimit": 500000,
        "maxFee": 2000000000,
        "data": b"\x60\x60\x60",  # Some bytecode
    }
    
    raw_tx = pack_signed(
        tx,
        signature=b"sig" * 30,
        alg_id=4098,
        public_key=b"pk" * 20,
        extra_fields={"memo": "test deploy"},
    )
    
    envelope = unpack_signed(raw_tx)
    
    # Verify core structure
    assert "body" in envelope
    assert "sig" in envelope
    assert isinstance(envelope["sig"], dict)
    
    # Verify extra field is preserved
    assert "memo" in envelope
    assert envelope["memo"] == "test deploy"


def test_unpack_signed_validates_structure():
    """Test that unpack_signed validates the envelope structure."""
    # Test 1: Missing 'body' field
    bad_envelope_1 = cbor_dumps({"sig": {"algId": 1, "pubkey": b"pk", "sig": b"sig"}})
    with pytest.raises(ValueError, match="missing field 'body'"):
        unpack_signed(bad_envelope_1)
    
    # Test 2: Missing 'sig' field
    bad_envelope_2 = cbor_dumps({"body": {"chainId": 1}})
    with pytest.raises(ValueError, match="missing field 'sig'"):
        unpack_signed(bad_envelope_2)
    
    # Test 3: sig is not a dict (old broken format)
    bad_envelope_3 = cbor_dumps({"body": {"chainId": 1}, "sig": b"raw_sig_bytes"})
    with pytest.raises(ValueError, match="'sig' field must be a dict"):
        unpack_signed(bad_envelope_3)
    
    # Test 4: sig dict missing required fields
    bad_envelope_4 = cbor_dumps({
        "body": {"chainId": 1},
        "sig": {"algId": 1}  # Missing pubkey and sig
    })
    with pytest.raises(ValueError, match="sig envelope missing field"):
        unpack_signed(bad_envelope_4)


def test_submit_raw_sends_cbor_with_proper_sig():
    """
    Test that submit_raw sends CBOR-encoded transaction with proper sig structure.
    
    This validates the end-to-end flow: pack_signed → submit_raw → node RPC.
    """
    from omni_sdk.tx.send import submit_raw
    
    # Mock RPC client that captures the raw tx param
    captured_params = []
    
    class MockRpcClient:
        def request(self, method, params=None):
            captured_params.append((method, params))
            if method == "tx.sendRawTransaction":
                return "0xabcdef123456"
            return None
    
    # Create and pack a transaction
    tx = {
        "chainId": 42,
        "from": "anim1sender",
        "to": "anim1receiver",
        "nonce": 10,
        "value": 5000,
        "gasLimit": 30000,
        "maxFee": 1500000000,
        "data": b"",
    }
    
    raw_tx = pack_signed(
        tx,
        signature=b"test_signature" * 10,
        alg_id=4098,
        public_key=b"test_pubkey" * 5,
    )
    
    # Submit via SDK
    rpc = MockRpcClient()
    tx_hash = submit_raw(rpc, raw_tx)
    
    # Verify the call was made
    assert len(captured_params) == 1
    method, params = captured_params[0]
    assert method == "tx.sendRawTransaction"
    assert len(params) == 1
    
    # Verify the param is raw bytes (RPC client will convert to hex)
    raw_tx_param = params[0]
    assert isinstance(raw_tx_param, bytes)
    
    # Decode the CBOR to verify structure
    envelope = cbor_loads(raw_tx_param)
    
    # Verify envelope structure matches node expectations
    assert "body" in envelope
    assert "sig" in envelope
    assert isinstance(envelope["sig"], dict)
    assert "algId" in envelope["sig"]
    assert "pubkey" in envelope["sig"]
    assert "sig" in envelope["sig"]
    
    # Verify returned tx hash
    assert tx_hash == "0xabcdef123456"
