"""
Useful Work Artifact (UWA) Generation
======================================

This module implements UWA generation for miners across different device types.

Miners use these functions to:
1. Generate a WorkChallenge from block template
2. Perform useful work (VM compilation, hash work, quantum circuits)
3. Package the work into a UWA for block submission

"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, Optional

from consensus.uwa_types import (
    DeviceType,
    UsefulWorkArtifact,
    VMCompileInput,
    VMCompileOutput,
    VMCompileProof,
    WorkChallenge,
    WorkDomain,
)


def create_work_challenge(
    height: int,
    prev_hash: bytes,
    chain_id: int,
    timestamp: int,
    mix_seed: bytes,
) -> WorkChallenge:
    """Create a WorkChallenge from block template context."""
    return WorkChallenge(
        height=height,
        prev_hash=prev_hash,
        chain_id=chain_id,
        timestamp=timestamp,
        mix_seed=mix_seed,
    )


def generate_vm_compile_uwa(
    challenge: WorkChallenge,
    source_code: str,
    manifest: Dict[str, Any],
    device_type: DeviceType,
    miner_address: str,
) -> UsefulWorkArtifact:
    """
    Generate a UWA by compiling Python VM source code.
    
    This performs actual useful work that benefits the chain:
    - Compiles contract source to bytecode
    - Estimates gas costs
    - Produces reusable compilation artifacts
    
    Args:
        challenge: Work challenge binding to block context
        source_code: Python source code to compile
        manifest: Contract manifest dictionary
        device_type: CPU/GPU/Quantum (affects scoring)
        miner_address: Bech32m address for attribution
    
    Returns:
        UsefulWorkArtifact ready for block inclusion
    """
    # Create input commitment
    source_hash = hashlib.sha3_256(source_code.encode('utf-8')).digest()
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode('utf-8')
    manifest_hash = hashlib.sha3_256(manifest_bytes).digest()
    
    input_obj = VMCompileInput(
        source_hash=source_hash,
        manifest_hash=manifest_hash,
        abi_version=1,
    )
    input_commitment = hashlib.sha3_256(input_obj.to_bytes()).digest()
    
    # Perform compilation (simplified for demo - real implementation would use vm_py.compiler)
    # For now, generate a simple bytecode representation
    bytecode = _simple_compile(source_code)
    
    # Estimate gas (simplified - real implementation would use vm_py.compiler.gas_estimator)
    gas_breakdown = _simple_gas_estimate(source_code)
    gas_estimate = sum(gas_breakdown.values())
    
    # Count symbols (simplified)
    symbols_count = len([line for line in source_code.split('\n') if 'def ' in line])
    dependencies_count = len([line for line in source_code.split('\n') if 'import ' in line])
    
    # Create output commitment
    bytecode_hash = hashlib.sha3_256(bytecode).digest()
    output_obj = VMCompileOutput(
        bytecode_hash=bytecode_hash,
        gas_estimate=gas_estimate,
        symbols_count=symbols_count,
        dependencies_count=dependencies_count,
    )
    output_commitment = hashlib.sha3_256(output_obj.to_bytes()).digest()
    
    # Package proof
    proof_dict = {
        'bytecode': bytecode.hex(),
        'gas_breakdown': gas_breakdown,
        'symbols_count': symbols_count,
        'dependencies_count': dependencies_count,
    }
    proof_data = json.dumps(proof_dict, sort_keys=True).encode('utf-8')
    
    # Calculate work score (verifier will recompute this)
    base_score = 100_000
    complexity_bonus = min(gas_estimate // 1000, 400_000)
    symbols_bonus = min(symbols_count * 1000, 100_000)
    work_score = base_score + complexity_bonus + symbols_bonus
    
    return UsefulWorkArtifact(
        version=1,
        work_domain=WorkDomain.VM_COMPILE_V1,
        device_type=device_type,
        challenge=challenge,
        input_commitment=input_commitment,
        output_commitment=output_commitment,
        proof_data=proof_data,
        work_score=work_score,
        miner=miner_address,
        timestamp=int(time.time()),
    )


def generate_hash_work_uwa(
    challenge: WorkChallenge,
    nonce: bytes,
    iterations: int,
    device_type: DeviceType,
    miner_address: str,
) -> UsefulWorkArtifact:
    """
    Generate a UWA using memory-hard hash work.
    
    This is the device-agnostic fallback that still provides useful work
    through scrypt (memory-hard function that resists ASICs).
    
    Args:
        challenge: Work challenge binding to block context
        nonce: 8-byte nonce found by miner
        iterations: Scrypt N parameter (power of 2)
        device_type: CPU/GPU/Quantum (affects scoring)
        miner_address: Bech32m address for attribution
    
    Returns:
        UsefulWorkArtifact ready for block inclusion
    """
    # Input is just the challenge
    input_commitment = challenge.to_commitment()
    
    # Perform scrypt hash work
    job_id = challenge.to_commitment()
    n_cost = max(2**10, min(2**18, iterations))
    output_hash = hashlib.scrypt(
        nonce, salt=job_id, n=n_cost, r=8, p=1, dklen=32
    )
    
    # Output commitment
    output_commitment = hashlib.sha3_256(output_hash).digest()
    
    # Package proof
    proof_dict = {
        'nonce': nonce.hex(),
        'iterations': n_cost,
        'output_hash': output_hash.hex(),
    }
    proof_data = json.dumps(proof_dict, sort_keys=True).encode('utf-8')
    
    # Calculate work score
    import math
    log_factor = math.log2(n_cost)
    base_factor = 50_000
    work_score = int(log_factor * base_factor)
    
    return UsefulWorkArtifact(
        version=1,
        work_domain=WorkDomain.HASH_WORK_V1,
        device_type=device_type,
        challenge=challenge,
        input_commitment=input_commitment,
        output_commitment=output_commitment,
        proof_data=proof_data,
        work_score=work_score,
        miner=miner_address,
        timestamp=int(time.time()),
    )


def generate_quantum_work_uwa(
    challenge: WorkChallenge,
    circuit_depth: int,
    qubit_count: int,
    shots: int,
    miner_address: str,
) -> UsefulWorkArtifact:
    """
    Generate a UWA for quantum circuit work.
    
    This is a placeholder for real quantum work. Production version would:
    - Execute actual quantum circuits
    - Verify provider attestations
    - Run trap circuits for validation
    
    Args:
        challenge: Work challenge binding to block context
        circuit_depth: Depth of quantum circuit
        qubit_count: Number of qubits used
        shots: Number of measurement shots
        miner_address: Bech32m address for attribution
    
    Returns:
        UsefulWorkArtifact ready for block inclusion
    """
    # Input is challenge + circuit parameters
    input_data = (
        challenge.to_commitment() +
        circuit_depth.to_bytes(4, 'big') +
        qubit_count.to_bytes(4, 'big') +
        shots.to_bytes(4, 'big')
    )
    input_commitment = hashlib.sha3_256(input_data).digest()
    
    # Simulate quantum output (placeholder)
    # Real implementation would execute quantum circuit
    output_digest = hashlib.sha3_256(
        input_data + b"quantum_result"
    ).digest()
    
    # Output commitment
    output_commitment = hashlib.sha3_256(output_digest).digest()
    
    # Package proof
    proof_dict = {
        'circuit_depth': circuit_depth,
        'qubit_count': qubit_count,
        'shots': shots,
        'output_digest': output_digest.hex(),
    }
    proof_data = json.dumps(proof_dict, sort_keys=True).encode('utf-8')
    
    # Calculate work score
    base_score = 1_000_000  # 1.0 nats minimum
    depth_bonus = min(circuit_depth * 10_000, 500_000)
    qubit_bonus = min(qubit_count * 20_000, 1_000_000)
    shots_bonus = min(shots * 100, 500_000)
    work_score = base_score + depth_bonus + qubit_bonus + shots_bonus
    
    return UsefulWorkArtifact(
        version=1,
        work_domain=WorkDomain.QUANTUM_CIRCUIT_V1,
        device_type=DeviceType.QUANTUM,
        challenge=challenge,
        input_commitment=input_commitment,
        output_commitment=output_commitment,
        proof_data=proof_data,
        work_score=work_score,
        miner=miner_address,
        timestamp=int(time.time()),
    )


# -------------------------
# Helper functions (simplified implementations)
# -------------------------


def _simple_compile(source_code: str) -> bytes:
    """
    Simplified bytecode compilation for demo.
    Real implementation would use vm_py.compiler.
    """
    # Generate a simple bytecode representation
    # In production, this would be actual Python VM bytecode
    import hashlib
    source_hash = hashlib.sha3_256(source_code.encode('utf-8')).digest()
    return b"BYTECODE_V1:" + source_hash[:16]


def _simple_gas_estimate(source_code: str) -> Dict[str, int]:
    """
    Simplified gas estimation for demo.
    Real implementation would use vm_py.compiler.gas_estimator.
    """
    # Estimate gas based on source code size and complexity
    lines = source_code.split('\n')
    base_gas = len(lines) * 100
    
    # Count operations
    loops = len([l for l in lines if 'for ' in l or 'while ' in l])
    conditions = len([l for l in lines if 'if ' in l])
    functions = len([l for l in lines if 'def ' in l])
    
    return {
        'base': base_gas,
        'loops': loops * 1000,
        'conditions': conditions * 500,
        'functions': functions * 2000,
    }


__all__ = [
    "create_work_challenge",
    "generate_vm_compile_uwa",
    "generate_hash_work_uwa",
    "generate_quantum_work_uwa",
]
