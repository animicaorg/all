# Useful Work Proof (UWP) Schema Documentation

## Overview

Useful Work Proofs (UWPs) allow miners to attach verifiable proofs of computational work (AI training, evaluation, etc.) to their mining shares. Validators verify these proofs without recomputing the entire workload, using deterministic spot-checks, cryptographic receipts, or ZK proofs.

## Core Envelope

A UWP is a CBOR-encoded envelope with the following schema:

```cddl
UsefulWorkProof = {
  scheme_id: tstr,              ; Proof scheme identifier (e.g., "ena.eval.micro")
  plan_commitment: bytes .size 32,   ; SHA3-256 hash of work plan (stored in DA)
  instance_id: bytes .size 32,       ; Unique instance ID (prevents replay)
  input_commitment: bytes .size 32,  ; SHA3-256 hash of inputs
  output_commitment: bytes .size 32, ; SHA3-256 hash of outputs
  receipt_bytes: bytes,              ; Scheme-specific proof data
  ? metadata: { * tstr => any }      ; Optional scheme-specific metadata
}
```

### Field Descriptions

- **scheme_id**: String identifying the proof scheme (1-64 chars). Examples:
  - `"ena.eval.micro"` - Tier 0 deterministic evaluation
  - `"compute.receipt.v1"` - Tier 1 signed compute receipt
  - `"zkml.infer.v1"` - Tier 2 ZK proof (future)

- **plan_commitment**: 32-byte SHA3-256 hash of the work plan/task definition. The plan is stored off-chain (in DA) and contains:
  - Task description
  - Dataset references
  - Model specifications
  - Expected outputs (for Tier 0)

- **instance_id**: 32-byte unique identifier for this proof instance. Computed as:
  ```
  SHA3-256(scheme_id || plan_commitment || worker_id || timestamp || nonce)
  ```
  
- **input_commitment**: 32-byte SHA3-256 hash of inputs (dataset shards, prompts, etc.)

- **output_commitment**: 32-byte SHA3-256 hash of outputs (model predictions, metrics, etc.)

- **receipt_bytes**: Opaque bytes containing scheme-specific proof data. Size limited by policy (typically 64KB max).

- **metadata**: Optional map for scheme-specific metadata (max 32 keys, values limited to 1KB each). Examples:
  - `steps`: Number of training/inference steps
  - `tokens`: Number of tokens processed
  - `model_id`: Model identifier

## Proof Schemes

### Tier 0: `ena.eval.micro` (Deterministic Evaluation)

Worker processes a batch of inputs and produces outputs. Validators verify random spot-checks using Merkle proofs.

**Receipt Format** (CBOR):
```cddl
EnaEvalMicroReceipt = {
  num_items: uint,                    ; Total items processed
  outputs_merkle_root: bytes .size 32,; Merkle root of (input_hash, output_hash) pairs
  spot_check_indices: [* uint],       ; k deterministically selected indices
  spot_check_proofs: [* bytes],       ; Merkle paths for selected indices
  spot_check_values: [* [bytes .size 32, bytes .size 32]]  ; (input_hash, output_hash) pairs
}
```

**Verification**:
1. Derive spot-check indices deterministically:
   ```
   indices = PRF(jobId || nonce || mixSeed || instance_id) mod num_items
   ```
2. For each index, verify Merkle proof against `outputs_merkle_root`
3. Accept if all proofs valid

**Bonus**: Fixed credits (default: 2000)

### Tier 1: `compute.receipt.v1` (Signed Compute Receipt)

GPU contributor runs compute and produces a signed receipt. Validators check signature + policy.

**Receipt Format** (CBOR):
```cddl
ComputeReceiptV1 = {
  contributor_id: tstr,               ; Registered contributor address/ID
  steps: uint,                        ; Training/inference steps performed
  tokens: uint,                       ; Tokens processed (for LLMs)
  model_id: tstr,                     ; Model identifier
  timestamp: uint,                    ; Unix timestamp
  trace_summary_hash: bytes .size 32, ; Hash of execution trace summary
  signature: bytes,                   ; PQ signature (Dilithium3)
  public_key: bytes                   ; Contributor's public key
}
```

**Signed Message**:
```
message = plan_commitment || instance_id || input_commitment || 
          output_commitment || trace_summary_hash ||
          steps (8 bytes BE) || tokens (8 bytes BE) || timestamp (8 bytes BE)
```

**Verification**:
1. Check contributor is registered and active
2. Verify PQ signature with public key
3. Validate counters (steps, tokens) within reasonable bounds
4. Accept if signature valid and policy checks pass

**Bonus**: Scaled by work done (default: 5000, scaled by `min(steps/10000, 10)`)

### Tier 2: `zkml.infer.v1` / `zkml.train.step.v1` (Future)

ZK proof of ML inference or training step. Interface defined, implementation stubbed.

## Policy Control

Policy file: `spec/uwp_policy.yaml`

Key policy parameters:
- `enabled`: Global UWP system toggle
- `max_proofs_per_share`: Max proofs per share (default: 5)
- `max_total_bytes_per_share`: Max total proof bytes (default: 256KB)
- `max_verify_ms_per_share`: Total verification time budget (default: 2000ms)
- `require_proofs`: If true, shares MUST have valid proofs (default: false)

Per-scheme policy:
- `enabled`: Scheme enabled/disabled
- `min_version`: Minimum required version
- `bonus_credits`: Fixed bonus credits per proof
- `bonus_credits_bp`: Alternative bonus as basis points of base reward
- `per_share_cap`: Max proofs of this scheme per share
- `per_miner_hourly_cap`: Rate limit per miner
- `per_epoch_cap`: Global cap per epoch
- `max_verify_ms`: Max verification time per proof

## CBOR Encoding

Proofs are encoded using canonical CBOR (RFC 8949):
- Deterministic map key ordering (lexicographic by CBOR encoding)
- No floating-point
- No indefinite-length items
- No duplicate keys

### Size Limits

- `receipt_bytes`: 64KB max (policy-controlled)
- `metadata`: 32 keys max, 1KB per value
- Total CBOR: 256KB max per share (all proofs combined)

### Encoding API

```python
from core.usefulwork import (
    UsefulWorkProof,
    encode_proof_to_hex,
    decode_proof_from_hex,
)

# Create proof
proof = UsefulWorkProof(
    scheme_id="ena.eval.micro",
    plan_commitment=b'\x01' * 32,
    instance_id=b'\x02' * 32,
    input_commitment=b'\x03' * 32,
    output_commitment=b'\x04' * 32,
    receipt_bytes=b'...',
    metadata={"num_items": 100},
)

# Encode to hex for RPC
hex_str = encode_proof_to_hex(proof)

# Decode from hex
proof2 = decode_proof_from_hex(hex_str)
```

## Share Submission with Proofs

Shares now accept an `attachedProofs` field (array of hex-encoded proofs):

```json
{
  "jobId": "abc123",
  "nonce": "0x1234567890abcdef",
  "attachedProofs": [
    "a2...f3",  // Hex-encoded CBOR proof 1
    "b4...e2"   // Hex-encoded CBOR proof 2
  ]
}
```

RPC endpoint: `miner.submitShare` / `miner_submitShare`

## Verification Flow

1. **Parse proofs**: Decode each hex string to CBOR
2. **Enforce limits**: Check count, total bytes, CBOR structure
3. **Time budget**: Allocate verification time per proof
4. **Verify each proof**:
   - Check scheme enabled by policy
   - Get registered verifier
   - Run verifier with time budget
   - Catch exceptions → mark as `VERIFIER_ERROR`
5. **Record results**: Accepted proofs earn bonus credits
6. **Return share result**: Share accepted even if proofs invalid (unless `require_proofs=true`)

## Bonus Credit Accounting

Verified proofs earn bonus AICF credits paid from escrow budget:

- **Source**: AICF epoch bonus budget (default: 1M credits/epoch)
- **Per-proof**: Fixed credits (scheme-specific) or scaled by work
- **Caps**: Per-share, per-miner-hourly, per-epoch
- **Deterministic**: Same proof verification → same credits (on replay)

Credits are logged for transparency and auditing.

## Anti-DoS Protection

- **Early rejection**: Oversized proofs rejected before CBOR decode
- **CBOR depth limit**: Max 8 levels deep
- **Time budget**: Verification stops after budget exhausted
- **Rate limits**: Per-miner hourly caps prevent spam
- **Graceful degradation**: Invalid proofs don't crash node; share still accepted

## Error Codes

Verification status codes:
- `ACCEPTED` (0): Proof valid, bonus awarded
- `REJECTED` (1): Proof invalid
- `SKIPPED_BUDGET` (2): Verification exceeded time budget
- `SCHEME_DISABLED` (3): Scheme disabled by policy
- `SCHEME_UNSUPPORTED` (4): No verifier for scheme
- `VERIFIER_ERROR` (5): Internal verifier error (caught exception)

## RPC Endpoints

### `debug.verifyUsefulWorkProof`

Verify a proof without submitting a share (for testing).

**Request**:
```json
{
  "proofHex": "a2...",
  "context": {
    "jobId": "abc123",
    "nonce": "0x1234...",
    "mixSeed": "0x5678...",
    "height": 12345,
    "minerAddress": "anim1...",
    "timestamp": 1234567890
  }
}
```

**Response**:
```json
{
  "status": "ACCEPTED",
  "statusCode": 0,
  "accepted": true,
  "reason": "Valid ena.eval.micro proof",
  "bonusCredits": 2000,
  "metadata": {
    "num_items": 100,
    "spot_checks": 8
  }
}
```

### `miner.submitShare` (updated)

Submit a mining share with optional attached proofs.

**Request**:
```json
{
  "jobId": "abc123",
  "nonce": "0x1234567890abcdef",
  "attachedProofs": ["a2...", "b4..."]
}
```

**Response**:
```json
{
  "accepted": true,
  "hash": "0xabcd...",
  "proofs": [
    {
      "index": 0,
      "status": "ACCEPTED",
      "bonusCredits": 2000
    },
    {
      "index": 1,
      "status": "REJECTED",
      "reason": "Invalid Merkle proof"
    }
  ],
  "totalBonusCredits": 2000
}
```

## Examples

See:
- `tests/unit/test_uwp_basic.py` - Basic encoding/decoding
- `tests/unit/test_uwp_verifiers.py` - Verifier tests
- `tests/e2e/test_uwp_mining.py` - End-to-end share submission
- `scripts/uwp_example_submit.py` - CLI example

## Security Considerations

1. **No ML recomputation**: Validators NEVER run full training/inference
2. **Deterministic verification**: Same inputs → same result (for replay)
3. **Bounded resources**: Time and memory limits strictly enforced
4. **Graceful failures**: Proof failures don't crash node
5. **Policy gating**: Schemes can be disabled if abused
6. **Rate limiting**: Per-miner and global caps prevent spam
7. **Signature verification**: Tier 1 receipts use PQ-safe signatures
8. **Nullifier/replay prevention**: `instance_id` must be unique

## Future Extensions

- **ZK proofs**: Cryptographically prove ML correctness
- **TEE attestation**: Require SGX/SEV for Tier 1 receipts
- **Contributor slashing**: Slash stake for fraudulent receipts
- **Multi-party computation**: Aggregated proofs from multiple workers
- **Adaptive bonuses**: Scale rewards by difficulty/scarcity

## References

- [Phase 2 Implementation Plan](../PR_SUMMARY.md)
- [AICF Economics](../aicf/README.md)
- [PoIES Consensus](../spec/poies_math.md)
- [CBOR Encoding](../core/encoding/cbor.py)
