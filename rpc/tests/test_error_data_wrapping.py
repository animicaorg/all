"""
Test that RpcError subclasses correctly handle the data parameter.

This is a regression test for the bug where using `data=` keyword argument
would cause double-wrapping: {'data': {'mempoolError': ...}} instead of
{'mempoolError': ...}.
"""
from rpc import errors as rpc_errors


def test_invalid_tx_with_data_keyword():
    """Test InvalidTx with data= keyword argument (single key pattern)."""
    reject = {
        "code": 2999,
        "reason": "internal_error",
        "message": "mempool admission failed",
        "hint": "check node logs",
        "context": {"tx_hash": "0xtest", "error_class": "TypeError"}
    }
    
    try:
        raise rpc_errors.InvalidTx(
            f"mempool admission failed: {reject.get('reason','admission_failed')}",
            data={"mempoolError": reject},
        )
    except rpc_errors.RpcError as e:
        # The data should be {'mempoolError': ...}, NOT {'data': {'mempoolError': ...}}
        assert e.data is not None
        assert "mempoolError" in e.data, f"Expected 'mempoolError' key, got: {list(e.data.keys())}"
        assert "data" not in e.data, f"Should not have 'data' key (double-wrapped), got: {list(e.data.keys())}"
        
        # Verify to_dict() also doesn't double-wrap
        err_dict = e.to_dict()
        assert "data" in err_dict
        data_field = err_dict["data"]
        assert "mempoolError" in data_field, f"Expected 'mempoolError' in data field, got: {list(data_field.keys())}"
        assert "data" not in data_field or data_field["data"] is None, f"Should not have nested 'data' key, got: {list(data_field.keys())}"


def test_invalid_tx_with_multiple_kwargs():
    """Test InvalidTx with multiple kwargs (unpacked dict pattern)."""
    try:
        raise rpc_errors.InvalidTx(
            "decode failed",
            kind="decode",
            cause="TypeError",
            where="_decode_tx",
            hint="Check format",
        )
    except rpc_errors.RpcError as e:
        # All keys should be present at the top level
        assert e.data is not None
        assert "kind" in e.data
        assert "cause" in e.data
        assert "where" in e.data
        assert "hint" in e.data
        assert e.data["kind"] == "decode"
        assert e.data["cause"] == "TypeError"


def test_parse_error_with_data():
    """Test ParseError with data= keyword."""
    try:
        raise rpc_errors.ParseError("malformed JSON", data={"line": 42, "column": 5})
    except rpc_errors.RpcError as e:
        assert e.data is not None
        assert "line" in e.data
        assert "column" in e.data
        assert "data" not in e.data  # Should not be double-wrapped


def test_internal_error_with_data():
    """Test InternalError with data= keyword."""
    try:
        raise rpc_errors.InternalError("database error", data={"error": "timeout"})
    except rpc_errors.RpcError as e:
        assert e.data is not None
        assert "error" in e.data
        assert e.data["error"] == "timeout"
        assert "data" not in e.data


def test_rate_limited_with_data():
    """Test RateLimited with data= keyword and retry_after_ms."""
    try:
        raise rpc_errors.RateLimited(retry_after_ms=5000, data={"ip": "1.2.3.4"})
    except rpc_errors.RpcError as e:
        assert e.data is not None
        assert "retryAfterMs" in e.data
        assert e.data["retryAfterMs"] == 5000
        assert "ip" in e.data
        assert e.data["ip"] == "1.2.3.4"
        assert "data" not in e.data


def test_rate_limited_retry_only():
    """Test RateLimited with only retry_after_ms."""
    try:
        raise rpc_errors.RateLimited(retry_after_ms=5000)
    except rpc_errors.RpcError as e:
        assert e.data is not None
        assert "retryAfterMs" in e.data
        assert e.data["retryAfterMs"] == 5000
        assert len(e.data) == 1  # Only retryAfterMs


def test_rate_limited_no_params():
    """Test RateLimited with no parameters."""
    try:
        raise rpc_errors.RateLimited()
    except rpc_errors.RpcError as e:
        # Should have None data since no retry_after_ms and no data kwargs
        assert e.data is None


def test_bad_signature_with_multiple_kwargs():
    """Test BadSignature with unpacked kwargs."""
    try:
        raise rpc_errors.BadSignature(
            "signature verification failed",
            scheme="dilithium3",
            expected_len=2420,
            got_len=100,
        )
    except rpc_errors.RpcError as e:
        assert e.data is not None
        assert "scheme" in e.data
        assert "expected_len" in e.data
        assert "got_len" in e.data
        assert e.data["scheme"] == "dilithium3"


def test_not_found_with_data():
    """Test NotFound with data= keyword."""
    try:
        raise rpc_errors.NotFound("block", data={"height": 12345})
    except rpc_errors.RpcError as e:
        assert e.data is not None
        assert "height" in e.data
        assert e.data["height"] == 12345
        assert "data" not in e.data


def test_empty_data_kwargs():
    """Test that empty kwargs work correctly."""
    try:
        raise rpc_errors.InvalidTx("test error")
    except rpc_errors.RpcError as e:
        # Empty kwargs should result in None data
        assert e.data is None


def test_chain_id_mismatch_no_kwargs():
    """Test errors that don't use **data pattern still work."""
    try:
        raise rpc_errors.ChainIdMismatch(got=1, expected=1337)
    except rpc_errors.RpcError as e:
        assert e.data is not None
        assert "got" in e.data
        assert "expected" in e.data
        assert e.data["got"] == 1
        assert e.data["expected"] == 1337


def test_backwards_compatibility_no_data_key():
    """Test that calling without 'data' key still works (multiple keys)."""
    try:
        # This simulates the pattern: raise InvalidTx(..., **{'kind': 'x', 'cause': 'y'})
        raise rpc_errors.InvalidTx(
            "error",
            kind="validation",
            message="failed",
        )
    except rpc_errors.RpcError as e:
        assert e.data is not None
        assert "kind" in e.data
        assert "message" in e.data
        assert e.data["kind"] == "validation"
        assert e.data["message"] == "failed"
        # Should NOT have a 'data' key since we didn't pass one
        assert "data" not in e.data
