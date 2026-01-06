# Qt Wallet Miner GUI - Hashrate and Time-to-Block Fix

## Summary

This fix addresses three main issues with the Qt wallet miner GUI:

1. **Hashrate display showing 0 H/s** despite shares being submitted
2. **Missing time to find block estimation**
3. **Unclear if blocks can actually be found** (not just shares)

## Changes Made

### 1. Hashrate Calculation (miner_runner.py)

#### Problem
The GUI relied on parsing hashrate from mining log output, but the mining CLI doesn't consistently output hashrate information in a parseable format. This caused the hashrate to show as "0 H/s" even when shares were being found.

#### Solution
Calculate hashrate from the share submission rate using the exponential probability formula:

```python
# Track share timestamps in a rolling 5-minute window
share_times = [t1, t2, t3, ...]  # Recent share timestamps

# Calculate share rate
shares_per_second = len(share_times) / time_span

# Use exponential probability to estimate hashrate
# probability = e^(-threshold) where threshold = theta * share_target
threshold_nats = (theta_micro * share_target) / 1_000_000
probability = math.exp(-threshold_nats)
hashrate = shares_per_second / probability
```

**Key Features:**
- Tracks last 5 minutes of shares for stable calculation
- Uses exponential probability for mathematical accuracy
- Falls back to simple approximation when theta is unknown
- Emits hashrate updates when shares are found and periodically (every 5 seconds)

### 2. Time to Find Block (dashboard.py)

#### Problem
No estimation of how long it would take to find a block at current hashrate.

#### Solution
Calculate expected time based on current hashrate and network difficulty:

```python
# Calculate block probability
theta_nats = theta_micro / 1_000_000
block_probability = math.exp(-theta_nats)

# Average hashes needed to find a block
avg_hashes_needed = 1.0 / block_probability

# Time in seconds
time_seconds = avg_hashes_needed / hashrate
```

**Display Format:**
- `< 60s` → "30s"
- `< 1h` → "5.0m"
- `< 24h` → "2.5h"
- `≥ 24h` → "3.2d"

### 3. Difficulty Display (dashboard.py)

#### Problem
Users couldn't see the current network difficulty, making it hard to understand mining progress.

#### Solution
- Display theta (network difficulty) in nats
- Extract from mining logs when available
- Query from RPC template as fallback
- Add tooltip: "Network difficulty (theta) - higher values mean harder to find blocks"

### 4. Block Finding Verification

#### Problem
Unclear if the miner can actually find blocks or just shares.

#### Solution
**Verified that the mining backend automatically handles this:**
- Shares meeting share difficulty (easier) → submitted as shares
- Shares that also meet block difficulty (harder) → automatically submitted as blocks
- The orchestrator (`mining/orchestrator.py`) has separate `_submit_block()` logic
- No GUI changes needed - blocks are automatically detected and counted

## UI Changes

### Before
```
Mining Status:
  Status: Running
  Hashrate: 0 H/s         ← Always shows 0
  Shares Found: 5
  Blocks Found: 0
  Last Submit: 14:23:15
```

### After
```
Mining Status:
  Status: Running
  Hashrate: 4.51 KH/s     ← Calculated from share rate
  Difficulty: 10.25 nats  ← Network difficulty
  Time to Block: ~1.4h    ← Estimated time
  Shares Found: 5
  Blocks Found: 0
  Last Submit: 14:23:15
```

## Technical Details

### Hashrate Calculation Math

The probability of finding a share with threshold `t` is:
```
P(share) = e^(-t)
```

Where for shares: `t_share = theta * share_target`

If we find `n` shares in time `T`, the hashrate is:
```
hashrate = n / (T * P(share))
        = n / (T * e^(-t_share))
        = (n/T) / e^(-t_share)
        = shares_per_second / e^(-t_share)
```

### Time to Block Calculation

Expected number of hashes to find a block:
```
E[hashes] = 1 / P(block)
          = 1 / e^(-theta)
          = e^(theta)
```

Expected time:
```
time = E[hashes] / hashrate
     = e^(theta) / hashrate
```

### Example Calculation

Given:
- Theta = 10 nats (network difficulty)
- Share target = 25% (0.25)
- 10 shares found in 27 seconds

Calculate hashrate:
```
shares_per_second = 10 / 27 = 0.370 shares/s
t_share = 10 * 0.25 = 2.5 nats
P(share) = e^(-2.5) = 0.082
hashrate = 0.370 / 0.082 = 4.51 H/s
```

Calculate time to block:
```
P(block) = e^(-10) = 0.000045
E[hashes] = 1 / 0.000045 = 22,026 hashes
time = 22,026 / 4.51 = 4,882 seconds ≈ 1.4 hours
```

## Testing

### Unit Test Results
```bash
$ python apps/miner-gui/test_hashrate_calculation.py

Testing hashrate calculation logic...
------------------------------------------------------------
Shares found: 10 over 27 seconds
Shares per second: 0.3704
Theta (difficulty): 10.00 nats
Share threshold: 2.50 nats
Calculated hashrate: 4.51 H/s
Estimated time to block: 4882 seconds (1.36 hours)
Block is 1808.04x harder than share

✓ Calculation verified - ratio matches expected value!
✓ Time formatting tests pass
------------------------------------------------------------
```

## Files Changed

1. **apps/miner-gui/animica_miner_gui/backend/miner_runner.py**
   - Added share timestamp tracking
   - Implemented `_calculate_hashrate_from_shares()` method
   - Enhanced log parsing for theta and share target
   - Added periodic hashrate updates

2. **apps/miner-gui/animica_miner_gui/ui/tabs/dashboard.py**
   - Added "Difficulty" field
   - Added "Time to Block" field
   - Implemented `_update_time_to_block()` method
   - Enhanced RPC polling to fetch template info
   - Added tooltips for user guidance

3. **apps/miner-gui/test_hashrate_calculation.py** (new)
   - Comprehensive test suite
   - Validates mathematical formulas
   - Tests time formatting
   - Verifies difficulty ratios

## Usage

### For Users

After these changes, the miner GUI will:
1. Show actual hashrate based on share submissions (no more "0 H/s")
2. Display network difficulty so you can see how hard mining is
3. Estimate how long until you might find a block
4. Continue to show share and block counts as before

### For Developers

The hashrate calculation can be tested independently:
```bash
python apps/miner-gui/test_hashrate_calculation.py
```

To verify the math:
- Hashrate should increase as shares are found more quickly
- Time to block should decrease as hashrate increases
- Block difficulty should be 1/share_target times harder than share difficulty

## Edge Cases Handled

1. **No shares yet**: Hashrate shows "0 H/s"
2. **Only 1 share**: Hashrate shows "0 H/s" (need 2+ for rate)
3. **Theta unknown**: Falls back to approximate calculation
4. **Very high difficulty**: Time to block shows "Very high" instead of enormous number
5. **Rapid difficulty changes**: Rolling 5-minute window smooths out variations

## Known Limitations

1. **Initial delay**: Hashrate won't display until at least 2 shares are found
2. **Accuracy**: Estimation improves with more shares (5-10 shares for good accuracy)
3. **Network changes**: If network difficulty changes rapidly, time-to-block may be temporarily inaccurate
4. **Solo mining**: Time estimates assume solo mining (not pooled mining)

## Future Improvements

Potential enhancements not included in this PR:
- Display confidence intervals for time-to-block
- Show hashrate trend graph over time
- Alert when hashrate drops significantly
- Compare local hashrate to network hashrate
- Show expected daily/weekly rewards

## References

- Mining probability math: Based on Animica's PoIES (Proof of Integrated External Services) consensus
- Exponential distribution: Standard probability theory for Poisson processes
- Hash search implementation: `mining/hash_search.py`
- Mining orchestrator: `mining/orchestrator.py`
