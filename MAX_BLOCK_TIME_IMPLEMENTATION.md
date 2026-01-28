# Max Block Time Emergency Difficulty Reduction

## Overview

This feature implements a backwards-compatible safety mechanism that prevents the blockchain from stalling when no blocks are found for extended periods. When the time between blocks exceeds a configurable maximum (default: 1 hour), the difficulty automatically drops to minimum, enabling miners to quickly produce blocks and restore the network.

## Motivation

In blockchain networks, if the hash rate suddenly drops significantly (e.g., miners go offline, network partitions occur), it may become extremely difficult to find the next block. Without intervention, this could result in:

- Hours or days without new blocks
- Transactions stuck in mempool
- Network effectively halted

The max block time feature provides an automatic recovery mechanism that activates when blocks take too long.

## Configuration

The feature is configured in `spec/params.yaml` under each network's `monetary.issuance` section:

```yaml
networks:
  "animica:1":
    monetary:
      issuance:
        target_block_interval_ms: 300000  # 5 minute target
        max_block_time_s: 3600            # 1 hour maximum (emergency threshold)
```

### Parameters

- **`max_block_time_s`**: Maximum time (in seconds) allowed between blocks before emergency difficulty reduction activates. 
  - Default: `3600` (1 hour)
  - Set to `null` or omit to disable the feature (backwards compatible)
  - Recommended: 3600-7200 seconds (1-2 hours) for production networks

## Behavior

### Normal Operation

When blocks arrive within the maximum time threshold, the standard EMA-based difficulty adjustment applies:
- Blocks faster than target → difficulty increases
- Blocks slower than target → difficulty decreases (gradual)
- All changes are clamped by `step_clamp_micro` to prevent wild swings

### Emergency Mode

When a block takes longer than `max_block_time_s`:

1. **Immediate Difficulty Reduction**: Difficulty (Θ) drops to the configured minimum (`theta_min_micro`)
2. **EMA Reset**: The EMA is set to reflect the very slow block time
3. **Fast Recovery**: Miners can now find blocks quickly due to minimal difficulty
4. **Gradual Return**: As fast blocks arrive, difficulty gradually increases back toward target using normal retargeting

### Example Scenario

```
Network parameters:
- target_block_time_s: 300 (5 minutes)
- max_block_time_s: 3600 (1 hour)
- theta_min_micro: 500_000 (0.5 nats - very easy)

Timeline:
T=0:     Block N at height 100, theta = 3.0 nats (normal)
T=4000s: Block N+1 arrives (66 minutes later - exceeds 1 hour max!)
         → Emergency mode activated
         → Theta = 0.5 nats (minimum)
         
T=4100s: Block N+2 arrives (100s later - fast due to low difficulty)
T=4200s: Block N+3 arrives (100s later)
T=4300s: Block N+4 arrives (100s later)
         → After several fast blocks, theta gradually increases
         
T=5500s: Block N+10, theta = 1.2 nats (recovering toward normal)
```

## Implementation Details

### Consensus Module Changes

**File**: `consensus/difficulty.py`

- Added `max_block_time_s` parameter to `RetargetParams` dataclass (optional, defaults to `None`)
- Modified `update_theta()` function to check for emergency condition before normal retargeting
- When emergency triggered:
  - Logs warning with details
  - Returns state with `theta_micro = theta_min_micro`
  - Sets EMA to large positive value to reflect slow blocks

### Block Import Changes

**File**: `core/chain/block_import.py`

- Modified `_build_retarget_params()` to read `max_block_time_s` from network configuration
- Passes parameter to `RetargetParams` constructor
- Falls back to defaults if not specified in config

### Configuration Changes

**File**: `spec/params.yaml`

- Added `max_block_time_s: 3600` to all three network profiles (mainnet, testnet, devnet)
- Added to global defaults section for consistency

## Testing

### Unit Tests

**File**: `consensus/tests/test_max_block_time.py`

Comprehensive test suite covering:
- Normal operation (no emergency trigger)
- Emergency mode activation when threshold exceeded
- Recovery after emergency
- Edge cases (exactly at max, multiple emergencies)
- Backwards compatibility (feature disabled when `max_block_time_s = None`)

All tests pass:
```bash
$ pytest consensus/tests/test_max_block_time.py -v
7 passed in 0.18s
```

### Integration Tests

Existing difficulty retarget tests continue to pass, demonstrating backwards compatibility:
```bash
$ pytest consensus/tests/test_difficulty_retarget.py -v
5 passed in 0.17s
```

## Backwards Compatibility

The feature is **fully backwards compatible**:

1. **Optional Parameter**: `max_block_time_s` is optional in `RetargetParams`. If not specified or set to `None`, emergency mode never activates.

2. **Existing Code**: All existing code that uses `RetargetParams` continues to work without modification. The parameter has a default value of `None`.

3. **Configuration**: Networks can opt-in by adding the parameter to their config. If omitted, behavior is unchanged.

4. **Tests**: All existing difficulty adjustment tests pass without modification.

## Security Considerations

### Potential Attack Vectors

1. **Gaming the Emergency**: Miners cannot profit from triggering emergency mode, as:
   - It only activates after a genuinely long wait (1+ hours)
   - Difficulty drops to minimum, so the first miner to find a block wins
   - Network quickly recovers, so opportunity is limited

2. **Chain Split Risk**: Emergency mode is deterministic based on timestamps:
   - All nodes see the same timestamps
   - All nodes apply the same emergency threshold
   - No consensus risk introduced

3. **Timestamp Manipulation**: 
   - Existing timestamp validation prevents future timestamps
   - Emergency only triggers for genuinely slow blocks
   - Cannot be used to artificially lower difficulty

### Safety Properties

1. **Deterministic**: Same inputs always produce same difficulty adjustments
2. **Bounded**: Difficulty can only drop to minimum, not below
3. **Gradual Recovery**: After emergency, normal retargeting ensures gradual return to equilibrium
4. **Logging**: Emergency activations are logged for monitoring and analysis

## Operational Notes

### Monitoring

Look for log messages indicating emergency activation:
```
WARNING: Block time 4000s exceeds maximum 3600s. 
         Emergency difficulty reduction activated: theta = 0.500 nats
```

### Tuning

If emergency mode activates frequently:
- **Increase** `max_block_time_s` to allow more time before emergency
- **Review** network hash rate and connectivity

If emergency mode never activates but you experience stalls:
- **Decrease** `max_block_time_s` to trigger recovery sooner
- **Check** if miners are online and connected

### Mainnet Recommendations

For production networks:
- `max_block_time_s`: 3600-7200 seconds (1-2 hours)
- Monitor emergency activations via logs/metrics
- Consider alerting if emergency mode activates

## Future Enhancements

Potential improvements for future versions:

1. **Graduated Response**: Instead of immediate drop to minimum, gradually reduce difficulty over multiple thresholds
2. **Metrics**: Expose emergency activation count via Prometheus
3. **Network Alerts**: P2P protocol message when emergency mode activates
4. **Adaptive Threshold**: Adjust `max_block_time_s` based on historical variance

## References

- `consensus/difficulty.py` - Core difficulty adjustment logic
- `spec/params.yaml` - Network configuration parameters  
- `consensus/tests/test_max_block_time.py` - Test suite
- Related: MIN_BLOCK_SPACING_IMPLEMENTATION.md - Prevents blocks from being too fast
