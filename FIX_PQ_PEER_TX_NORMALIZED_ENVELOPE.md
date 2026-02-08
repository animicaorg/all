# Fix: PQ Signature Verification Failures in P2P Peer Transaction Import

## Issue
Transactions from peers were being rejected with PQ verification failures during `p2p.importPeerKnownTxs`:

```json
{
  "state": "invalid_final",
  "last_reason": "verify_failed:[-32012] Invalid post-quantum signature: verification failed",
  "scheme_id": 4098,
  "pubkey_len": 64,
  "sig_len": 7856
}
```

Despite the signature being valid (SPHINCS+ shake_128s with correct key and signature sizes), verification was failing.

## Root Cause

The issue was caused by the interaction between transaction normalization and body extraction:

### The Problem Flow

1. **Peer sends CBOR transaction:** `{"body": {...}, "sig": {...}}`
2. **`_decode_tx()` normalizes the envelope:** Creates `{"tx": <normalized_body>, "sigs": [...]}`
3. **`_decode_tx()` adds original body back (PROBLEMATIC):** 
   ```python
   # Lines 722-725 in rpc/methods/tx.py (BEFORE FIX)
   if isinstance(obj, dict):
       raw_body = obj.get("body")
       if isinstance(raw_body, dict):
           normalized_env.setdefault("body", raw_body)
   ```
   Result: `{"tx": <normalized>, "sigs": [...], "body": <original_unnormalized>}`

4. **`_extract_body()` checks `"body"` key FIRST (PROBLEMATIC):**
   ```python
   # Lines 198-199 in python/animica/tx/signing.py (BEFORE FIX)
   if isinstance(obj, Mapping) and "body" in obj and isinstance(obj["body"], Mapping):
       body = dict(obj["body"])  # ← Uses original unnormalized body!
   ```

5. **Verification fails:** The signature was created using the normalized body, but verification used the unnormalized body → mismatch → verification fails.

### Why This Causes Verification Failure

- Transaction normalization may change field representations (e.g., `""` → `b""` for data field)
- The signing preimage is computed from the normalized body
- If verification uses a different body (the unnormalized one), the preimage won't match
- PQ signature verification fails because the message being verified differs from what was signed

## Solution

### Changes Made

#### 1. `python/animica/tx/signing.py` (Line 194-209)

**Before:**
```python
def _extract_body(tx: Any) -> dict:
    obj = _as_dict(tx)

    # If it's already an envelope, respect it
    if isinstance(obj, Mapping) and "body" in obj and isinstance(obj["body"], Mapping):
        body = dict(obj["body"])
    elif isinstance(obj, Mapping) and "tx" in obj and isinstance(obj["tx"], Mapping):
        # Handle normalized envelope format {"tx": {...}, "sigs": [...]}
        body = dict(obj["tx"])
    ...
```

**After:**
```python
def _extract_body(tx: Any) -> dict:
    obj = _as_dict(tx)

    # Check normalized envelope format FIRST (canonical representation)
    if isinstance(obj, Mapping) and "tx" in obj and isinstance(obj["tx"], Mapping):
        # Handle normalized envelope format {"tx": {...}, "sigs": [...]}
        body = dict(obj["tx"])
    elif isinstance(obj, Mapping) and "body" in obj and isinstance(obj["body"], Mapping):
        # Legacy envelope format {"body": {...}, "sig": {...}}
        body = dict(obj["body"])
    ...
```

**Rationale:** Prioritize the normalized `"tx"` key over the legacy `"body"` key. The normalized format is canonical and should always be used for verification when present.

#### 2. `rpc/methods/tx.py` (Lines 722-725)

**Before:**
```python
    if isinstance(obj, dict):
        raw_body = obj.get("body")
        if isinstance(raw_body, dict):
            normalized_env.setdefault("body", raw_body)
```

**After:**
```python
    # Note: Do NOT add the original "body" key back to normalized_env.
    # The normalized "tx" key is canonical and should be used for verification.
    # Adding "body" back causes _extract_body() to use unnormalized data.
```

**Rationale:** Don't add the unnormalized body back after normalization. The normalized envelope should only contain the canonical `"tx"` key.

#### 3. `rpc/methods/tx.py` (Lines 751-753)

**Before:**
```python
    enriched_obj = dict(normalized_env)
    if isinstance(obj, dict) and "body" in obj and isinstance(obj.get("body"), dict):
        enriched_obj["body"] = obj["body"]
    enriched_obj["hash"] = tx_hash_hex
```

**After:**
```python
    enriched_obj = dict(normalized_env)
    # Note: Do NOT add original "body" back - use normalized "tx" key only
    enriched_obj["hash"] = tx_hash_hex
```

**Rationale:** Same as above - don't reintroduce the unnormalized body.

### Tests Added

Added `test_extract_body_prioritizes_normalized_tx_over_body()` and `test_peer_tx_verification_after_normalization()` in `python/animica/tests/test_pq_peer_tx_import.py`:

```python
def test_extract_body_prioritizes_normalized_tx_over_body() -> None:
    """Test that _extract_body prioritizes "tx" over "body" when both are present."""
    envelope_with_both = {
        "tx": normalized_body,  # Should use this
        "body": original_body,  # Should ignore this
        "sigs": []
    }
    extracted = _extract_body(envelope_with_both)
    assert extracted == normalized_body  # Verifies "tx" was used, not "body"
```

## Verification

### Manual Testing

```python
# Test 1: Envelope with only 'tx' key → PASS ✅
envelope_tx_only = {'tx': {'nonce': 1}, 'sigs': []}
result1 = _extract_body(envelope_tx_only)
assert result1['nonce'] == 1

# Test 2: Envelope with only 'body' key → PASS ✅
envelope_body_only = {'body': {'nonce': 2}, 'sig': {}}
result2 = _extract_body(envelope_body_only)
assert result2['nonce'] == 2

# Test 3: Envelope with BOTH keys (the critical fix) → PASS ✅
envelope_both = {
    'tx': {'nonce': 3, 'data': b'normalized'},    # Should use this
    'body': {'nonce': 99, 'data': b'original'},   # Should ignore this
    'sigs': []
}
result3 = _extract_body(envelope_both)
assert result3['nonce'] == 3  # Uses normalized 'tx', not 'body'
assert result3['data'] == b'normalized'
```

### Expected Outcome

After this fix, transactions from peers should be correctly verified using the normalized body, matching what was signed:

```json
{
  "success": true,
  "requested": N,
  "outcomes": {
    "admitted": [{"txid": "0x...", "state": "accepted_in_mempool", ...}],
    "rejected": [],
    ...
  }
}
```

## Security Analysis

- ✅ **No PQ verification bypass:** The fix does NOT disable or skip PQ verification
- ✅ **Deterministic:** All nodes compute the same normalized body and signing preimage
- ✅ **Consensus-safe:** The fix only corrects which body is used for verification; it doesn't change how transactions are hashed or validated
- ✅ **No secret leakage:** Debug logs only show fingerprints (hashes), not raw keys/signatures

## Impact

- **Fixes:** P2P peer transaction import with PQ signatures
- **Backwards compatible:** CLI transactions continue to work (they use `"body"` key and it's still supported when `"tx"` is not present)
- **No breaking changes:** The fix only affects the priority order when BOTH keys are present, which shouldn't happen in normal operation except during decoding/normalization

## Related Files

- `python/animica/tx/signing.py`: Transaction signing and body extraction
- `rpc/methods/tx.py`: RPC transaction decoding and verification
- `p2p/deps.py`: P2P transaction admission calling verification
- `core/utils/tx.py`: Transaction normalization
- `python/animica/tests/test_pq_peer_tx_import.py`: Test coverage

---

**Date:** 2026-02-08  
**Status:** ✅ Fixed and Tested  
**Commit:** 79ecb094
