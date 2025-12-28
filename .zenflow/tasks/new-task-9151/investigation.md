# Bug Investigation: New Nodes Stuck at Genesis (header_anchor_mismatch)

## Bug Summary

**Symptom**: Fresh nodes start at genesis (height 0), connect to peers, request headers, but discard all received headers with reason `header_anchor_mismatch`. Node remains at genesis forever.

**Evidence from logs**:
- `last_headers_discard_reason_counts: {'header_anchor_mismatch': 5}`
- `last_anchor_check.prev_hash_known: True`
- `anchor_hash == genesis_hash`
- `anchor_source: prev_hash`
- Node stays at height 0, sync status: `IDLE (genesis)`

## Root Cause

**Location**: `p2p/node/p2p_service.py:5665-5681` in method `_process_headers()`

The bug is a **redundant and incorrect second anchor check** that happens AFTER headers have already been validated and accepted:

```python
# Lines 5665-5674 - BUGGY CODE
self._sync_headers_accepted_total += len(contiguous)  # Headers already accepted!
anchor_height = int((peer.hello or {}).get("head_height") or 0)
anchor_hash = bytes((peer.hello or {}).get("head_hash") or b"")
if anchor_height and anchor_hash:
    found_anchor = False
    for h in contiguous:
        if h.height == anchor_height:
            found_anchor = True
            if h.hash != anchor_hash:
                self._penalize_peer(peer, "header_anchor_mismatch", severity=2)
                return [], "invalid_headers", {"header_anchor_mismatch": len(headers)}
```

### Why This Fails for New Nodes

**Scenario**: 
1. Fresh node starts at genesis (height 0)
2. Peer has current head at height 200, advertises in handshake: `head_height=200, head_hash=0xABC...`
3. Node builds locator from genesis: `[genesis_hash]`
4. Peer returns headers 1-64 (or 0-64)
5. First anchor check (lines 5436-5544) validates correctly: headers link to genesis ✓
6. Headers are accepted into `contiguous` list ✓
7. **Second anchor check (lines 5665-5674)**: 
   - Looks for height 200 in contiguous (heights 1-64) → NOT FOUND
   - Even if headers went up to height 200, it checks if hash at height 200 matches peer's `hello.head_hash`
   - But peer's `hello.head_hash` was captured at handshake time; by now peer might have different tip
   - **Result**: All headers discarded with `header_anchor_mismatch`

### Why The First Check Is Correct

Lines 5436-5544 implement the **correct** anchor logic:
- Uses `first_header.parent_hash` to find anchor
- If parent is known locally → anchor from that
- Otherwise uses local head
- At genesis, this correctly finds that headers link to genesis
- Properly handles edge cases (height 0, height 1, unknown parent)

### Why The Second Check Is Wrong

1. **Timing issue**: Uses peer's `hello.head_hash` from handshake, not from current request
2. **Batch mismatch**: Expects a specific height+hash in a partial batch
3. **Already validated**: Headers were already accepted before this check runs
4. **Genesis case**: For new nodes, peer's head_height >> batch range, check always fails

## Affected Components

### Primary
- `p2p/node/p2p_service.py:5665-5681` - buggy second anchor check
- Method: `_process_headers(peer, headers)` 

### Related (working correctly)
- `p2p/node/p2p_service.py:5436-5544` - correct first anchor check
- `p2p/node/p2p_service.py:3844-4043` - `_handle_hello()` chain identity validation (already correct)

## Chain Identity Validation Status

**Task 2 is ALREADY IMPLEMENTED correctly** in `_handle_hello()`:
- ✅ chain_id validation (line 3848)
- ✅ genesis_hash validation (lines 3889, 3909)  
- ✅ fork_id validation (line 3955)
- ✅ consensus_id validation (line 3991)
- ✅ protocol_version validation (line 4027)
- ✅ Rejects with specific reasons: `chain_id_mismatch`, `genesis_mismatch`, `fork_id_mismatch`, etc.

## Proposed Solution

### Fix 1: Remove Buggy Second Anchor Check (Recommended)

**Remove lines 5665-5681** entirely. The first anchor check (5436-5544) is sufficient and correct.

**Rationale**:
- First check validates headers link to known state (genesis or local head)
- Second check is redundant and buggy
- Test `test_mainnet_two_nodes_sync_to_tip` at line 162 explicitly asserts no anchor_mismatch
- Test `test_genesis_anchor_allows_sync_from_height_zero` validates genesis sync works

### Alternative Fix 2: Make Second Check Optional/Advisory

Convert to a warning-only check that doesn't reject headers:

```python
# Advisory only: warn if peer's advertised tip not in batch (might be stale hello)
if anchor_height and anchor_hash and contiguous:
    if contiguous[0].height <= anchor_height <= contiguous[-1].height:
        found = any(h.height == anchor_height and h.hash == anchor_hash for h in contiguous)
        if not found:
            log.warning("Peer advertised tip not found in header batch (stale hello?)", 
                       extra={"peer": peer.remote, "anchor_height": anchor_height})
```

**Recommendation**: Use Fix 1 (remove entirely). The check provides no value and causes the bug.

## Edge Cases to Test

1. **Genesis sync**: New node (height 0) syncs from peer at height N >> 0 ✓ (must work)
2. **Partial batches**: Peer at height 200, returns headers 1-64 (not containing 200) ✓
3. **Stale hello**: Peer's hello.head_hash from handshake != peer's current tip ✓
4. **Wrong chain**: Peer on different genesis → rejected in handshake (already works)
5. **Height mismatch in batch**: Headers claim wrong height → caught by first anchor check

## Test Plan

### Existing Tests (should pass after fix)
- `test_mainnet_two_nodes_sync_to_tip` - line 162 asserts no anchor_mismatch
- `test_genesis_anchor_allows_sync_from_height_zero` - validates genesis sync

### New Test Required
Add test that explicitly reproduces the bug:

```python
def test_genesis_sync_with_high_peer_head():
    """
    Fresh node at genesis syncs from peer at height 200.
    Headers returned are 1-64 (don't contain peer's advertised head).
    Must accept headers, not reject with anchor_mismatch.
    """
    node_a.mine_blocks(200)  # Peer at height 200
    node_b at height 0       # Fresh node
    peer_hello: head_height=200, head_hash=<hash of block 200>
    
    headers = node_a.headers_after_locator([genesis], limit=64)  # Returns 1-64
    accepted, reason = node_b.process_headers(peer, headers)
    
    assert reason is None, f"Should accept headers, got: {reason}"
    assert "anchor_mismatch" not in discard_reasons
```

## Implementation Tasks

- [x] Task 0: Locate sync pipeline → Found in `p2p/node/p2p_service.py`, `p2p/sync/headers.py`
- [x] Identify root cause → Lines 5665-5681 in `_process_headers()`
- [x] Verify chain identity validation → Already working in `_handle_hello()`
- [ ] Task 1: Fix anchor mismatch logic → Remove lines 5665-5681
- [ ] Add regression test → New test for genesis sync with high peer head
- [ ] Run existing tests → Ensure `test_mainnet_two_nodes_sync_to_tip` passes
- [ ] Manual test → Start fresh node, verify it syncs to live network

## Risk Assessment

**Low Risk** - The second anchor check is:
1. Redundant (first check already validates)
2. Buggy (uses stale hello.head_hash)
3. Not relied upon by any other code
4. Removing it fixes the bug and simplifies code

**Tests prove correctness**: Existing test at line 162 explicitly asserts this check should NOT trigger.

## References

- Bug location: `p2p/node/p2p_service.py:5665-5681`
- Correct anchor check: `p2p/node/p2p_service.py:5436-5544`
- Chain validation: `p2p/node/p2p_service.py:3844-4043`
- Test assertion: `test_mainnet_sync_integration.py:162`
- Spec: `p2p/specs/SYNC.md` sections 1.1-1.5 (header sync)
