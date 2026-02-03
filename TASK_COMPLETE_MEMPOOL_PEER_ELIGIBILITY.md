# Task Complete: Fix Mempool Not Including Peer Transactions

## Summary
Successfully fixed the issue where the mempool was not including transactions from peers even though peers reported having known transaction IDs.

## Problem
Users reported that `animica mempool list` showed peers with `known_txids` but the local mempool was empty, preventing peer transactions from being mined into blocks.

## Root Cause
The `request_missing_known()` method in `p2p/txrelay.py` was processing ALL peer states without checking peer eligibility (line 1675). All other peer processing loops in the same file properly check `self._peer_eligible()` before processing peers, but this one did not.

This caused transaction requests to be sent to ineligible peers (disconnected, duplicate connections, unsupported peers) where they would fail silently.

## Solution
Added a simple peer eligibility check (2 lines) at line 1679:
```python
# Skip ineligible peers (disconnected, duplicate connections, etc.)
if not self._peer_eligible(state.conn_id):
    continue
```

## Changes Made
1. **p2p/txrelay.py** - Added eligibility check (2 lines)
2. **p2p/tests/test_request_missing_known_eligibility.py** - 3 comprehensive test cases
3. **MEMPOOL_PEER_ELIGIBILITY_FIX.md** - Complete documentation
4. **verify_mempool_peer_eligibility_fix.py** - Verification script

## Testing
✅ **All tests passing (8/8)**
- 3 new tests for eligibility checking
- 5 existing tests (no regressions)

✅ **Verification script confirms fix works**

✅ **Code review complete**
- All feedback addressed

✅ **Security check passed**
- CodeQL: No issues detected

## Impact
- **Before:** Ineligible peers processed → requests fail → mempool empty
- **After:** Only eligible peers processed → requests succeed → transactions in mempool

## Verification
```bash
$ python3 verify_mempool_peer_eligibility_fix.py
✓ FIX VERIFIED: Only eligible peer was processed!
  - peer1 and peer2 were SKIPPED (ineligible)
  - peer3 was PROCESSED (eligible)
  - Transaction from peer3 was requested
  - NO requests sent to ineligible peers
```

## Commits
1. Initial plan
2. Fix: Add peer eligibility check in request_missing_known to skip ineligible peers
3. Add documentation for mempool peer eligibility fix
4. Add verification script for mempool peer eligibility fix
5. Address code review feedback: remove unnecessary pytest.main() and move sys import

## Result
✅ **Task Complete**

The mempool will now successfully fetch and include transactions from eligible peers, resolving the issue where `animica mempool list` showed empty even though peers had known transactions.

This minimal 2-line change makes `request_missing_known()` consistent with all other peer processing loops in the codebase, ensuring only eligible peers are processed for transaction requests.
