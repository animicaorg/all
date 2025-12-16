# Dynamic Theta Micro Adjustment for Mining

## Overview

The dynamic theta micro adjustment feature adapts the mining difficulty threshold (Θ) based on observed network conditions during mining operations. This ensures efficient block production even under varying hash rates and network stress.

## Implementation

### Core Components

1. **Theta Adjustment State** (`_MINING_STATE` in `rpc/methods/miner.py`):
   - Tracks timing of recent blocks
   - Maintains EMA-based retargeting state
   - Stores configuration and adjustment history

2. **Adjustment Function** (`_adjust_theta_for_mining`):
   - Uses `consensus.difficulty` module for EMA-based retargeting
   - Applies faster response parameters optimized for mining (vs consensus)
   - Respects protocol constraints (min/max bounds, step clamps)

3. **Integration** (in `_mine_once` function):
   - Tracks block times automatically
   - Applies adjusted theta to each new block header
   - Updates state after successful mining

### Algorithm

The adjustment uses the same EMA-based retargeting as consensus validation, but with mining-optimized parameters:

```python
# Retarget Parameters (Mining-Optimized)
target_block_time_s: 12.0      # Target 12s blocks
half_life_blocks: 8.0          # Faster adaptation (vs 24 for consensus)
gain_beta: 0.9                 # More aggressive response (vs 0.75)
step_clamp_micro: 600_000      # Larger steps (~0.6 nats per update)
theta_min_micro: 300_000       # Lower minimum (~0.3 nats)
theta_max_micro: 40_000_000    # Higher maximum (~40 nats)
```

The update formula:
```
r_k = ln(dt_k / T)                                    # Log ratio of observed to target time
r̂_k = (1-α)^m · r̂_{k-1} + (1 - (1-α)^m) · r_k        # EMA update with skip-awareness
τ_{k+1} = τ_k - β · r̂_k                               # Proportional control
Θ_{k+1} = clamp_global(clamp_step(Θ_k + Δ))          # Apply clamps
```

Where:
- `α` is derived from half-life: `α = 1 - 2^(-1/H)`
- `β` is the proportional gain
- `Δ = round((τ_{k+1} - τ_k) · 10^6)` (convert to micro-nats)

### Behavior

**Fast Blocks (dt < target):**
- Θ increases (mining becomes harder)
- Prevents excessive block production
- Smoothed by EMA to avoid oscillation

**Slow Blocks (dt > target):**
- Θ decreases (mining becomes easier)
- Ensures timely block production
- Bounded by minimum threshold

**Extreme Conditions:**
- Invalid intervals (≤0, inf, NaN) are rejected
- Severe fluctuations are handled via step clamps
- Min/max bounds prevent unreasonable values

### Edge Cases

1. **First Block**: Initializes state with baseline theta from consensus
2. **Invalid dt**: Logs warning and returns current theta unchanged
3. **Initialization Failure**: Disables adjustment and falls back to consensus theta
4. **Adjustment Error**: Disables adjustment to prevent cascading failures

## Configuration

### Enabling/Disabling

Adjustment is enabled by default. To disable:

```python
from rpc.methods.miner import _MINING_STATE
_MINING_STATE["adjustment_enabled"] = False
```

### Monitoring

Block times are tracked in `_MINING_STATE["block_times"]` (last 20):
```python
from rpc.methods.miner import _MINING_STATE
block_times = _MINING_STATE.get("block_times", [])
avg_time = sum(block_times[-5:]) / 5  # Last 5 blocks average
```

### Logging

Significant theta changes (>0.01 nats) are logged:
```
INFO: Adjusted mining theta: 3.000 → 3.250 nats (dt=8.50s, avg_5=9.20s, target=12.0s)
```

## Testing

Comprehensive test coverage in `mining/tests/test_theta_micro_adjustment.py`:

- **Initialization**: State setup and baseline theta
- **Fast Blocks**: Theta increases appropriately
- **Slow Blocks**: Theta decreases appropriately
- **Extreme Values**: Handles edge cases safely
- **Clamping**: Respects min/max bounds
- **Disabled**: Works without adjustment
- **Mixed Intervals**: Realistic variation handling

## Performance Impact

- **Overhead**: Negligible (~μs per adjustment)
- **Memory**: ~160 bytes for state tracking
- **Disk**: No persistence required (resets per session)
- **Network**: No additional RPC calls

## Security Considerations

1. **DoS Protection**: Invalid dt values are rejected
2. **Bounds Enforcement**: Min/max clamps prevent unreasonable theta
3. **Graceful Degradation**: Falls back to consensus theta on error
4. **No Chain Impact**: Mining-local adjustment doesn't affect consensus

## Future Enhancements

1. **Persistence**: Save adjustment state across restarts
2. **Adaptive Parameters**: Auto-tune half-life and gain based on volatility
3. **Multi-Chain**: Per-chain adjustment state for multi-chain miners
4. **Metrics**: Expose theta adjustment metrics via Prometheus

## References

- `consensus/difficulty.py`: Core retargeting algorithm
- `spec/poies_math.md`: Theta and difficulty mathematics
- `mining/README.md`: Mining architecture overview
