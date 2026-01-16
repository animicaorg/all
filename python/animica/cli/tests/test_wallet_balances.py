"""Tests for wallet balance backup and restore functionality."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from animica.cli.wallet_balances import (
    _address_to_hex,
    _get_balance_backup_path,
    _load_wallet_file,
    export_wallet_balances_sync,
    restore_wallet_balances_sync,
)


def test_get_balance_backup_path():
    """Test that backup path is correctly constructed."""
    data_dir = Path("/home/user/.animica/chain-1337")
    backup_path = _get_balance_backup_path(data_dir)
    
    assert backup_path == Path("/home/user/.animica/chain-1337_balances_backup.json")
    assert backup_path.parent == data_dir.parent


def test_address_to_hex_already_hex():
    """Test conversion of hex addresses."""
    # Already 0x-prefixed
    addr = "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
    result = _address_to_hex(addr)
    assert result == addr.lower()
    
    # Not prefixed but hex
    addr_no_prefix = "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
    result = _address_to_hex(addr_no_prefix)
    assert result == "0x" + addr_no_prefix.lower()


def test_address_to_hex_padding():
    """Test that short hex addresses are padded."""
    short_addr = "0x1234"
    result = _address_to_hex(short_addr)
    assert len(result) == 66  # 0x + 64 chars
    assert result.startswith("0x")
    assert result.endswith("1234")


def test_address_to_hex_truncation():
    """Test that long hex addresses are truncated."""
    long_addr = "0x" + "ff" * 40  # 40 bytes instead of 32
    result = _address_to_hex(long_addr)
    assert len(result) == 66  # 0x + 64 chars (32 bytes)


def test_load_wallet_file_not_exists():
    """Test loading non-existent wallet file returns empty list."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wallet_path = Path(tmpdir) / "nonexistent.json"
        result = _load_wallet_file(wallet_path)
        assert result == []


def test_load_wallet_file_valid():
    """Test loading valid wallet file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wallet_path = Path(tmpdir) / "wallets.json"
        wallet_data = {
            "version": 1,
            "wallets": [
                {
                    "label": "test1",
                    "address": "anim1test123",
                    "alg_id": 1,
                    "alg_name": "dilithium3",
                    "public_key_hex": "abcd",
                    "secret_key_hex": "secret",
                    "created_at": "2024-01-01T00:00:00Z",
                },
            ],
        }
        wallet_path.write_text(json.dumps(wallet_data))
        
        result = _load_wallet_file(wallet_path)
        assert len(result) == 1
        assert result[0]["label"] == "test1"


def test_load_wallet_file_malformed():
    """Test loading malformed wallet file returns empty list."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wallet_path = Path(tmpdir) / "wallets.json"
        wallet_path.write_text("{invalid json")
        
        result = _load_wallet_file(wallet_path)
        assert result == []


@pytest.mark.asyncio
async def test_export_wallet_balances_no_wallets():
    """Test export with no wallets in file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wallet_path = Path(tmpdir) / "wallets.json"
        data_dir = Path(tmpdir) / "chain-1337"
        data_dir.mkdir()
        
        backup_file, total, non_zero = export_wallet_balances_sync(
            wallet_path=wallet_path,
            data_dir=data_dir,
            rpc_url="http://localhost:8545/rpc",
            quiet=True,
        )
        
        assert total == 0
        assert non_zero == 0
        assert backup_file == _get_balance_backup_path(data_dir)


@pytest.mark.asyncio
async def test_export_wallet_balances_with_mocked_rpc():
    """Test export with mocked RPC responses."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create wallet file
        wallet_path = Path(tmpdir) / "wallets.json"
        wallet_data = {
            "version": 1,
            "wallets": [
                {
                    "label": "test1",
                    "address": "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
                },
                {
                    "label": "test2",
                    "address": "0xfedcba0987654321fedcba0987654321fedcba0987654321fedcba0987654321",
                },
            ],
        }
        wallet_path.write_text(json.dumps(wallet_data))
        
        data_dir = Path(tmpdir) / "chain-1337"
        data_dir.mkdir()
        
        # Mock RPC calls to return balances
        with patch("animica.cli.wallet_balances._rpc_call") as mock_rpc:
            # First wallet has balance, second doesn't
            mock_rpc.side_effect = [1000000000, 0]  # 1 ANM and 0
            
            backup_file, total, non_zero = export_wallet_balances_sync(
                wallet_path=wallet_path,
                data_dir=data_dir,
                rpc_url="http://localhost:8545/rpc",
                quiet=True,
            )
            
            assert total == 2
            assert non_zero == 1
            assert backup_file.exists()
            
            # Check backup file contents
            backup_data = json.loads(backup_file.read_text())
            assert backup_data["version"] == 1
            assert len(backup_data["balances"]) == 2
            assert backup_data["balances"][0]["balance"] == 1000000000
            assert backup_data["balances"][1]["balance"] == 0


@pytest.mark.asyncio
async def test_restore_wallet_balances_no_backup():
    """Test restore when no backup file exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir) / "chain-1337"
        data_dir.mkdir()
        
        # Try to restore without a backup file
        with pytest.raises(RuntimeError, match="backup file not found"):
            restore_wallet_balances_sync(
                data_dir=data_dir,
                rpc_url="http://localhost:8545/rpc",
                quiet=True,
            )


@pytest.mark.asyncio
async def test_restore_wallet_balances_empty_backup():
    """Test restore with empty backup (no balances)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir) / "chain-1337"
        data_dir.mkdir()
        
        # Create empty backup file
        backup_file = _get_balance_backup_path(data_dir)
        backup_data = {
            "version": 1,
            "exported_at": "2024-01-01T00:00:00Z",
            "data_dir": str(data_dir),
            "rpc_url": "http://localhost:8545/rpc",
            "balances": [],
        }
        backup_file.write_text(json.dumps(backup_data))
        
        restored, failed = restore_wallet_balances_sync(
            data_dir=data_dir,
            rpc_url="http://localhost:8545/rpc",
            quiet=True,
        )
        
        assert restored == 0
        assert failed == 0


@pytest.mark.asyncio
async def test_restore_wallet_balances_success():
    """Test successful restore with mocked RPC."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir) / "chain-1337"
        data_dir.mkdir()
        
        # Create backup file with balances
        backup_file = _get_balance_backup_path(data_dir)
        backup_data = {
            "version": 1,
            "exported_at": "2024-01-01T00:00:00Z",
            "data_dir": str(data_dir),
            "rpc_url": "http://localhost:8545/rpc",
            "balances": [
                {
                    "label": "test1",
                    "address": "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
                    "hex_address": "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
                    "balance": 1000000000,  # 1 ANM
                },
                {
                    "label": "test2",
                    "address": "0xfedcba0987654321fedcba0987654321fedcba0987654321fedcba0987654321",
                    "hex_address": "0xfedcba0987654321fedcba0987654321fedcba0987654321fedcba0987654321",
                    "balance": 2000000000,  # 2 ANM
                },
                {
                    "label": "test3",
                    "address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "hex_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "balance": 0,  # Should be skipped
                },
            ],
        }
        backup_file.write_text(json.dumps(backup_data))
        
        # Mock RPC calls to simulate successful setBalance calls
        with patch("animica.cli.wallet_balances._rpc_call") as mock_rpc:
            # Simulate successful RPC responses
            mock_rpc.side_effect = [
                {"success": True, "address": backup_data["balances"][0]["address"], "balance": "1000000000"},
                {"success": True, "address": backup_data["balances"][1]["address"], "balance": "2000000000"},
            ]
            
            restored, failed = restore_wallet_balances_sync(
                data_dir=data_dir,
                rpc_url="http://localhost:8545/rpc",
                quiet=True,
            )
            
            assert restored == 2
            assert failed == 0
            
            # Verify RPC was called correct number of times (only for non-zero balances)
            assert mock_rpc.call_count == 2


@pytest.mark.asyncio
async def test_restore_wallet_balances_partial_failure():
    """Test restore with some failures."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir) / "chain-1337"
        data_dir.mkdir()
        
        # Create backup file with balances
        backup_file = _get_balance_backup_path(data_dir)
        backup_data = {
            "version": 1,
            "exported_at": "2024-01-01T00:00:00Z",
            "data_dir": str(data_dir),
            "rpc_url": "http://localhost:8545/rpc",
            "balances": [
                {
                    "label": "test1",
                    "address": "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
                    "hex_address": "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
                    "balance": 1000000000,
                },
                {
                    "label": "test2",
                    "address": "0xfedcba0987654321fedcba0987654321fedcba0987654321fedcba0987654321",
                    "hex_address": "0xfedcba0987654321fedcba0987654321fedcba0987654321fedcba0987654321",
                    "balance": 2000000000,
                },
            ],
        }
        backup_file.write_text(json.dumps(backup_data))
        
        # Mock RPC calls - first succeeds, second fails
        with patch("animica.cli.wallet_balances._rpc_call") as mock_rpc:
            mock_rpc.side_effect = [
                {"success": True, "address": backup_data["balances"][0]["address"], "balance": "1000000000"},
                RuntimeError("RPC connection failed"),
            ]
            
            restored, failed = restore_wallet_balances_sync(
                data_dir=data_dir,
                rpc_url="http://localhost:8545/rpc",
                quiet=True,
            )
            
            assert restored == 1
            assert failed == 1


@pytest.mark.asyncio
async def test_restore_wallet_balances_admin_rpc_disabled():
    """Test restore when admin RPC is disabled."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir) / "chain-1337"
        data_dir.mkdir()
        
        # Create backup file
        backup_file = _get_balance_backup_path(data_dir)
        backup_data = {
            "version": 1,
            "exported_at": "2024-01-01T00:00:00Z",
            "data_dir": str(data_dir),
            "rpc_url": "http://localhost:8545/rpc",
            "balances": [
                {
                    "label": "test1",
                    "address": "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
                    "hex_address": "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
                    "balance": 1000000000,
                },
            ],
        }
        backup_file.write_text(json.dumps(backup_data))
        
        # Mock RPC to simulate "admin RPC disabled" error
        with patch("animica.cli.wallet_balances._rpc_call") as mock_rpc:
            mock_rpc.side_effect = RuntimeError("Admin RPC methods are disabled")
            
            with pytest.raises(RuntimeError, match="Admin RPC is not enabled"):
                restore_wallet_balances_sync(
                    data_dir=data_dir,
                    rpc_url="http://localhost:8545/rpc",
                    quiet=True,
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
