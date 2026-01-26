# Fix: Chain Reorganization for Unresponsive High-Height Nodes

## Problem Statement

When a node at the highest blockchain height stops broadcasting blocks, other nodes in the network become unable to sync. This occurs because:

1. The stopped node's advertised height remains in the network best height calculation
2. Other nodes continue waiting indefinitely for blocks from this unresponsive peer
3. Chain reorganization to lower-height but active seed nodes doesn't occur
4. The entire network becomes stuck, unable to make progress

## Root Cause Analysis

The issue was in the `_network_best_height()` method in `p2p/node/p2p_service.py`. This method computes the highest height known in the network by considering:

1. Direct peer heights (from their `head_height`)
2. Peer-reported network best heights (from `network_best_height` field)

**The bug**: The method applied staleness and cooldown checks to direct peer heights, but **unconditionally accepted** peer-reported `network_best_height` values, even from stale or penalized peers.

```python
# OLD (BUGGY) CODE:
if info is not None:
    if now - info.updated_at <= self._sync_peer_head_stale_sec:
        if not info.cooldown_until or info.cooldown_until <= now:
            heights.append(int(info.height))  # ✓ Checked for staleness

# BUG: Unconditionally accept network_best_height
try:
    network_height = (peer.hello or {}).get("network_best_height")
    if network_height is not None:
        heights.append(network_height)  # ❌ No staleness check!
```

## Solution

Modified `_network_best_height()` to consistently apply responsiveness checks to **both** direct peer heights and peer-reported network best heights:

1. **Added helper method** `_is_peer_responsive()` to encapsulate the responsiveness check
2. **Applied same checks** to `network_best_height` values from peers
3. **Only responsive peers** (not stale, not in cooldown) contribute to network best height

```python
# NEW (FIXED) CODE:
def _is_peer_responsive(self, info: Optional[_PeerHeadInfo], now: float) -> bool:
    """Check if a peer is responsive (not stale and not in cooldown)."""
    if info is None:
        return False
    if now - info.updated_at > self._sync_peer_head_stale_sec:
        return False
    if info.cooldown_until and info.cooldown_until > now:
        return False
    return True

# In _network_best_height():
if self._is_peer_responsive(info, now):
    heights.append(int(info.height))  # ✓ Add direct height
    
    # ✓ Only accept network_best_height from responsive peers
    network_height = (peer.hello or {}).get("network_best_height")
    if network_height is not None:
        heights.append(network_height)
```

## Behavior Change

### Before (Broken)

```
Network State:
  Peer A (stalled):     height=100, last_seen=120s ago, reports network_best=150
  Seed Node 1 (active): height=50,  last_seen=now,      reports network_best=60
  Seed Node 2 (active): height=55,  last_seen=5s ago,   reports network_best=60

Result:
  network_best_height = 150 (from stalled peer A) ❌
  
Impact:
  - Node waits for blocks 51-150 that will never arrive
  - Cannot reorganize to active chains at height 50-60
  - Network is stuck
```

### After (Fixed)

```
Network State:
  Peer A (stalled):     height=100, last_seen=120s ago, reports network_best=150 [EXCLUDED]
  Seed Node 1 (active): height=50,  last_seen=now,      reports network_best=60 ✓
  Seed Node 2 (active): height=55,  last_seen=5s ago,   reports network_best=60 ✓

Result:
  network_best_height = 60 (from active seed nodes) ✓
  
Impact:
  - Node can synchronize with active peers
  - Chain reorganization to highest active chain succeeds
  - Network continues to make progress
```

## Testing

Created comprehensive test suite in `p2p/tests/test_stale_peer_height_exclusion.py`:

1. ✅ `test_stale_peer_height_excluded_from_network_best` - Stale peer's direct height is excluded
2. ✅ `test_cooldown_peer_height_excluded_from_network_best` - Cooldown peer's direct height is excluded
3. ✅ `test_stale_peer_network_best_height_excluded` - Stale peer's network_best_height is excluded
4. ✅ `test_cooldown_peer_network_best_height_excluded` - Cooldown peer's network_best_height is excluded
5. ✅ `test_all_peers_stale_returns_none` - Returns None when all peers are stale
6. ✅ `test_peer_becomes_responsive_again` - Peer's height is included again once responsive

All tests pass. Existing test `test_network_best_height_snapshot` continues to pass, showing no regression.

## Configuration

The fix uses existing configuration parameters:

- **Staleness threshold**: `ANIMICA_P2P_PEER_HEAD_STALE_SEC` (default: 60 seconds)
  - Peers that haven't updated their height in 60+ seconds are considered stale
  
- **Cooldown**: `ANIMICA_P2P_PEER_HEAD_COOLDOWN_SEC` (default: 120 seconds)
  - Peers that have timed out or failed requests are put in cooldown

## Impact & Benefits

### Network Resilience
- Nodes automatically recover from stalled high-height peers
- Network remains operational even when highest-height node goes offline
- Chain reorganization to active seed nodes is no longer blocked

### Sync Reliability
- Prevents indefinite waiting for blocks from unresponsive peers
- Ensures sync targets are based on actually available chains
- Leverages multi-hop network best height propagation correctly

### Code Quality
- Extracted `_is_peer_responsive()` helper method improves readability
- Consistent responsiveness checks across all height sources
- Well-tested with comprehensive test coverage

## Files Changed

```
p2p/node/p2p_service.py                       |  54 ++++++++----
p2p/tests/test_stale_peer_height_exclusion.py | 275 +++++++++++++++++++++++++++
2 files changed, 314 insertions(+), 15 deletions(-)
```

## Minimal Change Principle

This fix follows the minimal change principle:
- ✅ Only 39 lines changed in the core file (extracted method + updated logic)
- ✅ No changes to configuration, protocols, or data structures
- ✅ Leverages existing staleness and cooldown mechanisms
- ✅ No breaking changes to API or behavior
- ✅ Comprehensive tests ensure correctness

## References

- Issue: "nodes at highest height stopping blocks other nodes from syncing"
- Implementation: `p2p/node/p2p_service.py` lines 9921-9979
- Tests: `p2p/tests/test_stale_peer_height_exclusion.py`
