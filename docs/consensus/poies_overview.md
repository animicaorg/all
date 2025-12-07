# PoIES Overview — Proof-of-Integrated-External-Services

**Status:** Stable (v1)  
**Audience:** Protocol engineers, miners, validators, researchers  
**Related:** `consensus/scorer.py`, `consensus/validator.py`, `spec/poies_policy.yaml`, `proofs/*`

---

## 1) What is PoIES?

**PoIES** (Proof-of-Integrated-External-Services) is Animica's consensus mechanism that combines traditional hash-based work with verified external service contributions. The core acceptance predicate is:

```
S = H(u) + Σψ(p)  ≥  Θ
```

Where:
- **S** = Total block score (in natural-log units, "nats")
- **H(u)** = Base entropy from hash mining (internal useful work)
- **Σψ(p)** = Sum of external proof contributions, each weighted by ψ
- **Θ** = Dynamic threshold (adjusted by difficulty retargeting)

A block is **accepted** if and only if `S ≥ Θ`.

---

## 2) Components

### 2.1 Hash Mining: H(u)

The **internal useful work** component comes from traditional proof-of-work:
- Miners search for a nonce such that `H(header) < target`
- The difficulty ratio `d_ratio = share_difficulty / target_difficulty` determines H(u)
- **H(u) ≈ ln(d_ratio)** under the exponential race model
- This provides baseline security and prevents nothing-at-stake attacks

### 2.2 External Services: Σψ(p)

External services contribute additional "useful work" that goes beyond pure hashing:

#### Supported Service Types:
1. **Quantum Computing** - Verified quantum circuit execution with trap circuits
2. **AI Compute** - Training, inference, and model serving with quality-of-service metrics
3. **Storage** - Distributed storage with availability guarantees
4. **VDF** - Verifiable delay functions for timelock encryption

Each service proof `p` is:
- **Verified deterministically** (schema, attestation, cryptographic bindings)
- **Mapped to ψ** (contribution weight) via policy-defined scoring functions
- **Capped** at multiple levels (per-proof, per-type, and global Γ)

### 2.3 Threshold: Θ

The acceptance threshold **Θ** is:
- **Dynamically adjusted** based on block times (EMA-based difficulty retargeting)
- **Network-wide consensus parameter** (via policy roots in headers)
- Expressed in **micro-nats (µ-nats)** for integer arithmetic (1 nat = 1,000,000 µ-nats)

---

## 3) Quantum Jobs as External Service

Quantum computing is a **first-class external service** in PoIES. Here's how it integrates:

### 3.1 Job Lifecycle

```
Contract → Submit Job → AICF Queue → Provider Assigned
                                          ↓
                                    Execute Circuit
                                          ↓
                                    Generate Traps
                                          ↓
Block N ← QuantumProof ← Provider Publishes Result
```

### 3.2 Quantum Proof Structure

A **QuantumProof** contains:
- **Provider attestation** - Identity certificate (X.509 or PQ-hybrid)
- **Job parameters** - Circuit depth, width, shots
- **Trap circuits** - Statistical verification of execution correctness
- **QoS metrics** - Latency, availability, success rate
- **Result digest** - SHA3-256 of canonical output

### 3.3 Scoring Function: ψ_quantum

The quantum contribution is calculated as:

```python
ψ_quantum = k_units × quantum_units × qos × q_traps
```

Where:
- `quantum_units` = normalized compute units from `depth × width × log(1 + shots)`
- `qos` = quality of service score ∈ [0, 1]
- `q_traps` = trap circuit quality factor:
  - `0` if `traps_ratio < t_min` (below minimum threshold)
  - `1` if `traps_ratio ≥ t_target` (meets target)
  - Linear ramp between `t_min` and `t_target`
- `k_units` = policy weight parameter (default 1.5 for quantum)

**Policy parameters** (from `spec/poies_policy.yaml`):
- `t_min = 0.65` - Minimum acceptable trap success ratio
- `t_target = 0.90` - Target trap success ratio
- `k_units = 1.5` - Quantum units weight multiplier

### 3.4 Caps and Fairness

Quantum proofs are subject to multiple caps:

1. **Per-proof cap** - Maximum contribution from a single proof (5,000,000 µ-nats)
2. **Per-type cap** - Maximum total from all quantum proofs (7,000,000 µ-nats)
3. **Global Γ cap** - Maximum total external contribution (12,000,000 µ-nats)

These caps ensure:
- **No single provider dominance**
- **Diversity across service types**
- **Bounded advantage** from external services

---

## 4) Validation Flow

When a block arrives, the validator:

1. **Verifies policy root** - Ensures header's policy matches current network policy
2. **Checks nullifiers** - Prevents proof reuse via sliding-window TTL
3. **Verifies each proof** - Cryptographic verification, schema validation
4. **Computes ψ for each proof** - Maps metrics → µ-nats via scoring hooks
5. **Applies caps** - Per-proof, per-type, and global Γ
6. **Calculates H(u)** - From best hash share's difficulty ratio
7. **Evaluates S ≥ Θ** - Block accepted if score meets threshold
8. **Records nullifiers** - Marks proofs as used on acceptance

All steps are **deterministic** and **pure** (no I/O, clock, or external randomness).

**Code reference:** `consensus/validator.py::validate_block()`

---

## 5) Impact on Block Production

### Without Quantum Jobs
```
H(u) = 2.8 nats (from hash mining)
Θ = 3.0 nats (threshold)
S = 2.8 nats → REJECTED ❌
```

### With Quantum Job Completion
```
H(u) = 2.8 nats (from hash mining)
ψ_quantum = 0.5 nats (from completed quantum job)
S = 2.8 + 0.5 = 3.3 nats → ACCEPTED ✅
```

**Quantum jobs can flip block validity**, providing:
- **Additional security** - More work required to attack
- **Useful computation** - Real-world value beyond hashing
- **Economic incentives** - Providers earn fees for compute

---

## 6) Scoring Example

Given a completed quantum job:
```python
metrics = {
    "quantum_units": 2.5,     # From depth×width×log(shots)
    "traps_ratio": 0.87,      # 87% trap success rate
    "qos": 0.95,              # 95% QoS score
}
```

Scoring calculation:
```python
# Policy parameters
k_units = 1.5
t_min = 0.65
t_target = 0.90

# Trap quality factor
q_traps = (0.87 - 0.65) / (0.90 - 0.65) = 0.88

# Raw ψ (in nats)
ψ_raw = 1.5 × 2.5 × 0.95 × 0.88 = 3.135 nats
ψ_raw_micro = 3,135,000 µ-nats

# Apply per-proof cap (5,000,000 µ-nats)
ψ_capped = min(3,135,000, 5,000,000) = 3,135,000 µ-nats

# Contributes to block score
S = H(u) + 3.135 nats
```

---

## 7) Configuration

### Network Policy

The active PoIES policy is defined in `spec/poies_policy.yaml`:

```yaml
# Per-type caps (µ-nats = 1e6 × nats)
caps:
  quantum:
    per_type_micro: 7_000_000      # Max 7 nats from all quantum
    per_proof_micro_max: 5_000_000 # Max 5 nats per proof

# Global cap across all external services
gamma_cap: 12_000_000  # 12 nats total

# Quantum-specific weights
weights:
  quantum:
    k_units: 1.5        # Units weight multiplier
    t_min: 0.65         # Minimum trap ratio
    t_target: 0.90      # Target trap ratio
```

### Threshold Targeting

The threshold **Θ** is dynamically adjusted via:
- **EMA-based retargeting** (exponential moving average of recent block times)
- **Target interval** = 12 seconds (configurable per network)
- **Retarget window** = 100 blocks

See `consensus/difficulty.py` for implementation details.

---

## 8) Security Model

### Assumptions

1. **Hash mining provides base security** - H(u) prevents nothing-at-stake
2. **External services are additive** - Σψ cannot bypass hash requirement entirely
3. **Caps prevent capture** - No single service or provider can dominate
4. **Trap circuits bound cheating** - Statistical confidence limits fake quantum proofs

### Attack Vectors and Mitigations

| Attack | Mitigation |
|--------|-----------|
| Fake quantum results | Trap circuits + provider attestation + slashing |
| Provider collusion | Caps + diversity incentives + reputation system |
| Proof replay | Nullifier window with TTL |
| Policy manipulation | Policy roots committed in headers |
| Nothing-at-stake | H(u) base component required |

### Liveness

The chain remains **live** even if:
- All external services are unavailable (falls back to hash-only)
- Quantum providers go offline (other services compensate)
- Individual proofs fail verification (block rejected, proposer tries again)

---

## 9) Monitoring and Observability

### Metrics

Block score breakdown is exposed in receipts and logs:

```json
{
  "theta_micro": 3_000_000,
  "base_entropy_micro": 2_500_000,
  "sum_after_gamma": 800_000,
  "per_type_after_gamma": {
    "QUANTUM": 500_000,
    "AI": 200_000,
    "HASH": 100_000
  },
  "distance_micro": 300_000
}
```

### Logging

When quantum proofs are included:
```
[consensus] Block 12345 ACCEPTED: S=3.3 (H=2.8, Σψ=0.5, Θ=3.0)
[consensus]   Quantum contribution: 0.5 nats from 1 proof
[consensus]   Task ID: qpu-a1b2c3d4...
```

---

## 10) Developer Guide

### Adding Quantum Proofs to Blocks

Miners include quantum proofs by:

1. **Enqueue jobs** via QuantumJobs contract:
```python
job_id = quantum_jobs.submit_job(
    job_spec=canonical_circuit_cbor,
    fee_escrow=1000
)
```

2. **Wait for completion** (off-chain quantum worker processes):
```python
result = quantum_worker.pop_ready()
```

3. **Assemble proof envelope** from worker result:
```python
proof = {
    "type_id": ProofType.QUANTUM,
    "body_cbor": result_to_cbor(result),
    "nullifier": compute_nullifier(result)
}
```

4. **Include in block** during mining:
```python
block.proofs.append(proof)
```

5. **Validation happens automatically** when block is broadcast.

### Testing

Unit tests: `consensus/tests/test_scorer_accept_reject.py`  
Integration tests: `tests/integration/test_poies_quantum_integration.py`

Run with:
```bash
pytest consensus/tests/test_scorer_accept_reject.py -v
RUN_INTEGRATION_TESTS=1 pytest tests/integration/test_poies_quantum_integration.py -v
```

---

## 11) References

- **Implementation:** `consensus/scorer.py`, `consensus/validator.py`
- **Policy:** `spec/poies_policy.yaml`, `consensus/policy.py`
- **Quantum proofs:** `proofs/quantum.py`, `proofs/quantum_attest/`
- **Contracts:** `contracts/examples/quantum/`
- **Worker:** `mining/quantum_worker.py`
- **Theory:** `spec/poies_math.md`

---

## Changelog

- **v1.0** - Initial PoIES overview with quantum integration details
