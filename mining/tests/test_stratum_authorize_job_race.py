"""
Test that stratum clients receive jobs after authorization.

This test verifies the fix for the race condition where clients would
not receive jobs because the authorize_hook was called after the
authorization response was sent.
"""
import asyncio
import socket

import pytest

from mining.stratum_client import StratumClient
from mining.stratum_server import StratumJob, StratumServer


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return port


@pytest.mark.asyncio
async def test_client_receives_job_after_authorization():
    """
    Test that a client receives a job immediately after authorization.
    
    This reproduces the bug where:
    1. Server starts with no job
    2. Client connects and subscribes (no job available)
    3. Client authorizes
    4. authorize_hook publishes a job (runs before auth response is sent)
    5. Client should receive the job after authorization completes
    """
    port = _free_port()
    
    # Track authorize hook calls
    hook_called = asyncio.Event()
    published_job_id = None
    
    async def authorize_hook(session, worker, address):
        nonlocal published_job_id
        # Simulate fetching template and publishing job
        await asyncio.sleep(0.1)  # Simulate some delay
        
        sign_hex = "0x" + "00" * 32
        hints = {"mixSeed": "0x" + "00" * 32}
        job = StratumJob(
            job_id="test-job-1",
            header={"signBytes": sign_hex},
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
        await server.publish_job(job)
        published_job_id = job.job_id
        hook_called.set()
    
    # Create server with authorize hook
    server = StratumServer(
        host="127.0.0.1",
        port=port,
        authorize_hook=authorize_hook,
    )
    await server.start()
    
    # Create client
    client = StratumClient(host="127.0.0.1", port=port)
    await client.connect()
    
    # Subscribe (no job available yet)
    await client.subscribe()
    assert client.last_job is None, "No job should be available before authorization"
    
    # Authorize (should trigger job publication via hook)
    await client.authorize(worker="test-worker", address="anim1test123")
    
    # Wait a bit for the NOTIFY to be processed by the client's rx loop
    await asyncio.sleep(0.2)
    
    # Verify hook was called
    assert hook_called.is_set(), "Authorize hook should have been called"
    
    # Verify client received the job
    assert client.last_job is not None, "Client should have received job after authorization"
    assert client.last_job.get("jobId") == published_job_id, "Client should have received the correct job"
    
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_client_waits_for_job_like_cli():
    """
    Test that simulates the actual CLI flow where client waits for a job.
    
    This tests the exact scenario from the bug report:
    1. Server starts with no job
    2. Client connects, subscribes, authorizes
    3. Client waits for job (up to 10 seconds)
    4. Job should arrive within the wait period
    """
    port = _free_port()
    
    async def authorize_hook(session, worker, address):
        # Simulate fetching template (with small delay)
        await asyncio.sleep(0.2)
        
        sign_hex = "0x" + "aa" * 32
        hints = {"mixSeed": "0x" + "bb" * 32}
        job = StratumJob(
            job_id="cli-test-job",
            header={"signBytes": sign_hex, "height": 42},
            share_target=0.01,
            theta_micro=800_000,
            hints=hints,
            target="0x" + "ff" * 32,
            sign_bytes=sign_hex,
            height=42,
            parent_hash="0x" + "cc" * 32,
            parent_height=41,
            chain_id=1,
        )
        await server.publish_job(job)
    
    server = StratumServer(
        host="127.0.0.1",
        port=port,
        authorize_hook=authorize_hook,
    )
    await server.start()
    
    client = StratumClient(host="127.0.0.1", port=port)
    await client.connect()
    await client.subscribe()
    await client.authorize(worker="cli-miner", address="anim1validaddress")
    
    # Simulate the CLI's wait loop (wait up to 10 seconds for job)
    job_received = False
    for _ in range(100):  # 100 * 0.1s = 10 seconds max
        if client.last_job:
            job_received = True
            break
        await asyncio.sleep(0.1)
    
    assert job_received, "Job should have been received within 10 seconds (like CLI expects)"
    assert client.last_job.get("jobId") == "cli-test-job"
    assert client.last_job.get("height") == 42
    
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_authorize_hook_runs_before_response():
    """
    Test that the authorize hook completes before the authorization response is sent.
    
    This ensures the fix is working: authorize_hook is called and awaited BEFORE
    the authorization response is sent to the client.
    """
    port = _free_port()
    
    hook_start_time = None
    hook_end_time = None
    auth_response_time = None
    
    async def authorize_hook(session, worker, address):
        nonlocal hook_start_time, hook_end_time
        hook_start_time = asyncio.get_event_loop().time()
        await asyncio.sleep(0.1)  # Simulate work
        hook_end_time = asyncio.get_event_loop().time()
    
    server = StratumServer(
        host="127.0.0.1",
        port=port,
        authorize_hook=authorize_hook,
    )
    await server.start()
    
    client = StratumClient(host="127.0.0.1", port=port)
    await client.connect()
    await client.subscribe()
    
    # Authorize and record when we get the response
    pre_auth_time = asyncio.get_event_loop().time()
    await client.authorize(worker="test", address="anim1test")
    auth_response_time = asyncio.get_event_loop().time()
    
    # Verify timing: hook should complete before response is received
    assert hook_start_time is not None, "Hook should have started"
    assert hook_end_time is not None, "Hook should have completed"
    assert hook_start_time >= pre_auth_time, "Hook should start after authorize call"
    assert hook_end_time <= auth_response_time, "Hook should complete before auth response"
    
    await client.close()
    await server.stop()
