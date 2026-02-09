# Root Cause Analysis: TypeError in Transaction Admission

## Problem Statement

The original system failed with:
```
RPC Error -32010: mempool admission failed: internal_error
error_class: TypeError
```

Users experienced:
1. `animica tx send ...` failing with opaque TypeError
2. Peer-advertised transactions not importing
3. Generic error codes (1000) without actionable context

## Root Cause Analysis

### 1. **Untyped Dict Blobs Crossing Boundaries**

**Old System**:
```python
# In rpc/methods/tx.py
raw_dict = cbor_loads(raw_bytes)  # dict with unknown structure
# Later...
tx_hash = raw_dict["hash"]  # TypeError if "hash" doesn't exist or wrong type
sender = raw_dict.get("from")  # might be None, int, bytes, anything
```

**Problem**: No validation at deserialization. Type errors surface deep in the call stack.

**New System**:
```python
# In coretx/canonical.py
envelope = decode_tx_envelope(raw_bytes)  # Returns TxEnvelope or raises ValueError
# envelope.body.from_addr is guaranteed to be bytes of length 32
# envelope.txid is guaranteed to be TxId with 32-byte hash
```

**Fix**: Strict dataclass validation in `__post_init__`, failing fast with clear errors.

---

### 2. **Exception-Throwing Verification**

**Old System**:
```python
# In mempool/pool.py or rpc/methods/tx.py
def verify_signature(tx):
    scheme = get_scheme(tx["scheme_id"])  # KeyError if missing
    pubkey = tx["pubkey"]  # TypeError if wrong type
    sig = tx["signature"]
    # Direct call to PQ library
    return pq_verify(pubkey, sig, message)  # Raises if inputs malformed
```

**Problem**: Exceptions escape through multiple layers, get caught as generic "internal_error" at RPC boundary.

**New System**:
```python
# In coretx/signing.py
def verify_tx(envelope: TxEnvelope) -> Optional[TxReject]:
    """Never raises. Returns None if valid, TxReject if invalid."""
    verify_result = verify_tx_signature(envelope)
    if not verify_result.ok:
        return reject(
            RejectReason.invalid_signature,
            message=f"Signature verification failed: {verify_result.reason}",
            hint="Check that the transaction was signed with the correct key",
            context={...},
            error_class=verify_result.diagnostics.get("error_class"),
        )
    return None
```

**Fix**: All verification returns typed results. No exceptions.

---

### 3. **Unguarded Admission Path**

**Old System**:
```python
# In mempool/pool.py
def admit_tx(self, raw_bytes):
    tx = decode(raw_bytes)  # May raise DecodeError
    verify(tx)  # May raise VerifyError
    check_nonce(tx)  # May raise NonceError
    self.storage.add(tx)  # May raise StorageError
    return tx_id
```

**Problem**: Any exception bubbles up. RPC layer catches it as generic Exception, logs error_class, returns -32010 without context.

**New System**:
```python
# In mempool2/admission.py
def admit_tx(...) -> tuple[bool, Optional[TxReject]]:
    """NEVER raises exceptions. Always returns (success, rejection)."""
    try:
        # 1. Decode
        # 2. Verify
        # 3. Policy checks
        # 4. Storage
        return (True, None)
    except Exception as e:
        log.error(...)
        return (False, reject(
            RejectReason.internal_error,
            message=f"Unexpected error: {type(e).__name__}",
            hint="Check node logs for details",
            context={"error_class": type(e).__name__},
            error_class=type(e).__name__,
        ))
```

**Fix**: Top-level try/except converts any exception to typed TxReject with error_class.

---

### 4. **Opaque Error Codes**

**Old System**:
```python
# In mempool/errors.py
ADMISSION = 1000  # Generic code for all admission failures
```

**Problem**: Client sees `code: 1000` for signature failure, nonce gap, fee too low, internal error - no way to distinguish.

**New System**:
```python
# In coretx/errors.py
REJECT_CODE = {
    RejectReason.invalid_signature: 2001,
    RejectReason.chain_id_mismatch: 2200,
    RejectReason.nonce_gap: 2303,
    RejectReason.fee_too_low: 2400,
    RejectReason.internal_error: 2999,
}
```

**Fix**: Stable, granular error codes. Each rejection reason has unique code.

---

### 5. **P2P Import Failures**

**Old System**:
```python
# Peer announces txid
# Node fetches tx bytes
# Tries to admit via same path as RPC
# TypeError in admission → entire P2P message handler crashes
# Peer not retried, tx lost
```

**Problem**: Exception in admission handler crashes P2P pipeline. No retry, no diagnostics.

**New System**:
```python
# In p2p2/txsync.py (future work) and mempool2/admission.py
admitted, rejection = admit_tx(envelope, storage, chain_id, source=TxSource.P2P, peer_id="...")
if not admitted:
    log.info(f"P2P tx rejected: {rejection.reason} - {rejection.message}")
    record_rejection(txid, rejection)
    # Continue processing other txs
```

**Fix**: Admission never crashes. Rejections logged and recorded. P2P continues.

---

## Summary of Fixes

| Problem | Old Behavior | New Behavior |
|---------|--------------|--------------|
| **Type safety** | Untyped dicts | Strict dataclasses with validation |
| **Verification** | Raises exceptions | Returns typed VerifyResult/TxReject |
| **Admission** | Unguarded, can crash | Never throws, always returns result |
| **Error codes** | Generic 1000 | Granular 2001-2999 with reason enum |
| **Context** | Opaque "internal_error" | Structured context + hints + error_class |
| **P2P** | Crashes on bad tx | Gracefully rejects, continues |

## Verification

The new system guarantees:
1. **No TypeErrors in admission path** - All types validated at construction
2. **No unhandled exceptions** - Top-level guards in admission.py
3. **Actionable errors** - Every reject has reason, message, hint, context
4. **Deterministic** - Same input always produces same rejection
5. **Debuggable** - error_class + context allow root cause diagnosis

## Example: Before vs After

### Before (TypeError)
```
Error: -32010 mempool admission failed: internal_error
{
  "context": {
    "error_class": "TypeError",
    "tx_hash": "0xabc..."
  }
}
```

User action: ❌ No clear next step. Check logs? Retry? Different node?

### After (Invalid Signature)
```
Error: -32010 Signature verification failed: signature_invalid
{
  "reason": "invalid_signature",
  "code": 2001,
  "message": "Signature verification failed: signature_invalid",
  "hint": "Check that the transaction was signed with the correct key and algorithm",
  "context": {
    "txid": "0xabc...",
    "scheme_id": 1,
    "pubkey_fp": "a1b2c3d4"
  }
}
```

User action: ✅ Clear. Check signing key matches from_addr. Verify scheme_id is correct.

---

## Testing Strategy

To prevent regressions:

1. **Fuzz admission** - Random bytes must not crash, only return invalid_format
2. **Type validation tests** - Wrong field types must fail fast in TxBody.__post_init__
3. **Exception wrapping tests** - Simulate unexpected errors, verify they become internal_error with error_class
4. **P2P integration tests** - Malformed peer txs must not crash import pipeline
5. **Error code stability tests** - Golden test vectors ensure codes don't change

See:
- `coretx/tests/test_types.py` - Type validation
- `mempool2/tests/test_admission.py::TestAdmissionNeverThrows` - Exception wrapping
- `mempool2/tests/test_policy.py` - Policy returns, never raises
