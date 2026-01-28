# Force Block When Previous Block is Older Than 1 Hour

## Summary

This patch adds logic to force the creation of a new block when the previous block is older than 1 hour (3600 seconds), ensuring the chain progresses even when no miners are active for extended periods.

## Implementation

### Changes Made

1. **rpc/methods/miner.py** (`_mine_once` function):
   - Added check for `force_block_due_to_time` when parent block timestamp exceeds `max_block_time_s`
   - When triggered, sets mining difficulty (theta) to minimum (100,000 µ-nats ≈ 0.1 nats)
   - Logs warning message for visibility: "Previous block is Xs old (exceeds max_block_time_s=3600s). Forcing new block with minimum difficulty to ensure chain progress."

### Configuration

Uses the existing `max_block_time_s` parameter from `spec/params.yaml`:

```yaml
issuance:
  max_block_time_s: 3600  # 3600 seconds = 1 hour
```

Environment variable override: `ANIMICA_MAX_BLOCK_TIME_S`

### Backwards Compatibility

✅ **Fully backwards compatible**:
- Default behavior unchanged (max_block_time_s is already set to 3600 in config)
- Can be disabled by setting `ANIMICA_MAX_BLOCK_TIME_S=0` or negative value
- Existing `max_block_time_s` parameter in consensus/difficulty.py already reduces difficulty on timeout
- This change adds **explicit forcing** with minimum theta to complement the existing mechanism

## Behavior

### Normal Operation
- Previous block: < 1 hour old → Normal mining with dynamic theta adjustment
- Difficulty adjusts based on network hash rate and block times

### Forced Block Mode
- Previous block: > 1 hour old → Force block with minimum theta (100K µ-nats)
- Ensures block can be mined quickly to unblock the chain
- Allows network to recover from extended periods without blocks

### Example Scenario

```
Time     Action                           Theta
------   -------------------------------- --------
T=0      Block N mined                    3.0 nats (normal)
T=3700s  _mine_once() called              
         Previous block is 3700s old
         Forcing: theta → 0.1 nats (min)
         Block N+1 mined quickly
T=3710s  Block N+1 complete               
         Network recovers, theta increases
```

## Testing

### Unit Tests
- `test_force_block_1_hour.py`:
  - ✅ Forces block when previous > 1 hour old
  - ✅ Does not force when previous < 1 hour old  
  - ✅ Can be disabled (backwards compatibility)

### Integration with Existing Tests
- Existing `consensus/tests/test_max_block_time.py` validates difficulty reduction
- This change complements that by adding explicit forcing in the mining loop

## Security Considerations

1. **No timestamp manipulation**: Uses real system time and parent block timestamp from chain
2. **Minimum theta is safe**: 100K µ-nats is the standard minimum for mining operations
3. **Deterministic**: All nodes see same parent timestamps, so forcing behavior is consistent
4. **Logged**: Warning messages provide visibility into when forcing occurs
5. **Backwards compatible**: Can be disabled without code changes

## Related Files

- `rpc/methods/miner.py` - Mining and block creation logic
- `consensus/difficulty.py` - Difficulty adjustment and max_block_time_s parameter
- `spec/params.yaml` - Network configuration (max_block_time_s: 3600)
- `test_force_block_1_hour.py` - Unit tests for forcing logic

## References

- Existing max_block_time_s documentation: `consensus/tests/test_max_block_time.py`
- Emergency difficulty reduction: `consensus/difficulty.py` lines 249-265
- Mining loop: `rpc/methods/miner.py` `_auto_mine_loop()` function
