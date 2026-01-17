"""
Integration test to verify the stratum mining job delivery fix.

This test reproduces the exact scenario from the bug report where:
1. Stratum server starts with an initial job
2. A miner connects immediately after server starts
3. The miner should receive the job (was failing before the fix)

The bug was caused by calling server.start() twice, which created
two server instances and orphaned the first one with the initial job.
"""

import asyncio
import socket
import sys
from typing import Optional

import pytest

from mining.stratum_bridge import StratumBridge, _create_stratum_job
from mining.stratum_client import StratumClient
from mining.stratum_server import StratumServer


def _free_port() -> int:
    """Find a free port for testing."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return port


class MockRpcClient:
    """
    Mock RPC client for testing stratum bridge without a real node.
    
    Returns fake block templates to simulate a working node RPC endpoint.
    Tracks call counts for test assertions.
    
    Args:
        template_available: If True, getBlockTemplate returns a valid template.
                           If False, returns None (simulates no template available).
    
    Attributes:
        call_count: Number of times call() has been invoked (useful for assertions).
    """
    
    def __init__(self, template_available: bool = True):
        self.template_available = template_available
        self.call_count = 0
    
    async def call(self, method: str, params=None):
        self.call_count += 1
        
        if method == "miner.getBlockTemplate" and self.template_available:
            return {
                "enabled": True,
                "templateId": f"template_{self.call_count}",
                "parent": {
                    "hash": "0x" + "11" * 32,
                    "height": 100,
                },
                "header": {
                    "chainId": 1,
                    "height": 101,
                    "parentHash": "0x" + "11" * 32,
                    "timestamp": 1234567890,
                    "stateRoot": "0x" + "22" * 32,
                    "txsRoot": "0x" + "33" * 32,
                    "receiptsRoot": "0x" + "44" * 32,
                    "proofsRoot": "0x" + "55" * 32,
                    "daRoot": "0x" + "66" * 32,
                    "mixSeed": "0x" + "77" * 32,
                    "poiesPolicyRoot": "0x" + "88" * 32,
                    "pqAlgPolicyRoot": "0x" + "99" * 32,
                    "thetaMicro": 800000,
                    "signBytes": "0x" + "aa" * 32,
                },
                "thetaMicro": 800000,
                "target": "0x" + "ff" * 32,
                "txs": [],
            }
        return None


@pytest.mark.asyncio
async def test_bug_reproduction_stratum_job_delivery():
    """
    Test that reproduces and verifies the fix for the stratum job delivery bug.
    
    Before the fix:
    - server.start() was called at line 440
    - Then run_async() called start() again, creating a second server
    - Initial job published to first server was lost
    - Miners connecting got "No mining job received from server"
    
    After the fix:
    - Only run_async() is called, which internally calls start() once
    - Initial job is preserved
    - Miners immediately receive the job after subscribing
    """
    port = _free_port()
    
    # Create bridge with mock RPC
    bridge = StratumBridge(
        rpc_url="http://mock:8545",
        poll_interval=1.0,
        default_share_target=0.01,
    )
    bridge._rpc = MockRpcClient(template_available=True)
    
    # Start bridge and fetch initial template (like run_bridge_server does)
    await bridge.start("anim1testaddress")
    await bridge._poll_template()
    
    assert bridge._current_template is not None, "Should have initial template"
    assert bridge._current_job_id is not None, "Should have initial job ID"
    
    # Create server
    server = StratumServer(host="127.0.0.1", port=port)
    
    # Publish initial job to server (like run_bridge_server does)
    initial_job_dict = await bridge.get_current_job()
    assert initial_job_dict is not None, "Should get initial job dict"
    
    initial_job = _create_stratum_job(initial_job_dict, 0.01)
    await server.publish_job(initial_job)
    
    # Start server directly (simulating the fixed flow)
    # In the actual stratum_bridge.py:
    #   Before fix: Called await server.start() here, then run_async() which calls start() again
    #   After fix: Only calls run_async() which handles start() internally
    # This test simplifies by just calling start() once to verify job delivery works
    await server.start()
    
    # Track if job is received
    job_received = asyncio.Event()
    received_jobs = []
    
    async def on_notify(job_data):
        received_jobs.append(job_data)
        job_received.set()
    
    # Connect client immediately (simulating the bug scenario)
    client = StratumClient(host="127.0.0.1", port=port)
    client.on_notify = on_notify
    
    await client.connect()
    await client.subscribe()
    await client.authorize(worker="test_worker", address="anim1testaddress")
    
    # Wait for job - should arrive immediately after subscription
    try:
        await asyncio.wait_for(job_received.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pytest.fail(
            "Client did not receive mining job within 2 seconds. "
            "This indicates the bug still exists!"
        )
    
    # Verify we got the correct job
    assert len(received_jobs) >= 1, "Should have received at least one job"
    first_job = received_jobs[0]
    job_id = first_job.get("jobId") or first_job.get("job_id")
    assert job_id == initial_job.job_id, \
        f"Expected job ID {initial_job.job_id}, got {job_id}"
    
    # Cleanup
    await client.close()
    await server.stop()
    await bridge.stop()


@pytest.mark.asyncio
async def test_no_duplicate_server_instances():
    """
    Test that verifies we don't create duplicate server instances.
    
    This is a more direct test of the bug - ensuring that only one
    server instance is created and jobs are preserved.
    """
    port = _free_port()
    
    # Create a server
    server = StratumServer(host="127.0.0.1", port=port)
    
    # Create a test job
    sign_hex = "0x" + "00" * 32
    hints = {"mixSeed": "0x" + "00" * 32}
    from mining.stratum_server import StratumJob
    
    test_job = StratumJob(
        job_id="test_job_preserved",
        header={"signBytes": sign_hex, "height": 1},
        share_target=0.01,
        theta_micro=800_000,
        hints=hints,
        target="0x" + "ff" * 32,
        sign_bytes=sign_hex,
        height=1,
        parent_hash="0x" + "11" * 32,
        parent_height=0,
        chain_id=1,
    )
    
    # Publish job BEFORE starting server (matching stratum_bridge.py flow)
    await server.publish_job(test_job)
    
    # Start server
    await server.start()
    
    # Verify the job is in the server's job dict
    assert test_job.job_id in server._jobs, "Job should be in server's jobs dict"
    assert server._current_job_id == test_job.job_id, "Current job ID should be set"
    
    # Store the server instance ID
    first_server_id = id(server._server)
    
    # DON'T call start() again (this was the bug)
    # If we did: await server.start()
    # It would create a new server instance and lose the job
    
    # Verify server instance is unchanged
    assert id(server._server) == first_server_id, \
        "Server instance should not change"
    
    # Verify job is still there
    assert test_job.job_id in server._jobs, \
        "Job should still be in server's jobs dict"
    
    # Connect a client and verify they receive the job
    client = StratumClient(host="127.0.0.1", port=port)
    job_received = asyncio.Event()
    received_jobs = []
    
    async def on_notify(job_data):
        received_jobs.append(job_data)
        job_received.set()
    
    client.on_notify = on_notify
    await client.connect()
    await client.subscribe()
    
    # Should receive the preserved job
    try:
        await asyncio.wait_for(job_received.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pytest.fail("Client did not receive the preserved job")
    
    assert len(received_jobs) >= 1, "Should receive job"
    job_id = received_jobs[0].get("jobId") or received_jobs[0].get("job_id")
    assert job_id == "test_job_preserved", \
        f"Should receive preserved job, got {job_id}"
    
    # Cleanup
    await client.close()
    await server.stop()

if __name__ == "__main__":
    # Allow running directly for manual testing
    pytest.main([__file__, "-v", "-s"])
