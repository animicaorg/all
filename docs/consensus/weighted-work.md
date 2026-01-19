# Weighted Work Consensus in Animica

## Overview

Animica implements a **Weighted Work** consensus mechanism that assigns different value to computational work based on the device type used. This creates a fair incentive structure where:

- CPU miners can participate but earn baseline rewards
- GPU miners earn 5x more per unit of verified work
- Quantum miners earn 25x more per unit of verified work

This design ensures the chain remains decentralized (accessible to CPU miners) while incentivizing advanced hardware that can produce higher-value useful work.

## Core Formula

```
effective_work = cpu_score + (gpu_weight × gpu_score) + (quantum_weight × quantum_score)
```

Where:
- `cpu_score`: Base work score from CPU-verified useful work (µ-nats)
- `gpu_score`: Base work score from GPU-verified useful work (µ-nats)  
- `quantum_score`: Base work score from Quantum-verified useful work (µ-nats)
- `gpu_weight`: 5.0 (consensus constant)
- `quantum_weight`: 25.0 (consensus constant)

## Consensus Constants

Defined in `consensus/uwa_types.py`:

```python
WEIGHT_CPU = 1.0      # Baseline
WEIGHT_GPU = 5.0      # GPU work is 5x more valuable
WEIGHT_QUANTUM = 25.0 # Quantum work is 25x more valuable
```

These weights are **consensus-critical** and cannot be changed without a hard fork.

## Device Type Verification

Device types are **not self-reported**. Instead, they are verified through the work proof:

1. **CPU Work**: Memory-hard scrypt with N=2^14-2^16
2. **GPU Work**: Memory-hard scrypt with N=2^16-2^18 OR specialized GPU kernels
3. **Quantum Work**: Quantum circuit execution with provider attestation

Miners cannot claim a higher device type without providing the corresponding proof artifacts. The verifier rejects invalid claims.

## Scoring Examples

### Example 1: Pure CPU Mining

```python
cpu_score = 800_000  # 0.8 nats from hash work
gpu_score = 0
quantum_score = 0

effective_work = (1.0 × 800_000) + (5.0 × 0) + (25.0 × 0)
               = 800_000 µ-nats
```

### Example 2: Pure GPU Mining

```python
cpu_score = 0
gpu_score = 1_000_000  # 1.0 nats from GPU hash work
quantum_score = 0

effective_work = (1.0 × 0) + (5.0 × 1_000_000) + (25.0 × 0)
               = 5_000_000 µ-nats (5.0 nats effective)
```

GPU miner earns **5x more** than equivalent CPU work.

### Example 3: Pure Quantum Mining

```python
cpu_score = 0
gpu_score = 0
quantum_score = 2_000_000  # 2.0 nats from quantum circuit

effective_work = (1.0 × 0) + (5.0 × 0) + (25.0 × 2_000_000)
               = 50_000_000 µ-nats (50.0 nats effective)
```

Quantum miner earns **25x more** than equivalent CPU work, **5x more** than equivalent GPU work.

### Example 4: Hybrid Mining (CPU + GPU)

```python
cpu_score = 500_000  # Some CPU work
gpu_score = 800_000  # Some GPU work
quantum_score = 0

effective_work = (1.0 × 500_000) + (5.0 × 800_000) + (25.0 × 0)
               = 500_000 + 4_000_000
               = 4_500_000 µ-nats (4.5 nats effective)
```

Hybrid miners can combine different device types in the same block.

## Difficulty Adjustment

The difficulty target (Θ) adjusts based on **effective work**, not raw hash rate:

```python
def adjust_difficulty(prev_theta: int, actual_time: int, target_time: int) -> int:
    """
    EMA-based difficulty adjustment.
    
    Targets effective work to achieve desired block time.
    """
    alpha = 0.1  # EMA smoothing factor
    
    # Calculate adjustment ratio
    if actual_time > 0:
        ratio = target_time / actual_time
    else:
        ratio = 1.0
    
    # Apply EMA update
    new_theta = int(prev_theta * (1 - alpha) + prev_theta * ratio * alpha)
    
    # Clamp to prevent extreme swings
    max_change = prev_theta // 4  # Max 25% change per block
    new_theta = max(prev_theta - max_change, min(prev_theta + max_change, new_theta))
    
    return new_theta
```

This ensures:
- Quantum miners don't make blocks too easy for themselves
- CPU miners can still participate when quantum miners are offline
- Network adapts smoothly to changing miner composition

## Block Acceptance Predicate

A block is accepted if its effective work meets the difficulty threshold:

```python
def is_block_valid(block) -> bool:
    # Extract UWA from block
    uwa = extract_uwa(block)
    
    # Verify UWA and get base score
    result = verify_uwa(uwa, block.header.height, block.header.prev_hash, block.header.chain_id)
    if not result.valid:
        return False
    
    # Calculate device-specific score
    if result.device_type == DeviceType.CPU:
        cpu_score = result.work_score
        gpu_score = 0
        quantum_score = 0
    elif result.device_type == DeviceType.GPU:
        cpu_score = 0
        gpu_score = result.work_score
        quantum_score = 0
    elif result.device_type == DeviceType.QUANTUM:
        cpu_score = 0
        gpu_score = 0
        quantum_score = result.work_score
    
    # Calculate effective work
    effective_work = calculate_effective_work(cpu_score, gpu_score, quantum_score)
    
    # Check against threshold
    theta = get_difficulty_at_height(block.header.height)
    return effective_work >= theta
```

## Incentive Alignment

### For CPU Miners

- **Low barrier to entry**: Anyone with a CPU can mine
- **Predictable rewards**: Difficulty adjusts to maintain block time
- **Useful work**: Even CPU mining produces reusable VM artifacts

### For GPU Miners

- **5x reward multiplier**: Higher ROI on GPU hardware
- **Specialized work**: Can perform GPU-optimized tasks (parallel compilation, batch verification)
- **Network security**: GPU miners provide significant hashrate when needed

### For Quantum Miners

- **25x reward multiplier**: Maximum incentive for quantum computing
- **Cutting-edge work**: Quantum proofs enable post-quantum security research
- **Future-proof**: Network is ready for quantum era

## Economic Sustainability

### Reward Distribution

Assuming 300 ANM base block reward:

| Device Type | Base Score | Effective Score | Reward Share (if only this type) |
|-------------|------------|-----------------|----------------------------------|
| CPU         | 800K       | 800K            | 100% (baseline)                  |
| GPU         | 800K       | 4M (5×)         | 5× CPU (if competing)            |
| Quantum     | 800K       | 20M (25×)       | 25× CPU (if competing)           |

In a mixed-miner network, rewards are proportional to effective work contribution.

### Network Composition

Expected steady-state composition:
- **60% CPU miners**: Decentralization, accessibility
- **35% GPU miners**: Performance, security
- **5% Quantum miners**: Innovation, future-proofing

This distribution ensures:
- Decentralization (CPU miners prevent centralization)
- Performance (GPU miners provide throughput)
- Innovation (Quantum miners enable research)

## Determinism & Safety

### Deterministic Scoring

All scoring is deterministic:
```python
# Same input always produces same output
assert calculate_effective_work(100, 200, 50) == calculate_effective_work(100, 200, 50)
```

### No Hardware Detection

Device types are **not** detected from system hardware. They are explicitly specified in the UWA and verified through proof artifacts. This prevents:
- Miners lying about device type
- Timing attacks based on verification speed
- Non-deterministic "hardware fingerprinting"

### Anti-Inflation

Weighted scoring does **not** inflate supply:
- Block rewards are fixed (300 ANM initially)
- Difficulty adjusts to maintain target block time
- More powerful miners → higher difficulty → same block rate

## Governance & Upgrades

### Changing Weights

Weight changes require a **hard fork** because they affect consensus. The process:

1. Community proposal and discussion
2. BIP-style improvement proposal document
3. Testnet deployment and testing
4. Mainnet activation at specific height

### Adding New Device Types

New device types can be added via hard fork:

```python
class DeviceType(IntEnum):
    CPU = 1
    GPU = 2
    QUANTUM = 3
    NEUROMORPHIC = 4  # Future: neuromorphic computing
    OPTICAL = 5       # Future: optical computing
```

Each new type requires:
- Weight constant definition
- Verification function
- Work domain specification
- Test vectors

## Testing & Validation

### Test Vectors

See `consensus/tests/test_uwa_system.py`:

```python
def test_weighted_scoring():
    # Verify CPU < GPU < Quantum
    assert calculate_effective_work(100, 0, 0) == 100
    assert calculate_effective_work(0, 100, 0) == 500
    assert calculate_effective_work(0, 0, 100) == 2500
```

### Simulation

Run network simulation to verify economic incentives:

```bash
python scripts/simulate_weighted_mining.py --duration 1000 --cpu-ratio 0.6 --gpu-ratio 0.35 --quantum-ratio 0.05
```

## References

- `consensus/uwa_types.py` - Weight constants and `calculate_effective_work()`
- `consensus/uwa_verifier.py` - Device type verification
- `consensus/validator.py` - Block acceptance with weighted scoring
- `docs/consensus/useful-work.md` - Useful work artifact specification

## Future Research

- **Adaptive Weights**: Adjust weights based on network composition
- **Proof-of-Useful-Work**: More complex useful work types with dynamic weights
- **Multi-Tier Scoring**: Fine-grained device tiers (CPU/GPU-low/GPU-high/Quantum)
- **Hybrid Consensus**: Combine weighted work with other mechanisms (stake, reputation)
