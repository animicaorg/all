# PR Summary: Fix Genesis Reset Loop Preventing Blockchain Sync

## Problem
Node was experiencing two critical issues:
1. **Repeatedly resetting to genesis** - Even when already at genesis (height 0)
2. **Unable to sync** - Despite having 20 connected peers, node remained stuck

### User Report
```
Blockchain is both resetting to genesis inappropriately and not syncing please fix
Last matched ancestor: Height: 0
Recent blocks: 0: 0x27fab3a1 2026-01-01 00:00:00Z txs=0
Peers: total=20 inbound=12 outbound=8
```

## Root Cause
Infinite loop in sync recovery logic (`p2p/node/p2p_service.py` line 12184):
- When at genesis (height 0) and unable to anchor headers from peers
- Node would reset to genesis after 3 failed attempts
- Resetting to genesis when already there does nothing → infinite loop

## Solution
**Minimal surgical fix**: Add check to prevent genesis reset when already at genesis

```python
# Before (buggy)
should_reset = (
    anchor_height <= self._sync_not_anchored_reset_height
    ...
)

# After (fixed)
should_reset = (
    anchor_height > 0  # Don't reset to genesis if already at genesis
    and anchor_height <= self._sync_not_anchored_reset_height
    ...
)
```

## Changes
1. **`p2p/node/p2p_service.py`** (1 line changed): Added `anchor_height > 0` condition
2. **`test_genesis_reset_loop_fix.py`** (new): Comprehensive test suite
3. **`GENESIS_RESET_LOOP_FIX.md`** (new): Detailed documentation

## Testing
✅ New test validates fix works correctly  
✅ Existing sync tests still pass  
✅ No regressions in fork resolution logic  
✅ Code review completed  
✅ Security scan passed  

## Impact
### Fixes
- ✅ Node no longer resets to genesis when already at genesis
- ✅ Node can bootstrap from genesis and sync normally
- ✅ No more infinite reset loops blocking sync

### Preserved Behavior
- ✅ Reset-to-genesis still works for heights 1-10 (useful for recovery)
- ✅ Reset-to-ancestor still works for longer forks
- ✅ All other sync recovery mechanisms unchanged

## Verification
To verify the fix on a live node:
1. Start fresh node from genesis
2. Connect to seed peers  
3. Observe node progresses past height 0
4. Check logs - no "Reset chain to genesis" at height 0
5. Monitor sync - continuous progress

## Files Changed
- Modified: `p2p/node/p2p_service.py` (+2 lines)
- Added: `test_genesis_reset_loop_fix.py` (183 lines)
- Added: `GENESIS_RESET_LOOP_FIX.md` (130 lines)

**Total**: 3 files changed, 315 insertions(+), 1 deletion(-)
