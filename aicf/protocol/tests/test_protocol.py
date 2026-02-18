"""
AICF Protocol Tests
===================

Basic unit tests for the AICF GPU redistribution protocol.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from aicf.protocol.state import ProtocolState, GPUWorker, TrainingJob, WorkSubmission
from aicf.protocol.economics import EpochAccountant, RewardSplit
from aicf.protocol.rpc import ProtocolRPCMethods


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        yield db_path


@pytest.fixture
def protocol_state(temp_db):
    """Create a protocol state instance."""
    return ProtocolState(temp_db)


@pytest.fixture
def accountant(protocol_state):
    """Create an epoch accountant."""
    return EpochAccountant(protocol_state)


@pytest.fixture
def rpc_methods(protocol_state, accountant):
    """Create RPC methods."""
    return ProtocolRPCMethods(protocol_state, accountant)


class TestProtocolState:
    """Test protocol state management."""

    def test_register_worker(self, protocol_state):
        """Test worker registration."""
        worker = GPUWorker(
            worker_id="worker_001",
            address="anim1test123",
            display_name="Test Worker",
            stake_amount=1000000000,
            status="ACTIVE",
        )
        
        worker_id = protocol_state.register_worker(worker)
        assert worker_id == "worker_001"
        
        # Retrieve worker
        retrieved = protocol_state.get_worker("worker_001")
        assert retrieved.address == "anim1test123"
        assert retrieved.display_name == "Test Worker"
        assert retrieved.stake_amount == 1000000000
        assert retrieved.status == "ACTIVE"

    def test_list_workers(self, protocol_state):
        """Test listing workers."""
        # Register multiple workers
        for i in range(5):
            worker = GPUWorker(
                worker_id=f"worker_{i:03d}",
                address=f"anim1test{i}",
                status="ACTIVE",
            )
            protocol_state.register_worker(worker)
        
        # List all workers
        workers = protocol_state.list_workers(limit=10)
        assert len(workers) == 5
        
        # List with pagination
        workers_page1 = protocol_state.list_workers(limit=2)
        assert len(workers_page1) == 2
        
        workers_page2 = protocol_state.list_workers(offset=2, limit=2)
        assert len(workers_page2) == 2

    def test_create_job(self, protocol_state):
        """Test job creation."""
        job = TrainingJob(
            job_id="job_001",
            spec_hash="0xabc123",
            job_type="TRAINING",
            difficulty=5,
            reward_weight=100,
        )
        
        job_id = protocol_state.create_job(job)
        assert job_id == "job_001"
        
        # Retrieve job
        retrieved = protocol_state.get_job("job_001")
        assert retrieved.spec_hash == "0xabc123"
        assert retrieved.job_type == "TRAINING"
        assert retrieved.difficulty == 5

    def test_submit_work(self, protocol_state):
        """Test work submission."""
        # Create worker and job first
        worker = GPUWorker(worker_id="worker_001", address="anim1test")
        protocol_state.register_worker(worker)
        
        job = TrainingJob(job_id="job_001", spec_hash="0xabc")
        protocol_state.create_job(job)
        
        # Submit work
        submission = WorkSubmission(
            submission_id="sub_001",
            job_id="job_001",
            worker_id="worker_001",
            artifact_commit="0xdef456",
            proof_commit="0xghi789",
            challenge_deadline=1100,
        )
        
        sub_id = protocol_state.submit_work(submission)
        assert sub_id == "sub_001"
        
        # Retrieve submission
        retrieved = protocol_state.get_submission("sub_001")
        assert retrieved.artifact_commit == "0xdef456"
        assert retrieved.worker_id == "worker_001"
        assert retrieved.status == "PENDING"

    def test_epoch_management(self, protocol_state):
        """Test epoch creation and management."""
        # Create epoch
        epoch = protocol_state.get_or_create_epoch(1, 0)
        assert epoch.epoch_id == 1
        assert epoch.start_height == 0
        assert epoch.inflow_total == "0"
        assert not epoch.finalized
        
        # Update epoch
        protocol_state.update_epoch(1, inflow_total="1000000000")
        
        # Retrieve updated epoch
        updated = protocol_state.get_epoch(1)
        assert updated.inflow_total == "1000000000"

    def test_credits(self, protocol_state):
        """Test credit management."""
        # Create worker and epoch
        worker = GPUWorker(worker_id="worker_001", address="anim1test")
        protocol_state.register_worker(worker)
        
        epoch = protocol_state.get_or_create_epoch(1, 0)
        
        # Add credits
        protocol_state.add_credits(1, "worker_001", "100")
        
        # Check credits
        credits = protocol_state.get_worker_credits(1, "worker_001")
        assert credits == "100"
        
        # Add more credits
        protocol_state.add_credits(1, "worker_001", "50")
        credits = protocol_state.get_worker_credits(1, "worker_001")
        assert credits == "150"


class TestEconomics:
    """Test epoch economics."""

    def test_reward_split(self):
        """Test reward split validation."""
        # Valid split
        split = RewardSplit(
            gpu_workers_bp=7000,
            treasury_bp=2000,
            dev_bp=500,
            burn_bp=500,
        )
        assert split.gpu_workers_bp == 7000
        
        # Invalid split (doesn't total 10000)
        with pytest.raises(ValueError):
            RewardSplit(
                gpu_workers_bp=5000,
                treasury_bp=2000,
                dev_bp=500,
                burn_bp=500,
            )

    def test_record_inflow(self, accountant, protocol_state):
        """Test inflow recording."""
        # Create epoch
        protocol_state.get_or_create_epoch(1, 0)
        
        # Record ENA inflow
        inflow_id = accountant.record_inflow(
            epoch_id=1,
            source="ena",
            amount="1000000000",
            tx_hash="0xabc123",
        )
        
        assert inflow_id.startswith("inflow_")
        
        # Check epoch was updated
        epoch = protocol_state.get_epoch(1)
        assert epoch.inflow_ena == "1000000000"
        assert epoch.inflow_total == "1000000000"

    def test_award_credits(self, accountant, protocol_state):
        """Test credit awarding."""
        # Create worker and epoch
        worker = GPUWorker(worker_id="worker_001", address="anim1test")
        protocol_state.register_worker(worker)
        
        protocol_state.get_or_create_epoch(1, 0)
        
        # Award credits
        accountant.award_credits(1, "worker_001", 100)
        
        # Check credits
        credits = protocol_state.get_worker_credits(1, "worker_001")
        assert credits == "100"
        
        # Check epoch total
        epoch = protocol_state.get_epoch(1)
        assert epoch.total_credits == "100"

    def test_finalize_epoch(self, accountant, protocol_state):
        """Test epoch finalization."""
        # Setup: create workers and epoch
        for i in range(3):
            worker = GPUWorker(worker_id=f"worker_{i}", address=f"anim{i}")
            protocol_state.register_worker(worker)
        
        protocol_state.get_or_create_epoch(1, 0)
        
        # Record inflow
        accountant.record_inflow(1, "ena", "10000000000")
        
        # Award credits
        accountant.award_credits(1, "worker_0", 100)
        accountant.award_credits(1, "worker_1", 200)
        accountant.award_credits(1, "worker_2", 100)
        # Total: 400 credits
        
        # Finalize epoch
        rewards = accountant.finalize_epoch(1, 1000)
        
        # Check rewards
        # Total inflow: 10000000000
        # Workers get 70% = 7000000000
        # Worker 0: 7000000000 * 100 / 400 = 1750000000
        # Worker 1: 7000000000 * 200 / 400 = 3500000000
        # Worker 2: 7000000000 * 100 / 400 = 1750000000
        
        assert rewards["worker_0"] == "1750000000"
        assert rewards["worker_1"] == "3500000000"
        assert rewards["worker_2"] == "1750000000"
        
        # Check epoch is finalized
        epoch = protocol_state.get_epoch(1)
        assert epoch.finalized
        assert epoch.end_height == 1000

    def test_create_claim(self, accountant, protocol_state):
        """Test claim creation."""
        # Setup
        worker = GPUWorker(worker_id="worker_001", address="anim1test")
        protocol_state.register_worker(worker)
        
        protocol_state.get_or_create_epoch(1, 0)
        accountant.record_inflow(1, "ena", "10000000000")
        accountant.award_credits(1, "worker_001", 100)
        accountant.finalize_epoch(1, 1000)
        
        # Create claim
        claim_id, amount = accountant.create_claim(1, "worker_001")
        
        assert claim_id.startswith("claim_")
        assert int(amount) > 0
        
        # Expected: 10000000000 * 0.7 = 7000000000 (all to one worker)
        assert amount == "7000000000"


class TestRPCMethods:
    """Test RPC methods."""

    def test_register_worker(self, rpc_methods):
        """Test worker registration via RPC."""
        methods = rpc_methods.make_methods()
        register = methods["aicf.protocol.registerWorker"]
        
        result = register(
            address="anim1test123",
            displayName="Test Worker",
            stakeAmount=1000000000,
        )
        
        assert "workerId" in result
        assert result["address"] == "anim1test123"
        assert result["status"] == "ACTIVE"

    def test_create_job(self, rpc_methods):
        """Test job creation via RPC."""
        methods = rpc_methods.make_methods()
        create = methods["aicf.protocol.createJob"]
        
        result = create(
            specHash="0xabc123",
            jobType="TRAINING",
            difficulty=5,
        )
        
        assert "jobId" in result
        assert result["specHash"] == "0xabc123"
        assert result["status"] == "OPEN"

    def test_submit_work(self, rpc_methods, protocol_state):
        """Test work submission via RPC."""
        # Setup: create worker and job
        worker = GPUWorker(worker_id="worker_001", address="anim1test")
        protocol_state.register_worker(worker)
        
        job = TrainingJob(job_id="job_001", spec_hash="0xabc")
        protocol_state.create_job(job)
        
        # Submit via RPC
        methods = rpc_methods.make_methods()
        submit = methods["aicf.protocol.submitWork"]
        
        result = submit(
            jobId="job_001",
            workerId="worker_001",
            artifactCommit="0xdef456",
            currentHeight=1000,
        )
        
        assert "submissionId" in result
        assert result["status"] == "PENDING"
        assert "challengeDeadline" in result

    def test_get_status(self, rpc_methods):
        """Test protocol status via RPC."""
        methods = rpc_methods.make_methods()
        get_status = methods["aicf.protocol.getStatus"]
        
        result = get_status()
        
        assert "params" in result
        assert "currentEpoch" in result
        assert result["params"]["epochLengthBlocks"] == 1000
        assert result["params"]["challengeWindowBlocks"] == 100
        assert result["params"]["rewardSplit"]["gpuWorkersBp"] == 7000
