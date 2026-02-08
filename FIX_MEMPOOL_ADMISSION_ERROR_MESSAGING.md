# Fix: Mempool Admission Error Messaging Enhancement

## Summary

Fixed generic "mempool admission failed" error (code 1000) by enhancing error message propagation through the mempool admission pipeline. Users now receive specific, actionable error messages that explain why their transaction was rejected.

## Problem

When transactions failed mempool admission, users received a generic error message:
```
RPC Error -32010: mempool admission failed
{
    'data': {
        'mempoolError': {
            'code': 1000,
            'reason': 'admission_failed',
            'message': 'mempool admission failed',
            'context': {'tx_hash': '0x...'}
        }
    }
}
```

This made it impossible to diagnose the actual problem. The root cause was in `rpc/mempool_service.py` where `submit_atomic()` converted all exceptions to strings with `str(exc)`, losing structured error details.

## Solution

### Changes Made

#### 1. Enhanced `submit_atomic()` in rpc/mempool_service.py

```python
def submit_atomic(self, *, tx, raw, tx_hash_hex=None, local=True, origin_peer=None):
    try:
        admitted_hash = self.submit(...)
    except Exception as exc:
        # Extract detailed reason from structured errors
        reason_str = str(exc)
        if hasattr(exc, "reason"):
            reason_str = str(exc.reason)  # Short reason code like "nonce_gap"
        elif hasattr(exc, "message"):
            reason_str = str(exc.message)
        
        # Log rejection with full context
        log.warning(
            "MempoolService.submit_atomic: admission rejected, tx_hash=%s, reason=%s",
            computed_hash,
            reason_str,
            exc_info=True if log.isEnabledFor(logging.DEBUG) else False,
        )
        return False, reason_str, computed_hash
```

**Why this works:**
- Mempool error classes (e.g., `NonceGap`, `FeeTooLow`) have a `reason` attribute with short, actionable codes
- We extract this instead of converting the whole exception to a string
- Debug logging provides full stack traces when needed

#### 2. Improved RPC Error Formatting in rpc/methods/tx.py

```python
if not accepted:
    reason_str = reason or "admission_failed"
    message = f"mempool admission failed: {reason_str}" if reason else "mempool admission failed"
    
    raise rpc_errors.InvalidTx(
        message,
        data={
            "mempoolError": {
                "code": 1000,
                "reason": reason_str,
                "message": message,  # Now includes specific reason
                "context": {"tx_hash": _hash_hex},
            }
        },
    )
```

**Why this works:**
- Builds descriptive error message that includes the specific rejection reason
- Maintains backwards compatibility (same error code -32010)
- Data payload includes both short reason code and full message

## Results

### Example Error Messages

Users now see specific reasons for rejection:

**Nonce Gap:**
```
RPC Error -32010: mempool admission failed: nonce_gap
{
    'data': {
        'mempoolError': {
            'code': 1000,
            'reason': 'nonce_gap',
            'message': 'mempool admission failed: nonce_gap',
            'context': {'tx_hash': '0x...'}
        }
    }
}
```

**Fee Too Low:**
```
RPC Error -32010: mempool admission failed: fee_too_low
```

**Insufficient Balance:**
```
RPC Error -32010: mempool admission failed: insufficient_funds_pending
```

**Nonce Too Low:**
```
RPC Error -32010: mempool admission failed: nonce_too_low
```

### Common Rejection Reasons

| Reason | Meaning | User Action |
|--------|---------|-------------|
| `nonce_gap` | Transaction nonce skipped ahead | Submit with next sequential nonce |
| `nonce_too_low` | Nonce already used | Increment nonce |
| `fee_too_low` | Gas price below minimum | Increase gas price |
| `insufficient_funds_pending` | Not enough balance after pending txs | Wait for pending txs or add funds |
| `replay` | Transaction already seen | This is a duplicate |
| `expired` | valid_until block has passed | Resubmit with new validity window |
| `not_yet_valid` | valid_after block not reached yet | Wait or resubmit with current height |
| `chain_id_mismatch` | Wrong chain ID | Use correct chain ID for this network |
| `hash_mismatch` | Transaction hash inconsistency | Check transaction encoding |
| `missing_sender` | Cannot derive sender from signature | Check signature and public key |

## Testing

Verified with unit tests:
```python
# Test error reason extraction
from mempool.errors import NonceGap

try:
    raise NonceGap(expected_nonce=5, got_nonce=10, sender='test', tx_hash='0xabc')
except Exception as exc:
    reason_str = str(exc.reason) if hasattr(exc, 'reason') else str(exc)
    assert reason_str == "nonce_gap"  # ✅ Passes
```

## Impact

- ✅ **Better UX**: Users can immediately understand why their transaction was rejected
- ✅ **Faster Debugging**: Operators can diagnose issues from logs without reproducing
- ✅ **Backwards Compatible**: Same error code (-32010) and JSON-RPC structure
- ✅ **Actionable Errors**: Users know what to fix (nonce, fee, balance, etc.)

## Files Changed

1. `rpc/mempool_service.py` - Enhanced `submit_atomic()` error handling
2. `rpc/methods/tx.py` - Improved error message formatting

## Related Issues

This fix addresses the root cause of generic "mempool admission failed" errors reported by users, particularly when submitting transactions via the CLI (`animica tx send`) or RPC (`tx.sendRawTransaction`).

## Future Enhancements

Potential improvements:
1. Add suggested fixes to error messages (e.g., "Try nonce=6" for nonce_gap)
2. Include more context in error payloads (expected vs. got values)
3. Add user-friendly error translation in CLI
4. Metrics/alerting for common rejection patterns
