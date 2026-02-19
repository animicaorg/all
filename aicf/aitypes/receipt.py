"""
aicf.aitypes.receipt - ComputeReceipt Schema (Phase 2)
=======================================================

Canonical schema for ENA "useful work" receipts.
Future-compatible, deterministic, with off-chain DA support.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional
import hashlib

__all__ = [
    "ReceiptVersion",
    "ComputeReceipt",
    "ReceiptSignature",
    "hash_receipt",
]


class ReceiptVersion(IntEnum):
    """Receipt schema version for forward compatibility"""
    V1 = 1


@dataclass(frozen=True)
class ReceiptSignature:
    """
    Signature on a compute receipt.
    Uses PQ-safe signature scheme (Dilithium3 by default).
    """
    scheme: str  # "dilithium3" or "sphincs+"
    public_key: bytes  # Raw public key
    signature: bytes  # Signature over canonical receipt encoding
    
    def __post_init__(self):
        if self.scheme not in ("dilithium3", "sphincs+"):
            raise ValueError(f"Unsupported signature scheme: {self.scheme}")
        if not self.public_key:
            raise ValueError("public_key cannot be empty")
        if not self.signature:
            raise ValueError("signature cannot be empty")


@dataclass(frozen=True)
class ComputeReceipt:
    """
    Canonical compute receipt for ENA/AICF useful work.
    
    On-chain: hashes only (prompt_hash, output_hash, receipt_hash)
    Off-chain: full prompts/outputs in DA blobs (optional)
    
    All fields are consensus-critical and must be deterministically encoded.
    """
    # Receipt metadata
    receipt_version: int  # ReceiptVersion enum value
    chain_id: int  # Must match chain to prevent replay
    
    # Job identification
    job_id: bytes  # 32-byte job identifier (deterministic)
    requester_address: bytes  # 32-byte address
    provider_id: str  # Provider identifier
    
    # Model & work description
    model_id: str  # ENA model identifier (e.g., "ena-v1", "llama3-8b")
    prompt_hash: bytes  # 32-byte SHA3-256 hash of prompt
    output_hash: bytes  # 32-byte SHA3-256 hash of output
    
    # Resource accounting
    tokens_in: int  # Input tokens (or byte count)
    tokens_out: int  # Output tokens (or byte count)
    
    # Financial settlement
    fee_paid: int  # Total fee in nano-ANM
    aicf_cut: int  # Amount to AICF pool
    provider_cut: int  # Amount to provider
    
    # Temporal bounds
    timestamp: int  # Unix timestamp when work completed
    expiry: int  # Unix timestamp when receipt expires
    
    # Signatures
    provider_sig: ReceiptSignature  # Provider's attestation
    requester_sig: Optional[ReceiptSignature] = None  # Optional requester confirmation
    
    # Optional DA pointer (off-chain prompt/output storage)
    da_namespace: Optional[bytes] = None  # DA namespace (if using blob storage)
    da_commitment: Optional[bytes] = None  # Blob commitment hash
    
    def __post_init__(self):
        """Validate receipt fields"""
        # Version check
        if self.receipt_version not in [v.value for v in ReceiptVersion]:
            raise ValueError(f"Unsupported receipt version: {self.receipt_version}")
        
        # Chain ID
        if self.chain_id <= 0:
            raise ValueError(f"Invalid chain_id: {self.chain_id}")
        
        # Job identification
        if len(self.job_id) != 32:
            raise ValueError(f"job_id must be 32 bytes, got {len(self.job_id)}")
        if len(self.requester_address) != 32:
            raise ValueError(f"requester_address must be 32 bytes")
        if not self.provider_id:
            raise ValueError("provider_id cannot be empty")
        
        # Hashes
        if len(self.prompt_hash) != 32:
            raise ValueError(f"prompt_hash must be 32 bytes")
        if len(self.output_hash) != 32:
            raise ValueError(f"output_hash must be 32 bytes")
        
        # Resource counts
        if self.tokens_in < 0:
            raise ValueError(f"tokens_in must be >= 0")
        if self.tokens_out < 0:
            raise ValueError(f"tokens_out must be >= 0")
        
        # Fees
        if self.fee_paid < 0:
            raise ValueError("fee_paid must be >= 0")
        if self.aicf_cut < 0:
            raise ValueError("aicf_cut must be >= 0")
        if self.provider_cut < 0:
            raise ValueError("provider_cut must be >= 0")
        if self.aicf_cut + self.provider_cut > self.fee_paid:
            raise ValueError("aicf_cut + provider_cut cannot exceed fee_paid")
        
        # Timestamps
        if self.timestamp < 0:
            raise ValueError("timestamp must be >= 0")
        if self.expiry < 0:
            raise ValueError("expiry must be >= 0")
        if self.expiry <= self.timestamp:
            raise ValueError("expiry must be > timestamp")
        
        # DA consistency
        if (self.da_namespace is None) != (self.da_commitment is None):
            raise ValueError("da_namespace and da_commitment must both be set or both be None")
        if self.da_namespace is not None and len(self.da_namespace) != 8:
            raise ValueError("da_namespace must be 8 bytes")
        if self.da_commitment is not None and len(self.da_commitment) != 32:
            raise ValueError("da_commitment must be 32 bytes")
    
    def total_tokens(self) -> int:
        """Total token count (in + out)"""
        return self.tokens_in + self.tokens_out
    
    def is_expired(self, now: int) -> bool:
        """Check if receipt has expired"""
        return now >= self.expiry


def hash_receipt(receipt: ComputeReceipt) -> bytes:
    """
    Compute canonical hash of a ComputeReceipt for anchoring on-chain.
    
    Hash = SHA3-256(CBOR-deterministic(receipt))
    
    Note: This is a placeholder. Actual implementation should use
    canonical CBOR encoding from cbor2 with sorted keys.
    """
    # Build deterministic byte representation
    # In production, use cbor2.dumps(receipt, canonical=True)
    parts = [
        receipt.receipt_version.to_bytes(2, 'big'),
        receipt.chain_id.to_bytes(8, 'big'),
        receipt.job_id,
        receipt.requester_address,
        receipt.provider_id.encode('utf-8'),
        receipt.model_id.encode('utf-8'),
        receipt.prompt_hash,
        receipt.output_hash,
        receipt.tokens_in.to_bytes(8, 'big'),
        receipt.tokens_out.to_bytes(8, 'big'),
        receipt.fee_paid.to_bytes(8, 'big'),
        receipt.aicf_cut.to_bytes(8, 'big'),
        receipt.provider_cut.to_bytes(8, 'big'),
        receipt.timestamp.to_bytes(8, 'big'),
        receipt.expiry.to_bytes(8, 'big'),
        receipt.provider_sig.public_key,
        receipt.provider_sig.signature,
    ]
    
    if receipt.requester_sig:
        parts.extend([
            receipt.requester_sig.public_key,
            receipt.requester_sig.signature,
        ])
    
    if receipt.da_namespace:
        parts.append(receipt.da_namespace)
        parts.append(receipt.da_commitment)
    
    data = b''.join(parts)
    return hashlib.sha3_256(data).digest()
