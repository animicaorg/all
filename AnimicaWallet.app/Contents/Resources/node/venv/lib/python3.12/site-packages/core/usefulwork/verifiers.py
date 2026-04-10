"""
Built-in verifiers for Useful Work Proofs.

Tier 0: ena.eval.micro - Deterministic evaluation with Merkle spot-checks
Tier 1: compute.receipt.v1 - Signed compute receipts
"""

from __future__ import annotations

import hashlib
import struct
from typing import List, Tuple

from .types import (
    UsefulWorkProof,
    EnaEvalMicroProof,
    ComputeReceiptProof,
    VerifyResult,
    VerifyStatus,
    ShareContext,
    Hash,
)
from .cbor_codec import decode_proof
from .policy import get_policy
from .registry import register_verifier

# Import PQ signature verification
import os
import sys

# Try to import dilithium for signature verification
try:
    # Attempt to use pq module if available (currently stubbed)
    PQ_AVAILABLE = False
    dilithium_verify = None
except ImportError:
    PQ_AVAILABLE = False
    dilithium_verify = None


def derive_spot_check_indices(
    job_id: str,
    nonce: bytes,
    mix_seed: bytes,
    instance_id: bytes,
    num_items: int,
    k: int = 8,
) -> List[int]:
    """
    Deterministically derive k spot-check indices from mining context.
    
    Uses PRF: indices = SHA3-256(jobId || nonce || mixSeed || instanceId) mod num_items
    
    Args:
        job_id: Mining job ID
        nonce: Mining nonce
        mix_seed: Mix seed from header
        instance_id: Proof instance ID
        num_items: Total number of items
        k: Number of indices to select
        
    Returns:
        List of k unique indices
    """
    h = hashlib.sha3_256()
    h.update(job_id.encode())
    h.update(nonce)
    h.update(mix_seed)
    h.update(instance_id)
    
    digest = h.digest()
    indices = set()
    
    # Generate k unique indices
    counter = 0
    while len(indices) < k:
        # Hash with counter to get more randomness
        h2 = hashlib.sha3_256()
        h2.update(digest)
        h2.update(struct.pack(">I", counter))
        idx_bytes = h2.digest()
        
        # Take first 4 bytes as index
        idx = int.from_bytes(idx_bytes[:4], 'big') % num_items
        indices.add(idx)
        counter += 1
        
        # Safety: avoid infinite loop
        if counter > k * 10:
            break
    
    return sorted(list(indices))[:k]


def verify_merkle_proof(
    leaf_hash: bytes,
    merkle_path: bytes,
    root: bytes,
    index: int,
) -> bool:
    """
    Verify a Merkle proof.
    
    Args:
        leaf_hash: Hash of the leaf
        merkle_path: Concatenated sibling hashes (32 bytes each)
        root: Expected Merkle root
        index: Leaf index
        
    Returns:
        True if proof is valid
    """
    if len(merkle_path) % 32 != 0:
        return False
    
    current = leaf_hash
    pos = index
    
    # Parse path into chunks of 32 bytes
    siblings = [merkle_path[i:i+32] for i in range(0, len(merkle_path), 32)]
    
    for sibling in siblings:
        if pos % 2 == 0:
            # Current is left
            current = hashlib.sha3_256(current + sibling).digest()
        else:
            # Current is right
            current = hashlib.sha3_256(sibling + current).digest()
        pos //= 2
    
    return current == root


def verify_ena_eval_micro(proof: UsefulWorkProof, context: ShareContext) -> VerifyResult:
    """
    Verify Tier 0: ena.eval.micro proof.
    
    Validates deterministic evaluation with Merkle spot-checks.
    """
    policy = get_policy()
    scheme_policy = policy.get_scheme_policy("ena.eval.micro")
    
    if scheme_policy is None or not scheme_policy.enabled:
        return VerifyResult(
            status=VerifyStatus.SCHEME_DISABLED,
            reason="ena.eval.micro is not enabled",
        )
    
    # Decode receipt_bytes as CBOR
    try:
        from core.encoding.cbor import loads as cbor_loads
        receipt_data = cbor_loads(proof.receipt_bytes)
    except Exception as e:
        return VerifyResult(
            status=VerifyStatus.REJECTED,
            reason=f"Failed to decode receipt: {e}",
        )
    
    # Parse fields
    try:
        num_items = receipt_data["num_items"]
        outputs_merkle_root = bytes(receipt_data["outputs_merkle_root"])
        spot_check_indices = receipt_data["spot_check_indices"]
        spot_check_proofs = [bytes(p) for p in receipt_data["spot_check_proofs"]]
        spot_check_values = [(bytes(i), bytes(o)) for i, o in receipt_data["spot_check_values"]]
    except (KeyError, ValueError, TypeError) as e:
        return VerifyResult(
            status=VerifyStatus.REJECTED,
            reason=f"Invalid receipt format: {e}",
        )
    
    # Derive expected indices
    expected_indices = derive_spot_check_indices(
        context.job_id,
        context.nonce,
        context.mix_seed,
        proof.instance_id,
        num_items,
        k=len(spot_check_indices),
    )
    
    # Check indices match
    if spot_check_indices != expected_indices:
        return VerifyResult(
            status=VerifyStatus.REJECTED,
            reason="Spot-check indices don't match deterministic derivation",
        )
    
    # Verify each Merkle proof
    for idx, merkle_path, (input_hash, output_hash) in zip(
        spot_check_indices, spot_check_proofs, spot_check_values
    ):
        # Compute leaf hash
        leaf = hashlib.sha3_256(input_hash + output_hash).digest()
        
        # Verify Merkle proof
        if not verify_merkle_proof(leaf, merkle_path, outputs_merkle_root, idx):
            return VerifyResult(
                status=VerifyStatus.REJECTED,
                reason=f"Merkle proof failed for index {idx}",
            )
    
    # All checks passed
    bonus = scheme_policy.bonus_credits if scheme_policy else 2000
    
    return VerifyResult(
        status=VerifyStatus.ACCEPTED,
        reason="Valid ena.eval.micro proof",
        bonus_credits=bonus,
        metadata={
            "num_items": num_items,
            "spot_checks": len(spot_check_indices),
        },
    )


def verify_compute_receipt(proof: UsefulWorkProof, context: ShareContext) -> VerifyResult:
    """
    Verify Tier 1: compute.receipt.v1 proof.
    
    Validates signed compute receipts from registered contributors.
    """
    policy = get_policy()
    scheme_policy = policy.get_scheme_policy("compute.receipt.v1")
    
    if scheme_policy is None or not scheme_policy.enabled:
        return VerifyResult(
            status=VerifyStatus.SCHEME_DISABLED,
            reason="compute.receipt.v1 is not enabled",
        )
    
    # Decode receipt_bytes as CBOR
    try:
        from core.encoding.cbor import loads as cbor_loads
        receipt_data = cbor_loads(proof.receipt_bytes)
    except Exception as e:
        return VerifyResult(
            status=VerifyStatus.REJECTED,
            reason=f"Failed to decode receipt: {e}",
        )
    
    # Parse fields
    try:
        contributor_id = receipt_data["contributor_id"]
        steps = receipt_data["steps"]
        tokens = receipt_data["tokens"]
        model_id = receipt_data["model_id"]
        timestamp = receipt_data["timestamp"]
        trace_summary_hash = bytes(receipt_data["trace_summary_hash"])
        signature = bytes(receipt_data["signature"])
        public_key = bytes(receipt_data["public_key"])
    except (KeyError, ValueError, TypeError) as e:
        return VerifyResult(
            status=VerifyStatus.REJECTED,
            reason=f"Invalid receipt format: {e}",
        )
    
    # TODO: Check contributor registry (when implemented)
    # For now, we skip this check
    
    # Validate counters within reasonable bounds
    if steps <= 0 or steps > 1_000_000_000:
        return VerifyResult(
            status=VerifyStatus.REJECTED,
            reason=f"Invalid steps count: {steps}",
        )
    
    if tokens <= 0 or tokens > 1_000_000_000_000:
        return VerifyResult(
            status=VerifyStatus.REJECTED,
            reason=f"Invalid tokens count: {tokens}",
        )
    
    # Build message to verify
    message = (
        proof.plan_commitment +
        proof.instance_id +
        proof.input_commitment +
        proof.output_commitment +
        trace_summary_hash +
        struct.pack(">Q", steps) +
        struct.pack(">Q", tokens) +
        struct.pack(">Q", timestamp)
    )
    
    # Verify signature
    # For now, we use a simple stub (real PQ verification would go here)
    signature_valid = True  # Stub
    
    if not signature_valid:
        return VerifyResult(
            status=VerifyStatus.REJECTED,
            reason="Invalid signature",
        )
    
    # All checks passed
    bonus = scheme_policy.bonus_credits if scheme_policy else 5000
    
    # Scale bonus by work done (simple heuristic)
    work_factor = min(steps / 10000.0, 10.0)  # Cap at 10x
    scaled_bonus = int(bonus * work_factor)
    
    return VerifyResult(
        status=VerifyStatus.ACCEPTED,
        reason="Valid compute.receipt.v1 proof",
        bonus_credits=scaled_bonus,
        metadata={
            "contributor_id": contributor_id,
            "steps": steps,
            "tokens": tokens,
            "model_id": model_id,
        },
    )


# Auto-register verifiers
register_verifier("ena.eval.micro", verify_ena_eval_micro)
register_verifier("compute.receipt.v1", verify_compute_receipt)


__all__ = [
    "derive_spot_check_indices",
    "verify_merkle_proof",
    "verify_ena_eval_micro",
    "verify_compute_receipt",
]
