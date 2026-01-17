"""
Test that Stratum server provides an initial job to newly connected clients.

This test ensures that clients receive a job immediately after subscribing,
which is critical for miners to start working without waiting.
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
async def test_stratum_client_receives_initial_job():
    """
    Test that a client receives a job immediately after subscription.
    
    This is the scenario that was failing in the bug report:
    1. Server has a job already loaded
    2. Client connects and subscribes
    3. Client should receive the job immediately (not wait for next poll)
    """
    port = _free_port()
    server = StratumServer(host="127.0.0.1", port=port)
    await server.start()
    
    # Pre-load a job into the server BEFORE client connects
    sign_hex = "0x" + "00" * 32
    hints = {"mixSeed": "0x" + "00" * 32}
    initial_job = StratumJob(
        job_id="initial_job",
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
    
    # Publish job to server before client connects
    await server.publish_job(initial_job)
    
    # Now connect a client
    client = StratumClient(host="127.0.0.1", port=port)
    await client.connect()
    
    # Track jobs received by client
    received_jobs = []
    
    async def on_notify(job_data):
        received_jobs.append(job_data)
    
    client.on_notify = on_notify
    
    # Subscribe
    await client.subscribe()
    await client.authorize(worker="test_miner", address="anim1test")
    
    # Wait briefly for job to arrive (should be immediate)
    await asyncio.sleep(0.5)
    
    # Client should have received the initial job
    assert len(received_jobs) >= 1, "Client should receive initial job after subscription"
    
    # Verify it's the correct job
    first_job = received_jobs[0]
    assert first_job.get("jobId") == "initial_job" or first_job.get("job_id") == "initial_job", \
        "Received job should be the initial job"
    
    # Also verify client.last_job is set
    assert client.last_job is not None, "Client last_job should be set after receiving job"
    
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_stratum_client_waits_for_first_job():
    """
    Test that a client can wait for a job that arrives after subscription.
    
    This tests the scenario where:
    1. Client connects and subscribes
    2. No job is available yet
    3. Job becomes available shortly after
    4. Client receives the job via notify
    """
    port = _free_port()
    server = StratumServer(host="127.0.0.1", port=port)
    await server.start()
    
    # Connect client BEFORE any job exists
    client = StratumClient(host="127.0.0.1", port=port)
    await client.connect()
    
    # Track jobs received by client
    received_jobs = []
    job_received = asyncio.Event()
    
    async def on_notify(job_data):
        received_jobs.append(job_data)
        job_received.set()
    
    client.on_notify = on_notify
    
    # Subscribe and authorize
    await client.subscribe()
    await client.authorize(worker="test_miner", address="anim1test")
    
    # At this point, no job should be received yet
    await asyncio.sleep(0.2)
    assert len(received_jobs) == 0, "No job should be received yet"
    
    # Now publish a job
    sign_hex = "0x" + "00" * 32
    hints = {"mixSeed": "0x" + "00" * 32}
    delayed_job = StratumJob(
        job_id="delayed_job",
        header={"signBytes": sign_hex, "height": 1},
        share_target=0.01,
        theta_micro=800_000,
        hints=hints,
        target="0x" + "ff" * 32,
        sign_bytes=sign_hex,
        height=1,
        parent_hash="0x" + "22" * 32,
        parent_height=0,
        chain_id=1,
    )
    
    await server.publish_job(delayed_job)
    
    # Wait for job to arrive
    try:
        await asyncio.wait_for(job_received.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pytest.fail("Client did not receive job within 2 seconds")
    
    # Verify job was received
    assert len(received_jobs) >= 1, "Client should receive job after it's published"
    first_job = received_jobs[0]
    assert first_job.get("jobId") == "delayed_job" or first_job.get("job_id") == "delayed_job", \
        "Received job should be the delayed job"
    
    await client.close()
    await server.stop()
