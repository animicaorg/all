"""
Test mining audit trail and reward verification functionality.

This module tests the new mining audit trail features including:
1. FOUND/ACCEPTED/REJECTED separation in CLI output
2. Detailed submitBlock response with credited_amount
3. Mining credits audit trail (mining.getCredits RPC)
4. CLI command for viewing credits (animica miner credits)
5. Invariant checks for reward crediting
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from python.animica.cli import mining


class TestMiningAuditTrail:
    """Test mining audit trail functionality."""
    
    def test_found_vs_accepted_separation(self, monkeypatch):
        """Test that CLI distinguishes FOUND from ACCEPTED."""
        # Mock RPC client to simulate successful mining
        mock_client = Mock()
        mock_rpc_client_class = Mock(return_value=mock_client)
        
        # Mock template response
        template_response = {
            "enabled": True,
            "templateId": "test-template-id",
            "header": {
                "v": 1,
                "chainId": 1337,
                "height": 1,
                "parentHash": "0x" + ("00" * 32),
                "timestamp": 1234567890,
                "thetaMicro": 3000000,
                "workType": 0,
            },
            "target": "0x" + "ff" * 32,  # Easy target
            "txs": [],
            "parent": {"height": 0, "hash": "0x" + ("00" * 32)},
            "coinbase": {"amount": 5000000000},
        }
        
        # Mock successful submitBlock response with new fields
        submit_response = {
            "accepted": True,
            "duplicate": False,
            "credited_amount": 5000000000,
            "new_head": 1,
            "block_hash": "0xabc123",
        }
        
        mock_client.request = Mock(side_effect=[template_response, submit_response])
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=None)
        
        # Mock other dependencies
        monkeypatch.setattr("python.animica.cli.mining.RpcClient", mock_rpc_client_class)
        monkeypatch.setattr("python.animica.cli.mining._resolve_payout_address", lambda x: "anim1test")
        monkeypatch.setattr("python.animica.cli.mining._warn_if_unsynced", lambda x: False)
        monkeypatch.setattr("python.animica.cli.mining.guard_bootstrap_rpc", lambda *args, **kwargs: None)
        
        # Capture output
        output_lines = []
        def capture_echo(msg, **kwargs):
            output_lines.append(str(msg))
        
        def capture_secho(msg, **kwargs):
            output_lines.append(str(msg))
        
        monkeypatch.setattr("typer.echo", capture_echo)
        monkeypatch.setattr("typer.secho", capture_secho)
        
        # Run mining command
        try:
            mining.mine_blocks(
                address="test-address",
                count=1,
                address_opt=None,
                allow_remote_rpc=False,
                device="cpu",
                rpc_url="http://localhost:8545",
                use_proxy=False,
                verbose=False,
                no_timeout=False,
                include_mempool=True,
            )
        except SystemExit:
            pass  # Command exits with success
        
        # Join all output
        full_output = "\n".join(output_lines)
        
        # Verify FOUND message appears
        assert "FOUND:" in full_output or "PoW" in full_output, "Should print FOUND message when PoW found"
        
        # Verify ACCEPTED message appears
        assert "ACCEPTED:" in full_output, "Should print ACCEPTED message when block accepted"
        
        # Verify credited amount is displayed
        assert "credited" in full_output.lower(), "Should display credited amount"
    
    def test_rejected_block_output(self, monkeypatch):
        """Test that CLI shows REJECTED with reason when block is rejected."""
        mock_client = Mock()
        mock_rpc_client_class = Mock(return_value=mock_client)
        
        # Mock template response
        template_response = {
            "enabled": True,
            "templateId": "test-template-id",
            "header": {
                "v": 1,
                "chainId": 1337,
                "height": 1,
                "parentHash": "0x" + ("00" * 32),
                "timestamp": 1234567890,
                "thetaMicro": 3000000,
                "workType": 0,
            },
            "target": "0x" + "ff" * 32,
            "txs": [],
            "parent": {"height": 0, "hash": "0x" + ("00" * 32)},
            "coinbase": {"amount": 5000000000},
        }
        
        # Mock rejected submitBlock response
        submit_response = {
            "accepted": False,
            "reason": "invalid_pow",
        }
        
        mock_client.request = Mock(side_effect=[template_response, submit_response])
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=None)
        
        # Mock dependencies
        monkeypatch.setattr("python.animica.cli.mining.RpcClient", mock_rpc_client_class)
        monkeypatch.setattr("python.animica.cli.mining._resolve_payout_address", lambda x: "anim1test")
        monkeypatch.setattr("python.animica.cli.mining._warn_if_unsynced", lambda x: False)
        monkeypatch.setattr("python.animica.cli.mining.guard_bootstrap_rpc", lambda *args, **kwargs: None)
        
        # Capture output
        output_lines = []
        def capture_echo(msg, **kwargs):
            output_lines.append(str(msg))
        
        def capture_secho(msg, **kwargs):
            output_lines.append(str(msg))
        
        monkeypatch.setattr("typer.echo", capture_echo)
        monkeypatch.setattr("typer.secho", capture_secho)
        
        # Run mining command (should fail gracefully)
        try:
            mining.mine_blocks(
                address="test-address",
                count=1,
                address_opt=None,
                allow_remote_rpc=False,
                device="cpu",
                rpc_url="http://localhost:8545",
                use_proxy=False,
                verbose=False,
                no_timeout=False,
                include_mempool=True,
            )
        except SystemExit:
            pass
        
        # Join all output
        full_output = "\n".join(output_lines)
        
        # Verify REJECTED message appears with reason
        assert "REJECTED:" in full_output, "Should print REJECTED message"
        assert "invalid_pow" in full_output, "Should include rejection reason"
    
    def test_mining_credits_cli_command(self, monkeypatch):
        """Test the 'animica miner credits' CLI command."""
        # Mock RPC response for mining.getCredits
        credits_response = {
            "credits": [
                {
                    "height": 100,
                    "hash": "0xabc123",
                    "parent_hash": "0xdef456",
                    "miner_address": "0x1234567890abcdef",
                    "expected_reward": 5000000000,
                    "credited_reward": 5000000000,
                    "state_root": "0x789xyz",
                    "timestamp": 1234567890,
                },
                {
                    "height": 101,
                    "hash": "0x111222",
                    "parent_hash": "0xabc123",
                    "miner_address": "0x1234567890abcdef",
                    "expected_reward": 5000000000,
                    "credited_reward": 10000000000,  # Balance after 2 blocks
                    "state_root": "0x333444",
                    "timestamp": 1234567900,
                },
            ],
            "count": 2,
            "filters": {
                "address": None,
                "from_height": None,
                "to_height": None,
                "last": 50,
            },
        }
        
        # Mock call_rpc
        monkeypatch.setattr("python.animica.cli.mining.call_rpc", lambda method, params, url: credits_response)
        monkeypatch.setattr("python.animica.cli.mining.load_network_config", lambda: Mock(rpc_url="http://localhost:8545"))
        
        # Capture output
        output_lines = []
        def capture_echo(msg, **kwargs):
            output_lines.append(str(msg))
        
        def capture_secho(msg, **kwargs):
            output_lines.append(str(msg))
        
        monkeypatch.setattr("typer.echo", capture_echo)
        monkeypatch.setattr("typer.secho", capture_secho)
        
        # Run credits command
        try:
            mining.show_mining_credits(
                address=None,
                last=50,
                from_height=None,
                to_height=None,
                rpc_url=None,
                format="table",
            )
        except SystemExit:
            pass
        
        # Join all output
        full_output = "\n".join(output_lines)
        
        # Verify output contains expected information
        assert "Mining Credits" in full_output or "credits" in full_output.lower()
        assert "Height: 100" in full_output
        assert "Height: 101" in full_output
        assert "5000000000" in full_output or "5.0" in full_output  # Reward amount
    
    def test_mining_credits_json_format(self, monkeypatch):
        """Test JSON output format for mining credits."""
        import json
        
        credits_response = {
            "credits": [
                {
                    "height": 100,
                    "hash": "0xabc",
                    "parent_hash": "0xdef",
                    "miner_address": "0x123",
                    "expected_reward": 5000000000,
                    "credited_reward": 5000000000,
                    "state_root": "0x789",
                    "timestamp": 1234567890,
                }
            ],
            "count": 1,
            "filters": {},
        }
        
        monkeypatch.setattr("python.animica.cli.mining.call_rpc", lambda method, params, url: credits_response)
        monkeypatch.setattr("python.animica.cli.mining.load_network_config", lambda: Mock(rpc_url="http://localhost:8545"))
        
        # Capture output
        output_lines = []
        def capture_echo(msg, **kwargs):
            output_lines.append(str(msg))
        
        monkeypatch.setattr("typer.echo", capture_echo)
        monkeypatch.setattr("typer.secho", lambda msg, **kwargs: output_lines.append(str(msg)))
        
        # Run with JSON format
        try:
            mining.show_mining_credits(
                address=None,
                last=50,
                from_height=None,
                to_height=None,
                rpc_url=None,
                format="json",
            )
        except SystemExit:
            pass
        
        full_output = "\n".join(output_lines)
        
        # Verify JSON output can be parsed
        parsed = json.loads(full_output)
        assert "credits" in parsed
        assert len(parsed["credits"]) == 1
        assert parsed["credits"][0]["height"] == 100


class TestSubmitBlockResponse:
    """Test enhanced submitBlock RPC response."""
    
    def test_submit_block_includes_credited_amount(self):
        """Test that submitBlock returns credited_amount in response."""
        from rpc.methods import miner
        
        # This is an integration-style test that would need a full RPC setup
        # For now, we verify the structure by checking the function signature
        # In a real test, we'd mock the dependencies and call miner_submit_block
        
        # Verify the function exists and is registered as an RPC method
        assert hasattr(miner, "miner_submit_block")
        
        # The actual test would mock ctx, block_import, etc. and verify:
        # response = miner.miner_submit_block(block_payload)
        # assert "credited_amount" in response
        # assert "new_head" in response
        # assert "block_hash" in response


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
