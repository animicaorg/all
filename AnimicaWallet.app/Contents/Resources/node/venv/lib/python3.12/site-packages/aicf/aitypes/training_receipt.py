"""
aicf.aitypes.training_receipt - Training Receipt for Mining→AI Link (Phase 2)
===============================================================================

Schema for training contribution receipts that miners can attach to blocks
to earn extra AICF rewards for funding AI model training.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import hashlib

__all__ = [
    "TrainingReceipt",
    "TrainingProof",
    "hash_training_receipt",
]


@dataclass(frozen=True)
class TrainingReceipt:
    """
    Receipt for training contribution work.
    
    Miners include these in blocks (as hashes only) to claim extra rewards
    for funding AI training tasks via AICF.
    """
    # Training job identification
    task_id: bytes  # 32-byte training task identifier
    job_type: str  # e.g., "ena.train.sft", "ena.eval", "ena.distill"
    
    # Contributor identification
    miner_address: bytes  # 32-byte miner address (funded the training)
    provider_id: str  # Provider who executed the training
    
    # Training metrics
    dataset_hash: bytes  # 32-byte hash of training dataset
    model_checkpoint_hash: bytes  # 32-byte hash of output model checkpoint
    epochs_completed: int  # Number of training epochs
    samples_processed: int  # Total samples trained on
    
    # Resource accounting
    gpu_hours: float  # GPU compute time in hours
    cost_paid: int  # Cost in nano-ANM paid to provider
    
    # Reward calculation
    training_credit: int  # AICF credit earned for this contribution
    
    # Timestamps
    started_at: int  # Unix timestamp when training started
    completed_at: int  # Unix timestamp when training completed
    
    # Chain anchoring
    chain_id: int
    submitted_at_height: int  # Block height when receipt submitted
    
    def __post_init__(self):
        """Validate training receipt fields"""
        if len(self.task_id) != 32:
            raise ValueError("task_id must be 32 bytes")
        if not self.job_type:
            raise ValueError("job_type cannot be empty")
        if len(self.miner_address) != 32:
            raise ValueError("miner_address must be 32 bytes")
        if not self.provider_id:
            raise ValueError("provider_id cannot be empty")
        if len(self.dataset_hash) != 32:
            raise ValueError("dataset_hash must be 32 bytes")
        if len(self.model_checkpoint_hash) != 32:
            raise ValueError("model_checkpoint_hash must be 32 bytes")
        if self.epochs_completed < 0:
            raise ValueError("epochs_completed must be >= 0")
        if self.samples_processed < 0:
            raise ValueError("samples_processed must be >= 0")
        if self.gpu_hours < 0:
            raise ValueError("gpu_hours must be >= 0")
        if self.cost_paid < 0:
            raise ValueError("cost_paid must be >= 0")
        if self.training_credit < 0:
            raise ValueError("training_credit must be >= 0")
        if self.started_at < 0:
            raise ValueError("started_at must be >= 0")
        if self.completed_at < 0:
            raise ValueError("completed_at must be >= 0")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must be >= started_at")
        if self.chain_id <= 0:
            raise ValueError("chain_id must be > 0")
        if self.submitted_at_height < 0:
            raise ValueError("submitted_at_height must be >= 0")
    
    def duration_seconds(self) -> int:
        """Training duration in seconds"""
        return self.completed_at - self.started_at
    
    def samples_per_second(self) -> float:
        """Average throughput in samples/second"""
        duration = self.duration_seconds()
        if duration == 0:
            return 0.0
        return self.samples_processed / duration


@dataclass(frozen=True)
class TrainingProof:
    """
    Lightweight proof envelope for attaching training receipts to blocks.
    
    Miners include this in block's attachedProofs to claim training credits.
    Only hash + signature stored on-chain; full receipt in DA blob.
    """
    receipt_hash: bytes  # 32-byte hash of TrainingReceipt
    miner_address: bytes  # 32-byte miner claiming credit
    provider_signature: bytes  # Provider's attestation (Dilithium3/SPHINCS+)
    
    # Optional DA pointer for full receipt
    da_namespace: Optional[bytes] = None  # 8-byte namespace
    da_commitment: Optional[bytes] = None  # 32-byte blob commitment
    
    def __post_init__(self):
        """Validate training proof fields"""
        if len(self.receipt_hash) != 32:
            raise ValueError("receipt_hash must be 32 bytes")
        if len(self.miner_address) != 32:
            raise ValueError("miner_address must be 32 bytes")
        if not self.provider_signature:
            raise ValueError("provider_signature cannot be empty")
        
        # DA consistency
        if (self.da_namespace is None) != (self.da_commitment is None):
            raise ValueError("da_namespace and da_commitment must both be set or both be None")
        if self.da_namespace is not None and len(self.da_namespace) != 8:
            raise ValueError("da_namespace must be 8 bytes")
        if self.da_commitment is not None and len(self.da_commitment) != 32:
            raise ValueError("da_commitment must be 32 bytes")


def hash_training_receipt(receipt: TrainingReceipt) -> bytes:
    """
    Compute canonical hash of a TrainingReceipt for on-chain anchoring.
    
    Hash = SHA3-256(canonical encoding)
    """
    # Build deterministic byte representation
    parts = [
        receipt.task_id,
        receipt.job_type.encode('utf-8'),
        receipt.miner_address,
        receipt.provider_id.encode('utf-8'),
        receipt.dataset_hash,
        receipt.model_checkpoint_hash,
        receipt.epochs_completed.to_bytes(4, 'big'),
        receipt.samples_processed.to_bytes(8, 'big'),
        # GPU hours as fixed-point (2 decimal places)
        int(receipt.gpu_hours * 100).to_bytes(8, 'big'),
        receipt.cost_paid.to_bytes(8, 'big'),
        receipt.training_credit.to_bytes(8, 'big'),
        receipt.started_at.to_bytes(8, 'big'),
        receipt.completed_at.to_bytes(8, 'big'),
        receipt.chain_id.to_bytes(8, 'big'),
        receipt.submitted_at_height.to_bytes(8, 'big'),
    ]
    
    data = b''.join(parts)
    return hashlib.sha3_256(data).digest()
