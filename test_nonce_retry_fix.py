"""
Test that CLI properly retries transactions with correct nonce after nonce_too_low errors.

This test validates:
1. CLI extracts expected_nonce from mempoolError wrapper
2. CLI retries with the correct nonce from the error
3. RPC properly wraps mempool NonceTooLow/NonceGap errors
"""

from __future__ import annotations

import json
from contextlib import nullcontext
from unittest.mock import Mock, patch

import cbor2
import pytest
from typer.testing import CliRunner

from animica.cli import tx
from mempool.errors import NonceGap, NonceTooLow
from rpc.errors import to_error


def test_cli_extracts_nonce_from_mempool_error():
    """Test that _extract_nonce_mismatch handles mempoolError wrapper."""
    # Simulate the RPC error data structure with mempoolError wrapper
    error_data = {
        "mempoolError": {
            "code": 1005,
            "reason": "nonce_too_low",
            "message": "nonce too low: expected 10, got 8",
            "context": {
                "sender": "0x1234",
                "tx_hash": "0xabcd",
                "expected_nonce": 10,
                "got_nonce": 8,
            },
        }
    }
    
    reason, expected, got = tx._extract_nonce_mismatch(error_data, verbose=False)
    
    assert reason == "nonce_too_low"
    assert expected == 10
    assert got == 8


def test_cli_extracts_nonce_from_direct_context():
    """Test that _extract_nonce_mismatch handles direct context (mempool.getStatus)."""
    error_data = {
        "reason": "nonce_gap",
        "expected": 15,
        "got": 20,
    }
    
    reason, expected, got = tx._extract_nonce_mismatch(error_data, verbose=False)
    
    assert reason == "nonce_gap"
    assert expected == 15
    assert got == 20


def test_mempool_error_to_rpc_preserves_context():
    """Test that RPC layer properly wraps mempool errors with context."""
    # Create a NonceTooLow error
    exc = NonceTooLow(
        expected_nonce=10,
        got_nonce=8,
        sender="0x1234",
        tx_hash="0xabcd",
    )
    
    # Convert to RPC error
    rpc_error = to_error(exc)
    
    # Verify the structure
    assert rpc_error.data is not None
    assert "mempoolError" in rpc_error.data
    mempool_error = rpc_error.data["mempoolError"]
    assert mempool_error["reason"] == "nonce_too_low"
    assert mempool_error["context"]["expected_nonce"] == 10
    assert mempool_error["context"]["got_nonce"] == 8


def test_nonce_gap_error_uses_pending_next():
    """Test that NonceGap error uses pending_next as expected_nonce."""
    # When there's a gap, the expected nonce should be the next pending nonce
    exc = NonceGap(
        expected_nonce=15,  # This is pending_next (the next available slot)
        got_nonce=20,      # Transaction tried to use nonce 20
        sender="0x1234",
        tx_hash="0xabcd",
    )
    
    # Convert to RPC error
    rpc_error = to_error(exc)
    
    # Verify the expected_nonce is what we should retry with
    mempool_error = rpc_error.data["mempoolError"]
    assert mempool_error["context"]["expected_nonce"] == 15
    assert mempool_error["context"]["got_nonce"] == 20


def test_cli_send_retries_with_expected_nonce():
    """Test full CLI tx send with retry using expected nonce from error."""
    runner = CliRunner()
    nonces_used = []
    rpc_calls = []
    
    def fake_rpc(_url: str, method: str, params):
        rpc_calls.append((method, params))
        
        if method == "sync.getStatus":
            return {"synchronized": True}
        
        if method == "chain.getChainIdentity":
            return {"chainId": 1337, "forkId": None}
        
        # First call to getNextNonce returns 10
        # Second call (after retry) returns 10 (should use expected from error)
        if method in {"state.getNextNonce", "state_getNextNonce"}:
            return 10
        
        if method in {"tx.gasPrice", "gasPrice", "fee.getGasPrice"}:
            return 1
        
        if method == "tx.sendRawTransaction":
            raw_hex = params[0]
            raw_bytes = bytes.fromhex(raw_hex[2:] if raw_hex.startswith("0x") else raw_hex)
            decoded = cbor2.loads(raw_bytes)
            nonce = int(decoded["body"]["nonce"])
            nonces_used.append(nonce)
            
            # First submission (nonce=10) returns error with expected_nonce=11
            if nonce == 10:
                # Simulate RPC error with mempoolError wrapper
                from animica.cli.tx import RpcError
                raise RpcError(
                    code=-32014,  # NONCE_TOO_LOW
                    message="nonce too low: expected 11, got 10",
                    data={
                        "mempoolError": {
                            "code": 1005,
                            "reason": "nonce_too_low",
                            "message": "nonce too low: expected 11, got 10",
                            "context": {
                                "expected_nonce": 11,
                                "got_nonce": 10,
                                "sender": "0x" + "11" * 32,
                                "tx_hash": "0xhash1",
                            },
                        }
                    },
                )
            
            # Second submission (nonce=11) succeeds
            return f"0xhash{nonce}"
        
        if method == "mempool.getStatus":
            # Second submission is in mempool
            if len(nonces_used) >= 2:
                return {"hash": params[0], "known": True, "state": "pending"}
            return {"hash": params[0], "known": False}
        
        return None
    
    class DummySig:
        alg_id = 1
        sig = b"\x01" * 64
    
    with patch.object(tx, "_rpc", fake_rpc):
        with patch.object(tx, "_load_wallet_entry", return_value={
            "public_key_hex": "11" * 32,
            "secret_key_hex": "22" * 32
        }):
            with patch.object(tx, "build_sign_bytes", return_value=b"signbytes"):
                with patch.object(tx, "pq_sign_detached", return_value=DummySig()):
                    with patch.object(tx, "verify_detached", return_value=True):
                        with patch.object(tx, "_nonce_lock", return_value=nullcontext()):
                            result = runner.invoke(
                                tx.app,
                                [
                                    "send",
                                    "--from", "0x" + "11" * 32,
                                    "--to", "0x" + "22" * 32,
                                    "--value-nanm", "1",
                                    "--rpc-url", "http://node",
                                ],
                            )
    
    # Check that the command succeeded
    assert result.exit_code == 0, f"CLI failed: {result.output}"
    
    # Verify retry happened with correct nonce
    assert "nonce mismatch" in result.output.lower() or "retrying" in result.output.lower()
    assert nonces_used == [10, 11], f"Expected nonces [10, 11], got {nonces_used}"
    
    print("✓ CLI properly extracted expected_nonce=11 from error and retried")
    print(f"✓ Nonces used: {nonces_used}")


if __name__ == "__main__":
    # Run the tests
    print("Testing nonce retry fix...\n")
    
    test_cli_extracts_nonce_from_mempool_error()
    print("✓ CLI extracts nonce from mempoolError wrapper")
    
    test_cli_extracts_nonce_from_direct_context()
    print("✓ CLI extracts nonce from direct context")
    
    test_mempool_error_to_rpc_preserves_context()
    print("✓ RPC properly wraps mempool errors")
    
    test_nonce_gap_error_uses_pending_next()
    print("✓ NonceGap uses pending_next as expected")
    
    test_cli_send_retries_with_expected_nonce()
    
    print("\n✅ All nonce retry tests passed!")
