# Manual Testing Guide: Sync Stall Fix

## Overview
This guide helps verify the fix for the sync stall issue where nodes get stuck alternating between SYNCING_HEADERS and SYNCING_BLOCKS without making progress.

## Prerequisites
- A running Animica node
- Access to the `animica` CLI
- Node should be behind the network (e.g., local height < network height)

## Test Scenario 1: Verify Force Sync Clears "at_tip" Error

### Setup
1. Get a node that's behind the network but all connected peers are at same height
2. Run `animica sync status` and observe it shows "at_tip" or similar stall

### Test Steps
1. Run `animica sync force`
2. Monitor the node logs for the message: `"Cleared 'at_tip' error state due to forced sync"`
3. Run `animica sync status` again after a few seconds

### Expected Results
- The "at_tip" error should be cleared
- Node should start requesting headers from peers again
- Sync status should show progress or at least attempt to sync

## Test Scenario 2: Verify Headers == Blocks Stall Detection

### Setup
1. Get a node where `best_header_height == best_block_height`
2. Ensure the node has been idle for more than the stall timeout (typically 5 seconds)

### Test Steps
1. Run `animica sync status` multiple times over 10 seconds
2. Monitor node logs for the warning: `"Sync stalled: headers == blocks with no progress"`

### Expected Results
- After stall timeout, the node should detect the stall condition
- Log message should show: `"headers_blocks_equal_stall"`
- Sync should be marked as stalled
- Peer rotation should be triggered

## Test Scenario 3: Verify Recovery After Stall

### Setup
1. Start with a node in stalled state (headers == blocks)
2. Have at least one peer that has higher height

### Test Steps
1. Wait for stall detection to trigger (see logs)
2. Observe peer rotation in logs
3. Run `animica sync status` to check if sync resumes
4. Monitor for height increases

### Expected Results
- After stall detection, peer should be rotated
- New peer should be selected for sync
- Headers should be requested from new peer
- Height should start increasing

## Verification Checklist

- [ ] Force sync clears "at_tip" error (check logs)
- [ ] Headers == blocks stall is detected after timeout
- [ ] Stall handler triggers peer rotation
- [ ] Sync recovers and makes progress
- [ ] Height increases after recovery
- [ ] No infinite loop between SYNCING_HEADERS and SYNCING_BLOCKS

## Log Messages to Look For

### Success Indicators
```
Cleared 'at_tip' error state due to forced sync
Sync stalled: headers == blocks with no progress
Block sync stall handled
Rotated sync peer
```

### Progress Indicators
```
Received N headers from peers
Blocks queued
Progress: +N blocks
```

## Troubleshooting

### If sync still stalls:
1. Check peer count: `animica peer list`
2. Ensure peers have higher height than local node
3. Check if peers are reachable
4. Try `animica peer bootstrap` to connect to seed nodes
5. Check node logs for any other errors

### If no peers available:
1. Run `animica peer bootstrap` to connect to seed nodes
2. Check network connectivity
3. Verify firewall settings
4. Check if seed nodes are reachable

## Example Output

### Before Fix (Stuck)
```
Status:    SYNCING_BLOCKS
Headers:   6495 | Blocks: 6495
... (no progress)
Status:    SYNCING_HEADERS
Headers:   6495 | Blocks: 6495
... (still no progress, loops forever)
```

### After Fix (Recovering)
```
Status:    SYNCING_BLOCKS
Headers:   6495 | Blocks: 6495
⚠ Sync appears stalled
(Force sync triggered)
Status:    SYNCING_HEADERS
Headers:   6510 | Blocks: 6495
(Progress!)
Status:    SYNCING_BLOCKS
Headers:   6520 | Blocks: 6510
(More progress!)
```

## Notes
- The stall timeout is typically 5 seconds
- Peer rotation happens every 5 seconds after stall detection
- Force sync can be triggered manually with `animica sync force`
- The watchdog will also trigger recovery after 30 seconds of no progress
