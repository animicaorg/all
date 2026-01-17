"""
Test that wallet show correctly displays the height at which balance was queried.

This test verifies the fix for the issue where wallet show would display
misleading height information when the balance was queried at a different
height than what was displayed to the user.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pytest
from animica.cli import wallet
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def test_wallet_store(tmp_path: Path) -> Path:
    """Create a test wallet store with a single wallet entry."""
    wallet_file = tmp_path / "wallets.json"
    store = {
        "version": 1,
        "wallets": [
            {
                "label": "test",
                "address": "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz",
                "alg_id": 4098,
                "alg_name": "sphincs_shake_128s",
                "public_key_hex": "a1b2c3d4e5f6" * 8,
                "secret_key_hex": "0011223344556677" * 8,
                "created_at": "2026-01-01T00:00:00Z"
            }
        ]
    }
    wallet_file.write_text(json.dumps(store, indent=2))
    return wallet_file


def test_wallet_show_displays_balance_height_with_safe_and_tip(
    test_wallet_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that wallet show displays both safe and tip head correctly."""
    
    # Mock functions to return controlled values
    monkeypatch.setattr(wallet, "_wallet_file_path", lambda x: test_wallet_store)
    monkeypatch.setattr(wallet, "_resolve_rpc_url", lambda x: "http://127.0.0.1:8545")
    
    safe_height = 145
    best_block_height = 148
    tip_height = 150
    safe_balance = 1000000000
    tip_balance = 1100000000
    
    # Mock _get_head_info to return different heights for safe and tip
    call_count = {"count": 0}
    def mock_get_head_info(rpc_url: str, method: str) -> Optional[Dict[str, Any]]:
        call_count["count"] += 1
        if method == "chain.getSafeHead":
            return {"height": safe_height, "hash": "0x" + "a" * 64}
        elif method == "chain.getHead":
            return {"height": tip_height, "hash": "0x" + "b" * 64}
        return None
    
    monkeypatch.setattr(wallet, "_get_head_info", mock_get_head_info)
    monkeypatch.setattr(
        wallet,
        "_get_sync_status",
        lambda rpc_url: {
            "best_block_height": best_block_height,
            "best_block_hash": "0x" + "c" * 64,
            "best_header_height": tip_height,
        },
    )
    
    # Mock get_balance to return different balances for safe and latest
    def mock_get_balance(address: str, rpc_url: str, *, tag: str = "latest") -> int:
        if tag == "safe":
            return safe_balance
        elif tag == "latest":
            return tip_balance
        return 0
    
    monkeypatch.setattr(wallet, "get_balance", mock_get_balance)
    
    # Run wallet show with --include-tip flag
    result = runner.invoke(
        wallet.app,
        ["--wallet-file", str(test_wallet_store), "show", "test", "--include-tip"],
    )
    
    assert result.exit_code == 0, f"Command failed: {result.output}"
    
    # Parse the output
    data = json.loads(result.output)
    
    # Verify safe head is present and correct
    assert "safe_head" in data, "safe_head should be in output"
    assert data["safe_head"]["height"] == safe_height
    assert data["safe_head"]["hash"] == "0x" + "a" * 64
    
    # Verify tip head is present and correct
    assert "head" in data, "head (tip) should be in output"
    assert data["head"]["height"] == tip_height
    assert data["head"]["hash"] == "0x" + "b" * 64
    
    # Verify balance_confirmed has the height it was queried at
    assert data["balance_confirmed"] == safe_balance
    assert "balance_confirmed_height" in data, "balance_confirmed_height should be in output"
    assert data["balance_confirmed_height"] == best_block_height, \
        f"balance_confirmed_height should match best block height ({best_block_height})"
    
    # Verify balance_tip has the height it was queried at
    assert data["balance_tip"] == tip_balance
    assert "balance_tip_height" in data, "balance_tip_height should be in output"
    assert data["balance_tip_height"] == tip_height, \
        f"balance_tip_height should match tip head height ({tip_height})"
    
    # Verify the heights are different (demonstrating the fix)
    assert data["balance_confirmed_height"] != data["balance_tip_height"], \
        "Best block and tip heights should be different in this test"


def test_wallet_show_without_include_tip_shows_safe_only(
    test_wallet_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that wallet show without --include-tip only shows safe balance."""
    
    monkeypatch.setattr(wallet, "_wallet_file_path", lambda x: test_wallet_store)
    monkeypatch.setattr(wallet, "_resolve_rpc_url", lambda x: "http://127.0.0.1:8545")
    
    safe_height = 145
    best_block_height = 146
    tip_height = 150
    
    # Mock _get_head_info
    def mock_get_head_info(rpc_url: str, method: str) -> Optional[Dict[str, Any]]:
        if method == "chain.getSafeHead":
            return {"height": safe_height, "hash": "0x" + "a" * 64}
        elif method == "chain.getHead":
            return {"height": tip_height, "hash": "0x" + "b" * 64}
        return None
    
    monkeypatch.setattr(wallet, "_get_head_info", mock_get_head_info)
    monkeypatch.setattr(
        wallet,
        "_get_sync_status",
        lambda rpc_url: {
            "best_block_height": best_block_height,
            "best_block_hash": "0x" + "c" * 64,
            "best_header_height": tip_height,
        },
    )
    monkeypatch.setattr(wallet, "get_balance", lambda addr, url, tag="latest": 1000000000)
    
    # Run wallet show WITHOUT --include-tip flag
    result = runner.invoke(
        wallet.app,
        ["--wallet-file", str(test_wallet_store), "show", "test"],
    )
    
    assert result.exit_code == 0, f"Command failed: {result.output}"
    
    data = json.loads(result.output)
    
    # Verify safe head is present
    assert "safe_head" in data
    assert data["safe_head"]["height"] == safe_height
    
    # Verify tip head is also present (always fetched now for clarity)
    assert "head" in data
    assert data["head"]["height"] == tip_height
    
    # Verify balance_confirmed_height is present
    assert "balance_confirmed_height" in data
    assert data["balance_confirmed_height"] == best_block_height
    
    # Verify balance_tip is NOT present (not requested)
    assert "balance_tip" not in data
    assert "balance_tip_height" not in data


def test_wallet_show_fallback_when_safe_head_unavailable(
    test_wallet_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test backward compatibility when getSafeHead is not available."""
    
    monkeypatch.setattr(wallet, "_wallet_file_path", lambda x: test_wallet_store)
    monkeypatch.setattr(wallet, "_resolve_rpc_url", lambda x: "http://127.0.0.1:8545")
    
    tip_height = 150
    best_block_height = 149
    
    # Mock _get_head_info to simulate getSafeHead not available
    def mock_get_head_info(rpc_url: str, method: str) -> Optional[Dict[str, Any]]:
        if method == "chain.getSafeHead":
            # Not available
            return None
        elif method == "chain.getHead":
            return {"height": tip_height, "hash": "0x" + "b" * 64}
        return None
    
    monkeypatch.setattr(wallet, "_get_head_info", mock_get_head_info)
    monkeypatch.setattr(
        wallet,
        "_get_sync_status",
        lambda rpc_url: {
            "best_block_height": best_block_height,
            "best_block_hash": "0x" + "c" * 64,
            "best_header_height": tip_height,
        },
    )
    monkeypatch.setattr(wallet, "get_balance", lambda addr, url, tag="latest": 2000000000)
    
    # Run wallet show
    result = runner.invoke(
        wallet.app,
        ["--wallet-file", str(test_wallet_store), "show", "test"],
    )
    
    assert result.exit_code == 0, f"Command failed: {result.output}"
    
    data = json.loads(result.output)
    
    # When getSafeHead is not available, safe_head should fallback to tip
    assert "safe_head" in data
    assert data["safe_head"]["height"] == tip_height
    
    # Tip head should also be present
    assert "head" in data
    assert data["head"]["height"] == tip_height
    
    # Both should be the same in this case (backward compatibility)
    assert data["safe_head"]["height"] == data["head"]["height"]
    
    # balance_confirmed_height should still be present
    assert "balance_confirmed_height" in data
    assert data["balance_confirmed_height"] == best_block_height


def test_wallet_show_balance_height_matches_best_block(
    test_wallet_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that balance_confirmed_height always matches best block height."""
    
    monkeypatch.setattr(wallet, "_wallet_file_path", lambda x: test_wallet_store)
    monkeypatch.setattr(wallet, "_resolve_rpc_url", lambda x: "http://127.0.0.1:8545")
    
    safe_height = 200
    best_block_height = 205
    tip_height = 210
    
    def mock_get_head_info(rpc_url: str, method: str) -> Optional[Dict[str, Any]]:
        if method == "chain.getSafeHead":
            return {"height": safe_height, "hash": "0x" + "a" * 64}
        elif method == "chain.getHead":
            return {"height": tip_height, "hash": "0x" + "b" * 64}
        return None
    
    monkeypatch.setattr(wallet, "_get_head_info", mock_get_head_info)
    monkeypatch.setattr(
        wallet,
        "_get_sync_status",
        lambda rpc_url: {
            "best_block_height": best_block_height,
            "best_block_hash": "0x" + "c" * 64,
            "best_header_height": tip_height,
        },
    )
    monkeypatch.setattr(wallet, "get_balance", lambda addr, url, tag="latest": 5000000000)
    
    result = runner.invoke(
        wallet.app,
        ["--wallet-file", str(test_wallet_store), "show", "test"],
    )
    
    assert result.exit_code == 0
    data = json.loads(result.output)
    
    # This is the key assertion: balance_confirmed_height must match best block
    assert data["balance_confirmed_height"] == best_block_height
    
    # And it should NOT match the tip head height in this case
    assert data["balance_confirmed_height"] != data["head"]["height"]


def test_wallet_show_emits_sync_warning_when_headers_ahead(
    test_wallet_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that wallet show surfaces syncing warning when headers are ahead of blocks."""

    monkeypatch.setattr(wallet, "_wallet_file_path", lambda x: test_wallet_store)
    monkeypatch.setattr(wallet, "_resolve_rpc_url", lambda x: "http://127.0.0.1:8545")

    monkeypatch.setattr(
        wallet,
        "_get_sync_status",
        lambda rpc_url: {
            "best_block_height": 95,
            "best_block_hash": "0x" + "c" * 64,
            "best_header_height": 100,
        },
    )
    monkeypatch.setattr(
        wallet,
        "_get_head_info",
        lambda rpc_url, method: {"height": 100, "hash": "0x" + "b" * 64},
    )
    monkeypatch.setattr(wallet, "get_balance", lambda addr, url, tag="latest": 42)

    result = runner.invoke(
        wallet.app,
        ["--wallet-file", str(test_wallet_store), "show", "test"],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)

    assert data["node_syncing"] is True
    assert "sync_warning" in data
