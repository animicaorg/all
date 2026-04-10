"""
Test for PQ signature pubkey size validation in transaction normalization.

This test validates the fix for the issue where 32-byte address hashes were
incorrectly being used instead of full PQ public keys in transaction signatures,
causing verification failures.
"""

import pytest
from core.utils.tx import normalize_tx_envelope, TxNormalizationError
from core.encoding.cbor import dumps as cbor_dumps


def test_sphincs_rejects_32byte_address_hash():
    """
    Test that SPHINCS+ (scheme_id 4098 / 0x1002) rejects 32-byte address hashes
    and requires the full 64-byte public key.
    """
    # Create a transaction with SPHINCS+ signature but with 32-byte "pubkey" (address hash)
    tx_body = {
        "v": 1,
        "chainId": 1,
        "from": b"\x00" * 32,
        "nonce": 1,
        "gas": {"price": 1, "limit": 21000},
        "payload": {"t": 0, "v": {"to": b"\x00" * 32, "amount": 0, "data": b""}},
        "accessList": [],
    }
    
    # 32-byte pubkey (address hash) - WRONG for SPHINCS+
    bad_signature = {
        "alg": 4098,  # SPHINCS+ requires 64 bytes
        "pubkey": b"\x01" * 32,  # Address hash size - WRONG!
        "sig": b"\x02" * 7856,  # Correct sig size for SPHINCS+
    }
    
    envelope = {"tx": tx_body, "sigs": [bad_signature]}
    raw = cbor_dumps(envelope)
    
    # Should raise error about invalid pubkey size
    with pytest.raises(TxNormalizationError) as exc_info:
        normalize_tx_envelope(raw)
    
    assert exc_info.value.reason == "invalid_pubkey_size"
    assert "address hash" in str(exc_info.value).lower()
    assert "64" in str(exc_info.value)  # Expected size


def test_sphincs_accepts_correct_64byte_pubkey():
    """
    Test that SPHINCS+ (scheme_id 4098) accepts correct 64-byte public keys.
    """
    tx_body = {
        "v": 1,
        "chainId": 1,
        "from": b"\x00" * 32,
        "nonce": 1,
        "gas": {"price": 1, "limit": 21000},
        "payload": {"t": 0, "v": {"to": b"\x00" * 32, "amount": 0, "data": b""}},
        "accessList": [],
    }
    
    # 64-byte pubkey - CORRECT for SPHINCS+
    good_signature = {
        "alg": 4098,
        "pubkey": b"\x01" * 64,  # Correct size
        "sig": b"\x02" * 7856,
    }
    
    envelope = {"tx": tx_body, "sigs": [good_signature]}
    raw = cbor_dumps(envelope)
    
    # Should not raise error
    result = normalize_tx_envelope(raw)
    assert result is not None
    assert len(result["sigs"]) == 1
    assert len(result["sigs"][0]["pubkey"]) == 64


def test_dilithium3_rejects_32byte_address_hash():
    """
    Test that Dilithium3 (scheme_id 4097 / 0x1001) rejects 32-byte address hashes
    and requires the full 1952-byte public key.
    """
    tx_body = {
        "v": 1,
        "chainId": 1,
        "from": b"\x00" * 32,
        "nonce": 1,
        "gas": {"price": 1, "limit": 21000},
        "payload": {"t": 0, "v": {"to": b"\x00" * 32, "amount": 0, "data": b""}},
        "accessList": [],
    }
    
    # 32-byte pubkey (address hash) - WRONG for Dilithium3
    bad_signature = {
        "alg": 4097,  # Dilithium3 requires 1952 bytes
        "pubkey": b"\x01" * 32,  # Address hash size - WRONG!
        "sig": b"\x02" * 3293,  # Correct sig size for Dilithium3
    }
    
    envelope = {"tx": tx_body, "sigs": [bad_signature]}
    raw = cbor_dumps(envelope)
    
    # Should raise error about invalid pubkey size
    with pytest.raises(TxNormalizationError) as exc_info:
        normalize_tx_envelope(raw)
    
    assert exc_info.value.reason == "invalid_pubkey_size"
    assert "address hash" in str(exc_info.value).lower()
    assert "1952" in str(exc_info.value)  # Expected size


def test_dilithium3_accepts_correct_1952byte_pubkey():
    """
    Test that Dilithium3 (scheme_id 4097) accepts correct 1952-byte public keys.
    """
    tx_body = {
        "v": 1,
        "chainId": 1,
        "from": b"\x00" * 32,
        "nonce": 1,
        "gas": {"price": 1, "limit": 21000},
        "payload": {"t": 0, "v": {"to": b"\x00" * 32, "amount": 0, "data": b""}},
        "accessList": [],
    }
    
    # 1952-byte pubkey - CORRECT for Dilithium3
    good_signature = {
        "alg": 4097,
        "pubkey": b"\x01" * 1952,  # Correct size
        "sig": b"\x02" * 3293,
    }
    
    envelope = {"tx": tx_body, "sigs": [good_signature]}
    raw = cbor_dumps(envelope)
    
    # Should not raise error
    result = normalize_tx_envelope(raw)
    assert result is not None
    assert len(result["sigs"]) == 1
    assert len(result["sigs"][0]["pubkey"]) == 1952


def test_hex_alg_id_format():
    """
    Test that hex format algorithm IDs (0x1002) are properly parsed and validated.
    """
    tx_body = {
        "v": 1,
        "chainId": 1,
        "from": b"\x00" * 32,
        "nonce": 1,
        "gas": {"price": 1, "limit": 21000},
        "payload": {"t": 0, "v": {"to": b"\x00" * 32, "amount": 0, "data": b""}},
        "accessList": [],
    }
    
    # Using hex alg ID
    bad_signature = {
        "alg": 0x1002,  # SPHINCS+ in hex
        "pubkey": b"\x01" * 32,  # Too short
        "sig": b"\x02" * 7856,
    }
    
    envelope = {"tx": tx_body, "sigs": [bad_signature]}
    raw = cbor_dumps(envelope)
    
    with pytest.raises(TxNormalizationError) as exc_info:
        normalize_tx_envelope(raw)
    
    assert exc_info.value.reason == "invalid_pubkey_size"


def test_unknown_alg_id_skips_validation():
    """
    Test that unknown algorithm IDs skip validation for backward compatibility.
    """
    tx_body = {
        "v": 1,
        "chainId": 1,
        "from": b"\x00" * 32,
        "nonce": 1,
        "gas": {"price": 1, "limit": 21000},
        "payload": {"t": 0, "v": {"to": b"\x00" * 32, "amount": 0, "data": b""}},
        "accessList": [],
    }
    
    # Unknown algorithm ID
    signature = {
        "alg": 9999,  # Unknown
        "pubkey": b"\x01" * 100,  # Any size should be accepted
        "sig": b"\x02" * 200,
    }
    
    envelope = {"tx": tx_body, "sigs": [signature]}
    raw = cbor_dumps(envelope)
    
    # Should not raise error for unknown algorithms
    result = normalize_tx_envelope(raw)
    assert result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
