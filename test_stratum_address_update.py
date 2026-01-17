"""
Test that the Stratum bridge updates its payout address when a miner authorizes.

This test verifies the fix for the issue where miners connecting to a bridge
started with a placeholder address would never receive jobs.
"""
import asyncio
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mining.stratum_bridge import StratumBridge, run_bridge_server
from mining.stratum_client import StratumClient
from mining.stratum_server import StratumServer


def _free_port() -> int:
    """Find a free port for testing."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return port


@pytest.mark.asyncio
async def test_bridge_updates_address_on_miner_authorization():
    """
    Test that the bridge updates its payout address when a miner authorizes.
    
    Scenario:
    1. Bridge starts with a placeholder/invalid address
    2. Miner connects and authorizes with a valid address
    3. Bridge updates its payout address
    4. Bridge fetches a template with the new address
    5. Miner receives a job
    """
    # Mock the RPC client to avoid needing a real node
    with patch('mining.stratum_bridge.RpcClient') as mock_rpc_class:
        # Create a mock RPC instance
        mock_rpc = AsyncMock()
        mock_rpc_class.return_value = mock_rpc
        
        # Track calls to getBlockTemplate
        template_calls = []
        
        async def mock_get_template(method, params):
            template_calls.append(params)
            # Return a mock template
            return {
                "enabled": True,
                "header": {
                    "signBytes": "0x" + "00" * 32,
                    "height": 1,
                    "chainId": 1,
                },
                "parent": {"hash": "0x" + "11" * 32, "height": 0},
                "target": "0x" + "ff" * 32,
                "templateId": "test_template",
                "thetaMicro": 800_000,
            }
        
        mock_rpc.call = mock_get_template
        
        # Create bridge with placeholder address
        bridge = StratumBridge(rpc_url="http://test:8545", poll_interval=0.5)
        await bridge.start("anim1placeholder")
        
        # Wait a moment to let initial poll attempt happen
        await asyncio.sleep(0.2)
        
        # Verify that getBlockTemplate was called with placeholder (should fail validation)
        assert len(template_calls) > 0
        assert template_calls[0].get("address") == "anim1placeholder"
        
        # Now update the address to a valid one (simulating miner authorization)
        valid_address = "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq3j5kq"
        await bridge.set_payout_address(valid_address)
        
        # Wait a moment for the immediate template fetch
        await asyncio.sleep(0.2)
        
        # Verify that getBlockTemplate was called again with the new address
        assert len(template_calls) >= 2
        last_call = template_calls[-1]
        assert last_call.get("address") == valid_address
        
        # Verify the bridge now has a current template
        job = await bridge.get_current_job()
        assert job is not None
        assert job.get("payout_address") == valid_address
        
        await bridge.stop()


@pytest.mark.asyncio
async def test_end_to_end_miner_receives_job_after_authorization():
    """
    End-to-end test: Miner connects to bridge started with placeholder,
    authorizes with valid address, and receives a job.
    
    This simulates the actual use case:
    - `animica stratum up` starts with placeholder
    - `animica miner stratum --address anim1...` connects with valid address
    - Miner should receive job after authorization
    """
    port = _free_port()
    
    # Mock the RPC client
    with patch('mining.stratum_bridge.RpcClient') as mock_rpc_class:
        mock_rpc = AsyncMock()
        mock_rpc_class.return_value = mock_rpc
        
        # Mock getBlockTemplate to return a valid template when called with valid address
        async def mock_get_template(method, params):
            address = params.get("address", "")
            
            # Simulate address validation (reject placeholder)
            if not address or not address.startswith("anim1") or address == "anim1placeholder":
                return {"enabled": False, "reason": "invalid_address"}
            
            # Return valid template for valid addresses
            return {
                "enabled": True,
                "header": {
                    "signBytes": "0x" + "00" * 32,
                    "height": 1,
                    "chainId": 1,
                    "parentHash": "0x" + "11" * 32,
                },
                "parent": {"hash": "0x" + "11" * 32, "height": 0},
                "target": "0x" + "ff" * 32,
                "templateId": "test_template",
                "thetaMicro": 800_000,
                "coinbase": {"amount": 50_000_000_000},
            }
        
        mock_rpc.call = mock_get_template
        
        # Start bridge with placeholder (simulating `animica stratum up`)
        bridge = StratumBridge(rpc_url="http://test:8545", poll_interval=0.5)
        await bridge.start("anim1placeholder")
        
        # Create and start server
        server = StratumServer(host="127.0.0.1", port=port)
        await server.start()
        
        # Set up authorize hook to update bridge address AND publish job
        async def authorize_hook(session, worker, address):
            if address and address.startswith("anim1") and address != "anim1placeholder":
                # Update bridge payout address
                await bridge.set_payout_address(address)
                
                # Immediately publish job if template is available
                job_dict = await bridge.get_current_job()
                if job_dict:
                    from mining.stratum_server import StratumJob
                    job = StratumJob(
                        job_id=job_dict["job_id"],
                        header=job_dict.get("header", {}),
                        share_target=job_dict.get("share_target", 0.01),
                        theta_micro=job_dict.get("theta_micro", 800_000),
                        hints={},
                        target=job_dict.get("target"),
                        sign_bytes=job_dict.get("sign_bytes"),
                        height=job_dict.get("height"),
                        parent_hash=job_dict.get("parent_hash"),
                        parent_height=job_dict.get("parent_height"),
                        chain_id=job_dict.get("chain_id"),
                    )
                    await server.publish_job(job)
        
        server.set_authorize_hook(authorize_hook)
        
        # Create client (simulating miner)
        client = StratumClient(host="127.0.0.1", port=port)
        await client.connect()
        
        # Track received jobs
        received_jobs = []
        job_received = asyncio.Event()
        
        async def on_notify(job_data):
            received_jobs.append(job_data)
            job_received.set()
        
        client.on_notify = on_notify
        
        # Subscribe
        await client.subscribe()
        
        # At this point, no job should be available (placeholder address)
        await asyncio.sleep(0.2)
        assert len(received_jobs) == 0, "No job should be sent before valid address"
        
        # Authorize with valid address (simulating miner connecting)
        valid_address = "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq3j5kq"
        await client.authorize(worker="test_miner", address=valid_address)
        
        # Wait for job to arrive (should happen after authorize hook updates bridge)
        try:
            await asyncio.wait_for(job_received.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pytest.fail("Miner did not receive job within 3 seconds after authorization")
        
        # Verify job was received
        assert len(received_jobs) >= 1, "Miner should receive job after authorization"
        
        # Clean up
        await client.close()
        await server.stop()
        await bridge.stop()


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
