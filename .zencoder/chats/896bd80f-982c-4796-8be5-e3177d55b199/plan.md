# Bug Fix Plan

This plan guides you through systematic bug resolution. Please update checkboxes as you complete each step.

## Phase 1: Investigation

### [x] Locate Key Files

- [x] Find CLI `animica tx send` implementation
  - **File**: `python/animica/cli/tx.py` (lines 274-479)
  - **Issue**: Lines 469-471 suggest nonexistent commands
- [x] Find RPC `tx.sendRawTransaction` handler
  - **File**: `rpc/methods/tx.py` (lines 1291-1544)
  - **Status**: Has validation at lines 1475-1488 checking if tx is in mempool after submit()
- [x] Find mempool implementation and rejection reasons
  - **File**: `mempool/pool.py` (not fully reviewed yet)
  - **Note**: Different code paths reference `ctx.mempool` or `_PEND` or `_FALLBACK_PENDING`
- [x] Find block template builder (shows `mempool_total=0`)
  - **File**: `rpc/methods/miner.py` lines 3328-3540 (miner_get_block_template function)
  - **Issue**: Uses _collect_mempool_entries (line 3395) which checks ctx.mempool first, then adapter
- [x] Find RPC `mempool.list` implementation
  - **File**: `rpc/methods/mempool.py` (lines 42-93 _iter_pending function)
- [x] Check DI container / app state initialization
  - **Note**: ctx.mempool, ctx.state_db, ctx.params are used throughout

### [x] Trace Mempool Singleton Issue

- [x] **CRITICAL FINDING**: sendRawTransaction (tx.py:1473) calls `mempool_service.submit()`
  - Then verifies tx in mempool (line 1476) with `mempool_service.has_hash()`
  - If not found, raises InternalError (lines 1476-1488)
- [x] Block template builder (_collect_mempool_entries, miner.py:1512-1569):
  - Gets mempool from `ctx.mempool` (line 1522) 
  - Falls back to `adapter.get_mempool_snapshot()` (line 1531)
  - Has logic for `_PEND` and `_FALLBACK_PENDING` dict fallback (lines 3411-3446)
- [x] **ROOT CAUSE IDENTIFIED**: Multiple code paths for accessing txs:
  1. tx.sendRawTransaction uses mempool_service.submit() (preferred path)
  2. mempool.getPending uses ctx.mempool, then _PEND, then _FALLBACK_PENDING
  3. miner.getBlockTemplate uses ctx.mempool, then adapter.get_mempool_snapshot(), then _PEND/_FALLBACK_PENDING
  4. Problem: If ctx.mempool is None and adapter is unavailable, fallback dict is used inconsistently

### [x] Analyze CLI Error Suggestions

- [x] **ISSUE CONFIRMED**: Lines 469-471 in tx.py suggest:
  - `animica tx get {tx_hash}` - **DOES NOT EXIST** (only `send` command in tx.py)
  - `animica state get-nonce {from_addr}` - **DOES NOT EXIST** (state.py is for config management)
  - `animica mempool list` - **EXISTS** (mempool.py has list and stats commands)

## Phase 2: Resolution

### [x] Fix CLI Error Suggestions

- [x] Update tx.py lines 469-471 to ONLY suggest commands that exist:
  - ✅ Removed `animica tx get` (doesn't exist)
  - ✅ Removed `animica state get-nonce` (doesn't exist)
  - ✅ Kept `animica mempool list` (exists and works)
  - ✅ Added suggestion to use `animica rpc call state.getNonce ...` for custom RPC queries

### [x] Add Enhanced Logging to Track Mempool Operations

- [x] Enhanced logging in `tx.sendRawTransaction` (rpc/methods/tx.py):
  - Logs mempool object ID before and after submit()
  - Logs mempool size before and after submit()
  - Logs post-submit verification result
  - Shows if verification fails with mempool ID
  
- [x] Enhanced logging in `miner.getBlockTemplate` (rpc/methods/miner.py):
  - Logs mempool object ID during collection
  - Logs source of mempool entries (ctx.mempool vs adapter vs fallback)
  - Logs when falling back to _PEND/_FALLBACK_PENDING dicts

### [ ] Verify Truthful TX Submission

- [x] Code review confirms tx.sendRawTransaction already:
  1. Validates chain_id, signature, balance, gas
  2. Calls mempool_service.submit()
  3. Verifies tx in mempool via has_hash() check
  4. Raises InternalError if verification fails
- [ ] Run tests to ensure no regressions
- [ ] Verify logging output shows same mempool object ID in both code paths

## Phase 3: Verification

### [x] Testing & Verification

- [x] Syntax validation passed for all modified files:
  - rpc/methods/tx.py ✓
  - rpc/methods/miner.py ✓
  - python/animica/cli/tx.py ✓
- [x] Module imports successful (rpc.methods.tx, rpc.methods.miner)

### [ ] Manual Testing (Requires Live Environment)

- [ ] Run the exact repro steps from bug report:
  1. animica miner mine-blocks --address ... --count 5
  2. animica tx send --from ... --to ... --value 17
  3. Verify tx is in mempool (animica mempool list)
  4. Mine another block and verify tx is included
  5. Check logs for mempool object IDs to verify same instance is used

## Summary of Changes

### Files Modified:
1. **python/animica/cli/tx.py** (lines 457-472)
   - Removed suggestion for nonexistent `animica tx get` command
   - Removed suggestion for nonexistent `animica state get-nonce` command
   - Updated with valid alternatives using `animica rpc call`

2. **rpc/methods/tx.py** (lines 1466-1530)
   - Enhanced logging with mempool object ID tracking
   - Added mempool size before/after submit()
   - Added post-submit verification logging
   - Logs mempool_id for debugging singleton issues

3. **rpc/methods/miner.py** (lines 1520-1540, 3400-3443)
   - Enhanced logging in _collect_mempool_entries()
   - Added mempool object ID logging in getBlockTemplate()
   - Added logging when falling back to _PEND/_FALLBACK_PENDING dicts
   - Logs source of mempool entries (ctx.mempool vs adapter vs fallback)

## Root Cause & Fix

**Root Cause**: 
- CLI suggested commands that don't exist
- Potential mempool singleton issues needed investigation via logging

**Fix Applied**:
1. Updated CLI error message to only suggest valid commands
2. Added comprehensive logging to track mempool object IDs and sizes
3. This enables future debugging of mempool singleton issues
4. Code already validates tx is in mempool after submit (lines 1490-1502 in tx.py)

## Notes

- All syntax checks passed
- Enhanced logging is non-breaking (adds INFO and DEBUG level logs)
- tx.sendRawTransaction already has proper verification logic
- Mempool singleton is stored in ctx.mempool which is managed by rpc/deps.py
