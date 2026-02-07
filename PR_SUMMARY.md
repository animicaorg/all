# PR Summary: Fix Transaction Fetching from Peers

## Problem
Transactions known by peers were not being fetched and added to the local mempool. The system would report "Peers know about N transaction(s)" and attempt to fetch them, but the mempool would remain empty.

## Root Cause
The `on_tx_notfound` handler in `p2p/txrelay.py` was clearing the transaction ID from **ALL** peers' `known_txids` sets when any single peer responded with TX_NOTFOUND. This prevented retry attempts to other peers who actually had the transaction.

## Solution
Modified `on_tx_notfound` to:
1. Clear txid only from the peer that responded with NOTFOUND
2. Check for other peers who still have the txid
3. Automatically retry request with another eligible peer if available
4. Only mark as permanently rejected if no other peers have it

## Changes
- **Core Fix**: Modified `p2p/txrelay.py:on_tx_notfound()` (112 lines changed)
- **Tests**: Added 3 new unit tests + 2 integration tests
- **Documentation**: Added fix summary and security analysis

## Test Results
- ✓ All 3 new tests pass
- ✓ All 13 existing txrelay tests pass (except 1 pre-existing failure unrelated to this change)
- ✓ Manual integration tests validate the fix for the exact bug report scenario

## Security
- **Risk Level**: LOW
- No new vulnerabilities introduced
- All existing security properties maintained (rate limiting, retry limits, reject cache)
- Actually IMPROVES resilience against peer failures and attacks

## Deployment
- No breaking changes
- Backward compatible
- No configuration changes needed
- Should improve transaction propagation immediately

## Files Changed
```
FIX_SUMMARY_TX_NOTFOUND_RETRY.md         | 113 +++++++++++++++
SECURITY_SUMMARY_TX_NOTFOUND_RETRY.md    | 116 ++++++++++++++++
p2p/tests/test_txrelay_notfound_retry.py | 299 +++++++++++++++++++++++++++++++
p2p/txrelay.py                           | 137 +++++++++++++----
test_bug_report_scenario.py              | 160 ++++++++++++++++++
test_notfound_retry_manual.py            | 173 ++++++++++++++++++++
6 files changed, 973 insertions(+), 25 deletions(-)
```

## Verification
To verify the fix works:
1. Run `python3 test_bug_report_scenario.py` - should show "PASS ✓"
2. Run `python3 -m pytest p2p/tests/test_txrelay_notfound_retry.py -v` - should show 3/3 tests passed
3. In production, monitor logs for "TX_NOTFOUND_RETRY_OTHER_PEER" messages showing successful retries
