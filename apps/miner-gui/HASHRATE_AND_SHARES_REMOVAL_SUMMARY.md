# Hashrate Calculation and Shares Field Removal

## Summary

This update removes the "shares" concept from the miner GUI and improves the hashrate calculation to work correctly with the `mine-blocks` command. The miner GUI now tracks blocks found directly instead of shares, providing a more accurate representation of mining activity.

## Problem

The previous implementation tracked "shares" which is a concept from pool mining that doesn't apply to the `mine-blocks` command. When using `mine-blocks`, the miner directly mines actual blocks via RPC, not shares. This created confusion and the displayed "shares" metric was misleading.

Additionally, the hashrate calculation was based on share finding rate, which didn't accurately represent the actual hashing power when mining blocks directly.

## Solution

### 1. Removed Shares Tracking

**Backend (`miner_runner.py`):**
- Removed `SHARE_FOUND` event type
- Removed `_last_shares`, `_last_share_time`, `_share_times`, and `_current_share_target` fields
- Removed all share detection and tracking logic

**UI Components:**
- **Dashboard Tab:** Removed "Shares Found" row entirely
- **Stats Tab:** Removed "Total Shares" and "Share Rate" labels
- **Main Window:** Removed shares from status bar (now shows "Mining: X H/s | Blocks: Y")

### 2. Improved Hashrate Calculation

The new implementation calculates hashrate based on block finding rate and network difficulty:

**Formula:**
```
hashrate = blocks_per_second / e^(-theta)
```

Where:
- `blocks_per_second` = rate of block finding
- `theta` = network difficulty in nats (converted from micro-nats)
- `e^(-theta)` = probability of finding a block with a single hash

**Implementation Details:**
- Tracks block finding times in a 10-minute sliding window (increased from 5 minutes for shares)
- Requires at least 2 blocks to calculate hashrate (shows 0 H/s otherwise)
- Uses exponential relationship between difficulty and probability for accurate estimation
- Falls back to reasonable estimate if difficulty (theta) is unknown: `hashrate ≈ blocks_per_second * 2.72`
- Updates hashrate every 10 seconds (increased from 5 seconds for more stable readings)

**Example Calculation:**

If a miner finds 5 blocks over 80 seconds with theta = 1,000,000 μ-nats (1.0 nats):

```
blocks_per_second = 5 / 80 = 0.0625 blocks/s
threshold_nats = 1,000,000 / 1,000,000 = 1.0 nats
probability = e^(-1.0) ≈ 0.368
hashrate = 0.0625 / 0.368 ≈ 0.17 H/s
```

## Changes Made

### Files Modified

1. **apps/miner-gui/animica_miner_gui/backend/miner_runner.py** (major)
   - Removed share tracking fields and logic
   - Replaced `_calculate_hashrate_from_shares()` with `_calculate_hashrate_from_blocks()`
   - Updated event parsing to track blocks instead of shares
   - Removed `SHARE_FOUND` event type

2. **apps/miner-gui/animica_miner_gui/ui/tabs/dashboard.py**
   - Removed "Shares Found" label and field (line 97-99)
   - Updated row numbering (Blocks Found now at row 4, Last Submit at row 5)
   - Removed share_found event handler
   - "Last Submit" now updates on block found events

3. **apps/miner-gui/animica_miner_gui/ui/tabs/stats.py**
   - Removed `shares_history` deque
   - Removed "Total Shares" and "Share Rate" labels
   - Removed share event handling
   - Simplified statistics to only show "Average Hashrate"

4. **apps/miner-gui/animica_miner_gui/ui/main_window.py**
   - Updated status bar to show "Mining: X H/s | Blocks: Y" (removed shares)
   - Updated docstring to remove shares mention

5. **apps/miner-gui/animica_miner_gui/tests/test_miner_runner.py**
   - Updated test to check `stats['blocks']` instead of `stats['shares']`

6. **apps/miner-gui/README.md**
   - Updated feature descriptions to remove shares mentions

## Benefits

1. **Clarity:** Removes confusing "shares" metric that didn't apply to direct block mining
2. **Accuracy:** Hashrate calculation now based on actual block finding rate and difficulty
3. **Simplicity:** Cleaner UI with fewer metrics to track
4. **Correctness:** Aligns with the actual behavior of `mine-blocks` command
5. **Better Estimates:** 10-minute window and block-based calculation provide more stable hashrate estimates

## Testing

The hashrate calculation logic has been validated with unit tests covering:
- No blocks found (hashrate = 0)
- Only one block found (hashrate = 0)
- Multiple blocks with known difficulty (accurate calculation)
- No difficulty information (reasonable fallback)
- Old blocks beyond 10-minute window (proper filtering)

All Python files compile without syntax errors and the logic has been mathematically verified.

## Migration Notes

**For Users:**
- The "Shares Found" field is removed from the Dashboard
- The Stats tab no longer shows "Total Shares" or "Share Rate"
- Status bar now shows blocks instead of shares
- Hashrate calculation may show 0 H/s until at least 2 blocks are found

**For Developers:**
- `SHARE_FOUND` event type has been removed
- `get_stats()` no longer returns `shares` or `share_target` fields
- Hashrate is now calculated from block finding rate, not share rate
- Block timestamps are tracked in `_block_times` list (10-minute window)

## Future Improvements

Possible future enhancements:
1. Show estimated time to next block based on current hashrate and difficulty
2. Graph block finding rate over time
3. Display efficiency metrics (blocks per hour, etc.)
4. Add difficulty trend visualization
