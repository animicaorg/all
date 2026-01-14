# Visual Guide: Sync Fix for SYNCED Phase Issue

## Before the Fix

```
┌─────────────────────────────────────────────────────────────┐
│ Node State: SYNCED                                          │
│ Local Height: 11242                                         │
│ Best Peer Height: 11258                                     │
│ Gap: 16 blocks                                              │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Sync Loop Tick                                              │
│                                                             │
│  ❌ Phase is SYNCED                                         │
│  ❌ _sync_once() returns early (line 8937)                  │
│  ❌ No headers requested                                    │
│  ❌ No blocks downloaded                                    │
│                                                             │
│  Result: Node stays stuck at 11242                          │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Node remains at height 11242                                │
│ Manual intervention required: animica sync force            │
└─────────────────────────────────────────────────────────────┘
```

## After the Fix

```
┌─────────────────────────────────────────────────────────────┐
│ Node State: SYNCED                                          │
│ Local Height: 11242                                         │
│ Best Peer Height: 11258                                     │
│ Gap: 16 blocks                                              │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Sync Loop Tick                                              │
│                                                             │
│  ✅ Detect: SYNCED but behind target (11242 < 11258)        │
│  ✅ Log: "Node in SYNCED phase but behind target"           │
│  ✅ Action: Change phase to SYNCING                         │
│  ✅ Action: Kick sync with aggressive=True                  │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Phase: SYNCING                                              │
│                                                             │
│  ✅ Request headers from peers                              │
│  ✅ Download missing blocks (11243-11258)                   │
│  ✅ Process and import blocks                               │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Node catches up to height 11258                             │
│ Phase returns to SYNCED (correctly this time)               │
│ No manual intervention needed ✨                            │
└─────────────────────────────────────────────────────────────┘
```

## Key Fix Locations

### Location 1: Sync Loop Detection (line ~9448)
```python
# Primary fix: Detect SYNCED-but-behind condition
if (
    self._sync_phase == "SYNCED"          # Currently marked as synced
    and target_height is not None         # Have a target
    and best_block_height < target_height # But actually behind
    and not self._sync_inflight_headers   # Not already syncing
    and not self._sync_inflight_blocks
):
    # Resume sync
    self._sync_phase = "SYNCING"
    self._sync_kick(reason="synced_but_behind", aggressive=True)
```

### Location 2: Network Best Height Check (line ~8928)
```python
# Secondary fix: Use network_best_height to avoid premature SYNCED
target_height = self._sync_target_height
if target_height is None:
    target_height = remote_height

# NEW: Also check network best height
network_best = self._network_best_height()
if network_best is not None:
    if target_height is None:
        target_height = network_best
    else:
        target_height = max(target_height, network_best)
```

## Example Log Output

### Before Fix (stuck):
```
Sync loop tick
  phase=SYNCED
  head_height=11242
  best_peer_height=11258
  gap=16
  → No action taken, stays stuck
```

### After Fix (automatic recovery):
```
Sync loop tick
  phase=SYNCED
  head_height=11242
  best_peer_height=11258
  gap=16

Node in SYNCED phase but behind target - resuming sync
  local_height=11242
  target_height=11258
  gap=16
  best_peer=173.212.254.121:44306

Sync loop tick
  phase=SYNCING
  head_height=11242
  requesting headers from peers...

Sync loop tick
  phase=SYNCING
  head_height=11250
  downloading blocks...

Sync loop tick
  phase=SYNCED
  head_height=11258
  caught up! ✨
```

## Debug Command Output

### Before Fix:
```bash
$ animica debug sync-dump

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RPC URL:          http://127.0.0.1:8545/rpc
Local head:       11242 (0x0493...)
Best peer head:   11258 (90b3...) from 173.212.254.121:44306
Sync phase:       SYNCED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Problem: 16 blocks behind but phase is SYNCED ❌
```

### After Fix:
```bash
$ animica debug sync-dump

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RPC URL:          http://127.0.0.1:8545/rpc
Local head:       11258 (0x90b3...)
Best peer head:   11258 (90b3...) from 173.212.254.121:44306
Sync phase:       SYNCED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Status: In sync with peers ✅
```

## Timeline

1. **Issue Detected**: Node stuck at 11242 while peers at 11258
2. **Root Cause Found**: SYNCED phase prevents sync resumption
3. **Fix Implemented**: Two-part fix (detection + prevention)
4. **Testing**: All verification tests pass
5. **Deployment**: Ready for production

## Rollback Plan

If issues arise, the fix can be disabled by commenting out the check at line ~9448:
```python
# Temporarily disable SYNCED-but-behind detection
# if (
#     self._sync_phase == "SYNCED"
#     ...
```

However, this should not be necessary as the fix is defensive and only triggers when:
- Phase is explicitly SYNCED
- Target height is known and higher
- No sync work is already in flight
