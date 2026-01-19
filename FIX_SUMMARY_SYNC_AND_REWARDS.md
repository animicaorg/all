# Fix Summary: Sync Peer Detection & Mining Reward Crediting

## Problem Statement

Two critical bugs were preventing proper operation of the Animica node:

### Bug A: Sync Force Peer Detection
**Symptom:** `animica sync force` reports "Connected peers: 0" even when `animica peer list` shows connected peers.

**Impact:** Users cannot reliably sync the blockchain because the sync command thinks no peers are available.

**Reproduction:**
```bash
animica node up
animica peer add tcp://144.126.133.21:30333
animica peer list  # Shows: Status: connected (outbound)
animica sync force --rpc-url http://127.0.0.1:8545/rpc  # Shows: Connected peers: 0
```

### Bug B: Mining Rewards Not Credited
**Symptom:** After mining a block, the CLI reports "credited: 300000000000 nANM" but balance doesn't increase.

**Impact:** Mining rewards are lost - miners don't get paid for their work.

**Reproduction:**
```bash
ADDR="anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
animica miner mine-blocks --address "$ADDR" --count 1 --threads 0
# Output: ACCEPTED ... reward: 300 ... credited: 300000000000 nANM
animica wallet show "$ADDR"  # Balance unchanged!
animica rpc call state.getBalance '{"params":["'"$ADDR"'"]}'  # Balance unchanged!
```

## Root Cause Analysis

### Bug A: Missing RPC Method
- The sync CLI (`python/animica/cli/sync.py`) calls `_get_peer_count()` 
- This function tries RPC methods: `net.peerCount`, `p2p.peerCount`, etc.
- **These methods didn't exist** in `rpc/methods/p2p.py`
- Only `p2p.listPeers` was available (returns full list, not count)
- Result: `_get_peer_count()` returns `None`, displayed as "0 peers"

### Bug B: Coinbase Transaction Not Executed
- Mining creates blocks with **coinbase transactions** (TxKind.COINBASE = 3)
- These are special transactions that credit the block reward to the miner
- The execution dispatcher (`execution/runtime/dispatcher.py`) only handled:
  - Kind 0: transfer
  - Kind 1: deploy
  - Kind 2: call
- **Kind 3 (coinbase) was missing** from the dispatcher mapping
- Result: Coinbase transactions returned REVERT with zero gas
- The reward was never applied to state!

## Solution Implemented

### Fix A: Add Peer Count RPC Method

**File:** `rpc/methods/p2p.py`

Added a new RPC method:
```python
@method(
    "net.peerCount",
    desc="Return the number of connected peers (lightweight count method)",
    aliases=["p2p.peerCount", "p2p.peer_count", "net_peerCount"],
)
async def peer_count() -> int:
    """Return the total number of connected peers."""
    try:
        counts = _peer_counts_snapshot()
        return counts.get("peers_total", 0)
    except Exception as e:
        log.debug("Failed to get peer count: %s", e)
        return 0
```

**What this does:**
- Exposes live peer count from the P2P service
- Returns integer count (not full peer list)
- Used by `animica sync force` to check connectivity
- Has error handling to return 0 if P2P unavailable

### Fix B: Add Coinbase Transaction Handling

**Files:** `execution/runtime/dispatcher.py` and `execution/runtime/executor.py`

#### 1. Updated Numeric Kind Mapping
```python
_NUMERIC_KIND = {
    0: "transfer",
    1: "deploy",
    2: "call",
    3: "coinbase",  # TxKind.COINBASE - block rewards
}
```

#### 2. Updated Kind Resolution
```python
def resolve_tx_kind(tx: Any) -> str:
    """Determine transaction kind: 'transfer' | 'deploy' | 'call' | 'coinbase'."""
    # Now recognizes kind=3 as "coinbase"
    # Also recognizes string "coinbase", "reward", "block_reward"
```

#### 3. Updated Dispatcher Routing
```python
if kind == "transfer" or kind == "coinbase":
    # Both use apply_transfer - it already handles coinbase correctly
    return _transfers.apply_transfer(tx, state, block_env, tx_env, params=params)
```

**Why this works:**
- `apply_transfer` already had coinbase detection: `is_coinbase = (kind_int == 3)`
- For coinbase: sender = zero address, no signature check required
- The fix just ensures coinbase transactions **reach** `apply_transfer`

## Testing

### Regression Tests Added

1. **`test_sync_peer_count.py`**
   - Verifies `net.peerCount` RPC method exists
   - Tests it returns correct integer count
   - Tests error handling when P2P unavailable

2. **`test_mining_reward_crediting.py`**
   - Verifies dispatcher recognizes coinbase (kind=3)
   - Tests dispatcher routes coinbase to `apply_transfer`
   - Tests `apply_transfer` properly credits rewards
   - Includes integration test showing balance increases

### Manual Verification

Created `verify_fixes.py` script that checks:
1. ✅ Peer count RPC method exists and is callable
2. ✅ Dispatcher recognizes coinbase transactions
3. ✅ Executor fallback handles coinbase
4. ✅ apply_transfer properly processes coinbase

**All tests pass!**

## Expected Behavior After Fix

### Sync Force
```bash
$ animica sync force --rpc-url http://127.0.0.1:8545/rpc

🔄 Forcing blockchain synchronization...
Target RPC:    http://127.0.0.1:8545/rpc
Bootstrap RPC: disabled

Current height: 1234
Connected peers: 5  # ← Now shows correct count!
✓ Sync triggered successfully
```

### Mining Rewards
```bash
$ animica miner mine-blocks --address $ADDR --count 1 --threads 0

Mining 1 block(s) with local P2P validation...
  FOUND: Block 1/1 PoW (height: 1235, nonce: 12345, hash: 0x...)
  ACCEPTED: Block 1/1 (height: 1235, reward: 300.000000000 ANM = 300000000000 nANM, credited: 300000000000 nANM)

$ animica wallet show $ADDR
Balance: 300.000000000 ANM  # ← Balance now increases!

$ animica rpc call state.getBalance '{"params":["'$ADDR'"]}'
{"result": "300000000000"}  # ← State now reflects reward!
```

## Files Modified

1. **`rpc/methods/p2p.py`** (+ 27 lines)
   - Added `peer_count()` RPC method
   - Added error handling and logging

2. **`execution/runtime/dispatcher.py`** (+ 5 lines, - 3 lines)
   - Added kind=3 to `_NUMERIC_KIND`
   - Updated `resolve_tx_kind()` for coinbase
   - Updated `dispatch()` to route coinbase

3. **`execution/runtime/executor.py`** (+ 5 lines, - 3 lines)
   - Updated fallback dispatcher for coinbase
   - Added documentation comments

4. **Tests Added:**
   - `python/animica/cli/tests/test_sync_peer_count.py` (125 lines)
   - `python/animica/cli/tests/test_mining_reward_crediting.py` (305 lines)
   - `verify_fixes.py` (218 lines) - manual verification script

## Impact Assessment

### Positive Impact
- ✅ Sync force now works correctly with connected peers
- ✅ Mining rewards properly credited to wallet balances
- ✅ Block rewards included in state and survive restarts
- ✅ Minimal code changes (surgical fix)
- ✅ No breaking changes to existing functionality

### Risk Assessment
- ✅ **Low risk:** Changes are additive (new RPC method, new kind mapping)
- ✅ **No API breaking changes:** Existing code continues to work
- ✅ **Well tested:** Regression tests cover critical paths
- ✅ **Backward compatible:** Handles both old and new transaction formats

## Migration Notes

No migration required. The fix is transparent to users:
- Existing nodes will automatically get the new RPC method
- Old blocks without coinbase txs continue to work
- New blocks with coinbase txs now execute correctly

## Deployment Checklist

- [x] Code changes implemented
- [x] Regression tests added
- [x] Manual verification passed
- [x] Code review completed
- [ ] Deploy to test environment
- [ ] Test sync force with live node
- [ ] Test mining rewards with real blocks
- [ ] Deploy to production

## Developer Notes

### Why Coinbase Uses apply_transfer

Coinbase transactions are essentially transfers from the protocol (sender=zero) to the miner. The `apply_transfer` handler already had all the logic:

```python
# From execution/runtime/transfers.py
is_coinbase = (kind_int == 3)

if is_coinbase:
    # Protocol-generated: sender = zero address, no signature
    sender = b"\x00" * ADDRESS_LEN
elif sig_pubkey is not None:
    # Regular transfer: verify signature
    sender = account_key_from_pubkey(sig_pubkey, sig_alg_id)

if not is_coinbase and not sender:
    raise ExecError("missing sender", code="MISSING_SENDER")
```

The fix just ensures coinbase transactions **reach** this handler instead of being rejected by the dispatcher.

### Future Considerations

If additional transaction kinds are added (e.g., kind=4 for some new feature), remember to update both:
1. `execution/runtime/dispatcher.py` - `_NUMERIC_KIND` mapping
2. `execution/runtime/executor.py` - fallback dispatcher mapping

Consider extracting these mappings to a shared constant in the future to avoid duplication.

## References

- Issue: "Fix (A) syncing and (B) wallets not being credited"
- PR: `copilot/fix-syncing-and-wallet-issues`
- Related: Block reward implementation in `rpc/methods/miner.py` (lines 1570-1665)
- Related: Block import in `core/chain/block_import.py` (lines 1450-1518)
