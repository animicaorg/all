# Mempool Transaction Import Timeout Fix - Implementation Guide

## Overview

This document describes the fix for transactions not appearing in the mempool despite being requested from peers.

## Problem Statement

Users reported seeing:
```
Auto-imported peer transactions: requested=2, newly_visible=0 (timed out after 0.5s)
Mempool is empty (no pending transactions)
```

**Symptoms:**
- Peers advertise knowing about transactions (shown in `known_txids`)
- CLI requests transactions from peers (`requested=2`)
- But transactions never appear in local mempool (`newly_visible=0`)
- Timeout occurs after 0.5s

## Root Cause

The 0.5s polling timeout was insufficient for real-world network conditions:

1. **Network Latency**: Production networks have 100-300ms roundtrip time
2. **Processing Overhead**: TX_DATA validation (SHA3-256, signature checks) takes time
3. **Mempool Admission**: Nonce checks, balance verification, fee validation add delay
4. **P2P Propagation**: Transaction data may not be fully synced between peers

## Solution

### 1. Increased Timeout (0.5s → 2.0s)

**Old polling schedule:**
```python
delays = [0.05, 0.1, 0.15, 0.2]  # 4 polls, 0.5s total
```

**New polling schedule:**
```python
delays = [0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 0.7]  # 7 polls, 2.0s total
```

**Coverage:**
| Scenario | Latency | Old Result | New Result |
|----------|---------|------------|------------|
| Fast LAN | 50-200ms | ✓ Success | ✓ Success |
| Internet | 250ms | ✓ Success | ✓ Success |
| Congested | 800ms | ✗ Timeout | ✓ Success |
| Processing Delay | 1.5s | ✗ Timeout | ✓ Success |
| TX_NOTFOUND | Never | ✗ Timeout | ✗ Timeout* |

*Now with better diagnostics

### 2. Enhanced Diagnostics

**Old timeout message:**
```
Auto-imported peer transactions: requested=2, newly_visible=0 (timed out after 0.5s)
```

**New timeout message:**
```
Auto-imported peer transactions: requested=2, newly_visible=0 (timed out after 2.0s)
Note: Transactions may have been:
  • Rejected during validation (hash mismatch, invalid signature)
  • Failed mempool admission (insufficient balance, nonce conflict, low fee)
  • Not available on peers (responded with TX_NOTFOUND)
Check node logs for: TX_DATA_ADMIT_RESULT, TX_REJECTED, TX_NOTFOUND
```

## Implementation Details

### Files Changed

1. **`python/animica/cli/mempool.py`** (Lines 291-336)
   - Increased polling delays from 4 to 7 iterations
   - Added detailed timeout diagnostic messages
   - Added comments explaining the timeout rationale

2. **`python/animica/cli/tests/test_mempool_cli.py`**
   - Added `time.sleep` monkeypatch to prevent test delays
   - Tests run instantly while still verifying logic

3. **`test_mempool_import_timeout_fix.py`** (New)
   - Demonstration script showing fix effectiveness
   - Simulates various network latency scenarios

## Troubleshooting

If transactions still don't appear after 2.0s, check node logs for these patterns:

### 1. TX_NOTFOUND

**Log pattern:**
```
TX_NOTFOUND peer=0xabc123 hash=0x9e55fb...
```

**Meaning:** Peer doesn't have the transaction data, only the txid

**Possible causes:**
- Transaction was evicted from peer's mempool
- Peer received INV but never fetched TX_DATA
- Peer's mempool was cleared/reset

**Solution:**
- Wait for transaction to propagate to more peers
- Check if original sender is still connected
- Try broadcasting transaction again

### 2. TX_REJECTED

**Log pattern:**
```
TX_REJECTED hash=0x9e55fb... reason=hash_mismatch
```

**Meaning:** Transaction failed validation

**Common reasons:**
- `hash_mismatch`: TX_DATA doesn't match advertised txid
- `oversize`: Transaction exceeds MAX_TX_BYTES limit
- `invalid_signature`: Signature verification failed

**Solution:**
- Check transaction formatting
- Verify signature with correct private key
- Ensure transaction size is within limits

### 3. TX_DATA_ADMIT_RESULT (accepted: false)

**Log pattern:**
```
TX_DATA_ADMIT_RESULT hash=0x9e55fb... accepted=false reason=insufficient_balance
```

**Meaning:** Transaction failed mempool admission

**Common reasons:**
- `insufficient_balance`: Sender doesn't have enough balance
- `nonce_conflict`: Nonce already used or out of sequence
- `fee_too_low`: Transaction fee below minimum threshold
- `mempool_full`: Mempool size or count limit reached

**Solution:**
- Check sender's balance with `animica account balance <address>`
- Verify nonce with `animica account nonce <address>`
- Increase transaction fee
- Wait for mempool to have space

### 4. Timeout After 2.0s

**Meaning:** None of the above errors, but transaction still didn't arrive

**Possible causes:**
- Very slow network (>2s roundtrip)
- Node is heavily loaded
- P2P connections are unstable

**Solution:**
- Check node CPU/memory usage
- Verify P2P connectivity: `animica p2p peers`
- Try increasing timeout manually (future enhancement)
- Consider node restart if persistent

## Testing

### Automated Tests

Run the test suite:
```bash
pytest python/animica/cli/tests/test_mempool_cli.py -v
```

### Demonstration

Run the demonstration script:
```bash
python3 test_mempool_import_timeout_fix.py
```

This shows how the fix handles different latency scenarios.

### Manual Testing

1. Start a node with P2P enabled
2. Connect to other nodes
3. Send a transaction from another node
4. Run `animica mempool list` on your node
5. Verify transaction appears within 2.0s

**Expected output:**
```
Peer-known txids (sample):
  peer=0x180497f543 known_txids=1 sample=[0x9e55fb...]
Auto-imported peer transactions: requested=1, newly_visible=1
Pending transactions (1):
  1. 0x9e55fb... nonce=5 status=pending
```

## Performance Impact

**CPU/Memory:** Negligible
- Only 3 additional RPC calls (7 total vs 4)
- Each call returns quickly (mempool is in-memory)
- No blocking or heavy computation

**Latency:**
- **Fast networks:** No change (50-200ms, early exit)
- **Medium networks:** No change (300ms, covered by old timeout)
- **Slow networks:** **Improved** (now succeeds instead of timeout)

**User Experience:**
- Much clearer error messages
- Actionable guidance for debugging
- Higher success rate for transaction imports

## Future Enhancements

Potential improvements for consideration:

1. **Configurable Timeout**
   ```bash
   ANIMICA_MEMPOOL_IMPORT_TIMEOUT=5.0 animica mempool list
   ```

2. **RPC Method for Recent Failures**
   ```bash
   animica rpc call p2p.getRecentTxFailures
   # Returns: [{"hash": "0x...", "reason": "notfound", "peer": "0x..."}]
   ```

3. **Automatic Retry on TX_NOTFOUND**
   - Currently handled by `on_tx_notfound` in txrelay.py
   - Could be exposed to CLI for manual retry

4. **Progress Indicator**
   ```
   Fetching transactions from peers... [Poll 3/7]
   ```

## Related Files

- **Implementation:** `python/animica/cli/mempool.py`
- **Tests:** `python/animica/cli/tests/test_mempool_cli.py`
- **Demonstration:** `test_mempool_import_timeout_fix.py`
- **TX Relay Logic:** `p2p/txrelay.py`
- **RPC Methods:** `rpc/methods/p2p.py`

## Related Documentation

- `FIX_MEMPOOL_TRANSACTION_IMPORT_TIMING.md` - Original 0.5s fix
- `FIX_MEMPOOL_TRANSACTION_IMPORT_VISUAL.md` - Visual explanation
- `TX_PROPAGATION_ARCHITECTURE.md` - Overall design
- `MEMPOOL_SYNC_MISSING_FETCH_FIX.md` - Related mempool fixes

## Summary

This fix increases the mempool transaction import timeout from 0.5s to 2.0s, solving the issue where transactions were requested but didn't appear in time. The enhanced diagnostics help users understand why transactions might fail to import, making the system more debuggable and user-friendly.

**Status:** ✓ Ready for deployment
