"""
Useful Work Artifact (UWA) Verification
========================================

This module implements deterministic, bounded verification of Useful Work Artifacts.

Key Requirements:
1. Verification must be deterministic (same input → same result)
2. Verification must be bounded (time/space limits to prevent DoS)
3. Work output must be directly usable by the Python VM
4. Device type (CPU/GPU/Quantum) affects scoring weight

Verification Pipeline:
1. Schema validation (size bounds, required fields)
2. Challenge binding verification (height, prev_hash, chain_id)
3. Domain-specific proof verification
4. Work score computation
5. Return weighted score for consensus

"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, Optional, Tuple

from .uwa_types import (
    DeviceType,
    MAX_UWA_PROOF_SIZE,
    MAX_UWA_TOTAL_SIZE,
    MIN_CPU_WORK_SCORE,
    MIN_GPU_WORK_SCORE,
    MIN_QUANTUM_WORK_SCORE,
    UsefulWorkArtifact,
    UWAVerificationResult,
    VMCompileInput,
    VMCompileOutput,
    VMCompileProof,
    WorkDomain,
)

# Verification time limits (seconds) to prevent DoS
MAX_VERIFICATION_TIME_CPU = 5.0
MAX_VERIFICATION_TIME_GPU = 10.0
MAX_VERIFICATION_TIME_QUANTUM = 15.0


class UWAVerificationError(Exception):
    """Raised when UWA verification fails."""
    pass


def _verify_size_bounds(uwa: UsefulWorkArtifact) -> None:
    """Verify UWA size is within bounds."""
    if len(uwa.proof_data) > MAX_UWA_PROOF_SIZE:
        raise UWAVerificationError(
            f"Proof data exceeds max size: {len(uwa.proof_data)} > {MAX_UWA_PROOF_SIZE}"
        )
    
    total_size = uwa.size_bytes()
    if total_size > MAX_UWA_TOTAL_SIZE:
        raise UWAVerificationError(
            f"Total UWA size exceeds max: {total_size} > {MAX_UWA_TOTAL_SIZE}"
        )


def _verify_challenge_binding(
    uwa: UsefulWorkArtifact,
    header_height: int,
    header_prev_hash: bytes,
    header_chain_id: int,
) -> None:
    """Verify UWA is bound to the correct block context."""
    if not uwa.verify_binding(header_height, header_prev_hash, header_chain_id):
        raise UWAVerificationError(
            f"UWA challenge binding failed: height={uwa.challenge.height} vs {header_height}, "
            f"chain_id={uwa.challenge.chain_id} vs {header_chain_id}"
        )


def _verify_vm_compile_work(
    uwa: UsefulWorkArtifact,
    context: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Verify VM compilation work.
    
    Returns work score in µ-nats.
    """
    # Parse proof data (CBOR or JSON)
    try:
        if uwa.proof_data[0:1] == b'{':
            # JSON format
            proof_dict = json.loads(uwa.proof_data.decode('utf-8'))
        else:
            # CBOR format
            import cbor2
            proof_dict = cbor2.loads(uwa.proof_data)
    except Exception as e:
        raise UWAVerificationError(f"Failed to parse proof data: {e}")
    
    # Extract proof components
    try:
        bytecode = bytes.fromhex(proof_dict['bytecode']) if isinstance(proof_dict['bytecode'], str) else proof_dict['bytecode']
        gas_breakdown = proof_dict.get('gas_breakdown', {})
    except KeyError as e:
        raise UWAVerificationError(f"Missing required proof field: {e}")
    
    # Verify output commitment matches
    bytecode_hash = hashlib.sha3_256(bytecode).digest()
    gas_estimate = sum(gas_breakdown.values())
    
    # Reconstruct output
    output = VMCompileOutput(
        bytecode_hash=bytecode_hash,
        gas_estimate=gas_estimate,
        symbols_count=proof_dict.get('symbols_count', 0),
        dependencies_count=proof_dict.get('dependencies_count', 0),
    )
    
    expected_commitment = hashlib.sha3_256(output.to_bytes()).digest()
    if expected_commitment != uwa.output_commitment:
        raise UWAVerificationError(
            f"Output commitment mismatch: expected {expected_commitment.hex()}, "
            f"got {uwa.output_commitment.hex()}"
        )
    
    # Score based on compilation complexity
    # More complex code (more gas, more symbols) = higher score
    base_score = 100_000  # 0.1 nats base
    complexity_bonus = min(gas_estimate // 1000, 400_000)  # Up to 0.4 nats for complexity
    symbols_bonus = min(output.symbols_count * 1000, 100_000)  # Up to 0.1 nats for symbols
    
    total_score = base_score + complexity_bonus + symbols_bonus
    
    # Apply device type minimum
    min_score = {
        DeviceType.CPU: MIN_CPU_WORK_SCORE,
        DeviceType.GPU: MIN_GPU_WORK_SCORE,
        DeviceType.QUANTUM: MIN_QUANTUM_WORK_SCORE,
    }.get(uwa.device_type, MIN_CPU_WORK_SCORE)
    
    return max(total_score, min_score)


def _verify_hash_work(
    uwa: UsefulWorkArtifact,
    context: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Verify hash-based useful work (device-agnostic fallback).
    
    Uses memory-hard scrypt to verify computational effort.
    Returns work score in µ-nats.
    """
    import hashlib
    
    # Parse proof data
    try:
        if uwa.proof_data[0:1] == b'{':
            proof_dict = json.loads(uwa.proof_data.decode('utf-8'))
        else:
            import cbor2
            proof_dict = cbor2.loads(uwa.proof_data)
    except Exception as e:
        raise UWAVerificationError(f"Failed to parse proof data: {e}")
    
    # Extract components
    try:
        nonce = bytes.fromhex(proof_dict['nonce']) if isinstance(proof_dict['nonce'], str) else proof_dict['nonce']
        iterations = proof_dict.get('iterations', 2**14)  # Default N=16384
        output_hash = bytes.fromhex(proof_dict['output_hash']) if isinstance(proof_dict['output_hash'], str) else proof_dict['output_hash']
    except KeyError as e:
        raise UWAVerificationError(f"Missing required proof field: {e}")
    
    # Clamp iterations to safe bounds
    n_cost = max(2**10, min(2**18, iterations))
    
    # Derive expected output
    job_id = uwa.challenge.to_commitment()
    derived = hashlib.scrypt(
        nonce, salt=job_id, n=n_cost, r=8, p=1, dklen=32
    )
    
    # Verify output matches
    if derived != output_hash:
        raise UWAVerificationError("Hash work output mismatch")
    
    # Verify output commitment
    expected_commitment = hashlib.sha3_256(output_hash).digest()
    if expected_commitment != uwa.output_commitment:
        raise UWAVerificationError("Output commitment mismatch")
    
    # Score based on iterations (more work = higher score)
    # Use log scale: score ≈ log2(n_cost) * base_factor
    import math
    log_factor = math.log2(n_cost)
    base_factor = 50_000  # 0.05 nats per doubling
    
    total_score = int(log_factor * base_factor)
    
    # Apply device type minimum
    min_score = {
        DeviceType.CPU: MIN_CPU_WORK_SCORE,
        DeviceType.GPU: MIN_GPU_WORK_SCORE,
        DeviceType.QUANTUM: MIN_QUANTUM_WORK_SCORE,
    }.get(uwa.device_type, MIN_CPU_WORK_SCORE)
    
    return max(total_score, min_score)


def _verify_quantum_work(
    uwa: UsefulWorkArtifact,
    context: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Verify quantum useful work.
    
    For now, this is a placeholder that validates structure and returns
    a high score. Real quantum verification would involve:
    - Circuit validation
    - Provider attestation verification
    - Trap-circuit outcome verification
    
    Returns work score in µ-nats.
    """
    # Parse proof data
    try:
        if uwa.proof_data[0:1] == b'{':
            proof_dict = json.loads(uwa.proof_data.decode('utf-8'))
        else:
            import cbor2
            proof_dict = cbor2.loads(uwa.proof_data)
    except Exception as e:
        raise UWAVerificationError(f"Failed to parse proof data: {e}")
    
    # Validate required fields
    required = ['circuit_depth', 'qubit_count', 'shots', 'output_digest']
    for field in required:
        if field not in proof_dict:
            raise UWAVerificationError(f"Missing required quantum proof field: {field}")
    
    # Extract metrics
    depth = proof_dict['circuit_depth']
    qubits = proof_dict['qubit_count']
    shots = proof_dict['shots']
    
    # Verify output commitment
    output_digest = bytes.fromhex(proof_dict['output_digest']) if isinstance(proof_dict['output_digest'], str) else proof_dict['output_digest']
    expected_commitment = hashlib.sha3_256(output_digest).digest()
    if expected_commitment != uwa.output_commitment:
        raise UWAVerificationError("Output commitment mismatch")
    
    # Score based on quantum complexity
    # More qubits, deeper circuits, more shots = higher score
    base_score = MIN_QUANTUM_WORK_SCORE  # 1.0 nats minimum
    depth_bonus = min(depth * 10_000, 500_000)  # Up to 0.5 nats for depth
    qubit_bonus = min(qubits * 20_000, 1_000_000)  # Up to 1.0 nats for qubits
    shots_bonus = min(shots * 100, 500_000)  # Up to 0.5 nats for shots
    
    total_score = base_score + depth_bonus + qubit_bonus + shots_bonus
    
    return total_score


def verify_uwa(
    uwa: UsefulWorkArtifact,
    header_height: int,
    header_prev_hash: bytes,
    header_chain_id: int,
    context: Optional[Dict[str, Any]] = None,
) -> UWAVerificationResult:
    """
    Verify a Useful Work Artifact deterministically.
    
    Args:
        uwa: The artifact to verify
        header_height: Block height from header
        header_prev_hash: Previous block hash from header
        header_chain_id: Chain ID from header
        context: Optional verification context (e.g., test overrides)
    
    Returns:
        UWAVerificationResult with validation outcome and work score
    """
    start_time = time.time()
    
    try:
        # 1. Size bounds check
        _verify_size_bounds(uwa)
        
        # 2. Challenge binding check
        _verify_challenge_binding(uwa, header_height, header_prev_hash, header_chain_id)
        
        # 3. Domain-specific verification
        if uwa.work_domain == WorkDomain.VM_COMPILE_V1:
            work_score = _verify_vm_compile_work(uwa, context)
        elif uwa.work_domain == WorkDomain.HASH_WORK_V1:
            work_score = _verify_hash_work(uwa, context)
        elif uwa.work_domain == WorkDomain.QUANTUM_CIRCUIT_V1:
            work_score = _verify_quantum_work(uwa, context)
        else:
            raise UWAVerificationError(f"Unsupported work domain: {uwa.work_domain}")
        
        # 4. Check verification time bounds
        elapsed = time.time() - start_time
        max_time = {
            DeviceType.CPU: MAX_VERIFICATION_TIME_CPU,
            DeviceType.GPU: MAX_VERIFICATION_TIME_GPU,
            DeviceType.QUANTUM: MAX_VERIFICATION_TIME_QUANTUM,
        }.get(uwa.device_type, MAX_VERIFICATION_TIME_CPU)
        
        if elapsed > max_time:
            raise UWAVerificationError(
                f"Verification exceeded time limit: {elapsed:.2f}s > {max_time}s"
            )
        
        return UWAVerificationResult(
            valid=True,
            device_type=uwa.device_type,
            work_score=work_score,
            details={
                'work_domain': uwa.work_domain,
                'verification_time_ms': int(elapsed * 1000),
            }
        )
        
    except UWAVerificationError as e:
        return UWAVerificationResult(
            valid=False,
            device_type=uwa.device_type,
            work_score=0,
            reason=str(e),
        )
    except Exception as e:
        return UWAVerificationResult(
            valid=False,
            device_type=uwa.device_type,
            work_score=0,
            reason=f"Unexpected error: {e}",
        )


__all__ = [
    "verify_uwa",
    "UWAVerificationError",
]
