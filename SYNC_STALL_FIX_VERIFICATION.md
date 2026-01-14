# Sync Stall Fix Verification Guide

## Problem

When a node's sync is stuck with `headers == blocks` and the network has higher blocks available:

**Symptoms:**
- Local height: 11186
- Network best height: 11204
- In-flight headers: 0
- In-flight blocks: 0
- Queued blocks: 0
- Sync phase: SYNCING (but not progressing)

**Root Cause:**
1. Sync loop detects headers == blocks stall condition
2. Clears `_sync_last_header_error` state
3. BUT: Individual peers remain in backoff due to previous "headers_empty" or "peer_behind" responses
4. Result: All peers are ineligible for header requests
5. No headers requested → No progress

## Solution

When stall conditions are detected (headers==blocks OR behind_network), clear peer backoffs:
- Clear "headers_empty" backoff reason
- Clear "peer_behind" backoff reason
- Log number of peers unblocked

This allows peers to be re-evaluated and retried for header requests.

## Changes Made

### File: `p2p/node/p2p_service.py`

**Change 1: Headers==Blocks Stall Detection (lines 9464-9499)**
```python
# Before: Only cleared error states
if self._sync_last_header_error in ("at_tip", "invalid_headers"):
    # Clear error
    self._sync_last_header_error = None
    ...

# After: Clear error states AND peer backoffs
if self._sync_last_header_error in ("at_tip", "invalid_headers", "headers_empty"):
    # Clear error
    self._sync_last_header_error = None
    ...
# NEW: Clear peer backoffs
cleared_backoff = self._clear_sync_backoff_reason("headers_empty")
cleared_backoff += self._clear_sync_backoff_reason("peer_behind")
if cleared_backoff > 0:
    log.info("Cleared peer backoffs to retry sync", ...)
```

**Change 2: Behind Network Stall Detection (lines 9521-9545)**
```python
# Before: Only ensured block queue
self._sync_block_stalled_reason = "blocks stalled"
self._ensure_block_queue()
self._sync_kick(...)

# After: Clear peer backoffs before ensuring queue
self._sync_block_stalled_reason = "blocks stalled"
# NEW: Clear peer backoffs
cleared = self._clear_sync_backoff_reason("headers_empty")
cleared += self._clear_sync_backoff_reason("peer_behind")
if cleared > 0:
    log.info("Cleared peer backoffs for behind-network recovery", ...)
self._ensure_block_queue()
self._sync_kick(...)
```

## Expected Behavior After Fix

When sync stalls are detected, logs should show:

```
Sync stalled: headers == blocks with no progress
  height=11186, peers=15, network_best_height=11204

Clearing header error state to retry sync
  error=at_tip

Cleared peer backoffs to retry sync
  cleared_peers=12

Sync peer eligibility update
  peer_key=..., eligible=true, reason=eligible
```

Then sync should resume and progress to network height automatically.
