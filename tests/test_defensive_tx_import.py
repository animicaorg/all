"""
Tests for defensive transaction import that tries multiple decoding methods.

This test validates that when a transaction fails to decode with the primary
CBOR decoder, the system tries alternative decoders and provides clear error
messages about all attempted methods and their failure reasons.
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch

# We'll need to be careful with imports since rpc.methods.tx has many dependencies
# For unit testing, we'll test the logic directly


def test_defensive_decode_with_primary_cbor_success():
    """Test that primary CBOR decoder is used when it succeeds."""
    # We'll test the actual implementation when dependencies are available
    # For now, this is a placeholder to establish the test pattern
    pass


def test_defensive_decode_fallback_to_cbor2():
    """Test that cbor2 is tried when primary CBOR fails."""
    pass


def test_defensive_decode_fallback_to_json():
    """Test that JSON is tried as last resort when all CBOR decoders fail."""
    pass


def test_defensive_decode_all_fail_clear_message():
    """Test that when all decoders fail, a clear error message is provided."""
    pass


def test_try_alternative_decoders_cbor2_success():
    """Test _try_alternative_decoders with cbor2 succeeding."""
    from rpc.methods.tx import _try_alternative_decoders
    
    # Mock cbor2 to succeed
    test_data = b'\xa2\x64body\xa0\x64sigs\x80'  # CBOR for {"body": {}, "sigs": []}
    
    with patch('rpc.methods.tx._cbor2_module') as mock_cbor2:
        mock_cbor2.loads.return_value = {"body": {}, "sigs": []}
        
        result, failures = _try_alternative_decoders(test_data)
        
        assert result is not None
        assert isinstance(result, dict)
        assert "body" in result


def test_try_alternative_decoders_json_success():
    """Test _try_alternative_decoders with JSON succeeding."""
    from rpc.methods.tx import _try_alternative_decoders
    
    # JSON-encoded transaction
    test_data = b'{"body": {"nonce": 1}, "sigs": []}'
    
    with patch('rpc.methods.tx._cbor2_module', None):  # Disable cbor2
        with patch('rpc.methods.tx._msgspec_module', None):  # Disable msgspec
            result, failures = _try_alternative_decoders(test_data)
            
            assert result is not None
            assert isinstance(result, dict)
            assert "body" in result
            assert result["body"]["nonce"] == 1


def test_try_alternative_decoders_all_fail():
    """Test _try_alternative_decoders when all decoders fail."""
    from rpc.methods.tx import _try_alternative_decoders
    
    # Invalid data
    test_data = b'\xff\xff\xff\xff'
    
    with patch('rpc.methods.tx._cbor2_module', None):
        with patch('rpc.methods.tx._msgspec_module', None):
            result, failures = _try_alternative_decoders(test_data)
            
            assert result is None
            assert len(failures) >= 3  # cbor2, msgspec, json
            
            # Check that each failure has decoder name and reason
            for decoder, reason in failures:
                assert isinstance(decoder, str)
                assert isinstance(reason, str)
                assert len(reason) > 0


def test_error_message_includes_all_attempts():
    """Test that error messages include details about all attempted decoders."""
    # This will test the actual error message format
    from rpc.methods.tx import _decode_tx_defensive
    from rpc.errors import InvalidTx
    
    # Create invalid CBOR data
    invalid_data = b'\xff\xff\xff\xff'
    
    with patch('rpc.methods.tx._cbor_loads') as mock_loads:
        mock_loads.side_effect = Exception("Invalid CBOR")
        
        with patch('rpc.methods.tx._cbor2_module', None):
            with patch('rpc.methods.tx._msgspec_module', None):
                with pytest.raises(InvalidTx) as exc_info:
                    _decode_tx_defensive(invalid_data)
                
                error_msg = str(exc_info.value)
                # Should mention that all decoders were tried
                assert "all available decoders" in error_msg.lower() or "tried" in error_msg.lower()


def test_defensive_decode_logs_fallback_usage():
    """Test that fallback decoder usage is logged."""
    from rpc.methods.tx import _decode_tx_defensive
    import logging
    
    # Mock primary decoder to fail, cbor2 to succeed
    test_data = b'\xa2\x64body\xa0\x64sigs\x80'
    
    with patch('rpc.methods.tx._cbor_loads') as mock_primary:
        mock_primary.side_effect = Exception("Primary failed")
        
        with patch('rpc.methods.tx._cbor2_module') as mock_cbor2:
            mock_cbor2.loads.return_value = {"body": {}, "sigs": []}
            
            with patch('rpc.methods.tx._normalize_tx_envelope') as mock_norm:
                mock_norm.return_value = {
                    "tx": {"nonce": 0},
                    "sigs": [],
                    "raw": test_data,
                }
                
                with patch('rpc.methods.tx.log') as mock_log:
                    try:
                        _decode_tx_defensive(test_data)
                    except Exception:
                        pass  # We expect this to fail due to missing dependencies
                    
                    # Verify that fallback decoder attempt was logged
                    # Check for the "Primary CBOR decoder failed" info message
                    info_calls = [call for call in mock_log.info.call_args_list]
                    assert any(
                        "Primary CBOR decoder failed" in str(call) or 
                        "alternative decoders" in str(call).lower()
                        for call in info_calls
                    ), "Expected log message about trying alternative decoders"

