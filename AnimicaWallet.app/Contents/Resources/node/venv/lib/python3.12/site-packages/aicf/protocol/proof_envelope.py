"""
AICF Work Submission Envelope
==============================

Defines the ProofEnvelope format for AICF work submissions.
All quantum/GPU/CPU contributions use this standard envelope.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional

import cbor2


@dataclass
class ProofEnvelope:
    """
    Verifiable proof envelope for AICF work submissions.
    
    This envelope is used by all contribution types (quantum/GPU/CPU) and contains:
    - Metadata about the job and worker
    - Input/output commitments (hashes)
    - Execution metrics
    - Attestation (hash of run manifest + env)
    - Signature (wallet signature for authenticity)
    
    The envelope is CBOR-encoded for deterministic serialization.
    """
    
    version: int  # Envelope version (currently 1)
    job_id: str  # Job identifier
    worker_id: str  # Worker/provider identifier
    kind: str  # "quantum" | "gpu_train" | "cpu_train" | "eval" | "data_prep"
    
    # Commitments (sha3-256 hashes)
    inputs_commitment: str  # Hash of inputs (hex)
    outputs_commitment: str  # Hash of outputs (hex)
    
    # Execution metrics (kind-specific)
    metrics: Dict[str, Any]
    
    # Attestation and signature
    attestation: str  # Hash of run manifest + environment (hex)
    signature: str  # Wallet signature (hex)
    
    # Optional metadata
    timestamp: Optional[int] = None  # Unix timestamp
    nonce: Optional[int] = None  # Optional nonce for uniqueness
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)
    
    def to_cbor_hex(self) -> str:
        """Serialize to CBOR and return as hex string."""
        data = self.to_dict()
        cbor_bytes = cbor2.dumps(data)
        return cbor_bytes.hex()
    
    @classmethod
    def from_cbor_hex(cls, hex_str: str) -> "ProofEnvelope":
        """Deserialize from CBOR hex string."""
        cbor_bytes = bytes.fromhex(hex_str)
        data = cbor2.loads(cbor_bytes)
        return cls(**data)
    
    def validate_schema(self) -> tuple[bool, Optional[str]]:
        """
        Validate the envelope schema.
        
        Returns:
            (is_valid, error_message)
        """
        # Check version
        if self.version != 1:
            return False, f"Unsupported version: {self.version}"
        
        # Check kind
        valid_kinds = ["quantum", "gpu_train", "cpu_train", "eval", "data_prep", "stub_quantum_v1"]
        if self.kind not in valid_kinds:
            return False, f"Invalid kind: {self.kind}. Must be one of: {', '.join(valid_kinds)}"
        
        # Check commitments are hex strings
        try:
            bytes.fromhex(self.inputs_commitment)
            bytes.fromhex(self.outputs_commitment)
            bytes.fromhex(self.attestation)
            bytes.fromhex(self.signature)
        except ValueError as e:
            return False, f"Invalid hex encoding: {e}"
        
        # Check commitments are 32 bytes (64 hex chars)
        if len(self.inputs_commitment) != 64:
            return False, f"inputs_commitment must be 32 bytes (64 hex chars), got {len(self.inputs_commitment)}"
        if len(self.outputs_commitment) != 64:
            return False, f"outputs_commitment must be 32 bytes (64 hex chars), got {len(self.outputs_commitment)}"
        if len(self.attestation) != 64:
            return False, f"attestation must be 32 bytes (64 hex chars), got {len(self.attestation)}"
        
        # Check metrics is a dict
        if not isinstance(self.metrics, dict):
            return False, "metrics must be a dictionary"
        
        return True, None


def create_stub_quantum_envelope(
    job_id: str,
    worker_id: str,
    steps: int,
    runtime_sec: float,
    signature: str,
) -> ProofEnvelope:
    """
    Create a stub quantum envelope for testing/development.
    
    This is used when true quantum verification is not yet implemented.
    It produces deterministic envelopes that can be upgraded later.
    """
    # Create deterministic inputs commitment
    inputs_data = f"{job_id}|{worker_id}|{steps}".encode()
    inputs_commitment = hashlib.sha3_256(inputs_data).hexdigest()
    
    # Create deterministic outputs commitment
    # In a real implementation, this would hash actual quantum circuit outputs
    outputs_data = f"{job_id}|{worker_id}|{steps}|{runtime_sec}".encode()
    outputs_commitment = hashlib.sha3_256(outputs_data).hexdigest()
    
    # Create attestation (hash of run manifest)
    manifest = {
        "job_id": job_id,
        "worker_id": worker_id,
        "steps": steps,
        "runtime_sec": runtime_sec,
        "timestamp": int(time.time()),
    }
    manifest_str = str(sorted(manifest.items())).encode()
    attestation = hashlib.sha3_256(manifest_str).hexdigest()
    
    # Create metrics
    metrics = {
        "steps": steps,
        "runtime_sec": runtime_sec,
        "quantum_units": steps,  # 1 unit per step for stub
    }
    
    return ProofEnvelope(
        version=1,
        job_id=job_id,
        worker_id=worker_id,
        kind="stub_quantum_v1",
        inputs_commitment=inputs_commitment,
        outputs_commitment=outputs_commitment,
        metrics=metrics,
        attestation=attestation,
        signature=signature,
        timestamp=int(time.time()),
    )


def create_training_envelope(
    job_id: str,
    worker_id: str,
    kind: str,  # "gpu_train" | "cpu_train"
    inputs_hash: str,  # Hash of training data/model
    outputs_hash: str,  # Hash of trained model weights
    metrics: Dict[str, Any],  # loss, accuracy, tokens/sec, etc.
    attestation_hash: str,  # Hash of run manifest
    signature: str,
) -> ProofEnvelope:
    """
    Create a training envelope for GPU/CPU training jobs.
    """
    return ProofEnvelope(
        version=1,
        job_id=job_id,
        worker_id=worker_id,
        kind=kind,
        inputs_commitment=inputs_hash,
        outputs_commitment=outputs_hash,
        metrics=metrics,
        attestation=attestation_hash,
        signature=signature,
        timestamp=int(time.time()),
    )


__all__ = [
    "ProofEnvelope",
    "create_stub_quantum_envelope",
    "create_training_envelope",
]
