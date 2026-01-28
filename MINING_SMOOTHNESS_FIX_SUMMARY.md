# Mining Smoothness and Performance Improvements

## Problem Summary

You reported two main issues with the Animica blockchain mining:

1. **"Turn-based" mining behavior**: Nodes felt like they were taking turns mining instead of continuous smooth operation. One node would mine for a while, then stop, then another would start.

2. **Thread count not making a difference**: Setting `ANIMICA_MINER_THREADS=20000` vs `ANIMICA_MINER_THREADS=1` didn't seem to make any performance difference.

## Root Causes Identified

### Issue 1: Block Found Cooldown (60 seconds)
When any node successfully mines a block, it triggers a 60-second cooldown period where it **stops fetching new work templates**. This causes:
- The mining node pauses for 60 seconds after finding a block
- Other nodes then have a chance to mine
- Creates a "turn-based" feeling where nodes alternate
- Not ideal for production with multiple mining nodes

**Location**: `mining/cooldown.py` - `BlockFoundCooldown` class

### Issue 2: Inefficient Thread Utilization
The system was actually working correctly, but not optimally:
- Thread count **was** correctly capped at your physical CPU count (e.g., 8 cores)
  - This is correct behavior - 20000 threads on an 8-core CPU would be wasteful
- However, the batch size (50k iterations) was **not scaled** with thread count
  - With 8 threads and 50k iterations, each thread only gets ~6.25k iterations
  - Too small, leading to excessive context switching overhead
- Logs didn't explain why 20000 threads → 8 threads, causing confusion

**Location**: `mining/hash_search.py` - `scan_forever()` function, `mining/cpu_backend.py`

## Solutions Implemented (Backwards Compatible)

### 1. Configurable Block Cooldown

**Environment Variable**: `ANIMICA_MINING_BLOCK_COOLDOWN_SEC`

- **Default**: `60` (backwards compatible - existing behavior preserved)
- **Recommended for production**: `0` (disables cooldown for smooth continuous mining)
- **Custom**: Any value in seconds (e.g., `10` for light throttling)

**Usage**:
```bash
# Disable cooldown for smooth continuous mining (recommended)
export ANIMICA_MINING_BLOCK_COOLDOWN_SEC=0
python -m mining.cli.miner start

# Or use a short cooldown
export ANIMICA_MINING_BLOCK_COOLDOWN_SEC=10
python -m mining.cli.miner start
```

**How it works**:
- When set to `0`, nodes continue mining immediately after finding a block
- No pause in template fetching
- All nodes can mine continuously and smoothly
- Block production is now truly continuous, not "turn-based"

### 2. Automatic Batch Size Scaling

**Improvement**: Batch size now automatically scales based on thread count

- **Minimum**: 10,000 iterations per thread
- **Formula**: `max(batch_size, threads × 10,000)`
- **Examples**:
  - 1 thread: 50k batch (no change)
  - 8 threads: 80k batch (10k per thread)
  - 16 threads: 160k batch (10k per thread)
  - 100 threads (capped at CPU count): scales appropriately

**Benefits**:
- Reduces context switching overhead
- Each thread gets meaningful work to do
- Better CPU utilization
- More responsive to actual thread count

### 3. Improved Logging and Documentation

**New logging** in `mining/cpu_backend.py`:
```
CPU scan: requested 20000 threads, but capping at 8 (CPU count) to prevent oversubscription
CPU scan using multi-threaded mode with 8 threads (configured=20000, iterations=80000)
```

**Updated documentation** in `mining/README.md`:
- Explains thread capping behavior
- Documents new environment variables
- Provides usage examples
- Clarifies performance expectations

## Testing

All changes include comprehensive tests:

### Cooldown Tests (`mining/tests/test_block_found_cooldown.py`)
- ✅ Cooldown disabled when set to 0
- ✅ Continuous mining without pauses
- ✅ Backwards compatibility (default 60s behavior)
- **Result**: 4/4 tests passing

### Batch Size Scaling Tests (`mining/tests/test_batch_size_scaling.py`)
- ✅ Batch size scales with thread count
- ✅ Minimum iterations per thread respected
- ✅ Thread capping logic correct
- **Result**: 2/2 tests passing

## Quick Start Guide

### For Smooth Continuous Mining (Recommended)

```bash
# 1. Disable block found cooldown
export ANIMICA_MINING_BLOCK_COOLDOWN_SEC=0

# 2. Use auto-detected thread count (recommended)
export ANIMICA_MINER_THREADS=0

# 3. Start mining
python -m mining.cli.miner start --rpc-url http://127.0.0.1:8545
```

### For Multiple Nodes

On each node:
```bash
# Disable cooldown so all nodes can mine continuously
export ANIMICA_MINING_BLOCK_COOLDOWN_SEC=0
export ANIMICA_MINER_THREADS=0  # Auto-detect

# Start each node
python -m mining.cli.miner start --rpc-url http://127.0.0.1:8545
```

### Understanding Thread Behavior

```bash
# Don't do this - excessive threads provide no benefit
export ANIMICA_MINER_THREADS=20000  # Will be capped at CPU count anyway

# Do this instead - let the system decide
export ANIMICA_MINER_THREADS=0  # Auto-detects optimal thread count

# Or set to your actual CPU core count
export ANIMICA_MINER_THREADS=8  # For an 8-core CPU
```

## Expected Results

After applying these changes:

1. **Smooth Mining**:
   - No more "turn-based" behavior
   - Nodes mine continuously without pausing
   - Block production is smooth and distributed

2. **Better Thread Utilization**:
   - Batch sizes scale automatically with thread count
   - Less context switching overhead
   - More efficient CPU usage
   - Clear logging explains thread behavior

3. **Backwards Compatibility**:
   - Default behavior unchanged (60s cooldown)
   - Existing configurations continue to work
   - Opt-in via environment variables

## Files Changed

- `mining/cooldown.py` - Configurable cooldown implementation
- `mining/orchestrator.py` - Documentation updates
- `mining/hash_search.py` - Batch size auto-scaling
- `mining/cpu_backend.py` - Improved thread capping logging
- `mining/README.md` - Comprehensive documentation updates
- `mining/tests/test_block_found_cooldown.py` - Cooldown tests
- `mining/tests/test_batch_size_scaling.py` - Batch size tests (new)

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `ANIMICA_MINING_BLOCK_COOLDOWN_SEC` | `60` | Seconds to pause mining after finding a block. Set to `0` for continuous mining. |
| `ANIMICA_MINER_THREADS` | `0` (auto) | Number of mining threads. `0` = auto-detect CPU count. Capped at physical CPU count. |
| `ANIMICA_MINER_DEVICE` | `cpu` | Mining device: `cpu`, `cuda`, `rocm`, `opencl`, `metal`, or `auto` |

## Migration Guide

If you're currently running miners:

1. **No changes required** - everything works as before by default
2. **To enable smooth mining** - add `ANIMICA_MINING_BLOCK_COOLDOWN_SEC=0` to your environment
3. **To optimize threads** - use `ANIMICA_MINER_THREADS=0` (auto-detect) or set to your CPU core count

No restart of the blockchain required - just restart your miner processes with the new environment variables.
