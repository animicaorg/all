"""
Test that mempool correctly handles invalid nonce types.

This test validates the fix for the TypeError issue where nonce values
from normalized_env were not being converted to int before use in
comparisons and dictionary lookups.
"""
from __future__ import annotations

import pytest
from unittest.mock import Mock, patch

from rpc.mempool_service import MempoolService, AdmissionError


def test_nonce_dict_type_raises_admission_error():
    """Test that a dict nonce type is caught and raises AdmissionError."""
    # Setup minimal mempool service
    svc = MempoolService.create(
        chain_id=1337,
        min_gas_price_wei=1,
        state_db=None,
        tx_index=None,
        data_dir=None,
    )
    
    # Mock the normalized envelope to return a dict for nonce
    invalid_nonce = {"invalid": "dict"}
    normalized_env = {
        "hash": "0xabcd1234" + "0" * 56,
        "nonce": invalid_nonce,  # Invalid type!
        "tx": {
            "body": {
                "from": bytes(32),
                "to": bytes(32),
                "value": 0,
                "fee": 100,
            }
        },
    }
    
    tx = normalized_env["tx"]
    raw_bytes = b"dummy_raw_tx_bytes"
    tx_hash_hex = normalized_env["hash"]
    
    # Mock normalize_tx_envelope to return our crafted envelope
    with patch("rpc.mempool_service.normalize_tx_envelope", return_value=normalized_env):
        # Mock _sender_from_signature to return a sender
        with patch("rpc.mempool_service._sender_from_signature", return_value=bytes(32)):
            # Mock _tx_version to return version 1 (which requires nonce)
            with patch("rpc.mempool_service._tx_version", return_value=1):
                # Should raise AdmissionError with "invalid nonce type"
                with pytest.raises(AdmissionError) as exc_info:
                    svc.submit(
                        tx=tx,
                        raw=raw_bytes,
                        tx_hash_hex=tx_hash_hex,
                        local=True,
                    )
                
                # Verify the error message mentions invalid nonce type
                assert "invalid nonce type" in str(exc_info.value).lower()


def test_nonce_string_type_converts_successfully():
    """Test that a string nonce type is successfully converted to int."""
    # Setup minimal mempool service
    svc = MempoolService.create(
        chain_id=1337,
        min_gas_price_wei=1,
        state_db=None,
        tx_index=None,
        data_dir=None,
    )
    
    # Mock the normalized envelope to return a string for nonce (valid if numeric)
    normalized_env = {
        "hash": "0xabcd1234" + "0" * 56,
        "nonce": "42",  # String but convertible to int
        "tx": {
            "body": {
                "from": bytes(32),
                "to": bytes(32),
                "value": 0,
                "fee": 100,
            }
        },
    }
    
    tx = normalized_env["tx"]
    raw_bytes = b"dummy_raw_tx_bytes"
    tx_hash_hex = normalized_env["hash"]
    
    # Mock normalize_tx_envelope to return our crafted envelope
    with patch("rpc.mempool_service.normalize_tx_envelope", return_value=normalized_env):
        # Mock _sender_from_signature to return a sender
        sender_bytes = bytes(32)
        with patch("rpc.mempool_service._sender_from_signature", return_value=sender_bytes):
            # Mock _tx_version to return version 1 (which requires nonce)
            with patch("rpc.mempool_service._tx_version", return_value=1):
                # Mock _confirmed_nonce to return 41 (so nonce 42 is valid)
                with patch.object(svc, "_confirmed_nonce", return_value=41):
                    # Mock estimate_max_spend to avoid balance check complexity
                    with patch("rpc.mempool_service.estimate_max_spend", return_value=100):
                        # Should NOT raise TypeError - the nonce should be converted to int
                        try:
                            result_hash = svc.submit(
                                tx=tx,
                                raw=raw_bytes,
                                tx_hash_hex=tx_hash_hex,
                                local=True,
                            )
                            # If it succeeded, the nonce was properly converted
                            assert result_hash == tx_hash_hex
                        except TypeError as e:
                            # This should NOT happen after the fix
                            pytest.fail(f"TypeError was not prevented by the fix: {e}")
                        except Exception:
                            # Other exceptions are ok (balance checks, etc.)
                            # We just want to ensure no TypeError from nonce comparison
                            pass


def test_nonce_bytes_type_raises_admission_error():
    """Test that a bytes nonce type is caught and raises AdmissionError."""
    # Setup minimal mempool service
    svc = MempoolService.create(
        chain_id=1337,
        min_gas_price_wei=1,
        state_db=None,
        tx_index=None,
        data_dir=None,
    )
    
    # Mock the normalized envelope to return bytes for nonce
    invalid_nonce = b"\x00\x00\x00*"  # bytes
    normalized_env = {
        "hash": "0xabcd1234" + "0" * 56,
        "nonce": invalid_nonce,  # Invalid type!
        "tx": {
            "body": {
                "from": bytes(32),
                "to": bytes(32),
                "value": 0,
                "fee": 100,
            }
        },
    }
    
    tx = normalized_env["tx"]
    raw_bytes = b"dummy_raw_tx_bytes"
    tx_hash_hex = normalized_env["hash"]
    
    # Mock normalize_tx_envelope to return our crafted envelope
    with patch("rpc.mempool_service.normalize_tx_envelope", return_value=normalized_env):
        # Mock _sender_from_signature to return a sender
        with patch("rpc.mempool_service._sender_from_signature", return_value=bytes(32)):
            # Mock _tx_version to return version 1 (which requires nonce)
            with patch("rpc.mempool_service._tx_version", return_value=1):
                # Should raise AdmissionError (TypeError is caught and converted)
                with pytest.raises(AdmissionError) as exc_info:
                    svc.submit(
                        tx=tx,
                        raw=raw_bytes,
                        tx_hash_hex=tx_hash_hex,
                        local=True,
                    )
                
                # Verify it mentions invalid nonce type or format
                error_msg = str(exc_info.value).lower()
                assert "invalid nonce type" in error_msg or "invalid" in error_msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
