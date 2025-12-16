# Difficulty Adjustment Mechanism

## Overview

The Animica blockchain implements a dynamic difficulty adjustment mechanism that automatically adjusts the mining difficulty (Θ, theta) based on network conditions to maintain a stable block production rate. This document describes how the difficulty adjustment works, its integration into the block import process, and how to monitor and configure it.

## Key Concepts

### Difficulty (Θ - Theta)

- **Unit**: Micro-nats (µ-nats), where 1 nat = 1,000,000 µ-nats
- **Purpose**: Controls the acceptance threshold for blocks in the PoIES (Proof-of-Integrated-External-Services) consensus
- **Behavior**: 
  - Higher Θ = harder difficulty = slower block production
  - Lower Θ = easier difficulty = faster block production

### Target Block Time

The blockchain aims to maintain a consistent block production rate (e.g., 12 seconds per block). The difficulty adjustment mechanism monitors actual block intervals and adjusts Θ to keep the average interval close to the target.

## Algorithm

The difficulty adjustment uses an **Exponential Moving Average (EMA)** based retargeting algorithm with the following characteristics:

1. **Fractional updates**: Adjusts difficulty incrementally rather than in large steps
2. **EMA smoothing**: Uses a half-life parameter to smooth out variance in block times
3. **Proportional control**: Responds proportionally to the deviation from target
4. **Bounded adjustments**: Limits per-step changes and enforces global min/max bounds

### Mathematical Model

The algorithm tracks:
- `Θ`: Current acceptance threshold in µ-nats
- `dt`: Observed inter-block time in seconds
- `T`: Target inter-block time in seconds

On each new block:

```
r = ln(dt / T)                          # Log ratio of observed to target
r̂ = (1-α)^m · r̂_prev + (1-(1-α)^m) · r # EMA update
τ_next = τ - β · r̂                      # Proportional adjustment
Θ_next = clamp(Θ + Δ, Θ_min, Θ_max)   # Apply with bounds
```

Where:
- `α`: Smoothing factor derived from half-life
- `β`: Proportional gain (typically 0.5-1.0)
- `m`: Number of blocks skipped (usually 1)
- `τ`: Theta in nats (Θ / 1e6)

### Parameters

Difficulty adjustment is configured via `ChainParams` loaded from `spec/params.yaml`:

```yaml
consensus:
  theta_initial: 3000000        # Initial Θ at genesis (3.0 nats)
  retarget:
    window: 24                  # Half-life in blocks
    ema_alpha: 0.2              # Smoothing/gain factor
    bounds:
      min: 0.5                  # Min multiplier per retarget
      max: 2.0                  # Max multiplier per retarget

block:
  target_seconds: 12.0          # Target block interval
```

These parameters map to `consensus.difficulty.RetargetParams`:
- `target_block_time_s`: From `block.target_seconds`
- `half_life_blocks`: From `retarget.window`
- `gain_beta`: From `retarget.ema_alpha`
- `step_clamp_micro`: Computed from `retarget.bounds`
- `theta_min_micro`: 500,000 µ-nats (0.5 nats) - lower bound (required)
- `theta_max_micro`: None (unbounded) - upper bound is now optional for dynamic scaling

**Unbounded Theta:** The network now supports unbounded theta growth (theta_max_micro=None), allowing difficulty to scale indefinitely to match any hash rate. Stability is maintained through step clamps (limits rate of change) and overflow protection (caps at 10^9 nats). See `docs/UNBOUNDED_THETA.md` for details.

## Implementation

### Integration Points

The difficulty adjustment is integrated into the block import process at `core/chain/block_import.py`:

1. **Initialization**: `BlockImporter.__init__()` initializes difficulty state from `ChainParams`
2. **Genesis**: Genesis block timestamp sets the baseline for interval tracking
3. **Block Import**: Each accepted block triggers `_update_difficulty(timestamp)`
4. **Query**: `get_current_difficulty()` returns the current Θ value

### Code Flow

```python
# On BlockImporter creation
importer = BlockImporter(params=params, block_db=block_db)
# → Initializes difficulty_state from consensus.difficulty

# On block import
result = importer.import_block(block)
# → Extracts timestamp from block header
# → Calls _update_difficulty(timestamp)
#   → Computes dt = timestamp - last_block_time
#   → Calls consensus.difficulty.update_theta(state, dt_seconds=dt)
#   → Updates difficulty_state with new Θ

# Query current difficulty
theta = importer.get_current_difficulty()
# → Returns difficulty_state.theta_micro
```

### Graceful Degradation

The implementation handles missing dependencies gracefully:
- If `consensus.difficulty` module is unavailable, difficulty tracking is disabled
- The node continues to function, returning `theta_initial` as a constant
- Warnings are logged but operations continue

## Monitoring

### Metrics to Track

1. **Current Difficulty** (`Θ`): The current acceptance threshold in µ-nats
2. **Block Time** (`dt`): Actual time between consecutive blocks
3. **EMA Log Ratio** (`r̂`): Smoothed deviation from target (positive = slow, negative = fast)
4. **Difficulty Change Rate**: How quickly Θ is adjusting

### Expected Behavior

- **Stable Network**: Difficulty converges toward equilibrium; small oscillations around target
- **Hash Rate Increase**: Difficulty increases gradually to compensate
- **Hash Rate Decrease**: Difficulty decreases gradually to maintain block production
- **Transient Spikes**: EMA smoothing prevents over-reaction to temporary variance

### Diagnostic Queries

```python
# Get current difficulty
theta = importer.get_current_difficulty()
print(f"Current difficulty: {theta} µ-nats ({theta/1e6:.2f} nats)")

# Get difficulty state details
if importer.difficulty_state:
    state = importer.difficulty_state
    print(f"Theta: {state.theta_micro} µ-nats")
    print(f"Tau: {state.tau_nats:.6f} nats")
    print(f"EMA: {state.ema_log_dt_over_T:+.4f}")
    print(f"Alpha: {state.alpha:.4f}")
```

## Testing

### Unit Tests

Comprehensive unit tests are provided in `core/chain/tests/test_difficulty_integration.py`:

1. **Initialization**: Verifies difficulty state is properly initialized
2. **Fast Blocks**: Confirms difficulty increases when blocks arrive quickly
3. **Slow Blocks**: Confirms difficulty decreases when blocks arrive slowly
4. **Bounds**: Ensures difficulty stays within configured limits
5. **Convergence**: Validates that difficulty stabilizes at target interval
6. **Degradation**: Tests graceful handling when difficulty module is unavailable

Run tests:
```bash
pytest core/chain/tests/test_difficulty_integration.py -v
```

### Integration Testing

For integration tests simulating realistic network conditions:

1. Start with genesis difficulty
2. Simulate varying hash rates (miners joining/leaving)
3. Monitor difficulty adjustment over multiple adjustment periods
4. Verify block times converge toward target
5. Check that difficulty responds appropriately to sustained changes

## Configuration Guide

### Adjusting Responsiveness

To make difficulty adjust **faster**:
- Increase `ema_alpha` (more weight on recent observations)
- Decrease `window` (shorter half-life)
- Increase `bounds.max` / decrease `bounds.min` (larger per-step changes)

To make difficulty adjust **slower** (more stable):
- Decrease `ema_alpha` (more smoothing)
- Increase `window` (longer half-life)
- Decrease `bounds.max` / increase `bounds.min` (smaller per-step changes)

### Target Block Time

Adjust `block.target_seconds` to change the desired block interval:
- Faster blocks (e.g., 2s): Lower target
- Slower blocks (e.g., 30s): Higher target

Note: Changing target block time affects finality, network propagation requirements, and state growth rate.

### Initial Difficulty

Set `consensus.theta_initial` based on expected genesis hash rate:
- Higher theta_initial: More hash power needed at genesis
- Lower theta_initial: Less hash power needed at genesis

Typical range: 1,000,000 to 10,000,000 µ-nats (1 to 10 nats)

## Troubleshooting

### Difficulty Not Adjusting

**Symptoms**: Θ remains constant despite varying block times

**Possible Causes**:
1. `consensus.difficulty` module failed to import
2. No timestamps in block headers
3. Genesis block not properly initialized

**Diagnosis**:
```python
# Check if difficulty state is initialized
if importer.difficulty_state is None:
    print("Difficulty adjustment not active")

# Check last block time
if importer._last_block_time is None:
    print("No baseline timestamp set")
```

### Difficulty Oscillating

**Symptoms**: Θ swings wildly between high and low values

**Possible Causes**:
1. EMA alpha too high (over-responsive)
2. Bounds too wide (allowing large swings)
3. Inconsistent block times (mining centralization or network issues)

**Solutions**:
- Reduce `ema_alpha` for more smoothing
- Tighten `bounds` to limit per-step changes
- Increase `window` for longer smoothing period

### Difficulty Stuck at Bounds

**Symptoms**: Θ consistently at `theta_min_micro` or `theta_max_micro`

**Possible Causes**:
1. Hash rate far from expected (too high or too low)
2. Bounds set incorrectly
3. Genesis difficulty poorly calibrated

**Solutions**:
- Wait for equilibrium (may take multiple adjustment periods)
- Adjust bounds in params.yaml (requires governance/upgrade)
- Consider network state (are miners active?)

## Security Considerations

### Attack Vectors

1. **Timestamp Manipulation**: Miners could lie about timestamps to manipulate difficulty
   - **Mitigation**: Consensus rules should bound timestamp deviation from network time
   
2. **Hash Rate Attacks**: Sudden hash rate changes can temporarily affect block times
   - **Mitigation**: EMA smoothing prevents single-block manipulation
   - **Mitigation**: Bounded per-step adjustments prevent extreme swings

3. **Selfish Mining**: Withholding blocks can affect difficulty calculation
   - **Mitigation**: PoIES multi-factor consensus reduces pure hash power dominance

### Best Practices

1. **Monitor difficulty trends**: Unusual patterns may indicate attacks
2. **Set appropriate bounds**: Balance responsiveness with stability
3. **Test parameter changes**: Simulate effects before mainnet deployment
4. **Coordinate with PoIES**: Difficulty adjustment interacts with proof selection

## References

- `consensus/difficulty.py`: Core difficulty adjustment algorithm
- `core/chain/block_import.py`: Integration into block import
- `spec/DIFFICULTY_RETARGET.md`: Formal specification
- `docs/spec/poies/RETARGET.md`: PoIES-specific retargeting details
- `consensus/tests/test_difficulty_retarget.py`: Algorithm-level tests
- `core/chain/tests/test_difficulty_integration.py`: Integration tests

## Changelog

### v1.0 (Initial Implementation)
- Integrated difficulty adjustment into BlockImporter
- EMA-based retargeting algorithm
- Configurable parameters via ChainParams
- Comprehensive test coverage
- Graceful degradation when consensus module unavailable
