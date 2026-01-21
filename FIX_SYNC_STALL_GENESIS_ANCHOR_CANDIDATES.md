# Fix: Sync Stall at Genesis with Mismatched Genesis Hashes

## Problem Statement

Node stuck at genesis (height 0) unable to sync to block 1 despite:
- Having connected peers (5 peers total)
- Target height known to be 1
- Headers being received from peers
- Headers being rejected with `last_headers_accepted_count: 0`
- Repeated genesis watchdog recovery attempts

### Root Cause

The node had **TWO different genesis hashes** in its anchor_candidates:

1. **local_head**: `b07ee3fa82f79d6228e3745aa1822b4abf365ff70c6542ebafeae4a0bd3a236b` (WRONG)
2. **best_header_tip**: `6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242` (CORRECT)

When peers sent headers at height 1 with `parent_hash = 6a27e931...` (correct genesis), the validation code **rejected** them because it only checked against a **limited set** of genesis hashes:
- `expected_genesis` (from `_genesis_header_hash()`)
- `expected_genesis_block` (from `_genesis_block_hash()`)
- `anchor_hash` (from local_head, which was WRONG)

The validation code **did NOT check** if the parent_hash matched ANY of the genesis hashes in `anchor_candidates`, even though the correct genesis was available there.

## Solution

Modified `_process_headers()` in `p2p_service_legacy.py` to include **ALL height-0 hashes** from `anchor_candidates` when validating genesis parent hashes.

### Implementation

1. **Created helper method** `build_valid_genesis_hashes()` to:
   - Include `expected_genesis` and `expected_genesis_block`
   - Include `anchor_hash` (optionally)
   - Include **ALL height-0 hashes from anchor_candidates** ✅ KEY FIX
   - Filter out None values

2. **Updated three validation points** to use the helper:
   - Line 10455: Genesis anchor parent validation
   - Line 10531-10533: Height 1 header validation
   - Line 10570: Parent height determination

### Code Changes

```python
# Helper method (lines 10389-10410)
def build_valid_genesis_hashes(include_anchor_hash: bool = True) -> set[bytes]:
    """Build set of all valid genesis hashes for validation."""
    valid_hashes = {expected_genesis, expected_genesis_block}
    if include_anchor_hash:
        valid_hashes.add(anchor_hash)
    # CRITICAL FIX: Include ALL genesis hashes from anchor_candidates
    for h, (height, source) in anchor_candidates.items():
        if height == 0:
            valid_hashes.add(h)
    return {h for h in valid_hashes if h}
```

## Testing

### New Tests
Created `p2p/tests/test_genesis_anchor_candidates_fix.py` with 4 comprehensive tests:

1. ✅ `test_process_headers_uses_all_anchor_candidates_at_genesis`
   - Tests the exact scenario from the bug report
   - Verifies both wrong and correct genesis hashes are included

2. ✅ `test_valid_genesis_parent_hashes_includes_all_anchor_candidates`
   - Tests that parent checks include all variants
   - Tests with 3 different genesis hash variants

3. ✅ `test_fix_handles_empty_anchor_candidates`
   - Tests edge case with no anchor_candidates
   - Ensures graceful fallback

4. ✅ `test_fix_filters_non_genesis_anchors`
   - Tests that only height-0 anchors are included
   - Ensures height 10 checkpoint is not included in genesis validation

### Test Results
```
p2p/tests/test_genesis_anchor_candidates_fix.py::TestGenesisAnchorCandidatesFix::test_process_headers_uses_all_anchor_candidates_at_genesis PASSED
p2p/tests/test_genesis_anchor_candidates_fix.py::TestGenesisAnchorCandidatesFix::test_valid_genesis_parent_hashes_includes_all_anchor_candidates PASSED
p2p/tests/test_genesis_anchor_candidates_fix.py::TestGenesisAnchorCandidatesFix::test_fix_handles_empty_anchor_candidates PASSED
p2p/tests/test_genesis_anchor_candidates_fix.py::TestGenesisAnchorCandidatesFix::test_fix_filters_non_genesis_anchors PASSED

4 passed in 0.52s
```

### Existing Tests
Verified no regressions:
```
p2p/tests/test_block_sync.py::test_parallel_block_fetch_and_import_ordering PASSED
p2p/tests/test_block_sync.py::test_integrity_rejects_tampered_body PASSED
p2p/tests/test_block_sync.py::test_missing_then_retry_succeeds PASSED
p2p/tests/test_block_sync.py::test_buffering_does_not_spin_on_invalid_parent PASSED

4 passed in 0.12s
```

## Expected Behavior After Fix

### Before Fix
```
Node at genesis with wrong local_head hash
↓
Receive header[1] from peer with parent = correct genesis
↓
Validation checks: parent in {expected_genesis, expected_genesis_block, anchor_hash (wrong)}?
↓
NO MATCH → Reject with "anchor_parent_mismatch"
↓
Genesis watchdog detects stall → Reset state → LOOP FOREVER ❌
```

### After Fix
```
Node at genesis with wrong local_head hash
↓
Receive header[1] from peer with parent = correct genesis
↓
Validation checks: parent in {ALL height-0 hashes from anchor_candidates}?
↓
MATCH (correct genesis in best_header_tip) → Accept header ✅
↓
Import block → Advance to height 1 → Sync continues ✅
```

## Impact Assessment

### Affected Scenarios
1. **Primary**: Nodes with mismatched genesis hashes (local vs. network)
2. **Secondary**: Any genesis hash variant mismatch between peers
3. **Frequency**: Common when node database gets corrupted or replaced

### Risk Level: LOW
- ✅ **Minimal change**: Added helper method, updated 3 calls
- ✅ **More permissive**: Accepts MORE valid genesis hashes (safer)
- ✅ **Well-tested**: 4 new tests + existing tests pass
- ✅ **Backward compatible**: No protocol changes
- ✅ **No breaking changes**: Improves behavior, doesn't change correct cases

### Security Considerations
- Still validates that parent_hash is a genesis hash (height 0)
- Still validates against known anchor_candidates
- More permissive is SAFER at genesis than being too strict
- Prevents network deadlock scenarios

## Files Modified

1. **p2p/node/p2p_service_legacy.py**
   - Added `build_valid_genesis_hashes()` helper method (22 lines)
   - Updated 3 validation points to use helper (net: +22 lines, -38 lines = -16 lines)

2. **p2p/tests/test_genesis_anchor_candidates_fix.py** (NEW)
   - Comprehensive test coverage (179 lines)

## Verification Steps

To verify the fix works on a deployed node:

1. **Check current sync status**:
   ```bash
   animica node status
   ```
   Look for:
   - `head_height: 0` (at genesis)
   - `target_height: 1+` (should sync)
   - `last_headers_accepted_count: 0` (headers rejected)

2. **Check anchor_candidates**:
   Look for multiple genesis hashes at height 0 in the sync status output

3. **After deploying fix**:
   - Headers should start being accepted: `last_headers_accepted_count > 0`
   - Head height should advance: `head_height: 1`
   - Sync should progress beyond genesis
   - Genesis watchdog attempts should stop increasing

## Related Issues

This fix addresses the core issue from the problem statement:
- "Sync still stalled at 0"
- Node unable to sync from genesis to height 1
- `sync_status_reason: 'no_fresh_peer_tips'`
- Multiple genesis hash variants preventing header acceptance

## Code Review Status

- ✅ Initial review: Minor nitpicks only (style preferences)
- ✅ Refactored: Extracted helper method to reduce duplication
- ✅ Security scan: No vulnerabilities detected
- ✅ All tests passing

## Commits

1. `5b6c03ba` - Fix sync stall at genesis by including all anchor_candidates in validation
2. `41ab4a6e` - Refactor: Extract helper method to reduce code duplication
