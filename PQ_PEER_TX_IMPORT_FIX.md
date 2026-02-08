# Fix for PQ Signature Verification Failures in P2P Peer Transaction Import

## Problem

The node was rejecting peer-advertised transactions during `p2p.importPeerKnownTxs` with PQ verification failures:
```
state=invalid_final
last_reason="verify_failed:[-32012] Invalid post-quantum signature: verification failed"
details: scheme_id=4098, pubkey_len=64, sig_len=7856, prehash=sha3-512, domain='tx', chain_id=1
```

## Root Cause

The issue had two components:

### 1. Incorrect Transaction Object Passed to Verification

**Location:** `rpc/methods/tx.py:607`

**Before (Broken):**
```python
verify_result = _pq_verify_tx(
    obj.get("body", obj),  # ← Extracted only body if present
    sig_env,
    pub,
    ctx,
)
```

**Problem:** The CLI creates transactions as `{"body": {...}, "sig": {...}}`, and after CBOR decoding and normalization, the envelope is transformed. The code was extracting only the body portion before passing to verification, but the `_extract_body()` function in `signing.py` expects the full transaction object to properly extract the body.

**After (Fixed):**
```python
verify_result = _pq_verify_tx(
    obj,  # ← Pass full transaction object
    sig_env,
    pub,
    ctx,
)
```

### 2. Normalized Envelope Format Not Handled

**Location:** `python/animica/tx/signing.py:194`

After transaction normalization by `core/utils/tx.normalize_tx_envelope()`, envelopes are transformed from:
- CLI format: `{"body": {...}, "sig": {...}}`
- To normalized: `{"tx": {...}, "sigs": [...]}`

The `_extract_body()` function only checked for "body" key but not "tx" key, causing it to fail on normalized envelopes.

**Before (Broken):**
```python
def _extract_body(tx: Any) -> dict:
    obj = _as_dict(tx)
    
    if isinstance(obj, Mapping) and "body" in obj and isinstance(obj["body"], Mapping):
        body = dict(obj["body"])
    else:
        # Fell back to _canonical_body which failed on normalized envelopes
        body = _canonical_body(obj)
    ...
```

**After (Fixed):**
```python
def _extract_body(tx: Any) -> dict:
    obj = _as_dict(tx)
    
    if isinstance(obj, Mapping) and "body" in obj and isinstance(obj["body"], Mapping):
        body = dict(obj["body"])
    elif isinstance(obj, Mapping) and "tx" in obj and isinstance(obj["tx"], Mapping):
        # Handle normalized envelope format
        body = dict(obj["tx"])
    else:
        body = _canonical_body(obj)
    ...
```

## Solution

### Changes Made

1. **rpc/methods/tx.py (line 607):** 
   - Changed to pass full `obj` to `pq_verify_tx()` instead of `obj.get("body", obj)`
   - Let `_extract_body()` handle envelope extraction properly

2. **rpc/methods/tx.py (lines 624-648):**
   - Enhanced debug instrumentation when `ANIMICA_PQ_VERIFY_DEBUG=1`
   - Logs pub/sig fingerprints (no secret leakage), chain context, preimage prefix, etc.

3. **python/animica/tx/signing.py (lines 198-200):**
   - Added support for normalized envelope format with "tx" key
   - Now handles both `{"body": {...}}` and `{"tx": {...}}` formats

4. **Tests Added:**
   - `python/animica/tests/test_scheme_id_mapping.py`: 5 tests verifying scheme_id 4097/4098 mappings
   - `python/animica/tests/test_pq_peer_tx_import.py`: 3 regression tests for signing/verification consistency

## Verification

### Automated Tests

All tests pass:

```bash
# Scheme ID mapping tests
python -m pytest python/animica/tests/test_scheme_id_mapping.py -v
# Result: 5 passed

# PQ peer tx import tests
python -m pytest python/animica/tests/test_pq_peer_tx_import.py -v
# Result: 3 passed
```

### Manual Verification (2-Node Setup)

**Prerequisites:**
- 2 running nodes (Node A and Node B)
- SPHINCS+ wallet on Node A (scheme_id 4098)
- Enable debug logging: `export ANIMICA_PQ_VERIFY_DEBUG=1`

**Steps:**

1. **On Node A:** Create and broadcast a transaction using SPHINCS+ wallet:
   ```bash
   animica tx send --from <sphincs_wallet> --to <dest> --amount 1000
   ```

2. **On Node B:** Check peer-known transactions:
   ```bash
   animica mempool list
   ```
   Should show txids advertised by peers but not in local mempool.

3. **On Node B:** Import peer transactions:
   ```bash
   animica rpc call p2p.importPeerKnownTxs
   ```

4. **Expected Results:**
   - `requested_count` > 0
   - `bytes_received_count` > 0  
   - `validated_ok_count` > 0
   - `admitted` array contains the txid
   - No entries with `state=invalid_final` and `verify_failed` reason

5. **On Node B:** Verify transaction is now in mempool:
   ```bash
   animica mempool list
   ```
   Should show the imported transaction as pending.

### Debug Output

With `ANIMICA_PQ_VERIFY_DEBUG=1`, you'll see detailed logs:

```
PQ VERIFY DEBUG ok=True alg=sphincs_shake_128s(id=4098) used=animica.tx.v1.preimage 
pub_len=64 pub_fp=0xabc123... sig_len=7856 sig_fp=0xdef456... 
chain_id=1 genesis_len=32 network=devnet domain=tx prehash=sha3-512 
sign_hash=0x789... preimage_prefix=0xa2...
```

## Security Considerations

- **No PQ verification bypass:** The fix does NOT disable or skip PQ verification
- **Deterministic:** All nodes compute the same signing preimage
- **Consensus-safe:** The fix only corrects the preimage extraction logic
- **No secret leakage:** Debug logs only show fingerprints (hashes), not raw keys/signatures

## Scheme ID Validation

The fix includes comprehensive tests to ensure:
- `scheme_id 4097 (0x1001)` = dilithium3 (pubkey=1952 bytes, sig=3293 bytes)
- `scheme_id 4098 (0x1002)` = sphincs_shake_128s (pubkey=64 bytes, sig=7856 bytes)
- No off-by-one errors in algorithm mapping
- Consistent reverse lookup via `ALG_NAME[4098]` → `"sphincs_shake_128s"`

## Related Files

- `rpc/methods/tx.py`: Transaction RPC methods and PQ verification
- `python/animica/tx/signing.py`: Canonical signing preimage computation
- `python/animica/cli/tx.py`: CLI transaction creation (uses signing module)
- `p2p/deps.py`: P2P transaction admission (calls rpc verification)
- `core/utils/tx.py`: Transaction envelope normalization
- `pq/py/registry.py`: Algorithm ID mappings and metadata

## Rollback Plan

If issues arise:
1. Revert commits `c7af387e` and `a0761f88`
2. Restart nodes
3. Clear any cached `invalid_final` states with verification errors

## Future Improvements

1. Add integration test that spawns 2 nodes and verifies peer tx import end-to-end
2. Consider adding a CLI command to clear cached validation states: `animica mempool clear-validation-cache --reason pq_verify`
3. Add metrics/monitoring for PQ verification failure rates

---

**Date:** 2026-02-08  
**PR:** animicaorg/all#<PR_NUMBER>  
**Status:** ✅ Fixed and Tested
