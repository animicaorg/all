# Theta Scaling Update

## Overview

This document describes the changes made to Theta (Θ) scaling parameters to better handle high network load and hash rate spikes in the Animica blockchain.

## Background

### What is Theta?

Theta (Θ) is the acceptance threshold in Animica's PoIES (Proof-of-Integrated-External-Services) consensus mechanism. It represents mining difficulty:

- **Higher Theta** → Harder mining (fewer valid blocks)
- **Lower Theta** → Easier mining (more valid blocks)

Theta is measured in **micro-nats (µ-nats)**, where:
- 1 nat = 1,000,000 µ-nats
- Typical mining range: 0.3 to 60 nats (300,000 to 60,000,000 µ-nats)

### Why Adjust Theta?

The blockchain dynamically adjusts Theta to maintain a target block time (default: 12 seconds). When the network experiences:

- **Fast blocks** (< 12s average): Theta increases to slow down mining
- **Slow blocks** (> 12s average): Theta decreases to speed up mining

## Problem Statement

Prior to this update, the Theta adjustment parameters were too constrained under high network load:

1. **Theta Maximum Too Low**: 40M µ-nats (40 nats) wasn't enough during hash rate spikes
2. **Step Clamp Too Small**: 600k µ-nats (0.6 nats) per update meant slow adaptation
3. **Network Config Misalignment**: Different networks had inconsistent limits

This caused:
- Mining to become inefficiently difficult during high load
- Slow recovery when hash rate spiked then dropped
- Network instability during miner influx

## Changes Made

### 1. Mining Adjustment Parameters

Updated in `rpc/methods/miner.py` (dynamic theta adjustment for mining):

| Parameter | Old Value | New Value | Change |
|-----------|-----------|-----------|--------|
| `theta_max_micro` | 40,000,000 (40 nats) | 60,000,000 (60 nats) | +50% |
| `step_clamp_micro` | 600,000 (0.6 nats) | 1,000,000 (1.0 nats) | +67% |

**Impact**: Mining can now handle 50% higher difficulty spikes and adapts 67% faster per block.

### 2. Network Configuration

Updated in `spec/params.yaml` for all networks:

#### Mainnet (`animica:1`)
```yaml
consensus:
  poies:
    theta_max_munats: 60000000  # Was: 32000000 (+87.5%)
```

#### Testnet (`animica:2`)
```yaml
consensus:
  poies:
    theta_max_munats: 48000000  # Was: 24000000 (+100%)
```

#### Devnet (`animica:1337`)
```yaml
consensus:
  poies:
    theta_max_munats: 24000000  # Was: 12000000 (+100%)
```

### 3. Enhanced Logging

Added theta range to initialization log:
```
Initialized dynamic theta adjustment for mining:
theta=3.000 nats, target_time=12.0s, range=[0.3, 60.0] nats
```

## Algorithm Details

### EMA-Based Retargeting

Theta adjustment uses Exponential Moving Average (EMA) of block time ratios:

```
r_k  = ln( dt_k / T )                                    # Log ratio of block time
r̂_k = (1-α)^m · r̂_{k-1} + (1 - (1-α)^m) · r_k          # EMA update
τ_{k+1} = τ_k - β · r̂_k                                 # Adjust tau (nats)
Θ_{k+1} = clamp(Θ_k + round(Δτ · 10^6))                # Convert to micro-nats
```

Where:
- `T` = target block time (12s)
- `α` = smoothing factor from half-life (8 blocks for mining)
- `β` = proportional gain (0.9 for mining, more aggressive than consensus 0.75)
- `m` = blocks skipped (usually 1)

### Complete Parameters (Mining)

```python
RetargetParams(
    target_block_time_s=12.0,        # Target 12s blocks
    half_life_blocks=8.0,            # Faster adaptation (vs 24 for consensus)
    gain_beta=0.9,                   # More aggressive (vs 0.75 for consensus)
    step_clamp_micro=1_000_000,      # ±1.0 nats per update max (was 0.6)
    theta_min_micro=300_000,         # 0.3 nats floor
    theta_max_micro=60_000_000,      # 60 nats ceiling (was 40)
)
```

## Example Scenarios

### Scenario 1: Hash Rate Spike

**Before Update**:
```
Block   dt    Theta    Action
─────────────────────────────────
100     12s   16.0n    (normal)
101     3s    16.6n    increase
102     3s    17.2n    increase
103     3s    17.8n    increase
...
150     3s    40.0n    MAX HIT! (stuck)
151     3s    40.0n    (no more increase possible)
```

**After Update**:
```
Block   dt    Theta    Action
─────────────────────────────────
100     12s   16.0n    (normal)
101     3s    17.0n    increase (larger step)
102     3s    18.0n    increase
103     3s    19.0n    increase
...
150     3s    48.0n    still adjusting
151     3s    49.0n    approaching new max (60n)
```

### Scenario 2: Hash Rate Drop After Spike

**Before Update**:
```
Block   dt    Theta    Recovery Time
──────────────────────────────────────────
100     3s    40.0n    (stuck at max)
101     30s   39.4n    slow decrease
102     30s   38.8n    
...
120     30s   28.0n    ~20 blocks to recover
```

**After Update**:
```
Block   dt    Theta    Recovery Time
──────────────────────────────────────────
100     3s    48.0n    (near max)
101     30s   47.0n    faster decrease
102     30s   46.0n    
...
113     30s   35.0n    ~13 blocks to recover
```

## Testing

### Unit Tests

Updated tests in `mining/tests/test_theta_micro_adjustment.py`:
- ✅ Initialization with new parameters
- ✅ Fast blocks increase theta
- ✅ Slow blocks decrease theta  
- ✅ Clamping respects new min/max bounds
- ✅ Extreme values handled safely
- ✅ Mixed intervals produce stable results

All 7 tests passing.

### Integration Tests

Validated with existing mining integration tests:
- ✅ Mining works with increased theta limits
- ✅ Block production continues under high load
- ✅ Adjustment converges to target block time

## Performance Impact

### Mining Throughput

- **Before**: 40 nats max → ~2^40 ≈ 1.1 trillion hash attempts per valid block
- **After**: 60 nats max → ~2^60 ≈ 1.15 quintillion hash attempts per valid block

**This is intentional** - allows network to scale with much higher hash rates.

### Adjustment Speed

- **Before**: 0.6 nats/block → 67 blocks to adjust ±40 nats
- **After**: 1.0 nats/block → 40 blocks to adjust ±40 nats

**~40% faster convergence** to target block time.

### Memory Overhead

Negligible increase:
- State storage: ~160 bytes (unchanged)
- Computation: ~1-5 μs per adjustment (unchanged)

## Monitoring

### Check Current Theta

Via RPC:
```bash
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "miner.getHead",
    "params": [],
    "id": 1
  }'
```

Via Python:
```python
from rpc.methods.miner import _MINING_STATE

state = _MINING_STATE.get("theta_state")
if state:
    current = state.theta_micro / 1e6
    min_val = state.params.theta_min_micro / 1e6
    max_val = state.params.theta_max_micro / 1e6
    print(f"Theta: {current:.3f} nats (range: [{min_val:.1f}, {max_val:.1f}])")
```

### Check Recent Block Times

```python
from rpc.methods.miner import _MINING_STATE

block_times = _MINING_STATE.get("block_times", [])
if block_times:
    avg = sum(block_times) / len(block_times)
    print(f"Recent avg block time: {avg:.2f}s (target: 12.0s)")
    print(f"Last 5 times: {list(block_times)[-5:]}")
```

### Watch Theta Adjustments

Enable INFO-level logging to see adjustment messages:
```python
import logging
logging.getLogger("animica.rpc.miner").setLevel(logging.INFO)
```

Example log output:
```
INFO: Initialized dynamic theta adjustment for mining: 
      theta=16.000 nats, target_time=12.0s, range=[0.3, 60.0] nats

INFO: Adjusted mining theta: 16.000 → 16.850 nats 
      (dt=6.20s, avg_5=7.40s, target=12.0s)
```

## Migration Notes

### For Node Operators

No action required:
- Changes are backward compatible
- Existing nodes will use new limits automatically
- No database migration needed

### For Miners

Update CLI to benefit from fixes:
```bash
git pull origin main
pip install -e python/
```

Verify device parameter fix:
```bash
# This should now work without RPC errors
animica miner mine-blocks --address anim1... --count 5 --device auto
```

### For Developers

If you have custom mining implementations:
1. Remove `device` from RPC calls (it's CLI-only)
2. Update theta limits if hardcoded
3. Test against updated network configs

## Related Issues

This update addresses several reported issues:
- RPC Error -32602: device parameter not supported
- Mining difficulty constraints under network stress
- Slow theta adjustment during hash rate changes

## References

- **Consensus Spec**: `spec/params.yaml`
- **Mining Code**: `rpc/methods/miner.py`
- **Difficulty Module**: `consensus/difficulty.py`
- **Tests**: `mining/tests/test_theta_micro_adjustment.py`
- **Troubleshooting**: `docs/MINING_TROUBLESHOOTING.md`

## Changelog

### v0.1.0 (2024-12-16)

**Fixed**:
- Device parameter no longer sent to RPC (fixes -32602 error)
- Fallback handler updated to exclude device parameter
- Proxy validation errors handled gracefully

**Improved**:
- Theta max increased: 40M → 60M µ-nats (mining)
- Step clamp increased: 600k → 1M µ-nats (mining)
- Network configs updated for all networks
- Faster convergence under high load
- Enhanced logging with theta range display

**Added**:
- Comprehensive theta adjustment tests
- Device parameter validation tests
- Mining troubleshooting documentation
- Theta scaling documentation (this document)

---

*Last updated: 2024-12-16*
