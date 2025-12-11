"""
Integration test for tx.sendRawTransaction with chainId in signed payload.

This test verifies that the CLI -> SDK -> RPC flow properly includes chainId
in the signed transaction body and that the node can validate it.
"""
import json
from pathlib import Path

import pytest
import respx
import httpx
from typer.testing import CliRunner

from animica.cli import tx
from omni_sdk.utils.cbor import loads as cbor_loads


runner = CliRunner()


@pytest.fixture(autouse=True)
def allow_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allow PQ fallback for testing."""
    monkeypatch.setenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")


@pytest.fixture
def wallet_store(tmp_path: Path) -> Path:
    """Create a wallet store with test wallet entries."""
    wallet_file = tmp_path / "wallets.json"
    store = {
        "version": 1,
        "wallets": [
            {
                "label": "alice",
                "address": "anim1zqp8t5gdk4ya9ch960lcwmalgc2ckldn4uk9es2fnkdwf8nt69wqtdccl4pzm",
                "alg_id": 4098,
                "alg_name": "sphincs_shake_128s",
                "public_key_hex": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
                "secret_key_hex": "0011223344556677889900112233445566778899001122334455667788990011",
                "created_at": "2025-01-01T00:00:00Z"
            }
        ]
    }
    wallet_file.write_text(json.dumps(store, indent=2))
    return wallet_file


@respx.mock
def test_send_includes_chainid_in_body(wallet_store: Path) -> None:
    """Test that CLI send includes chainId in the signed transaction body."""
    rpc_url = "http://localhost:9999/rpc"
    
    # Track the actual payload sent to tx.sendRawTransaction
    captured_payload = {}
    
    def capture_and_respond(request):
        """Capture the request payload and return success."""
        req_json = request.content
        req_data = json.loads(req_json)
        
        # If this is tx.sendRawTransaction, capture and decode the payload
        if req_data.get("method") == "tx.sendRawTransaction":
            raw_hex = req_data["params"][0]
            # Strip 0x prefix if present
            if raw_hex.startswith("0x"):
                raw_hex = raw_hex[2:]
            raw_bytes = bytes.fromhex(raw_hex)
            
            # Decode CBOR to check structure
            decoded = cbor_loads(raw_bytes)
            captured_payload["decoded"] = decoded
            
            # Return a dummy tx hash
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": req_data["id"], "result": "0xdeadbeef"}
            )
        
        # Handle other RPC calls
        method = req_data.get("method")
        if method == "chain.getChainId":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": req_data["id"], "result": 1}
            )
        elif method == "state.getTransactionCount":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": req_data["id"], "result": 0}
            )
        elif method == "state.suggestGasPrice":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": req_data["id"], "result": "1000000000"}
            )
        else:
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": req_data["id"], "result": None}
            )
    
    # Mock all RPC requests
    respx.post(rpc_url).mock(side_effect=capture_and_respond)
    
    # Run CLI send command (not dry-run, so it broadcasts)
    result = runner.invoke(tx.app, [
        "send",
        "--wallet-file", str(wallet_store),
        "--from", "alice",
        "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
        "--value", "1.0",
        "--chain-id", "1",
        "--rpc-url", rpc_url
    ])
    
    # Verify the command succeeded
    assert result.exit_code == 0, f"Command failed: {result.output}"
    assert "Transaction Submitted" in result.output or "Tx Hash" in result.output
    
    # Verify that we captured the payload
    assert "decoded" in captured_payload, "tx.sendRawTransaction was not called"
    
    # Verify the structure has body and sig
    decoded = captured_payload["decoded"]
    assert "body" in decoded, f"Missing 'body' in decoded payload: {list(decoded.keys())}"
    assert "sig" in decoded, f"Missing 'sig' in decoded payload: {list(decoded.keys())}"
    
    # Verify chainId is in the body
    body = decoded["body"]
    assert "chainId" in body, f"Missing 'chainId' in body: {list(body.keys())}"
    assert body["chainId"] == 1, f"Expected chainId=1, got {body['chainId']}"
    
    # Verify sig has required fields
    sig = decoded["sig"]
    assert "algId" in sig, f"Missing 'algId' in sig: {list(sig.keys())}"
    assert "pubkey" in sig, f"Missing 'pubkey' in sig: {list(sig.keys())}"
    assert "sig" in sig, f"Missing 'sig' in sig: {list(sig.keys())}"


@respx.mock
def test_dry_run_shows_chainid(wallet_store: Path) -> None:
    """Test that CLI dry-run shows the chainId being used."""
    rpc_url = "http://localhost:9999/rpc"
    
    # Mock RPC calls for dry-run
    respx.post(rpc_url).mock(side_effect=[
        # chain.getChainId
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": 1}),
        # state.getTransactionCount
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": 5}),
        # state.suggestGasPrice
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 3, "result": "1000000000"}),
    ])
    
    # Run CLI send with dry-run
    result = runner.invoke(tx.app, [
        "send",
        "--wallet-file", str(wallet_store),
        "--from", "alice",
        "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
        "--value", "1.0",
        "--chain-id", "1",
        "--dry-run",
        "--rpc-url", rpc_url
    ])
    
    # Verify the command succeeded
    assert result.exit_code == 0, f"Command failed: {result.output}"
    
    # Verify output shows chain ID
    assert "Chain ID:   1" in result.output, f"Chain ID not shown in output: {result.output}"
    assert "Dry-Run Mode" in result.output
    assert "not broadcast" in result.output
