# Fix: Invalid Post-Quantum Signature Verification

## Problem

Users experienced signature verification failures when sending transactions via CLI:

```bash
animica tx send --from anim1zqpq5p7... --to anim1zqqjt3258... --value 10

RPC Error -32012: Invalid post-quantum signature: verification failed
{
    'scheme_id': 4098,  # SPHINCS+ SHAKE-128s
    'pubkey_len': 64,
    'sig_len': 7856,
    'prehash': 'sha3-512',
    'domain': 'tx',
    'chain_id': 1
}
```

## Root Cause

The issue occurred due to transaction body normalization in the RPC:

1. **CLI creates transaction** with original format:
   ```python
   {
       "chainId": 1,
       "from": "anim1...",  # String address
       "to": "anim1...",    # String address
       "value": 10000000000,
       "gasLimit": 21000,
       "maxFee": 1000000,
       "data": b""
   }
   ```

2. **CLI signs** this original format ✓

3. **CLI local verification** succeeds ✓

4. **RPC receives and normalizes** to canonical format:
   ```python
   {
       "v": 1,
       "chainId": 1,
       "from": bytes(32),  # Byte address (padded/hashed)
       "gas": {"price": 1000000, "limit": 21000},
       "payload": {
           "t": 0,
           "v": {"to": bytes(32), "amount": 10000000000, "data": b""}
       }
   }
   ```

5. **RPC verification** tried to verify signature against **normalized** body ✗

**Result**: Signature verification failed because the signature was created over the **original** body, not the **normalized** body.

## Solution

The fix preserves both the original and normalized body formats:

### Changes Made

#### 1. `python/animica/tx/signing.py` - Reorder body extraction

**Before:**
```python
def _extract_body(tx: Any) -> dict:
    obj = _as_dict(tx)
    
    # Check normalized envelope format FIRST (canonical representation)
    if isinstance(obj, Mapping) and "tx" in obj and isinstance(obj["tx"], Mapping):
        body = dict(obj["tx"])  # ← Uses normalized body (WRONG for verification!)
    elif isinstance(obj, Mapping) and "body" in obj and isinstance(obj["body"], Mapping):
        body = dict(obj["body"])
```

**After:**
```python
def _extract_body(tx: Any) -> dict:
    obj = _as_dict(tx)
    
    # Check for original "body" key FIRST (for signature verification)
    # The "body" key contains the original transaction as signed by the CLI,
    # while "tx" contains the normalized canonical format used for execution.
    if isinstance(obj, Mapping) and "body" in obj and isinstance(obj["body"], Mapping):
        body = dict(obj["body"])  # ← Uses original body (CORRECT!)
    elif isinstance(obj, Mapping) and "tx" in obj and isinstance(obj["tx"], Mapping):
        body = dict(obj["tx"])    # ← Fallback to normalized
```

**Impact:** Signature verification now uses the original body format that was actually signed.

#### 2. `rpc/methods/tx.py` - Preserve original body

**Before:**
```python
# Note: Do NOT add the original "body" key back to normalized_env.
# The normalized "tx" key is canonical and should be used for verification.
raw_canonical = normalized_env.get("raw") or raw
```

**After:**
```python
# IMPORTANT: Preserve the original "body" key for signature verification.
# The signature was created over the original body format from the CLI,
# NOT the normalized "tx" format.
if "body" in obj and isinstance(obj.get("body"), dict):
    normalized_env["body"] = obj["body"]

raw_canonical = normalized_env.get("raw") or raw
```

**Impact:** The RPC envelope now contains both:
- `"body"`: Original format (for signature verification)
- `"tx"`: Normalized format (for execution and canonical hashing)

## Testing

### New Regression Tests

Created `python/animica/tests/test_pq_signature_body_normalization.py` with 3 comprehensive tests:

1. **`test_pq_signature_survives_body_normalization()`**
   - Full CLI-to-RPC flow simulation
   - Validates signature verification works after normalization
   - **Result:** ✅ PASS

2. **`test_extract_body_prefers_original_over_normalized()`**
   - Validates `_extract_body()` prioritization logic
   - Ensures original body is used when both formats present
   - **Result:** ✅ PASS

3. **`test_extract_body_falls_back_to_normalized_when_no_original()`**
   - Validates fallback behavior for internally-generated transactions
   - **Result:** ✅ PASS

### Existing Tests

Verified backward compatibility:
- `test_pq_sign_and_verify_roundtrip_shared_module()` ✅ PASS
- `test_pq_verify_rejects_from_address_pubkey_mismatch()` ✅ PASS

## Verification

### Before Fix
```
CLI → Sign transaction → Local verify ✓ → Send to RPC → RPC verify ✗
                                                          ↓
                                                      FAIL: -32012
```

### After Fix
```
CLI → Sign transaction → Local verify ✓ → Send to RPC → RPC verify ✓
      (original body)                      (preserves    (uses original
                                            original)     for verify)
```

## Impact

- ✅ Fixes signature verification for all PQ algorithms (Dilithium3, SPHINCS+)
- ✅ Maintains backward compatibility
- ✅ No breaking changes to existing code
- ✅ Properly separates concerns:
  - Original body → Signature verification
  - Normalized body → Execution & canonical hashing

## Related Issues

This fix addresses a regression introduced in the previous PR #1532 which added support for normalized transaction envelopes but inadvertently broke signature verification by using the normalized body format.

Previous fix attempted to handle both `{"body": ...}` and `{"tx": ...}` formats but checked normalized format first, causing verification to use the wrong body.

## Files Changed

1. `python/animica/tx/signing.py` (10 lines modified)
2. `rpc/methods/tx.py` (12 lines modified)
3. `python/animica/tests/test_pq_signature_body_normalization.py` (179 lines added)

**Total:** 201 lines changed
