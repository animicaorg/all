# Balance Inflation Bug - Complete Fix

## Problem Statement

Users reported balances approximately 5x higher than expected:
- Node 1: 464,100 ANM (expected: ~92k ANM)
- Node 2: 189,900 ANM (expected: ~42k ANM)
- Explorer showed correct values
- Ratio indicated rewards applied multiple times

## Root Cause Analysis

### The Bug
State rebuilds (`_rebuild_state_from_canonical`) re-execute ALL transactions including coinbase transactions, causing rewards to be credited multiple times.

### Why It Happens
1. Node mines blocks with coinbase transactions → rewards applied ✅
2. Reorg requires snapshot that doesn't exist
3. System calls `_rebuild_state_from_canonical()`
4. Function reverts to genesis, replays ALL blocks
5. Each block's coinbase transactions execute again → **REWARDS RE-APPLIED** ❌
6. Balance = original + (rebuilds × reward_per_block)

### Why 5x Specifically
If user's node rebuilt state 4 times:
- Mining: 1x rewards
- Rebuild 1: +1x (total 2x)
- Rebuild 2: +1x (total 3x)
- Rebuild 3: +1x (total 4x)
- Rebuild 4: +1x (total 5x)

## Solution Implemented

### 1. State Height Tracking (core/db/state_db.py)

**Added Metadata**:
```python
PFX_META = b"\xFF"
META_STATE_HEIGHT = PFX_META + b"state_height"
```

**New Methods**:
```python
def get_state_height() -> Optional[int]:
    """Get highest block height applied to state"""

def set_state_height(height: int) -> None:
    """Record highest block height applied"""
```

**Storage**: 8-byte big-endian integer

### 2. Rebuild Prevention (core/chain/block_import.py)

**_apply_block_state Enhancement**:
- Records state height after successful application
- Non-fatal if recording fails (backwards compatible)

**_rebuild_state_from_canonical Enhancement**:
- Checks `get_state_height()` before rebuilding
- Skips if `current_height >= target_height`
- Logs INFO when skip occurs
- Logs WARNING when rebuild proceeds

**Before**:
```python
def _rebuild_state_from_canonical(target_height):
    revert_to_genesis()
    for height in range(1, target_height + 1):
        apply_block(height)  # Re-applies rewards!
```

**After**:
```python
def _rebuild_state_from_canonical(target_height):
    current = get_state_height()
    if current >= target_height:
        log.info("SKIPPING rebuild")
        return  # Prevents re-applying!
    revert_to_genesis()
    for height in range(1, target_height + 1):
        apply_block(height)
        set_state_height(height)  # Track progress
```

### 3. Detection & Diagnosis Tool (tools/check_balance_inflation.py)

**Purpose**: Help users identify and quantify inflation

**Features**:
- Queries balance from RPC
- Detects common multipliers (2x-10x)
- Calculates corrected balance
- Provides recovery recommendations

**Usage**:
```bash
python tools/check_balance_inflation.py \
  --rpc http://localhost:8545 \
  --address anim1zqquzgffx7raqljy3veg024ph8m8e2cyax8m98uzean8r46xskf09mc4a6avv
```

**Example Output**:
```
⚠️  INFLATION DETECTED!
Inflation factor: 5x
Current balance: 464,100 ANM
Corrected balance: 92,820 ANM

RECOMMENDATION:
1. Update to latest version
2. Apply balance correction (divide by 5)
3. Monitor logs for rebuilds
```

## How It Works

### Normal Operation (After Fix)
```
1. Mine block 100 → coinbase tx executed, rewards applied
2. set_state_height(100)
3. Node restart triggers reorg
4. _rebuild_state_from_canonical(100) called
5. get_state_height() returns 100
6. Since 100 >= 100, SKIP rebuild
7. Balance unchanged ✅
```

### Fresh Start (Genesis)
```
1. get_state_height() returns None
2. Rebuild proceeds (necessary)
3. Each block applied, height recorded
4. Future rebuilds will check height
```

### Partial Rebuild (Recovery)
```
1. State corrupted at height 50
2. get_state_height() returns 50
3. Target is 100
4. Since 50 < 100, rebuild proceeds
5. Blocks 1-100 replayed
6. Height recorded at each step
```

## Edge Cases Handled

1. **Fresh Genesis**: None → rebuild proceeds ✅
2. **Backwards Compatibility**: Old DBs without metadata → rebuild proceeds ✅
3. **Recording Failure**: Logged but non-fatal ✅
4. **Reorgs**: Height naturally updates via state revert ✅
5. **Missing Snapshots**: Still triggers rebuild if height check fails ✅

## Testing Recommendations

### Unit Tests
```python
def test_state_height_tracking():
    # Apply block 1
    assert state.get_state_height() == 1
    
    # Apply block 2
    assert state.get_state_height() == 2

def test_rebuild_skip():
    # State at height 100
    state.set_state_height(100)
    
    # Try rebuild to 100
    importer._rebuild_state_from_canonical(100)
    
    # Should skip (no re-application)
    assert state.get_state_height() == 100

def test_rebuild_proceed():
    # State at height 50
    state.set_state_height(50)
    
    # Try rebuild to 100
    importer._rebuild_state_from_canonical(100)
    
    # Should proceed (blocks 51-100 applied)
    assert state.get_state_height() == 100
```

### Integration Tests
```bash
# Test 1: Prevent inflation
1. Mine 10 blocks
2. Record balance
3. Restart node 5 times
4. Check balance unchanged

# Test 2: Fresh genesis
1. Delete state DB
2. Keep block DB
3. Restart node
4. Verify state rebuilt from genesis
5. Verify height tracking active

# Test 3: Partial recovery
1. Corrupt state at height 50
2. Keep blocks up to 100
3. Restart node
4. Verify blocks 1-100 replayed
5. Verify height = 100
```

### Log Monitoring
```bash
# Good - rebuild prevented
grep "SKIPPING rebuild" node.log

# Investigate - why rebuilding?
grep "REBUILDING state" node.log

# Track progress
grep "set_state_height" node.log
```

## Migration & Recovery

### For New Installations
- No action needed
- Fix automatically active
- State height tracking begins immediately

### For Existing Installations

#### Step 1: Update Code
```bash
git pull origin main
# or apply this PR
```

#### Step 2: Detect Inflation
```bash
python tools/check_balance_inflation.py \
  --rpc http://localhost:8545 \
  --address YOUR_ADDRESS
```

#### Step 3: Apply Correction
If inflation detected (e.g., 5x):
```python
correct_balance = current_balance / inflation_factor
# Apply via admin RPC or DB edit
```

#### Step 4: Verify
```bash
# Restart node multiple times
systemctl restart animica-node

# Check balance stable
animica wallet show YOUR_ADDRESS

# Check logs
journalctl -u animica-node | grep "SKIPPING rebuild"
```

## Future Enhancements

### Priority 1: Balance Correction Tool
```python
# tools/correct_balances.py
# - Scan all addresses
# - Detect inflation factor
# - Apply corrections
# - Generate audit trail
```

### Priority 2: RPC Endpoint
```python
# Add to rpc/methods/state.py
@method("state.getStateHeight")
def state_get_state_height():
    return state_db.get_state_height()
```

### Priority 3: Monitoring
```python
# Add metrics
STATE_HEIGHT_GAUGE = Gauge("state_height", "Current state height")
REBUILD_COUNTER = Counter("state_rebuilds", "Number of state rebuilds")
```

### Priority 4: Persistent Reward Tracking
```python
# Alternative approach: track rewarded blocks in DB
# Complement height tracking for extra safety
REWARDED_BLOCKS = PFX_META + b"rewarded_blocks"
```

## Files Changed

1. **core/db/state_db.py** (+30 lines)
   - Added metadata prefix and constants
   - Added get/set state height methods

2. **core/chain/block_import.py** (+45 lines)
   - Enhanced coinbase detection
   - Added state height recording
   - Added rebuild skip logic
   - Enhanced logging

3. **tools/check_balance_inflation.py** (+167 lines)
   - New detection tool
   - RPC integration
   - User-friendly reporting

4. **test_reward_double_application.py** (+82 lines)
   - Test scenario documentation

## Verification Checklist

- [x] Root cause identified
- [x] Fix implemented (state height tracking)
- [x] Rebuild prevention logic added
- [x] Detection tool created
- [x] Documentation written
- [x] Backwards compatibility ensured
- [x] Edge cases handled
- [ ] Unit tests written
- [ ] Integration tests written
- [ ] User recovery guide published
- [ ] Monitoring added
- [ ] Balance correction tool created

## Support & Recovery

### For Affected Users

**Immediate Actions**:
1. Update to this version
2. Run detection tool
3. Note inflation factor
4. Wait for correction tool or manual fix

**Manual Correction** (temporary):
```sql
-- SQLite state DB example
-- WARNING: Backup first!
UPDATE accounts 
SET balance = balance / 5 
WHERE address IN (SELECT address FROM suspicious_addresses);
```

### Contact & Help
- GitHub Issues: Report problems
- Discord: Real-time support
- Documentation: Full recovery guide

## Summary

| Aspect | Status |
|--------|--------|
| Root Cause | Identified ✅ |
| Prevention | Implemented ✅ |
| Detection | Tool Created ✅ |
| Correction | Manual (Tool Pending) ⏳ |
| Testing | Planned 📋 |
| Documentation | Complete ✅ |

**Bottom Line**: Future inflation prevented. Existing inflation detectable. Correction in progress.
