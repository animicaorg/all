"""
Useful Work Artifact (UWA) Types
=================================

This module defines the core types for Useful Work Artifacts that enable
CPU/GPU/Quantum mining to produce verifiable, deterministic work directly
usable by the Animica Python VM / smart-contract layer.

UWA Design Goals:
1. Every mined block carries a UWA binding the work to block context (height, prev_hash, chain_id)
2. Work output is deterministically verifiable and bounded (no DoS via expensive verification)
3. Work is directly consumable by the chain's Python execution environment
4. Different device types (CPU/GPU/Quantum) produce different UWA types with different scoring

UWA Schema Versioning:
- work_domain: Identifies the type and version of useful work (e.g., "vm.compile.v1", "vm.trace.v1")
- Each domain has specific input/output commitments and proof structures
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional

# -------------------------
# Work Domain Identifiers
# -------------------------


class WorkDomain(str):
    """
    Canonical work domain identifiers.
    Format: "{category}.{type}.v{version}"
    """
    # VM compilation and witness generation
    VM_COMPILE_V1 = "vm.compile.v1"
    VM_TRACE_V1 = "vm.trace.v1"
    VM_SIMULATE_V1 = "vm.simulate.v1"
    
    # Contract execution proof-of-work
    CONTRACT_EXEC_V1 = "contract.exec.v1"
    CONTRACT_ABI_V1 = "contract.abi.v1"
    
    # State transition acceleration
    STATE_DIFF_V1 = "state.diff.v1"
    STATE_MERKLE_V1 = "state.merkle.v1"
    
    # Hash-based useful work (device-agnostic fallback)
    HASH_WORK_V1 = "hash.work.v1"
    
    # Quantum circuit work
    QUANTUM_CIRCUIT_V1 = "quantum.circuit.v1"


# -------------------------
# Device Type for Scoring
# -------------------------


class DeviceType(IntEnum):
    """
    Device types for weighted work scoring.
    Lower values = lower weight in consensus.
    """
    CPU = 1
    GPU = 2
    QUANTUM = 3


# -------------------------
# UWA Core Types
# -------------------------


@dataclass(frozen=True)
class WorkChallenge:
    """
    Deterministic challenge derived from block context.
    Miners must bind their useful work to this challenge.
    """
    height: int
    prev_hash: bytes  # 32 bytes
    chain_id: int
    timestamp: int  # consensus timestamp for the block
    mix_seed: bytes  # 32 bytes - from parent header
    
    def to_commitment(self) -> bytes:
        """Generate a SHA3-256 commitment of the challenge."""
        import hashlib
        data = (
            self.height.to_bytes(8, 'big') +
            self.prev_hash +
            self.chain_id.to_bytes(4, 'big') +
            self.timestamp.to_bytes(8, 'big') +
            self.mix_seed
        )
        return hashlib.sha3_256(data).digest()


@dataclass(frozen=True)
class VMCompileInput:
    """Input specification for VM compilation work."""
    source_hash: bytes  # SHA3-256 of source code
    manifest_hash: bytes  # SHA3-256 of manifest.json
    abi_version: int
    
    def to_bytes(self) -> bytes:
        """Serialize for hashing."""
        return (
            self.source_hash +
            self.manifest_hash +
            self.abi_version.to_bytes(4, 'big')
        )


@dataclass(frozen=True)
class VMCompileOutput:
    """Output of VM compilation work."""
    bytecode_hash: bytes  # SHA3-256 of compiled bytecode
    gas_estimate: int
    symbols_count: int
    dependencies_count: int
    
    def to_bytes(self) -> bytes:
        """Serialize for hashing."""
        return (
            self.bytecode_hash +
            self.gas_estimate.to_bytes(8, 'big') +
            self.symbols_count.to_bytes(4, 'big') +
            self.dependencies_count.to_bytes(4, 'big')
        )


@dataclass(frozen=True)
class VMCompileProof:
    """Proof of VM compilation work."""
    bytecode: bytes  # The actual compiled bytecode (bounded size)
    gas_breakdown: Dict[str, int]  # Per-function gas estimates
    
    def size_bytes(self) -> int:
        """Calculate proof size for bounds checking."""
        return len(self.bytecode) + sum(len(str(k)) + 8 for k in self.gas_breakdown)


@dataclass(frozen=True)
class UsefulWorkArtifact:
    """
    Canonical Useful Work Artifact structure.
    
    This is included in every mined block and verified deterministically.
    """
    # Version and domain
    version: int  # Schema version (currently 1)
    work_domain: str  # WorkDomain identifier
    device_type: DeviceType  # CPU/GPU/Quantum for scoring
    
    # Binding to block context
    challenge: WorkChallenge
    
    # Work input/output commitments
    input_commitment: bytes  # SHA3-256 of work inputs
    output_commitment: bytes  # SHA3-256 of work outputs
    
    # Proof data (domain-specific, bounded)
    proof_data: bytes  # Serialized proof (CBOR or JSON)
    
    # Scoring inputs (computed deterministically from verification)
    work_score: int  # Base score from work verification (µ-nats)
    
    # Metadata
    miner: str  # Bech32m address (anim1...)
    timestamp: int  # When work was produced
    
    def size_bytes(self) -> int:
        """Total size for bounds checking."""
        return (
            4 + len(self.work_domain) +
            1 +  # device_type
            32 + 4 + 8 + 32 +  # challenge components
            32 + 32 +  # commitments
            len(self.proof_data) +
            8 +  # work_score
            len(self.miner.encode('utf-8')) +
            8  # timestamp
        )
    
    def verify_binding(self, header_height: int, header_prev_hash: bytes, header_chain_id: int) -> bool:
        """Verify UWA is bound to the correct block context."""
        if self.challenge.height != header_height:
            return False
        if self.challenge.prev_hash != header_prev_hash:
            return False
        if self.challenge.chain_id != header_chain_id:
            return False
        return True


# -------------------------
# UWA Verification Result
# -------------------------


@dataclass
class UWAVerificationResult:
    """Result of UWA verification."""
    valid: bool
    device_type: DeviceType
    work_score: int  # Base score in µ-nats before weighting
    reason: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


# -------------------------
# Constants
# -------------------------

# Maximum sizes to prevent DoS
MAX_UWA_PROOF_SIZE = 1_000_000  # 1MB max proof data
MAX_UWA_TOTAL_SIZE = 2_000_000  # 2MB max total UWA size

# Minimum work scores (µ-nats) for different device types
MIN_CPU_WORK_SCORE = 100_000  # 0.1 nats
MIN_GPU_WORK_SCORE = 500_000  # 0.5 nats
MIN_QUANTUM_WORK_SCORE = 1_000_000  # 1.0 nats

# Weighted work multipliers (consensus constants)
# These define CPU < GPU < Quantum weighting
WEIGHT_CPU = 1.0
WEIGHT_GPU = 5.0  # GPU work counts 5x more than CPU
WEIGHT_QUANTUM = 25.0  # Quantum work counts 25x more than CPU (5x more than GPU)


def calculate_effective_work(cpu_score: int, gpu_score: int, quantum_score: int) -> int:
    """
    Calculate effective work score with weighted contributions.
    
    Formula: effective_work = cpu_score + (WEIGHT_GPU * gpu_score) + (WEIGHT_QUANTUM * quantum_score)
    
    All inputs and output are in µ-nats (micro-nats).
    """
    # Apply weights and sum
    effective = (
        int(cpu_score * WEIGHT_CPU) +
        int(gpu_score * WEIGHT_GPU) +
        int(quantum_score * WEIGHT_QUANTUM)
    )
    return effective


__all__ = [
    "WorkDomain",
    "DeviceType",
    "WorkChallenge",
    "VMCompileInput",
    "VMCompileOutput",
    "VMCompileProof",
    "UsefulWorkArtifact",
    "UWAVerificationResult",
    "MAX_UWA_PROOF_SIZE",
    "MAX_UWA_TOTAL_SIZE",
    "MIN_CPU_WORK_SCORE",
    "MIN_GPU_WORK_SCORE",
    "MIN_QUANTUM_WORK_SCORE",
    "WEIGHT_CPU",
    "WEIGHT_GPU",
    "WEIGHT_QUANTUM",
    "calculate_effective_work",
]
