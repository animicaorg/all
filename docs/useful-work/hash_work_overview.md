# Hash-Based Useful Work Overview

## Introduction

Hash-based useful work is a first-class external service integrated with Animica's PoIES (Proof-of-Integrated-External-Services) consensus. It allows the blockchain to harness various hash computation resources (CPU, GPU, ASIC, Quantum simulators) as productive work that contributes to block acceptance.

Unlike traditional Proof-of-Work where hash computations only serve to secure the chain, Animica's hash work system provides useful computation that can be applied to real-world problems while still contributing to consensus security.

## Architecture

The hash work system consists of several integrated components:

### 1. Algorithm Registry (`python/animica/hash_work/algorithms.py`)

Defines supported hash algorithms with validation rules and PoIES weighting:

- **SHA256**: Standard SHA-256 hashing (weight: 1.0)
- **SHA256D**: Double SHA-256, Bitcoin-style (weight: 1.2)
- **SCRYPT**: Memory-hard hash function (weight: 2.0)
- **ARGON2**: Modern memory-hard function (weight: 2.5)
- **BLAKE2B**: High-performance hash (weight: 0.9)

Each algorithm has:
- Target difficulty validation (min/max bits)
- Iteration/work limits for bounded execution
- Algorithm-specific parameters (e.g., Scrypt N, r, p)
- PoIES weighting for scoring contributions

### 2. Job/Result Schemas (`python/animica/hash_work/schemas.py`)

Defines data structures for hash jobs and results:

**HashJob**: Complete job specification
```python
@dataclass
class HashJob:
    job_id: bytes              # 32-byte unique identifier
    algorithm: HashAlgorithm   # Algorithm to use
    input_commitment: bytes    # 32-byte hash commitment
    target_bits: int          # Difficulty target
    max_iterations: int       # Iteration limit
    max_cost: int            # Computational cost limit
    # Optional Scrypt parameters
    scrypt_n: Optional[int]
    scrypt_r: Optional[int]
    scrypt_p: Optional[int]
```

**HashResult**: Result with proof metadata
```python
@dataclass
class HashResult:
    job_id: bytes           # Job identifier
    output_hash: bytes      # 32-byte result hash
    nonce: bytes           # Solution nonce
    iterations: int        # Actual iterations performed
    device_type: DeviceType  # CPU/GPU/ASIC/QUANTUM/FPGA/OTHER
    backend_id: str        # Backend implementation ID
    worker_address: Optional[str]  # Worker address
```

All schemas support deterministic CBOR encoding for consensus-critical use.

### 3. VM-Py Stdlib (`vm_py/stdlib/hash_work.py`)

Provides on-chain-safe hash functions for contracts:

**Core Hash Functions**:
- `sha256(data) -> bytes`: SHA-256 hash
- `sha256d(data) -> bytes`: Double SHA-256
- `blake2b_256(data) -> bytes`: BLAKE2b-256
- `compute_commitment(data) -> bytes`: Create input commitment

**Job Descriptor Builders**:
- `make_hash_job_sha256(commitment, target_bits, max_iters) -> dict`
- `make_hash_job_sha256d(commitment, target_bits, max_iters) -> dict`
- `make_hash_job_scrypt(commitment, N, r, p, target_bits, max_cost) -> dict`

**Result Verification**:
- `verify_hash_result(job_desc, result_desc) -> bool`: Bounded on-chain verification

### 4. On-Chain Contracts

#### HashJobs Contract (`contracts/examples/hash_work/hash_jobs.py`)

Manages hash job registry and results:

```python
# Post a new job
job_id = post_job(
    algorithm=b"SHA256",
    input_commitment=commitment,
    target_bits=16,
    max_iterations=1000000
)

# Mark job as completed
mark_completed(
    job_id=job_id,
    output_hash=result_hash,
    nonce=solution_nonce,
    iterations=actual_iterations,
    device_type=b"GPU",
    backend_id=b"cuda-11.8"
)

# Query job status
exists, algo, commitment, target, max_iters, status = get_job(job_id)
```

Events emitted:
- `HashJobPosted(job_id, algorithm, input_commitment, target_bits)`
- `HashJobCompleted(job_id, output_hash, iterations, device_type)`

#### HashWorkers Contract (`contracts/examples/hash_work/hash_workers.py`)

Worker capability registry:

```python
# Register worker with capabilities
register_worker(
    address=worker_addr,
    device_type=b"GPU",
    metadata=worker_metadata
)

# Manage algorithm capabilities
add_algorithm_capability(address, b"SHA256")
remove_algorithm_capability(address, b"SCRYPT")

# Check capabilities
is_supported = supports_algorithm(address, b"SHA256")
is_active = is_active(address)
```

### 5. Off-Chain Worker Daemon (`python/animica/hash_worker/`)

Pluggable worker daemon with multiple backend support:

**Backends**:
- **CPUBackend**: Full Python implementation using hashlib
- **GPUBackend**: Mock (delegates to CPU, reports as GPU)
- **ASICBackend**: Mock (delegates to CPU, reports as ASIC)
- **QuantumBackend**: Mock (delegates to CPU, reports as QUANTUM)

**Features**:
- Event subscription for HashJobPosted
- Job execution with configurable backends
- Result submission to HashJobs contract
- State persistence for restart resilience
- Configuration via environment variables

**CLI Usage**:
```bash
# Start daemon
python -m python.animica.hash_worker.cli start --backend cpu

# Test job execution
python -m python.animica.hash_worker.cli test-job \
    --backend cpu \
    --algorithm SHA256 \
    --target 16 \
    --max-iterations 1000000

# Benchmark performance
python -m python.animica.hash_worker.cli benchmark \
    --backend cpu \
    --algorithm SHA256D
```

### 6. PoIES Integration

Hash work proofs contribute to block acceptance via the PoIES scoring system:

**Proof Type**: `HASH_WORK` (0x06)

**Proof Body**:
```python
@dataclass
class HashWorkBody:
    job_id: Bytes32          # Job identifier
    algorithm: str           # Algorithm used
    output_hash: Bytes32     # Result hash
    nonce: bytes            # Solution nonce
    iterations: int         # Iterations performed
    device_type: str        # Device type
    target_bits: int        # Difficulty met
    work_units: int         # Abstract work units
```

**Metrics** (`proofs/metrics.py`):
- `hash_work_units`: Abstract work measure
- `hash_iterations`: Iterations performed
- `hash_target_bits`: Difficulty achieved
- `qos`: Quality of service (0-1)

**Scoring** (`consensus/scorer.py`):
```python
score = k_units * (units + difficulty_bonus + iteration_bonus) * qos
```

Where:
- `difficulty_bonus = log(1 + target_bits) / 10`
- `iteration_bonus = log(1 + iterations) / 100`
- Default `k_units = 0.8`

## Hardware-Agnostic Policy

The hash work system is designed to be hardware-agnostic:

1. **Device Type Metadata**: Workers report device type (CPU/GPU/ASIC/QUANTUM/FPGA) but this is metadata only
2. **Algorithm-Based Weighting**: Different algorithms have different weights reflecting computational difficulty
3. **Difficulty-Based Scoring**: Higher target difficulty increases score contribution
4. **No Vendor Lock-In**: System doesn't prefer specific hardware vendors or implementations

## Cost Limits and Safety

All hash work execution is bounded:

1. **Max Iterations**: Prevents infinite loops
2. **Target Validation**: Ensures reasonable difficulty ranges (8-256 bits for SHA-family)
3. **Algorithm Parameters**: Scrypt N/r/p validated to safe ranges
4. **Deterministic Verification**: On-chain verification uses bounded checks

## Use Cases

### Mining Pools

Use hash work for distributed mining with reward distribution:

```python
# Create job
job_id = create_sha256_job(input_data)

# Workers submit solutions
submit_work(job_id, output_hash, nonce, iterations)

# Track shares and distribute rewards
```

### Computational Marketplaces

Clients post hash jobs with specific requirements, workers compete to solve them.

### Hybrid Consensus

Combine hash work with AI/Quantum/Storage proofs for diverse security:

```
Block Score = base_entropy + ψ_hash_work + ψ_ai + ψ_quantum + ψ_storage
```

## Deterministic Verification

Hash work results can be verified on-chain deterministically:

1. **Commitment Check**: Verify input commitment matches
2. **Difficulty Check**: Verify output hash meets target
3. **Iteration Bounds**: Verify iterations within limits
4. **Hash Recomputation**: For small jobs, recompute hash on-chain

Full cryptographic verification can be done off-chain or via recursive proofs.

## Configuration

### Worker Configuration (Environment Variables)

```bash
# RPC endpoint
ANIMICA_RPC_URL=http://localhost:8545

# Chain ID
ANIMICA_CHAIN_ID=1337

# Backend type
HASH_BACKEND_TYPE=cpu  # or gpu, asic, quantum

# Worker address
HASH_WORKER_ADDRESS=anim1...

# Poll interval (seconds)
HASH_POLL_INTERVAL=5.0

# State file for persistence
HASH_STATE_FILE=/var/lib/animica/hash_worker_state.json

# Backend-specific config (JSON)
HASH_BACKEND_CONFIG='{"cuda_device": 0}'
```

### Policy Configuration

PoIES policy weights are configurable in `spec/poies_policy.yaml`:

```yaml
weights:
  HASH_WORK:
    k_units: 0.8          # Base unit weight
    difficulty_scale: 0.1 # Difficulty bonus scaling
    iteration_scale: 0.01 # Iteration bonus scaling

caps:
  HASH_WORK:
    per_proof_micro_max: 5000000   # 5 nats max per proof
    per_type_micro: 10000000       # 10 nats max per block
```

## Testing

### Unit Tests

```bash
# Hash work module tests
pytest python/animica/hash_work/tests/

# VM-Py stdlib tests
pytest vm_py/tests/test_stdlib_hash_work.py
```

### Integration Tests

```bash
# Full integration scenario
pytest tests/integration/test_hash_work.py
```

### Worker Testing

```bash
# Test single job
python -m python.animica.hash_worker.cli test-job \
    --backend cpu \
    --algorithm SHA256 \
    --target 12 \
    --input-data "test data"

# Benchmark multiple difficulties
python -m python.animica.hash_worker.cli benchmark \
    --backend cpu \
    --algorithm SHA256D
```

## Future Extensions

1. **More Algorithms**: Add Argon2, Equihash, RandomX support
2. **GPU Acceleration**: Real CUDA/OpenCL implementations
3. **ASIC Integration**: Interface with actual mining hardware
4. **Quantum Hardware**: Interface with real quantum computers
5. **Proof Aggregation**: Batch multiple hash results into single proof
6. **Cross-Chain Bridging**: Use hash work to bridge to other chains

## Security Considerations

1. **Nullifier Uniqueness**: Each proof has unique nullifier to prevent double-counting
2. **Target Validation**: Bounds on difficulty prevent gaming
3. **Algorithm Limits**: Parameter validation prevents resource exhaustion
4. **Worker Registration**: Optional registration for trust/reputation
5. **QoS Metrics**: Track worker reliability over time

## References

- [PoIES Consensus Spec](../consensus/poies.md)
- [External Services Architecture](./external_services.md)
- [VM-Py Security Model](../vm_py/security.md)
- [Hash Work Schemas](../../python/animica/hash_work/schemas.py)
