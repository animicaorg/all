"""
End-to-end test verifying that miners receive signBytes in job header.

This test simulates the exact scenario from the bug report where miners
receive jobs but skip mining due to missing signBytes in the header.
"""
import asyncio
import socket

import pytest

from mining.stratum_client import StratumClient
from mining.stratum_server import StratumJob, StratumServer


def _free_port() -> int:
    """Find a free port for testing."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return port


@pytest.mark.asyncio
async def test_miner_receives_signbytes_in_header():
    """
    Test the fix: miners receive signBytes in header even when it's only
    in the StratumJob.sign_bytes field and not in job.header initially.
    
    This simulates the stratum_bridge flow where:
    1. Bridge creates job with sign_bytes as separate field
    2. Server broadcasts job
    3. Miner receives job with signBytes in header dict
    """
    port = _free_port()
    server = StratumServer(host="127.0.0.1", port=port)
    await server.start()
    
    # Create a job mimicking stratum_bridge behavior:
    # - header dict does NOT contain signBytes
    # - sign_bytes field contains the value
    # - hints contains mixSeed
    job = StratumJob(
        job_id="test-bridge-job",
        header={
            "chainId": 1,
            "height": 100,
            "parentHash": "0x" + "11" * 32,
            "stateRoot": "0x" + "00" * 32,
            "thetaMicro": 800_000,
            # NOTE: signBytes NOT in header dict
            # NOTE: mixSeed NOT in header dict
        },
        share_target=0.01,
        theta_micro=800_000,
        sign_bytes="0x" + "aa" * 80,  # But signBytes IS here
        hints={"mixSeed": "0x" + "bb" * 32},  # And mixSeed IS in hints
        target="0x" + "ff" * 32,
        height=100,
        parent_hash="0x" + "11" * 32,
        parent_height=99,
        chain_id=1,
    )
    
    # Verify initial state (before fix this would fail miners)
    assert "signBytes" not in job.header, "Initially signBytes should NOT be in header"
    assert "mixSeed" not in job.header, "Initially mixSeed should NOT be in header"
    assert job.sign_bytes is not None, "But sign_bytes field should be set"
    assert "mixSeed" in job.hints, "And mixSeed should be in hints"
    
    # Publish job to server
    await server.publish_job(job)
    
    # Connect a client (miner)
    client = StratumClient(host="127.0.0.1", port=port)
    await client.connect()
    
    # Track jobs received
    received_jobs = []
    
    async def on_notify(job_data):
        received_jobs.append(job_data)
    
    client.on_notify = on_notify
    
    # Subscribe and authorize
    await client.subscribe()
    await client.authorize(worker="test_miner", address="anim1test")
    
    # Wait for job to arrive
    await asyncio.sleep(0.5)
    
    # Verify job was received
    assert len(received_jobs) >= 1, "Client should receive job"
    
    received_job = received_jobs[0]
    received_header = received_job.get("header", {})
    
    # THE FIX: Verify signBytes is NOW in the header dict that miner receives
    assert "signBytes" in received_header, \
        "FIX: signBytes should be in header dict sent to miner"
    assert received_header["signBytes"] == "0x" + "aa" * 80, \
        "signBytes value should match"
    
    # THE FIX: Verify mixSeed is NOW in the header dict that miner receives
    assert "mixSeed" in received_header, \
        "FIX: mixSeed should be in header dict sent to miner"
    assert received_header["mixSeed"] == "0x" + "bb" * 32, \
        "mixSeed value should match"
    
    # Verify height is accessible at top level (for display)
    assert received_job.get("height") == 100, \
        "Height should be at top level for miner display"
    
    # Verify miner can parse the bytes for hashing
    try:
        sign_bytes = bytes.fromhex(
            received_header["signBytes"][2:] 
            if received_header["signBytes"].startswith("0x") 
            else received_header["signBytes"]
        )
        assert len(sign_bytes) == 80, "signBytes should be 80 bytes"
        
        mix_seed = bytes.fromhex(
            received_header["mixSeed"][2:] 
            if received_header["mixSeed"].startswith("0x") 
            else received_header["mixSeed"]
        )
        assert len(mix_seed) == 32, "mixSeed should be 32 bytes"
        
        # This is what the miner does - if it can parse, it won't show warning!
        print("✓ Miner can hash with received job - no warning!")
    except Exception as e:
        pytest.fail(f"Miner cannot parse job fields: {e}")
    
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_miner_with_signbytes_already_in_header():
    """
    Test that jobs with signBytes already in header still work.
    
    This ensures backward compatibility - if signBytes is already in the header,
    our fix doesn't break it.
    """
    port = _free_port()
    server = StratumServer(host="127.0.0.1", port=port)
    await server.start()
    
    # Create job with signBytes already in header (old style)
    job = StratumJob(
        job_id="test-old-style-job",
        header={
            "chainId": 1,
            "height": 100,
            "signBytes": "0x" + "cc" * 80,  # Already in header
            "mixSeed": "0x" + "dd" * 32,    # Already in header
        },
        share_target=0.01,
        theta_micro=800_000,
        sign_bytes="0x" + "aa" * 80,  # Different value (shouldn't override)
        hints={"mixSeed": "0x" + "bb" * 32},  # Different value (shouldn't override)
        height=100,
    )
    
    await server.publish_job(job)
    
    # Connect client
    client = StratumClient(host="127.0.0.1", port=port)
    await client.connect()
    
    received_jobs = []
    client.on_notify = lambda job_data: received_jobs.append(job_data)
    
    await client.subscribe()
    await client.authorize(worker="test_miner", address="anim1test")
    await asyncio.sleep(0.5)
    
    # Verify job was received
    assert len(received_jobs) >= 1
    
    received_header = received_jobs[0].get("header", {})
    
    # Verify existing values are PRESERVED (not overwritten by fix)
    assert received_header["signBytes"] == "0x" + "cc" * 80, \
        "Existing signBytes should be preserved"
    assert received_header["mixSeed"] == "0x" + "dd" * 32, \
        "Existing mixSeed should be preserved"
    
    await client.close()
    await server.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
