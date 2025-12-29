# Bug Investigation: Transaction Not in Mempool

## Bug Summary
Transactions submitted via `animica tx send` are accepted by the RPC (`tx.sendRawTransaction`) but do not appear in the mempool when queried via `mempool.getPending` or `mempool.explain`.

## Error Message
```
=== ERROR: Transaction Not in Mempool ===
TX hash: 0x25a87844012681bc369c4cad1acb755c796f5e0d54395c375c1ecaeccab5e0ee

The RPC accepted the transaction but it is NOT in the mempool.
Possible reasons:
  • Nonce gap (tx nonce is too high)
  • Fee too low (below minimum gas price)
  • Gas limit too high (exceeds block limit)
  • Mempool full (tx evicted)
  • Internal mempool error (transaction submitted but not persisted)
```

## Root Cause Analysis

After tracing through the code, I've identified a **type mismatch** issue in the mempool service:

### The Issue

In `rpc/mempool_service.py:240-244`, the `PoolTx` is created with `tx_hash_bytes` (bytes):
```python
pool_tx = PoolTx(
    tx=tx,
    tx_hash=tx_hash_bytes,  # <-- This is bytes
    raw=raw,
    meta=meta,
    fee=fee,
)
```

However, in `mempool/types.py:284`, the type annotation for `tx_hash` indicates it should be a **string**:
```python
@dataclass(order=True)
class PoolTx:
    ...
    tx_hash: TxHash = field(compare=False)  # TxHash = str (line 52)
```

### Why This Causes the Bug

1. **Storage**: When `pool.add()` is called, it stores the transaction using the hash:
   - `pool.py:413`: `h = getattr(tx, "hash", None) or getattr(tx, "tx_hash", None)`
   - If `tx_hash` is bytes, `h` becomes bytes
   - `pool.py:442`: `self.index.add(h, tx, meta)` - stores with bytes key

2. **Lookup**: When checking if transaction is in mempool:
   - `mempool_service.py:140-144`: `has_hash()` method calls `pool.get(_normalize_hash_bytes(tx_hash_hex))`
   - `_normalize_hash_bytes()` converts the hex string to bytes
   - `pool.get()` looks up using these bytes

3. **The Problem**: If there's any inconsistency in how the hash is stored vs looked up (e.g., if PoolTx.tx_hash is sometimes treated as string vs bytes), the lookup will fail even though the transaction was added.

### Additional Observations

Looking at the verification logic in the codebase:

- **RPC Handler** (`rpc/methods/tx.py:1476-1488`): Has verification after `mempool_service.submit()`
- **Mempool Service** (`rpc/mempool_service.py:255-263`): Has verification after `pool.add()`

Both verification steps check `has_hash()`, which should catch this issue. However, the CLI's error suggests that:
1. The RPC verification passes (so transaction returns success)
2. The CLI's subsequent check fails (transaction not found in `mempool.getPending`)

This indicates a **timing issue** or an **inconsistency** between:
- How the pool stores/retrieves internally (used by verification)
- How `mempool.getPending` iterates over transactions

### Affected Components

1. `rpc/mempool_service.py` - Creates PoolTx with bytes for tx_hash
2. `mempool/types.py` - PoolTx type hints say tx_hash should be str
3. `mempool/pool.py` - Stores and retrieves using hash attribute
4. `python/animica/cli/tx.py` - CLI verification that detects the bug

## Proposed Solution

**Option 1: Normalize tx_hash to always be hex string**
- Change `mempool_service.py` to pass hex string: `tx_hash=tx_hash_hex`
- Ensure `pool.py` handles string hashes consistently
- Update `pool.get()` and related methods to expect string hashes

**Option 2: Normalize tx_hash to always be bytes**
- Update `mempool/types.py` type hint: `tx_hash: bytes`
- Ensure all lookups use bytes consistently
- Update `has_hash()` to always convert to bytes

**Option 3 (Recommended): Use consistent types with explicit conversion**
- Store as bytes internally in pool
- Convert to hex at API boundaries
- Add explicit type conversions at interface points
- Add validation/assertions to catch type mismatches early

## Edge Cases to Consider

1. **Nonce gap scenario**: Transaction with future nonce is held but not ready
   - Pool adds to index but not to ready heap
   - Should still be findable via `pool.get()` and `pool.index.all_items()`

2. **Transaction eviction**: Transaction added then immediately evicted
   - Could cause timing issue if verification happens before eviction

3. **Hash normalization**: Different hash formats (0x-prefixed, lowercase, bytes)
   - Need consistent normalization across all code paths

## Next Steps

1. Add debug logging to track hash values (type and value) through the flow
2. Write regression test that reproduces the exact scenario
3. Implement fix with proper type conversions
4. Update pool internals to be consistent about hash types
5. Add assertions to catch type mismatches early in development
