"""
Type definitions for Useful Work Proofs.

Defines the core data structures for the UWP subsystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional, NewType
import hashlib

# Type aliases
Hash = NewType("Hash", bytes)  # 32-byte hash
Hex = NewType("Hex", str)  # Hex-encoded string


class VerifyStatus(IntEnum):
    """Result status from proof verification."""
    ACCEPTED = 0
    REJECTED = 1
    SKIPPED_BUDGET = 2
    SCHEME_DISABLED = 3
    SCHEME_UNSUPPORTED = 4
    VERIFIER_ERROR = 5


@dataclass(frozen=True)
class UsefulWorkProof:
    """
    Generic envelope for useful work proofs attached to mining shares.
    
    This is the on-wire format (CBOR-encoded to hex for RPC).
    
    Fields:
        scheme_id: Identifier for the proof scheme (e.g., "ena.eval.micro", "compute.receipt.v1")
        plan_commitment: Hash commitment to the work plan/task definition (stored in DA)
        instance_id: Unique identifier for this proof instance (prevents replay)
        input_commitment: Hash commitment to inputs (dataset shard, prompts, etc.)
        output_commitment: Hash commitment to produced artifacts/metrics
        receipt_bytes: Scheme-specific proof/receipt data (opaque to envelope)
        metadata: Small optional map for scheme-specific metadata (steps, tokens, model_id, etc.)
    
    Invariants:
        - All hash commitments are 32 bytes (SHA3-256)
        - receipt_bytes must be < max_receipt_bytes (policy-controlled, typically 64KB)
        - metadata is optional and limited in size
    """
    scheme_id: str
    plan_commitment: Hash
    instance_id: Hash
    input_commitment: Hash
    output_commitment: Hash
    receipt_bytes: bytes
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self) -> None:
        """Validate hash lengths."""
        for name, value in [
            ("plan_commitment", self.plan_commitment),
            ("instance_id", self.instance_id),
            ("input_commitment", self.input_commitment),
            ("output_commitment", self.output_commitment),
        ]:
            if not isinstance(value, bytes) or len(value) != 32:
                raise ValueError(f"{name} must be 32 bytes, got {len(value) if isinstance(value, bytes) else type(value)}")
    
    def compute_digest(self) -> Hash:
        """Compute a unique digest for this proof (for deduplication/nullifier)."""
        h = hashlib.sha3_256()
        h.update(self.scheme_id.encode())
        h.update(self.plan_commitment)
        h.update(self.instance_id)
        h.update(self.input_commitment)
        h.update(self.output_commitment)
        h.update(self.receipt_bytes)
        return Hash(h.digest())


@dataclass(frozen=True)
class EnaEvalMicroProof:
    """
    Tier 0 proof: Deterministic evaluation tasks.
    
    The worker processes a batch of inputs and produces outputs committed to DA.
    Validators verify a random spot-check of k indices using Merkle proofs.
    
    Fields:
        num_items: Total number of items processed
        outputs_merkle_root: Merkle root of (input_hash, output_hash) pairs
        spot_check_indices: k deterministically selected indices to verify
        spot_check_proofs: Merkle proofs for the selected indices
        spot_check_values: (input_hash, output_hash) pairs for verification
    """
    num_items: int
    outputs_merkle_root: Hash
    spot_check_indices: List[int]
    spot_check_proofs: List[bytes]  # Merkle paths
    spot_check_values: List[tuple[Hash, Hash]]  # (input_hash, output_hash)


@dataclass(frozen=True)
class ComputeReceiptProof:
    """
    Tier 1 proof: Signed compute receipt by registered GPU contributor.
    
    The contributor runs compute and produces a signed receipt.
    Validators check signature + policy (contributor allowed, receipt valid, counters within caps).
    
    Fields:
        contributor_id: Address or ID of registered contributor
        steps: Number of training/inference steps performed
        tokens: Number of tokens processed (for LLMs)
        model_id: Model identifier
        timestamp: Unix timestamp when work was performed
        trace_summary_hash: Hash of execution trace summary (deterministic)
        signature: PQ signature over receipt fields
        public_key: Contributor's public key (for verification)
    """
    contributor_id: str
    steps: int
    tokens: int
    model_id: str
    timestamp: int
    trace_summary_hash: Hash
    signature: bytes
    public_key: bytes


@dataclass
class VerifyResult:
    """
    Result from proof verification.
    
    Fields:
        status: Verification outcome
        reason: Human-readable reason (for rejections/errors)
        bonus_credits: AICF bonus credits to award (if accepted)
        metadata: Additional debug/audit info
    """
    status: VerifyStatus
    reason: str = ""
    bonus_credits: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def accepted(self) -> bool:
        return self.status == VerifyStatus.ACCEPTED
    
    @property
    def rejected(self) -> bool:
        return self.status in (VerifyStatus.REJECTED, VerifyStatus.SCHEME_DISABLED, VerifyStatus.SCHEME_UNSUPPORTED)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "status": self.status.name,
            "statusCode": int(self.status),
            "accepted": self.accepted,
            "reason": self.reason,
            "bonusCredits": self.bonus_credits,
            "metadata": self.metadata,
        }


@dataclass
class ShareContext:
    """
    Context passed to verifiers for share-related information.
    
    Fields:
        job_id: Mining job ID
        nonce: Mining nonce
        mix_seed: Mix seed from header
        height: Block height
        miner_address: Miner's address
        timestamp: Current timestamp
    """
    job_id: str
    nonce: bytes
    mix_seed: bytes
    height: int
    miner_address: str
    timestamp: int


__all__ = [
    "Hash",
    "Hex",
    "VerifyStatus",
    "UsefulWorkProof",
    "EnaEvalMicroProof",
    "ComputeReceiptProof",
    "VerifyResult",
    "ShareContext",
]
