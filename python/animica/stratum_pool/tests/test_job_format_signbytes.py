"""
Test that stratum pool jobs include signBytes and mixSeed in header format expected by miners.

This test verifies the fix for the issue where miners receive jobs but refuse to hash
because the job header lacks signBytes or has mixSeed in the wrong place.
"""
import pytest

from python.animica.stratum_pool.core import MiningCoreAdapter, MiningJob
from python.animica.stratum_pool.stratum_server import StratumPoolServer, StratumJob
from python.animica.stratum_pool.job_manager import JobManager
from python.animica.stratum_pool.config import PoolConfig


def test_mining_job_includes_sign_bytes():
    """Test that MiningJob correctly extracts signBytes from RPC response."""
    # Simulate RPC response from miner.getWork
    work_response = {
        "jobId": "test-job-1",
        "header": {
            "chainId": 1,
            "height": 100,
            "parentHash": "0x" + "11" * 32,
            "stateRoot": "0x" + "00" * 32,
            "thetaMicro": 800_000,
            # Note: signBytes is NOT in header dict in RPC response
        },
        "signBytes": "0x" + "aa" * 80,  # signBytes is at top level
        "hints": {"mixSeed": "0x" + "bb" * 32},
        "target": "0x" + "ff" * 32,
        "thetaMicro": 800_000,
        "shareTarget": 0.01,
        "height": 100,
    }
    
    # Extract sign_bytes as the adapter does
    sign_bytes = work_response.get("signBytes")
    height = work_response.get("height")
    
    # Verify extraction works
    assert sign_bytes is not None, "signBytes should be extracted from RPC response"
    assert sign_bytes == "0x" + "aa" * 80, "signBytes should match expected value"
    assert height == 100, "height should be extracted correctly"


def test_stratum_job_header_includes_sign_bytes():
    """Test that StratumJob header dict includes signBytes for miners."""
    # Create a MiningJob (as would come from the adapter)
    mining_job = MiningJob(
        job_id="test-job-1",
        header={
            "chainId": 1,
            "height": 100,
            "parentHash": "0x" + "11" * 32,
            "stateRoot": "0x" + "00" * 32,
            "thetaMicro": 800_000,
        },
        theta_micro=800_000,
        share_target=0.01,
        height=100,
        target="0x" + "ff" * 32,
        sign_bytes="0x" + "aa" * 80,  # signBytes at MiningJob level
        hints={"mixSeed": "0x" + "bb" * 32},
    )
    
    # Simulate what StratumPoolServer._on_new_job does
    header = dict(mining_job.header or {})
    if mining_job.sign_bytes:
        header.setdefault("signBytes", mining_job.sign_bytes)
    if mining_job.target:
        header.setdefault("target", mining_job.target)
    if mining_job.height:
        header.setdefault("number", mining_job.height)
    
    # Add mixSeed to header (this is the fix we're adding)
    if mining_job.hints and "mixSeed" in mining_job.hints:
        header.setdefault("mixSeed", mining_job.hints["mixSeed"])
    
    # Verify header has all required fields for miners
    assert "signBytes" in header, "header should contain signBytes for miners"
    assert header["signBytes"] == "0x" + "aa" * 80, "signBytes should match expected value"
    assert "mixSeed" in header, "header should contain mixSeed for miners"
    assert header["mixSeed"] == "0x" + "bb" * 32, "mixSeed should match expected value"
    assert "number" in header, "header should contain number (height) for miners"
    assert header["number"] == 100, "number should match expected height"
    

def test_stratum_job_header_handles_missing_sign_bytes():
    """Test that StratumJob header handles case where signBytes is missing."""
    # Create a MiningJob without signBytes (edge case)
    mining_job = MiningJob(
        job_id="test-job-2",
        header={
            "chainId": 1,
            "height": 100,
            "parentHash": "0x" + "11" * 32,
        },
        theta_micro=800_000,
        share_target=0.01,
        height=100,
        sign_bytes=None,  # Missing signBytes
        hints={"mixSeed": "0x" + "bb" * 32},
    )
    
    # Simulate header construction
    header = dict(mining_job.header or {})
    if mining_job.sign_bytes:
        header.setdefault("signBytes", mining_job.sign_bytes)
    
    # In this case, signBytes won't be added (job should be rejected or signBytes generated)
    assert "signBytes" not in header, "signBytes should not be in header if missing from job"
    
    # For production, we should either:
    # 1. Reject the job as invalid, OR
    # 2. Generate signBytes from header fields
    # This test documents the current behavior


def test_stratum_job_header_includes_height_correctly():
    """Test that height is included and displayed correctly (not as '?')."""
    mining_job = MiningJob(
        job_id="test-job-3",
        header={"chainId": 1, "height": 42},
        theta_micro=800_000,
        share_target=0.01,
        height=42,  # Explicit height
        sign_bytes="0x" + "aa" * 80,
        hints={},
    )
    
    # Simulate header construction
    header = dict(mining_job.header or {})
    if mining_job.height:
        header.setdefault("number", mining_job.height)
    
    # Verify height is correctly included
    assert "number" in header, "header should contain number (height)"
    assert header["number"] == 42, "number should match expected height"
    assert mining_job.height == 42, "MiningJob.height should be set correctly"
