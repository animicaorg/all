#!/usr/bin/env python3
"""
Example script demonstrating how to submit mining shares with useful work proofs.

This script shows how to:
1. Create useful work proofs (Tier 0 and Tier 1)
2. Encode them to CBOR hex format
3. Submit them with a mining share
4. Verify a proof using the debug endpoint
"""

import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import hashlib
import struct
import time
from typing import List, Tuple

# Import UWP modules
from core.usefulwork import (
    UsefulWorkProof,
    Hash,
    encode_proof_to_hex,
    ShareContext,
    verify_proof,
)
from core.encoding.cbor import dumps as cbor_dumps


def create_ena_eval_micro_proof(
    job_id: str,
    nonce: bytes,
    mix_seed: bytes,
    num_items: int = 100,
) -> UsefulWorkProof:
    """
    Create a Tier 0 (ena.eval.micro) proof.
    
    This demonstrates deterministic evaluation with Merkle spot-checks.
    """
    # 1. Generate fake outputs (in real use, these come from actual AI work)
    outputs: List[Tuple[bytes, bytes]] = []
    for i in range(num_items):
        input_hash = hashlib.sha3_256(f"input_{i}".encode()).digest()
        output_hash = hashlib.sha3_256(f"output_{i}".encode()).digest()
        outputs.append((input_hash, output_hash))
    
    # 2. Build Merkle tree (simplified - just hash all leaves)
    leaf_hashes = [hashlib.sha3_256(inp + out).digest() for inp, out in outputs]
    outputs_merkle_root = hashlib.sha3_256(b"".join(leaf_hashes)).digest()
    
    # 3. Create instance ID
    instance_id = hashlib.sha3_256(
        b"ena.eval.micro" + 
        str(time.time()).encode() + 
        nonce
    ).digest()
    
    # 4. Derive spot-check indices (deterministic)
    from core.usefulwork.verifiers import derive_spot_check_indices
    
    indices = derive_spot_check_indices(
        job_id, nonce, mix_seed, instance_id, num_items, k=8
    )
    
    # 5. Build receipt with Merkle proofs
    # (In real use, build actual Merkle proofs; here we stub)
    receipt_data = cbor_dumps({
        "num_items": num_items,
        "outputs_merkle_root": outputs_merkle_root,
        "spot_check_indices": indices,
        "spot_check_proofs": [b"\x00" * 32] * len(indices),  # Stub
        "spot_check_values": [outputs[i] for i in indices],
    })
    
    # 6. Create proof envelope
    proof = UsefulWorkProof(
        scheme_id="ena.eval.micro",
        plan_commitment=Hash(hashlib.sha3_256(b"plan definition").digest()),
        instance_id=Hash(instance_id),
        input_commitment=Hash(hashlib.sha3_256(b"input dataset").digest()),
        output_commitment=Hash(outputs_merkle_root),
        receipt_bytes=receipt_data,
        metadata={
            "num_items": num_items,
            "model": "test-model",
        },
    )
    
    return proof


def create_compute_receipt_proof() -> UsefulWorkProof:
    """
    Create a Tier 1 (compute.receipt.v1) proof.
    
    This demonstrates signed compute receipts.
    """
    # 1. Receipt fields
    contributor_id = "contributor-001"
    steps = 50000
    tokens = 250000
    model_id = "gpt-small-v1"
    timestamp = int(time.time())
    
    # 2. Trace summary
    trace_summary = {
        "model": model_id,
        "steps": steps,
        "tokens": tokens,
        "loss_bps": 1500,  # 0.15 expressed as basis points (15%)
    }
    trace_summary_hash = hashlib.sha3_256(
        cbor_dumps(trace_summary)
    ).digest()
    
    # 3. Build receipt (signature is stubbed)
    signature = b"\x00" * 64  # Real: Dilithium3 signature
    public_key = b"\x00" * 32  # Real: Contributor's public key
    
    receipt_data = cbor_dumps({
        "contributor_id": contributor_id,
        "steps": steps,
        "tokens": tokens,
        "model_id": model_id,
        "timestamp": timestamp,
        "trace_summary_hash": trace_summary_hash,
        "signature": signature,
        "public_key": public_key,
    })
    
    # 4. Create proof envelope
    instance_id = hashlib.sha3_256(
        contributor_id.encode() + 
        struct.pack(">Q", timestamp)
    ).digest()
    
    proof = UsefulWorkProof(
        scheme_id="compute.receipt.v1",
        plan_commitment=Hash(hashlib.sha3_256(b"training plan").digest()),
        instance_id=Hash(instance_id),
        input_commitment=Hash(hashlib.sha3_256(b"training data").digest()),
        output_commitment=Hash(hashlib.sha3_256(b"model weights").digest()),
        receipt_bytes=receipt_data,
        metadata={
            "contributor": contributor_id,
            "model": model_id,
        },
    )
    
    return proof


def main():
    """Main example function."""
    print("=" * 60)
    print("Useful Work Proof Example")
    print("=" * 60)
    
    # Mining context
    job_id = "example-job-123"
    nonce = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    mix_seed = b"\xaa" * 32
    
    # 1. Create Tier 0 proof
    print("\n1. Creating Tier 0 (ena.eval.micro) proof...")
    proof_t0 = create_ena_eval_micro_proof(job_id, nonce, mix_seed)
    proof_t0_hex = encode_proof_to_hex(proof_t0)
    print(f"   Proof size: {len(proof_t0_hex) // 2} bytes")
    print(f"   Hex (first 100 chars): {proof_t0_hex[:100]}...")
    
    # 2. Create Tier 1 proof
    print("\n2. Creating Tier 1 (compute.receipt.v1) proof...")
    proof_t1 = create_compute_receipt_proof()
    proof_t1_hex = encode_proof_to_hex(proof_t1)
    print(f"   Proof size: {len(proof_t1_hex) // 2} bytes")
    print(f"   Hex (first 100 chars): {proof_t1_hex[:100]}...")
    
    # 3. Verify proofs locally
    print("\n3. Verifying proofs locally...")
    context = ShareContext(
        job_id=job_id,
        nonce=nonce,
        mix_seed=mix_seed,
        height=1000,
        miner_address="test-miner",
        timestamp=int(time.time()),
    )
    
    result_t0 = verify_proof(proof_t0, context)
    print(f"   Tier 0 result: {result_t0.status.name}")
    print(f"   Reason: {result_t0.reason}")
    print(f"   Bonus credits: {result_t0.bonus_credits}")
    
    result_t1 = verify_proof(proof_t1, context)
    print(f"   Tier 1 result: {result_t1.status.name}")
    print(f"   Reason: {result_t1.reason}")
    print(f"   Bonus credits: {result_t1.bonus_credits}")
    
    # 4. Show RPC payload format
    print("\n4. RPC payload format (miner.submitShare):")
    print("   {")
    print(f'     "jobId": "{job_id}",')
    print(f'     "nonce": "{nonce.hex()}",')
    print('     "attachedProofs": [')
    print(f'       "{proof_t0_hex}",')
    print(f'       "{proof_t1_hex}"')
    print('     ]')
    print("   }")
    
    print("\n5. Expected response:")
    print("   {")
    print('     "accepted": true,')
    print('     "jobId": "example-job-123",')
    print('     "isBlock": false,')
    print('     "hash": "0x...",')
    print('     "proofs": [')
    print('       {')
    print('         "index": 0,')
    print('         "status": "ACCEPTED" | "REJECTED",')
    print('         "bonusCredits": 2000')
    print('       },')
    print('       {')
    print('         "index": 1,')
    print('         "status": "ACCEPTED" | "REJECTED",')
    print('         "bonusCredits": 5000')
    print('       }')
    print('     ],')
    print('     "totalBonusCredits": 7000')
    print("   }")
    
    print("\n" + "=" * 60)
    print("Example complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
