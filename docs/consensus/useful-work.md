# Useful Work Mining in Animica

## Overview

Animica implements **Useful Work Mining** where miners produce verifiable, deterministic work that directly benefits the chain's Python VM and smart contract execution layer. Unlike traditional Proof-of-Work that only secures the chain, Animica's mining produces reusable computational artifacts.

## Core Concepts

### Useful Work Artifacts (UWA)

Every mined block includes a **Useful Work Artifact** that contains:

1. **Work Domain**: Type of useful work (VM compilation, execution traces, state transitions)
2. **Device Type**: CPU, GPU, or Quantum (affects scoring weight)
3. **Challenge Binding**: Cryptographic binding to block context (height, prev_hash, chain_id)
4. **Input/Output Commitments**: SHA3-256 hashes of work inputs and outputs
5. **Proof Data**: Bounded, verifiable proof of work completion
6. **Work Score**: Deterministically computed contribution in µ-nats

### Work Domains

#### VM Compilation (`vm.compile.v1`)
Miners compile Python smart contract source code to bytecode, producing:
- Compiled bytecode (reusable by all nodes)
- Gas cost estimates per function
- Symbol tables and dependencies
- ABI encoding verification

**Benefits**: Accelerates contract deployment and reduces redundant compilation.

#### Hash Work (`hash.work.v1`)
Memory-hard scrypt-based work that:
- Resists ASIC optimization
- Provides portable fallback when specialized backends unavailable
- Binds work to block challenge via salted scrypt

**Benefits**: Ensures chain continues even without AI/Quantum providers.

#### Quantum Circuits (`quantum.circuit.v1`)
Execution of verifiable quantum circuits:
- Circuit depth and qubit count validation
- Provider attestation verification
- Trap-circuit fraud detection
- Measurement outcome commitments

**Benefits**: Enables quantum-resistant proof generation and quantum computing integration.

## Weighted Work Scoring

Different device types receive different weight multipliers:

```
effective_work = cpu_score + (5.0 × gpu_score) + (25.0 × quantum_score)
```

### Weight Ratios

- **CPU**: 1x weight (baseline)
- **GPU**: 5x weight (5x more valuable than CPU work)
- **Quantum**: 25x weight (25x more valuable than CPU, 5x more than GPU)

This incentivizes miners to use more advanced hardware while keeping the chain accessible to CPU miners.

## Determinism & Security

### Deterministic Verification

All UWA verification is:
- **Pure**: No I/O, no clock, no randomness
- **Bounded**: Time and space limits prevent DoS
- **Reproducible**: Same input always produces same result

### Anti-Replay Protection

Each UWA is cryptographically bound to its block context:
```python
challenge = WorkChallenge(
    height=100,
    prev_hash=parent_hash,
    chain_id=1337,
    timestamp=block_time,
    mix_seed=parent_mix_seed,
)
```

Attempting to replay a UWA at a different height or on a different chain fails verification.

### Bounds Checking

- Maximum proof size: 1 MB
- Maximum total UWA size: 2 MB
- Verification timeout: 5-15 seconds (varies by device type)
- Gas estimation caps: Prevent unbounded loops

## Mining Workflow

### 1. Get Mining Template

```python
from mining.templates import get_mining_template

template = get_mining_template(rpc_client)
work_challenge = template.get_work_challenge()
```

### 2. Generate Useful Work

```python
from consensus.uwa_generator import generate_hash_work_uwa, DeviceType

uwa = generate_hash_work_uwa(
    challenge=work_challenge,
    nonce=found_nonce,
    iterations=2**16,  # Higher for GPU
    device_type=DeviceType.GPU,
    miner_address="anim1...",
)
```

### 3. Submit Block with UWA

```python
block = {
    "header": header,
    "transactions": txs,
    "proofs": [hash_share_proof, uwa],  # Include UWA in proofs
}

rpc_client.submit_block(block)
```

## Consensus Integration

### Block Validation

During block import, the validator:

1. Extracts UWA from proofs array
2. Verifies size bounds (< 2 MB)
3. Checks challenge binding (height, prev_hash, chain_id)
4. Performs domain-specific verification
5. Computes work score deterministically
6. Applies device type weight
7. Adds weighted score to acceptance predicate

### Acceptance Predicate

```
S = H(u) + Σψ_proofs + UWA_effective >= Θ

where:
  H(u) = base entropy from hash u-draw
  Σψ_proofs = sum of proof contributions (AI, Storage, VDF)
  UWA_effective = cpu_score + 5×gpu_score + 25×quantum_score
  Θ = difficulty target at height
```

A block is valid only if its total score meets or exceeds the threshold.

## Device Type Guidelines

### CPU Mining

**Recommended Work**: Hash work with N=2^14 to 2^16

```bash
WORK_TYPE=hash_work DEVICE=cpu ./dev/mine-cpu.sh
```

**Expected Score**: ~800K µ-nats (0.8 nats)
**Weight**: 1x

### GPU Mining

**Recommended Work**: Hash work with N=2^16 to 2^18

```bash
WORK_TYPE=hash_work DEVICE=gpu ./dev/mine-gpu.sh
```

**Expected Score**: ~1M µ-nats (1.0 nats) × 5 = 5M effective
**Weight**: 5x

### Quantum Mining

**Recommended Work**: Quantum circuits with depth 50+, 20+ qubits

```bash
WORK_TYPE=quantum DEVICE=quantum ./dev/mine-quantum.sh
```

**Expected Score**: ~2M µ-nats (2.0 nats) × 25 = 50M effective
**Weight**: 25x

## Performance Characteristics

### Verification Costs

| Device Type | Verification Time | Proof Size | Score Range |
|-------------|-------------------|------------|-------------|
| CPU         | < 5 seconds       | 100-500 KB | 100K-1M µ-nats |
| GPU         | < 10 seconds      | 100-1 MB   | 500K-5M µ-nats |
| Quantum     | < 15 seconds      | 500 KB-1 MB | 1M-10M µ-nats |

### Storage Costs

Average UWA size: ~200 KB per block
At 10-second blocks: ~1.7 GB/day
Nodes can prune UWA data after validation (proofs are not needed for state consensus).

## Future Enhancements

### Planned Work Domains

- **VM Execution Traces** (`vm.trace.v1`): Witness generation for state transitions
- **Contract Simulation** (`contract.simulate.v1`): Pre-execution proofs for indexing
- **State Merkle Proofs** (`state.merkle.v1`): Accelerated state root verification
- **ABI Encoding Caches** (`contract.abi.v1`): Reusable ABI encoding results

### Optimization Opportunities

- **Batch Verification**: Verify multiple UWAs in parallel
- **Proof Compression**: Use zstd/snappy for proof data
- **Incremental Compilation**: Cache partial compilation results
- **GPU-Optimized Bytecode**: Target GPU execution for hot contracts

## References

- `consensus/uwa_types.py` - Core type definitions
- `consensus/uwa_verifier.py` - Deterministic verification
- `consensus/uwa_generator.py` - Mining-side generation
- `consensus/validator.py` - Consensus integration
- `mining/templates.py` - Template generation with challenges

## Security Considerations

1. **DoS Prevention**: All verification is bounded (time, space, loops)
2. **Replay Prevention**: Challenge binding prevents cross-block/chain reuse
3. **Hardware Lying**: Device type is not trusted; score is based on verified work
4. **Proof Forgery**: Output commitments use SHA3-256; collision-resistant
5. **Verification Bugs**: Formal specification and test vectors ensure correctness

## Getting Started

See the mining scripts in `dev/`:
- `dev/mine-cpu.sh` - CPU mining with useful work
- `dev/mine-gpu.sh` - GPU mining with weighted scoring
- `dev/mine-quantum.sh` - Quantum mining with highest weight

For testing:
```bash
pytest consensus/tests/test_uwa_system.py -v
```
