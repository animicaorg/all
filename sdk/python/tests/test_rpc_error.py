"""
Tests for RpcError construction, formatting, and usage.

Validates that:
- RpcError can be constructed with code and message (method optional)
- RpcError includes method in string representation when provided
- RpcError works in both http and tx.send contexts
- from_jsonrpc_error properly constructs RpcError from JSON-RPC error objects
"""

import pytest
from omni_sdk.errors import RpcError, from_jsonrpc_error, JsonRpcCode


def test_rpc_error_construction_minimal():
    """RpcError can be constructed with just code and message."""
    err = RpcError(code=-32603, message="Internal error")
    assert err.code == -32603
    assert err.message == "Internal error"
    assert err.method is None
    assert err.data is None


def test_rpc_error_construction_with_method():
    """RpcError can include method name."""
    err = RpcError(code=-32098, message="Request failed", method="tx.sendRawTransaction")
    assert err.code == -32098
    assert err.message == "Request failed"
    assert err.method == "tx.sendRawTransaction"
    assert err.data is None


def test_rpc_error_construction_with_all_fields():
    """RpcError can include all fields."""
    err = RpcError(
        code=-32011,
        message="Chain ID mismatch",
        method="tx.sendRawTransaction",
        data={"got": 1337, "expected": 31337},
        request_id=42,
        http_status=400,
    )
    assert err.code == -32011
    assert err.message == "Chain ID mismatch"
    assert err.method == "tx.sendRawTransaction"
    assert err.data == {"got": 1337, "expected": 31337}
    assert err.request_id == 42
    assert err.http_status == 400


def test_rpc_error_string_representation_with_method():
    """String representation includes method when present."""
    err = RpcError(
        code=-32098,
        message="Network timeout",
        method="tx.sendRawTransaction",
        data="Connection reset",
    )
    s = str(err)
    assert "tx.sendRawTransaction" in s
    assert "-32098" in s or "32098" in s
    assert "Network timeout" in s
    assert "Connection reset" in s


def test_rpc_error_string_representation_without_method():
    """String representation handles missing method gracefully."""
    err = RpcError(code=-32603, message="Internal error", data="Stack trace here")
    s = str(err)
    # Should show '-' or similar placeholder for method
    assert "RPC[-]" in s or "RPC[" in s
    assert "-32603" in s or "32603" in s
    assert "Internal error" in s


def test_from_jsonrpc_error_basic():
    """from_jsonrpc_error converts JSON-RPC error object to RpcError."""
    err_obj = {"code": -32600, "message": "Invalid Request"}
    err = from_jsonrpc_error(err_obj)
    assert err.code == -32600
    assert err.message == "Invalid Request"
    assert err.method is None
    assert err.data is None


def test_from_jsonrpc_error_with_data():
    """from_jsonrpc_error preserves data field."""
    err_obj = {
        "code": -32010,
        "message": "Invalid transaction",
        "data": {"reason": "nonce too low", "got": 5, "expected": 10},
    }
    err = from_jsonrpc_error(err_obj)
    assert err.code == -32010
    assert err.message == "Invalid transaction"
    assert err.data == {"reason": "nonce too low", "got": 5, "expected": 10}


def test_from_jsonrpc_error_with_method():
    """from_jsonrpc_error accepts method parameter."""
    err_obj = {"code": -32098, "message": "Transport failed"}
    err = from_jsonrpc_error(err_obj, method="tx.sendRawTransaction")
    assert err.code == -32098
    assert err.message == "Transport failed"
    assert err.method == "tx.sendRawTransaction"


def test_from_jsonrpc_error_with_request_id_and_http_status():
    """from_jsonrpc_error preserves request_id and http_status."""
    err_obj = {"code": -32601, "message": "Method not found"}
    err = from_jsonrpc_error(
        err_obj, method="unknown.method", request_id=123, http_status=404
    )
    assert err.code == -32601
    assert err.message == "Method not found"
    assert err.method == "unknown.method"
    assert err.request_id == 123
    assert err.http_status == 404


def test_from_jsonrpc_error_handles_missing_fields():
    """from_jsonrpc_error provides defaults for missing code/message."""
    err_obj = {}
    err = from_jsonrpc_error(err_obj)
    assert err.code == JsonRpcCode.SERVER_ERROR  # default
    assert err.message == "Unknown JSON-RPC error"  # default


def test_rpc_error_code_enum():
    """RpcError.code_enum returns JsonRpcCode enum when code is known."""
    err = RpcError(code=-32600, message="Invalid Request")
    assert err.code_enum == JsonRpcCode.INVALID_REQUEST

    err2 = RpcError(code=-32603, message="Internal error")
    assert err2.code_enum == JsonRpcCode.INTERNAL_ERROR


def test_rpc_error_code_enum_unknown():
    """RpcError.code_enum returns None for unknown codes."""
    err = RpcError(code=-32098, message="Custom error")
    assert err.code_enum is None


def test_rpc_error_as_exception():
    """RpcError can be raised and caught as an exception."""
    with pytest.raises(RpcError) as exc_info:
        raise RpcError(
            code=-32011,
            message="Chain ID mismatch",
            method="tx.sendRawTransaction",
            data={"got": 1, "expected": 31337},
        )
    err = exc_info.value
    assert err.code == -32011
    assert err.message == "Chain ID mismatch"
    assert err.method == "tx.sendRawTransaction"
    assert err.data["got"] == 1
