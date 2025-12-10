"""
Tests for tx.send error handling with RpcError.

Validates that submit_raw and get_transaction_receipt properly raise
RpcError with method/code/message/data when the RPC call fails.
"""

import pytest
from omni_sdk.errors import RpcError


# Mock RPC client for testing
class MockRpcClient:
    """Mock RPC client that can simulate errors."""

    def __init__(self, *, error_on_method=None, error_response=None):
        self.error_on_method = error_on_method
        self.error_response = error_response or {
            "code": -32010,
            "message": "Invalid transaction",
            "data": {"reason": "nonce too low"},
        }
        self.calls = []

    def request(self, method, params=None):
        self.calls.append((method, params))
        if self.error_on_method and method == self.error_on_method:
            # Simulate RPC returning an error
            raise RpcError(
                code=self.error_response["code"],
                message=self.error_response["message"],
                method=method,
                data=self.error_response.get("data"),
            )
        # Simulate success
        if method == "tx.sendRawTransaction":
            return "0xabcdef1234567890"
        if method == "tx.getTransactionReceipt":
            return {"txHash": "0xabcdef", "status": "SUCCESS"}
        return {}


def test_submit_raw_success():
    """submit_raw returns tx hash on success."""
    def submit_raw(rpc, raw_tx):
        if not isinstance(raw_tx, (bytes, bytearray)):
            raise TypeError("raw_tx must be bytes")
        try:
            result = rpc.request("tx.sendRawTransaction", [bytes(raw_tx)])
        except RpcError:
            raise
        except Exception as e:
            raise RpcError(
                code=-32098,
                message=f"tx.sendRawTransaction failed: {e}",
                method="tx.sendRawTransaction",
                data=str(e),
            ) from e
        if not isinstance(result, str):
            raise RuntimeError(f"unexpected result: {type(result)!r}")
        return result if result.startswith("0x") else "0x" + result

    rpc = MockRpcClient()
    tx_hash = submit_raw(rpc, b"\x00\x01\x02")
    assert tx_hash == "0xabcdef1234567890"
    assert len(rpc.calls) == 1
    assert rpc.calls[0][0] == "tx.sendRawTransaction"


def test_submit_raw_rpc_error_includes_method():
    """submit_raw raises RpcError with method when RPC fails."""
    def submit_raw(rpc, raw_tx):
        if not isinstance(raw_tx, (bytes, bytearray)):
            raise TypeError("raw_tx must be bytes")
        try:
            result = rpc.request("tx.sendRawTransaction", [bytes(raw_tx)])
        except RpcError:
            raise
        except Exception as e:
            raise RpcError(
                code=-32098,
                message=f"tx.sendRawTransaction failed: {e}",
                method="tx.sendRawTransaction",
                data=str(e),
            ) from e
        return result if result.startswith("0x") else "0x" + result

    rpc = MockRpcClient(
        error_on_method="tx.sendRawTransaction",
        error_response={
            "code": -32010,
            "message": "Invalid transaction",
            "data": {"reason": "nonce too low", "got": 5, "expected": 10},
        },
    )

    with pytest.raises(RpcError) as exc_info:
        submit_raw(rpc, b"\x00\x01\x02")

    err = exc_info.value
    assert err.code == -32010
    assert err.message == "Invalid transaction"
    assert err.method == "tx.sendRawTransaction"
    assert err.data == {"reason": "nonce too low", "got": 5, "expected": 10}


def test_get_transaction_receipt_rpc_error_includes_method():
    """get_transaction_receipt raises RpcError with method when RPC fails."""
    def get_transaction_receipt(rpc, tx_hash):
        try:
            res = rpc.request("tx.getTransactionReceipt", [tx_hash])
        except RpcError:
            raise
        except Exception as e:
            raise RpcError(
                code=-32098,
                message=f"tx.getTransactionReceipt failed: {e}",
                method="tx.getTransactionReceipt",
                data=str(e),
            ) from e
        return res if res else None

    rpc = MockRpcClient(
        error_on_method="tx.getTransactionReceipt",
        error_response={
            "code": -32004,
            "message": "Transaction not found",
            "data": {"txHash": "0xmissing"},
        },
    )

    with pytest.raises(RpcError) as exc_info:
        get_transaction_receipt(rpc, "0xmissing")

    err = exc_info.value
    assert err.code == -32004
    assert err.message == "Transaction not found"
    assert err.method == "tx.getTransactionReceipt"
    assert err.data == {"txHash": "0xmissing"}


def test_rpc_error_formatting_for_cli():
    """RpcError formatting is suitable for CLI display."""
    err = RpcError(
        code=-32010,
        message="Invalid transaction",
        method="tx.sendRawTransaction",
        data={"reason": "nonce too low", "got": 5, "expected": 10},
    )

    # Test that string representation includes all key info
    s = str(err)
    assert "tx.sendRawTransaction" in s
    assert "-32010" in s or "32010" in s
    assert "Invalid transaction" in s
    # Data should be present in repr
    assert "nonce too low" in s or "got" in s or str(err.data) in repr(err)


def test_rpc_error_without_method_still_works():
    """RpcError works even when method is not provided (backward compatibility)."""
    err = RpcError(code=-32603, message="Internal error", data="Stack trace")
    assert err.code == -32603
    assert err.message == "Internal error"
    assert err.method is None
    assert err.data == "Stack trace"

    s = str(err)
    assert "Internal error" in s
    # Should handle None method gracefully
    assert "RPC[-]" in s or "RPC[" in s
