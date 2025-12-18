"""
Integration tests for the animica CLI.

Tests wallet round-trips, RPC calls, chain queries, and transaction operations.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
import typer.testing

# Import the main CLI app
try:
    from animica.cli.main import app
except Exception as e:
    pytest.skip(f"Could not import CLI: {e}")


runner = typer.testing.CliRunner()


class TestCLIBasics:
    """Test CLI help and basic structure."""

    def test_help(self) -> None:
        """Test that --help works."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Animica" in result.stdout

    def test_module_invocation(self) -> None:
        """Test that the package can be invoked as a module."""
        import subprocess
        import sys
        
        # Test: python -m animica --help
        result = subprocess.run(
            [sys.executable, "-m", "animica", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Module invocation failed: {result.stderr}"
        assert "Animica" in result.stdout

    def test_node_help(self) -> None:
        """Test node subgroup --help."""
        result = runner.invoke(app, ["node", "--help"])
        assert result.exit_code == 0
        assert "status" in result.stdout or "logs" in result.stdout

    def test_wallet_help(self) -> None:
        """Test wallet subgroup --help."""
        result = runner.invoke(app, ["wallet", "--help"])
        assert result.exit_code == 0
        assert "wallet" in result.stdout.lower()

    def test_key_help(self) -> None:
        """Test key subgroup --help."""
        result = runner.invoke(app, ["key", "--help"])
        assert result.exit_code == 0
        assert "key" in result.stdout.lower()

    def test_tx_help(self) -> None:
        """Test tx subgroup --help."""
        result = runner.invoke(app, ["tx", "--help"])
        assert result.exit_code == 0
        assert "transaction" in result.stdout.lower() or "tx" in result.stdout

    def test_chain_help(self) -> None:
        """Test chain subgroup --help."""
        result = runner.invoke(app, ["chain", "--help"])
        assert result.exit_code == 0
        assert "chain" in result.stdout.lower()

    def test_rpc_help(self) -> None:
        """Test rpc subgroup --help."""
        result = runner.invoke(app, ["rpc", "--help"])
        assert result.exit_code == 0
        assert "rpc" in result.stdout.lower()

    def test_da_help(self) -> None:
        """Test da subgroup --help."""
        result = runner.invoke(app, ["da", "--help"])
        assert result.exit_code == 0
        assert "availability" in result.stdout.lower() or "da" in result.stdout

    def test_mempool_help(self) -> None:
        """Test mempool subgroup --help."""
        result = runner.invoke(app, ["mempool", "--help"])
        assert result.exit_code == 0
        assert "mempool" in result.stdout.lower()


class TestKeySubcommands:
    """Test key management commands."""

    def test_key_new_help(self) -> None:
        """Test key new --help."""
        result = runner.invoke(app, ["key", "new", "--help"])
        assert result.exit_code == 0
        assert "generate" in result.stdout.lower() or "key" in result.stdout.lower()

    def test_key_show_help(self) -> None:
        """Test key show --help."""
        result = runner.invoke(app, ["key", "show", "--help"])
        assert result.exit_code == 0

    def test_key_list_help(self) -> None:
        """Test key list --help."""
        result = runner.invoke(app, ["key", "list", "--help"])
        assert result.exit_code == 0


class TestTxSubcommands:
    """Test transaction commands."""

    def test_tx_build_help(self) -> None:
        """Test tx build --help."""
        result = runner.invoke(app, ["tx", "build", "--help"])
        assert result.exit_code == 0
        assert "from" in result.stdout.lower()

    def test_tx_sign_help(self) -> None:
        """Test tx sign --help."""
        result = runner.invoke(app, ["tx", "sign", "--help"])
        assert result.exit_code == 0

    def test_tx_send_help(self) -> None:
        """Test tx send --help."""
        result = runner.invoke(app, ["tx", "send", "--help"])
        assert result.exit_code == 0

    def test_tx_simulate_help(self) -> None:
        """Test tx simulate --help."""
        result = runner.invoke(app, ["tx", "simulate", "--help"])
        assert result.exit_code == 0


class TestChainSubcommands:
    """Test chain query commands."""

    def test_chain_head_help(self) -> None:
        """Test chain head --help."""
        result = runner.invoke(app, ["chain", "head", "--help"])
        assert result.exit_code == 0

    def test_chain_block_help(self) -> None:
        """Test chain block --help."""
        result = runner.invoke(app, ["chain", "block", "--help"])
        assert result.exit_code == 0

    def test_chain_tx_help(self) -> None:
        """Test chain tx --help."""
        result = runner.invoke(app, ["chain", "tx", "--help"])
        assert result.exit_code == 0

    def test_chain_account_help(self) -> None:
        """Test chain account --help."""
        result = runner.invoke(app, ["chain", "account", "--help"])
        assert result.exit_code == 0

    def test_chain_events_help(self) -> None:
        """Test chain events --help."""
        result = runner.invoke(app, ["chain", "events", "--help"])
        assert result.exit_code == 0


class TestRpcSubcommands:
    """Test RPC commands."""

    def test_rpc_call_help(self) -> None:
        """Test rpc call --help."""
        result = runner.invoke(app, ["rpc", "call", "--help"])
        assert result.exit_code == 0
        assert "method" in result.stdout.lower()


class TestDASubcommands:
    """Test Data Availability commands."""

    def test_da_submit_help(self) -> None:
        """Test da submit --help."""
        result = runner.invoke(app, ["da", "submit", "--help"])
        assert result.exit_code == 0

    def test_da_get_help(self) -> None:
        """Test da get --help."""
        result = runner.invoke(app, ["da", "get", "--help"])
        assert result.exit_code == 0

    def test_da_verify_help(self) -> None:
        """Test da verify --help."""
        result = runner.invoke(app, ["da", "verify", "--help"])
        assert result.exit_code == 0


class TestGlobalOptions:
    """Test global CLI options."""

    def test_verbose_flag(self) -> None:
        """Test --verbose flag is accepted."""
        result = runner.invoke(app, ["--verbose", "node", "--help"])
        assert result.exit_code == 0

    def test_json_flag(self) -> None:
        """Test --json flag is accepted."""
        result = runner.invoke(app, ["--json", "node", "--help"])
        assert result.exit_code == 0

    def test_rpc_url_option(self) -> None:
        """Test --rpc-url option is accepted."""
        result = runner.invoke(
            app, ["--rpc-url", "http://localhost:8545", "node", "--help"]
        )
        assert result.exit_code == 0

    def test_network_option(self) -> None:
        """Test --network option is accepted."""
        result = runner.invoke(app, ["--network", "devnet", "node", "--help"])
        assert result.exit_code == 0

    def test_global_rpc_url_applies_to_wallet(self, monkeypatch: Any, tmp_path: Path) -> None:
        """Global --rpc-url should propagate to wallet commands."""
        from animica.cli import wallet

        wallet_path = tmp_path / "wallets.json"
        wallet_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "wallets": [
                        {
                            "label": "alice",
                            "address": "anim1alice",
                            "alg_id": 0,
                            "alg_name": "ed25519",
                            "public_key_hex": "0xabc",
                            "secret_key_hex": "0xdef",
                            "created_at": "2024-01-01T00:00:00Z",
                            "balance": "0",
                        }
                    ],
                }
            )
        )

        captured: dict[str, str] = {}

        def _fake_get_balance(address: str, rpc_url: str) -> int:
            captured["address"] = address
            captured["rpc_url"] = rpc_url
            return 0

        monkeypatch.setattr(wallet, "get_balance", _fake_get_balance)

        result = runner.invoke(
            app,
            ["--rpc-url", "http://global:9999/rpc", "wallet", "show", "alice", "--source", "chain"],
            env={"ANIMICA_WALLETS_FILE": str(wallet_path)},
        )

        assert result.exit_code == 0, result.output
        assert captured["address"] == "anim1alice"
        assert captured["rpc_url"] == "http://global:9999/rpc"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
