"""
Tests for faucet RPC methods
=============================

Tests cover:
  - Faucet success on devnet (chainId=1337)
  - Faucet success on testnet (chainId=2)
  - Faucet rejection on mainnet (chainId=1)
  - Default amount handling
  - Custom amount handling
  - Address validation
  - Balance updates
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rpc import config as rpc_config
from rpc import server as rpc_server
from rpc.tests import rpc_call


@pytest.fixture
def devnet_client(tmp_path):
    """Create a test client with devnet chain ID."""
    db_uri = f"sqlite:///{tmp_path}/devnet.db"
    cfg = rpc_config.Config(
        host="127.0.0.1",
        port=0,
        db_uri=db_uri,
        chain_id=1337,  # devnet
        logging="ERROR",
    )
    app = rpc_server.create_app(cfg)
    rpc_server.deps.ensure_started(cfg)
    return TestClient(app)


@pytest.fixture
def testnet_client(tmp_path):
    """Create a test client with testnet chain ID."""
    db_uri = f"sqlite:///{tmp_path}/testnet.db"
    cfg = rpc_config.Config(
        host="127.0.0.1",
        port=0,
        db_uri=db_uri,
        chain_id=2,  # testnet
        logging="ERROR",
    )
    app = rpc_server.create_app(cfg)
    rpc_server.deps.ensure_started(cfg)
    return TestClient(app)


@pytest.fixture
def mainnet_client(tmp_path):
    """Create a test client with mainnet chain ID."""
    db_uri = f"sqlite:///{tmp_path}/mainnet.db"
    cfg = rpc_config.Config(
        host="127.0.0.1",
        port=0,
        db_uri=db_uri,
        chain_id=1,  # mainnet
        logging="ERROR",
    )
    app = rpc_server.create_app(cfg)
    rpc_server.deps.ensure_started(cfg)
    return TestClient(app)


def test_faucet_success_on_devnet(devnet_client):
    """Test faucet works on devnet (chainId=1337)."""
    client = devnet_client
    
    # Test with a valid address
    result = rpc_call(
        client,
        "faucet.request",
        {"address": "0x1234567890abcdef1234567890abcdef12345678"}
    )
    
    assert "result" in result
    res = result["result"]
    assert "address" in res
    assert "amount" in res
    assert "balance" in res
    assert "message" in res
    
    # Verify default amount (500M ANM = 500000000000000000)
    amount_hex = res["amount"]
    amount_int = int(amount_hex, 16) if amount_hex.startswith("0x") else int(amount_hex)
    assert amount_int == 500_000_000_000_000_000


def test_faucet_success_on_testnet(testnet_client):
    """Test faucet works on testnet (chainId=2)."""
    client = testnet_client
    
    # Test with a valid address
    result = rpc_call(
        client,
        "faucet.request",
        {"address": "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"}
    )
    
    assert "result" in result
    res = result["result"]
    assert res["address"] == "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"


def test_faucet_rejected_on_mainnet(mainnet_client):
    """Test faucet is rejected on mainnet (chainId=1)."""
    client = mainnet_client
    
    # Try to use faucet on mainnet - should fail
    result = rpc_call(
        client,
        "faucet.request",
        {"address": "0x1234567890abcdef1234567890abcdef12345678"},
        expect_error=True
    )
    
    assert "error" in result
    error = result["error"]
    assert "message" in error
    assert "mainnet" in error["message"].lower()
    assert error.get("code") == -32600  # Invalid Request


def test_faucet_custom_amount(devnet_client):
    """Test faucet with custom amount."""
    client = devnet_client
    
    custom_amount = 1_000_000_000_000_000  # 1M ANM
    
    result = rpc_call(
        client,
        "faucet.request",
        {
            "address": "0x1234567890abcdef1234567890abcdef12345678",
            "amount": custom_amount
        }
    )
    
    assert "result" in result
    res = result["result"]
    amount_hex = res["amount"]
    amount_int = int(amount_hex, 16) if amount_hex.startswith("0x") else int(amount_hex)
    assert amount_int == custom_amount


def test_faucet_balance_increases(devnet_client):
    """Test that faucet increases balance correctly."""
    client = devnet_client
    
    address = "0x1234567890abcdef1234567890abcdef12345678"
    
    # First request
    result1 = rpc_call(client, "faucet.request", {"address": address})
    balance1_hex = result1["result"]["balance"]
    balance1 = int(balance1_hex, 16) if balance1_hex.startswith("0x") else int(balance1_hex)
    
    # Second request
    result2 = rpc_call(client, "faucet.request", {"address": address})
    balance2_hex = result2["result"]["balance"]
    balance2 = int(balance2_hex, 16) if balance2_hex.startswith("0x") else int(balance2_hex)
    
    # Balance should have increased
    assert balance2 > balance1
    assert balance2 == balance1 + 500_000_000_000_000_000


def test_faucet_invalid_address(devnet_client):
    """Test faucet rejects invalid addresses."""
    client = devnet_client
    
    # Try with invalid address
    result = rpc_call(
        client,
        "faucet.request",
        {"address": "not-a-valid-address"},
        expect_error=True
    )
    
    assert "error" in result


def test_faucet_negative_amount(devnet_client):
    """Test faucet rejects negative amounts."""
    client = devnet_client
    
    # Try with negative amount
    result = rpc_call(
        client,
        "faucet.request",
        {
            "address": "0x1234567890abcdef1234567890abcdef12345678",
            "amount": -1000
        },
        expect_error=True
    )
    
    assert "error" in result
