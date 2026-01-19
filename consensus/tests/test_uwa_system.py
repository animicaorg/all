"""
Tests for Useful Work Artifact (UWA) system
"""

import hashlib
import json
import pytest

from consensus.uwa_types import (
    DeviceType,
    UsefulWorkArtifact,
    WorkChallenge,
    WorkDomain,
    calculate_effective_work,
    WEIGHT_CPU,
    WEIGHT_GPU,
    WEIGHT_QUANTUM,
)
from consensus.uwa_verifier import verify_uwa
from consensus.uwa_generator import (
    create_work_challenge,
    generate_hash_work_uwa,
    generate_quantum_work_uwa,
    generate_vm_compile_uwa,
)


# -------------------------
# Test fixtures
# -------------------------


@pytest.fixture
def work_challenge():
    """Create a sample work challenge."""
    return WorkChallenge(
        height=100,
        prev_hash=b"\x01" * 32,
        chain_id=1337,
        timestamp=1234567890,
        mix_seed=b"\x02" * 32,
    )


@pytest.fixture
def miner_address():
    """Sample miner address."""
    return "anim1test1234567890abcdefghijklmnop"


# -------------------------
# Schema and Binding Tests
# -------------------------


def test_uwa_schema_roundtrip(work_challenge, miner_address):
    """Test UWA can be serialized and deserialized."""
    # Generate a simple hash work UWA
    nonce = b"\x00" * 8
    uwa = generate_hash_work_uwa(
        challenge=work_challenge,
        nonce=nonce,
        iterations=2**14,
        device_type=DeviceType.CPU,
        miner_address=miner_address,
    )
    
    # Verify basic fields
    assert uwa.version == 1
    assert uwa.work_domain == WorkDomain.HASH_WORK_V1
    assert uwa.device_type == DeviceType.CPU
    assert uwa.challenge == work_challenge
    assert uwa.miner == miner_address
    
    # Verify sizes are within bounds
    from consensus.uwa_types import MAX_UWA_PROOF_SIZE, MAX_UWA_TOTAL_SIZE
    assert len(uwa.proof_data) <= MAX_UWA_PROOF_SIZE
    assert uwa.size_bytes() <= MAX_UWA_TOTAL_SIZE


def test_uwa_binds_to_block_context(work_challenge, miner_address):
    """Test UWA is properly bound to block context."""
    nonce = b"\x00" * 8
    uwa = generate_hash_work_uwa(
        challenge=work_challenge,
        nonce=nonce,
        iterations=2**14,
        device_type=DeviceType.CPU,
        miner_address=miner_address,
    )
    
    # Should verify with correct context
    assert uwa.verify_binding(
        header_height=100,
        header_prev_hash=b"\x01" * 32,
        header_chain_id=1337,
    )
    
    # Should fail with wrong height
    assert not uwa.verify_binding(
        header_height=101,
        header_prev_hash=b"\x01" * 32,
        header_chain_id=1337,
    )
    
    # Should fail with wrong prev_hash
    assert not uwa.verify_binding(
        header_height=100,
        header_prev_hash=b"\x99" * 32,
        header_chain_id=1337,
    )
    
    # Should fail with wrong chain_id
    assert not uwa.verify_binding(
        header_height=100,
        header_prev_hash=b"\x01" * 32,
        header_chain_id=1338,
    )


def test_uwa_rejects_replay_other_height(work_challenge, miner_address):
    """Test UWA cannot be replayed at different height."""
    nonce = b"\x00" * 8
    uwa = generate_hash_work_uwa(
        challenge=work_challenge,
        nonce=nonce,
        iterations=2**14,
        device_type=DeviceType.CPU,
        miner_address=miner_address,
    )
    
    # Verify with correct height
    result = verify_uwa(
        uwa=uwa,
        header_height=100,
        header_prev_hash=b"\x01" * 32,
        header_chain_id=1337,
    )
    assert result.valid
    
    # Should reject at different height
    result = verify_uwa(
        uwa=uwa,
        header_height=101,
        header_prev_hash=b"\x01" * 32,
        header_chain_id=1337,
    )
    assert not result.valid
    assert "binding failed" in result.reason.lower()


def test_uwa_verification_bounded(work_challenge, miner_address):
    """Test UWA verification completes within time bounds."""
    import time
    
    nonce = b"\x00" * 8
    uwa = generate_hash_work_uwa(
        challenge=work_challenge,
        nonce=nonce,
        iterations=2**14,  # Reasonable iteration count
        device_type=DeviceType.CPU,
        miner_address=miner_address,
    )
    
    # Time the verification
    start = time.time()
    result = verify_uwa(
        uwa=uwa,
        header_height=100,
        header_prev_hash=b"\x01" * 32,
        header_chain_id=1337,
    )
    elapsed = time.time() - start
    
    # Should complete successfully
    assert result.valid
    
    # Should complete within reasonable time (< 5 seconds for CPU)
    from consensus.uwa_verifier import MAX_VERIFICATION_TIME_CPU
    assert elapsed < MAX_VERIFICATION_TIME_CPU


# -------------------------
# Weighted Work Scoring Tests
# -------------------------


def test_gpu_score_increases_effective_work():
    """Test GPU work scores higher than CPU work."""
    cpu_score = 100_000
    gpu_score = 100_000
    quantum_score = 0
    
    # CPU only
    cpu_only = calculate_effective_work(cpu_score, 0, 0)
    
    # CPU + GPU
    cpu_gpu = calculate_effective_work(cpu_score, gpu_score, 0)
    
    # GPU should add 5x more weight
    assert cpu_gpu > cpu_only
    assert cpu_gpu == cpu_only + int(gpu_score * WEIGHT_GPU)


def test_quantum_score_increases_effective_work_more():
    """Test Quantum work scores highest."""
    base_score = 100_000
    
    # CPU only
    cpu_only = calculate_effective_work(base_score, 0, 0)
    
    # GPU only
    gpu_only = calculate_effective_work(0, base_score, 0)
    
    # Quantum only
    quantum_only = calculate_effective_work(0, 0, base_score)
    
    # Quantum should score highest
    assert quantum_only > gpu_only > cpu_only
    
    # Verify exact weights
    assert gpu_only == int(base_score * WEIGHT_GPU)
    assert quantum_only == int(base_score * WEIGHT_QUANTUM)
    assert WEIGHT_QUANTUM > WEIGHT_GPU > WEIGHT_CPU


def test_invalid_gpu_uwa_gives_no_score_and_block_rejects(work_challenge, miner_address):
    """Test invalid GPU UWA results in zero score."""
    nonce = b"\x00" * 8
    uwa = generate_hash_work_uwa(
        challenge=work_challenge,
        nonce=nonce,
        iterations=2**14,
        device_type=DeviceType.GPU,
        miner_address=miner_address,
    )
    
    # Corrupt the output commitment
    corrupted_uwa = UsefulWorkArtifact(
        version=uwa.version,
        work_domain=uwa.work_domain,
        device_type=uwa.device_type,
        challenge=uwa.challenge,
        input_commitment=uwa.input_commitment,
        output_commitment=b"\x99" * 32,  # Wrong commitment
        proof_data=uwa.proof_data,
        work_score=uwa.work_score,
        miner=uwa.miner,
        timestamp=uwa.timestamp,
    )
    
    # Verify - should fail
    result = verify_uwa(
        uwa=corrupted_uwa,
        header_height=100,
        header_prev_hash=b"\x01" * 32,
        header_chain_id=1337,
    )
    
    assert not result.valid
    assert result.work_score == 0


def test_effective_work_deterministic_across_nodes():
    """Test effective work calculation is deterministic."""
    scores = [
        (100_000, 200_000, 50_000),
        (0, 500_000, 0),
        (150_000, 0, 100_000),
    ]
    
    # Calculate multiple times
    for cpu, gpu, quantum in scores:
        result1 = calculate_effective_work(cpu, gpu, quantum)
        result2 = calculate_effective_work(cpu, gpu, quantum)
        result3 = calculate_effective_work(cpu, gpu, quantum)
        
        # Should always be the same
        assert result1 == result2 == result3


# -------------------------
# Device Type Tests
# -------------------------


def test_cpu_uwa_accepts_valid(work_challenge, miner_address):
    """Test CPU UWA with valid proof accepts."""
    nonce = b"\x00" * 8
    uwa = generate_hash_work_uwa(
        challenge=work_challenge,
        nonce=nonce,
        iterations=2**14,
        device_type=DeviceType.CPU,
        miner_address=miner_address,
    )
    
    result = verify_uwa(
        uwa=uwa,
        header_height=100,
        header_prev_hash=b"\x01" * 32,
        header_chain_id=1337,
    )
    
    assert result.valid
    assert result.device_type == DeviceType.CPU
    assert result.work_score > 0


def test_gpu_uwa_accepts_valid(work_challenge, miner_address):
    """Test GPU UWA with valid proof accepts."""
    nonce = b"\x00" * 8
    uwa = generate_hash_work_uwa(
        challenge=work_challenge,
        nonce=nonce,
        iterations=2**16,  # Higher iterations for GPU
        device_type=DeviceType.GPU,
        miner_address=miner_address,
    )
    
    result = verify_uwa(
        uwa=uwa,
        header_height=100,
        header_prev_hash=b"\x01" * 32,
        header_chain_id=1337,
    )
    
    assert result.valid
    assert result.device_type == DeviceType.GPU
    assert result.work_score > 0


def test_quantum_uwa_accepts_valid(work_challenge, miner_address):
    """Test Quantum UWA with valid proof accepts."""
    uwa = generate_quantum_work_uwa(
        challenge=work_challenge,
        circuit_depth=50,
        qubit_count=20,
        shots=1000,
        miner_address=miner_address,
    )
    
    result = verify_uwa(
        uwa=uwa,
        header_height=100,
        header_prev_hash=b"\x01" * 32,
        header_chain_id=1337,
    )
    
    assert result.valid
    assert result.device_type == DeviceType.QUANTUM
    assert result.work_score > 0


def test_quantum_uwa_rejects_invalid(work_challenge, miner_address):
    """Test Quantum UWA with invalid proof rejects."""
    uwa = generate_quantum_work_uwa(
        challenge=work_challenge,
        circuit_depth=50,
        qubit_count=20,
        shots=1000,
        miner_address=miner_address,
    )
    
    # Corrupt proof data
    corrupted_uwa = UsefulWorkArtifact(
        version=uwa.version,
        work_domain=uwa.work_domain,
        device_type=uwa.device_type,
        challenge=uwa.challenge,
        input_commitment=uwa.input_commitment,
        output_commitment=uwa.output_commitment,
        proof_data=b"{}",  # Invalid proof
        work_score=uwa.work_score,
        miner=uwa.miner,
        timestamp=uwa.timestamp,
    )
    
    result = verify_uwa(
        uwa=corrupted_uwa,
        header_height=100,
        header_prev_hash=b"\x01" * 32,
        header_chain_id=1337,
    )
    
    assert not result.valid


# -------------------------
# VM Compilation Work Tests
# -------------------------


def test_vm_compile_uwa_generates_correctly(work_challenge, miner_address):
    """Test VM compilation UWA generation."""
    source_code = """
def hello(name):
    return f"Hello, {name}!"

def calculate(a, b):
    return a + b
"""
    
    manifest = {
        "name": "test_contract",
        "version": "1.0.0",
        "abi": [],
    }
    
    uwa = generate_vm_compile_uwa(
        challenge=work_challenge,
        source_code=source_code,
        manifest=manifest,
        device_type=DeviceType.CPU,
        miner_address=miner_address,
    )
    
    assert uwa.work_domain == WorkDomain.VM_COMPILE_V1
    assert uwa.device_type == DeviceType.CPU
    assert len(uwa.proof_data) > 0
    
    # Verify it
    result = verify_uwa(
        uwa=uwa,
        header_height=100,
        header_prev_hash=b"\x01" * 32,
        header_chain_id=1337,
    )
    
    assert result.valid
    assert result.work_score > 0
