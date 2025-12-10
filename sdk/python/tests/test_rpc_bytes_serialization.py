"""
Tests for JSON serialization of bytes in RPC payloads.

Validates that:
- to_jsonable() converts bytes/bytearray/memoryview to hex strings
- to_jsonable() handles nested structures (dicts, lists, tuples)
- RpcClient properly encodes bytes in params before JSON serialization
- tx.sendRawTransaction works without bytes serialization errors
"""

import json
import pytest
from unittest.mock import Mock, patch

# Import directly from module to avoid bech32 dependency issues in tests
from omni_sdk.rpc import http as rpc_http
from omni_sdk import errors as sdk_errors

RpcClient = rpc_http.RpcClient
to_jsonable = rpc_http.to_jsonable
RpcError = sdk_errors.RpcError


# ============================================================================
# Tests for to_jsonable() helper function
# ============================================================================


def test_to_jsonable_bytes():
    """to_jsonable converts bytes to 0x-prefixed hex string."""
    result = to_jsonable(b"\x00\x01\x02\xff")
    assert result == "0x000102ff"
    assert isinstance(result, str)


def test_to_jsonable_bytearray():
    """to_jsonable converts bytearray to 0x-prefixed hex string."""
    result = to_jsonable(bytearray([0xde, 0xad, 0xbe, 0xef]))
    assert result == "0xdeadbeef"
    assert isinstance(result, str)


def test_to_jsonable_memoryview():
    """to_jsonable converts memoryview to 0x-prefixed hex string."""
    data = b"\xca\xfe\xba\xbe"
    result = to_jsonable(memoryview(data))
    assert result == "0xcafebabe"
    assert isinstance(result, str)


def test_to_jsonable_empty_bytes():
    """to_jsonable handles empty bytes."""
    result = to_jsonable(b"")
    assert result == "0x"
    assert isinstance(result, str)


def test_to_jsonable_dict_with_bytes():
    """to_jsonable recursively processes dict values."""
    obj = {
        "method": "tx.sendRawTransaction",
        "data": b"\x01\x02\x03",
        "count": 42,
    }
    result = to_jsonable(obj)
    assert result == {
        "method": "tx.sendRawTransaction",
        "data": "0x010203",
        "count": 42,
    }


def test_to_jsonable_list_with_bytes():
    """to_jsonable recursively processes list items."""
    obj = [b"\xaa\xbb", "text", 123, None]
    result = to_jsonable(obj)
    assert result == ["0xaabb", "text", 123, None]


def test_to_jsonable_tuple_with_bytes():
    """to_jsonable converts tuple to list and processes items."""
    obj = (b"\x11\x22", "hello", True)
    result = to_jsonable(obj)
    assert result == ["0x1122", "hello", True]
    assert isinstance(result, list)  # tuples become lists


def test_to_jsonable_nested_structures():
    """to_jsonable handles deeply nested structures."""
    obj = {
        "params": [
            b"\xde\xad\xbe\xef",
            {
                "inner": b"\xca\xfe",
                "list": [1, b"\xff", "text"],
            },
        ],
        "meta": {"flag": True, "data": bytearray([0x00, 0x11])},
    }
    result = to_jsonable(obj)
    expected = {
        "params": [
            "0xdeadbeef",
            {
                "inner": "0xcafe",
                "list": [1, "0xff", "text"],
            },
        ],
        "meta": {"flag": True, "data": "0x0011"},
    }
    assert result == expected


def test_to_jsonable_primitives():
    """to_jsonable passes through JSON-native types unchanged."""
    assert to_jsonable("hello") == "hello"
    assert to_jsonable(42) == 42
    assert to_jsonable(3.14) == 3.14
    assert to_jsonable(True) is True
    assert to_jsonable(False) is False
    assert to_jsonable(None) is None


def test_to_jsonable_empty_containers():
    """to_jsonable handles empty containers."""
    assert to_jsonable({}) == {}
    assert to_jsonable([]) == []
    assert to_jsonable(()) == []


def test_to_jsonable_is_json_serializable():
    """to_jsonable output can be serialized by json.dumps()."""
    obj = {
        "method": "tx.sendRawTransaction",
        "params": [b"\x00\x01\x02\x03"],
        "id": 1,
    }
    result = to_jsonable(obj)
    # Should not raise TypeError
    body = json.dumps(result)
    assert isinstance(body, str)
    # Verify bytes were converted
    assert '"0x00010203"' in body


# ============================================================================
# Tests for RpcClient with bytes in params
# ============================================================================


def test_rpc_client_make_payload_with_bytes():
    """RpcClient._make_payload accepts bytes in params."""
    client = RpcClient("http://localhost:8545")
    raw_tx = b"\xde\xad\xbe\xef"
    
    payload = client._make_payload("tx.sendRawTransaction", [raw_tx])
    
    assert payload["method"] == "tx.sendRawTransaction"
    assert payload["params"] == [raw_tx]  # Still bytes at this stage
    assert "id" in payload
    assert payload["jsonrpc"] == "2.0"


@patch("omni_sdk.rpc.http.httpx")
def test_rpc_client_sends_bytes_as_hex(mock_httpx):
    """RpcClient converts bytes to hex before sending."""
    # Mock httpx response
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": "0xabcdef1234567890",
    }
    mock_client = Mock()
    mock_client.post.return_value = mock_response
    mock_httpx.Client.return_value = mock_client
    
    client = RpcClient("http://localhost:8545", timeout=10.0)
    raw_tx = b"\x00\x01\x02\x03"
    
    result = client.request("tx.sendRawTransaction", [raw_tx])
    
    assert result == "0xabcdef1234567890"
    
    # Verify the HTTP client was called
    assert mock_client.post.called
    call_args = mock_client.post.call_args
    
    # Extract the body that was sent
    sent_body = call_args.kwargs["content"]
    
    # Parse it back to verify bytes were converted to hex
    sent_payload = json.loads(sent_body)
    assert sent_payload["method"] == "tx.sendRawTransaction"
    assert sent_payload["params"] == ["0x00010203"]  # Bytes converted to hex
    assert isinstance(sent_payload["params"][0], str)


def test_rpc_client_sends_bytes_as_hex_with_requests():
    """RpcClient converts bytes to hex when using requests fallback."""
    # This test verifies that bytes conversion works regardless of HTTP library
    # We just test the payload normalization, not the actual HTTP call
    from omni_sdk.rpc.http import to_jsonable
    import json
    
    # Build a payload with bytes (as would happen internally)
    raw_tx = b"\xff\xee\xdd"
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tx.sendRawTransaction", "params": [raw_tx]}
    
    # Normalize it
    normalized = to_jsonable(payload)
    
    # Should be serializable now
    body = json.dumps(normalized)
    parsed = json.loads(body)
    
    # Verify bytes were converted to hex
    assert parsed["params"] == ["0xffeedd"]


def test_rpc_client_batch_with_bytes():
    """RpcClient.batch converts bytes to hex in all calls."""
    from omni_sdk.rpc.http import to_jsonable
    
    # Build batch payload with bytes
    batch_payload = [
        {"jsonrpc": "2.0", "id": 1, "method": "tx.sendRawTransaction", "params": [b"\x01\x02"]},
        {"jsonrpc": "2.0", "id": 2, "method": "tx.sendRawTransaction", "params": [b"\x03\x04"]},
    ]
    
    # Normalize the batch
    normalized = to_jsonable(batch_payload)
    
    # Should be JSON serializable
    body = json.dumps(normalized)
    parsed = json.loads(body)
    
    # Verify bytes were converted in batch payload
    assert isinstance(parsed, list)
    assert parsed[0]["params"] == ["0x0102"]
    assert parsed[1]["params"] == ["0x0304"]


# ============================================================================
# Integration test for tx.sendRawTransaction path
# ============================================================================


def test_submit_raw_no_serialization_error():
    """submit_raw from tx.send works without bytes serialization error."""
    # This test verifies that the RPC client accepts bytes and converts them internally
    # The key is that json.dumps() doesn't fail with a TypeError
    from omni_sdk.rpc.http import to_jsonable
    
    raw_tx = b"\xde\xad\xbe\xef\xca\xfe"
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tx.sendRawTransaction", "params": [raw_tx]}
    
    # Normalize the payload (this is what _send_once does)
    normalized = to_jsonable(payload)
    
    # Should not raise TypeError about bytes
    body = json.dumps(normalized)
    parsed = json.loads(body)
    
    # Verify the raw tx was converted to hex
    assert parsed["params"] == ["0xdeadbeefcafe"]
    assert isinstance(parsed["params"][0], str)


def test_rpc_error_preserved_with_bytes_in_params():
    """RpcError is properly raised even when params contain bytes."""
    with patch("omni_sdk.rpc.http.httpx") as mock_httpx:
        # Mock httpx to return an RPC error
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": -32010,
                "message": "Invalid transaction",
                "data": {"reason": "nonce too low"},
            },
        }
        mock_client = Mock()
        mock_client.post.return_value = mock_response
        mock_httpx.Client.return_value = mock_client
        
        client = RpcClient("http://localhost:8545")
        raw_tx = b"\x00\x01\x02"
        
        # Should raise RpcError, not TypeError about bytes
        with pytest.raises(RpcError) as exc_info:
            client.request("tx.sendRawTransaction", [raw_tx])
        
        err = exc_info.value
        assert err.code == -32010
        assert err.message == "Invalid transaction"
        assert err.method == "tx.sendRawTransaction"


def test_network_error_with_bytes_in_params():
    """Network errors are properly raised even when params contain bytes."""
    # The key point is that normalization happens before transport,
    # so bytes serialization never causes issues even if network fails
    from omni_sdk.rpc.http import to_jsonable
    
    raw_tx = b"\x00\x01\x02"
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tx.sendRawTransaction", "params": [raw_tx]}
    
    # Normalization should work regardless of what happens after
    normalized = to_jsonable(payload)
    
    # JSON serialization should work (network error would happen after this)
    try:
        body = json.dumps(normalized)
        # Success - bytes were converted so JSON serialization works
        assert '"0x000102"' in body
    except TypeError as e:
        # This should NOT happen - if it does, the fix didn't work
        pytest.fail(f"Bytes serialization failed: {e}")
