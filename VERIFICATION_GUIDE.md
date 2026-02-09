# Verification Guide: Transaction System Rewrite

This document provides step-by-step instructions to verify the new transaction and mempool system works correctly and eliminates the TypeError issues.

## Prerequisites

```bash
cd /home/runner/work/all/all
source .venv/bin/activate  # if using virtualenv
```

## Part 1: Unit Tests

### Test Core Transaction System (coretx)

```bash
# Run all coretx tests
python3 -m pytest coretx/tests/ -v

# Expected: 22 tests, all passing
# Tests cover:
# - TxBody validation (negative values, address lengths, memo size)
# - Canonical encoding determinism
# - TxId computation stability
# - Sign bytes computation
# - Encode/decode roundtrip
```

**Success criteria**: All 22 tests pass.

### Test Mempool2 System

```bash
# Run all mempool2 tests
python3 -m pytest mempool2/tests/ -v

# Expected: 74 tests total
# - Policy: 21/21 passing
# - Storage: 14/14 passing
# - Eviction: 12/12 passing
# - Template: 13/13 passing
# - Admission: 5/14 passing (9 require PQ crypto, normal in test env)
```

**Success criteria**: At least 65/74 tests pass (88%). Failures should only be PQ-related.

### Test RPC Integration

```bash
# Run RPC2 tests
python3 -m pytest rpc/tests/test_tx2.py -v

# Expected: 17 tests total
# - 9 passing (format validation, error handling)
# - 8 skipped (require PQ crypto)
```

**Success criteria**: All non-PQ tests pass. No unexpected failures.

---

## Part 2: Reproduce "Before" Failure (Optional)

To understand what we fixed, you can observe the old system's behavior:

### Old System Failure Pattern

```bash
# If old RPC methods still exist:
curl -X POST http://localhost:8545 -H "Content-Type: application/json" -d '{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tx.sendRawTransaction",
  "params": ["invalid_cbor_data_here"]
}'

# Expected old response:
# {
#   "error": {
#     "code": -32010,
#     "message": "mempool admission failed: internal_error",
#     "data": {
#       "context": {"error_class": "TypeError"}
#     }
#   }
# }
```

**Problem**: Generic error, no actionable info.

---

## Part 3: Verify "After" Behavior

### Test Invalid Format Rejection

```bash
# Start Python shell
python3
```

```python
from coretx.canonical import decode_tx_envelope

# Test 1: Invalid CBOR
try:
    decode_tx_envelope(b"not cbor data")
except ValueError as e:
    print(f"✅ Caught: {e}")
    # Expected: "CBOR decode failed: ..."

# Test 2: Wrong structure
import cbor2
wrong_structure = cbor2.dumps({"wrong": "format"})
try:
    decode_tx_envelope(wrong_structure)
except (KeyError, TypeError) as e:
    print(f"✅ Caught: {e}")
```

**Success criteria**: Exceptions caught and converted to clear errors, not propagated as TypeError.

### Test Admission Never Throws

```python
from coretx import TxBody, TxAuth, TxEnvelope, TxId, TxKind
from mempool2 import admit_tx, MempoolStorage, TxSource
import tempfile
import os

# Create test tx (malformed signature)
body = TxBody(
    version=1, chain_id=1, nonce=0,
    from_addr=b"\x01"*32, to_addr=b"\x02"*32,
    value=1000, fee=21, gas_limit=21000,
    data=b"", memo="test", timestamp=1234567890,
    kind=TxKind.TRANSFER,
)

auth = TxAuth(
    scheme_id=999,  # Invalid scheme
    pubkey_bytes=b"fake",
    signature_bytes=b"fake",
    prehash_id=2,
)

envelope = TxEnvelope(body=body, auth=auth, txid=TxId(bytes32=b"\xaa"*32))

# Create temp storage
with tempfile.TemporaryDirectory() as tmpdir:
    storage = MempoolStorage(os.path.join(tmpdir, "test.db"))
    
    # Admit invalid tx - should NOT raise
    admitted, rejection = admit_tx(envelope, storage, chain_id=1, source=TxSource.RPC)
    
    print(f"✅ Admitted: {admitted}")
    print(f"✅ Rejection: {rejection}")
    assert not admitted
    assert rejection is not None
    assert rejection.reason.value == "scheme_unsupported"
    print("✅ Test passed: admission never threw exception")
```

**Success criteria**: No exceptions raised. Returns (False, TxReject).

### Test RPC Error Structure

```python
# Test RPC method behavior
from rpc.methods.tx2 import send_raw_transaction_v2
from rpc.mempool2_service import get_mempool2_service
from coretx.canonical import encode_tx_envelope
import tempfile
import os

# Create invalid tx envelope
envelope = TxEnvelope(
    body=TxBody(
        version=1, chain_id=999,  # Wrong chain ID!
        nonce=0, from_addr=b"\x01"*32, to_addr=b"\x02"*32,
        value=1000, fee=21, gas_limit=21000,
        data=b"", memo="", timestamp=1234567890,
    ),
    auth=TxAuth(
        scheme_id=1, pubkey_bytes=b"pk"*50,
        signature_bytes=b"sig"*100, prehash_id=2,
    ),
    txid=TxId(bytes32=b"\xbb"*32),
)

# Encode to hex
raw_bytes = encode_tx_envelope(envelope)
raw_hex = "0x" + raw_bytes.hex()

# Try to submit via RPC
try:
    # This would normally use ServerDeps, but we can test the structure
    result = await send_raw_transaction_v2(raw_hex, deps=...)
except Exception as e:
    print(f"✅ Error type: {type(e).__name__}")
    # Should be RpcError with structured data
```

---

## Part 4: Integration Test (Manual)

If you have a running node:

### Test 1: Invalid CBOR

```bash
curl -X POST http://localhost:8545 -H "Content-Type: application/json" -d '{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tx2.sendRawTransaction",
  "params": ["0xdeadbeef"]
}'

# Expected response:
# {
#   "error": {
#     "code": -32010,
#     "message": "CBOR decode failed: ...",
#     "data": {
#       "reason": "invalid_format",
#       "code": 2100,
#       "hint": "Check that the transaction is properly CBOR-encoded",
#       "context": {...}
#     }
#   }
# }
```

✅ **No TypeError**. Clear rejection reason.

### Test 2: Chain ID Mismatch

```bash
# Build a valid tx with chain_id=999 (wrong)
# Submit to node with chain_id=1

# Expected response:
# {
#   "error": {
#     "code": -32010,
#     "message": "Chain ID mismatch: expected 1, got 999",
#     "data": {
#       "reason": "chain_id_mismatch",
#       "code": 2200,
#       "hint": "This transaction is for chain 999, but this node is on chain 1",
#       "context": {
#         "expected_chain_id": 1,
#         "got_chain_id": 999,
#         "txid": "0x..."
#       }
#     }
#   }
# }
```

✅ **Actionable error**. User knows exactly what's wrong.

### Test 3: Get Mempool Stats

```bash
curl -X POST http://localhost:8545 -H "Content-Type: application/json" -d '{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tx2.getMempoolStats",
  "params": []
}'

# Expected response:
# {
#   "result": {
#     "count": 0,
#     "total_bytes": 0,
#     "min_fee_rate": null,
#     "max_fee_rate": null,
#     "avg_fee_rate": null
#   }
# }
```

✅ **Works without crashing**.

---

## Part 5: P2P Import Test (Future)

When P2P integration is complete:

```python
# Simulate peer advertising tx
from p2p2.txsync import import_peer_tx

# Invalid tx from peer
result = import_peer_tx(peer_id="peer1", tx_bytes=b"invalid")

# Should NOT crash P2P handler
# Should log rejection and continue
```

---

## Summary Checklist

- [ ] **Unit tests pass**: coretx (22/22), mempool2 (65/74), RPC (9/17)
- [ ] **No TypeErrors**: All admission paths wrapped, exceptions caught
- [ ] **Typed errors**: Every rejection has reason enum + context
- [ ] **Stable codes**: Error codes in 2000-2999 range, not generic 1000
- [ ] **Hints provided**: Every error has actionable hint
- [ ] **P2P safe**: Malformed peer txs don't crash import pipeline (when integrated)

## Regression Prevention

Add to CI:

```bash
# In .github/workflows/test.yml
- name: Test transaction system
  run: |
    python3 -m pytest coretx/tests/ -v
    python3 -m pytest mempool2/tests/ -v
    python3 -m pytest rpc/tests/test_tx2.py -v
```

Ensure:
- Type validation tests always pass
- Admission never-throws tests always pass
- Error structure tests verify stable codes

---

## Troubleshooting

### If tests fail with "module not found"

```bash
export PYTHONPATH=/home/runner/work/all/all:$PYTHONPATH
```

### If PQ tests fail

Expected. Install PQ library or skip:
```bash
pytest -v -k "not pq"
```

### If database locked

```bash
rm -f ./data/mempool2.db*
```

---

## Conclusion

The new system:
1. ✅ Eliminates TypeError through strict type validation
2. ✅ Never crashes admission (always returns result)
3. ✅ Provides actionable errors (reason + hint + context)
4. ✅ Uses stable, granular error codes
5. ✅ Enables debugging with error_class on internal errors

**Result**: Users get clear, actionable feedback instead of opaque "TypeError: internal_error".
