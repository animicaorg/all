# Technical Specification: Fix tx.sendRawTransaction Silent Failure

## Complexity Assessment
**Difficulty: Medium**

While the fix itself may be straightforward, this is a critical path bug that has been addressed multiple times in different ways (see git history with 15+ related commits). The complexity comes from:
- Multiple fallback storage mechanisms (_PEND, ctx.mempool, _FALLBACK_PENDING)
- Integration between RPC layer, mempool service, and mining
- Need for careful logging to prevent future recurrence
- Potential for subtle race conditions or instance mismatches

## Problem Statement

**BUG**: `animica tx send` returns a tx_hash successfully, but the transaction never appears in `animica mempool list` and never gets mined. The user receives no error, making this a silent failure that breaks the transaction flow.

### Reproduction Steps
```bash
# 1. Mine blocks to fund sender
animica miner mine-blocks --address <SENDER_ADDR> --count 5

# 2. Send transaction
animica tx send --from <SENDER_ADDR> --to <RECIP_ADDR> --value 19
# Output: {'tx_hash': '0x...'}  ← Success response

# 3. Check mempool immediately
animica mempool list
# Observed: "Mempool is empty"  ← BUG: tx not in mempool

# 4. Mine another block
animica miner mine-blocks --address <SENDER_ADDR> --count 1
# Observed: mempool_total=0, block txs=0  ← tx was never included
```

**Expected Behavior**:
- If tx is valid: RPC should insert it into mempool AND it should be visible via `mempool.list`
- If tx is invalid/rejected: RPC should return error (not success)

## Architecture Context

### Transaction Flow
```
CLI (animica tx send)
  → python/animica/cli/tx.py:send()
  → RPC call: tx.sendRawTransaction(rawTx)
  → rpc/methods/tx.py:tx_send_raw_transaction()  [LINE 1293-1518]
     ├─ Decode & validate tx
     ├─ Verify PQ signature
     ├─ Check balance
     └─ Admit to mempool:
        ├─ IF mempool_service exists: mempool_service.submit()  [LINE 1467-1475]
        │  └─ rpc/mempool_service.py:MempoolService.submit()  [LINE 146-235]
        │     └─ self.pool.add(pool_tx, meta, is_local=local)  [LINE 234]
        └─ ELSE: _pending_put()  [LINE 1477]
           └─ _PEND.add() OR _FALLBACK_PENDING[hash] = raw
```

### Mempool Query Flow
```
CLI (animica mempool list)
  → python/animica/cli/mempool.py:list_pending()
  → RPC call: mempool.getPending()
  → rpc/methods/mempool.py:mempool_get_pending()  [LINE 74-89]
     └─ _iter_pending()  [LINE 39-66]
        ├─ Try ctx.mempool.snapshot()  [LINE 46-50]
        ├─ Else try _PEND.items()  [LINE 55-62]
        └─ Else try _FALLBACK_PENDING.items()  [LINE 64-66]
```

### Mining Flow
```
CLI (animica miner mine-blocks)
  → RPC call: miner.getBlockTemplate()
  → rpc/methods/miner.py:miner_get_block_template()  [LINE 3328-3700]
     └─ Get pending txs from:
        ├─ ctx.mempool.snapshot() if available  [LINE 3395-3406]
        └─ Else fallback to _PEND or _FALLBACK_PENDING  [LINE 3410-3446]
```

## Root Cause Analysis

### Hypothesis 1: ctx.mempool Instance Mismatch (Most Likely)
The `mempool_service` instance used in `tx.sendRawTransaction` may be:
1. **Different from** the instance accessed by `mempool.getPending`
2. **None** (causing fallback to `_pending_put`), but then `mempool.getPending` looks at a different pool
3. **Not properly initialized** in the RPC context when the node starts

**Evidence**:
- `tx.py:1438`: `mempool_service = _get_mempool_service()` returns `ctx.mempool`
- `mempool.py:46`: `mempool_service = getattr(ctx, "mempool", None)` also returns `ctx.mempool`
- BUT: Multiple pool fallbacks exist (_PEND, _FALLBACK_PENDING), suggesting initialization issues

### Hypothesis 2: Silent pool.add() Failure
`MempoolService.submit()` calls `self.pool.add()` at line 234, but:
- If `pool.add()` fails silently without raising an exception
- OR if it returns without actually adding the tx
- Then `submit()` would return success but the tx wouldn't be in the pool

### Hypothesis 3: Immediate Eviction
The tx is added successfully but immediately evicted due to:
- TTL=0 or very short TTL
- Revalidation logic rejecting it
- Race condition with block mining

### Hypothesis 4: Logging Gaps
Previous fixes show this is a recurring issue. Current code may lack sufficient logging to diagnose where tx submission actually succeeds vs. where it fails.

## Implementation Approach

### Phase 1: Enhanced Logging & Diagnostics
Add comprehensive logging at every step to identify where the bug occurs:

1. **In tx.sendRawTransaction** (rpc/methods/tx.py):
   - Log mempool_service availability (None vs instance)
   - Log which path is taken (mempool_service.submit vs _pending_put)
   - Log the result of submit() and whether it raises exceptions

2. **In MempoolService.submit()** (rpc/mempool_service.py):
   - Log entry with tx_hash_hex
   - Log if tx already in pool (duplicate)
   - Log validation steps (chain_id, nonce, balance)
   - Log result of pool.add() call
   - Log exit with success/failure

3. **In mempool.getPending** (rpc/methods/mempool.py):
   - Log which source is used (ctx.mempool vs _PEND vs _FALLBACK_PENDING)
   - Log count from each source

### Phase 2: Fix Core Issue

Based on logging results, implement one or more of:

**Fix A: Ensure Consistent Pool Instance**
```python
# In rpc/__init__.py or deps.py
def ensure_mempool_singleton():
    """Ensure ctx.mempool is initialized once and reused."""
    if not hasattr(ctx, "mempool") or ctx.mempool is None:
        ctx.mempool = MempoolService.create(...)
        log.info("Initialized singleton mempool service")
    return ctx.mempool
```

**Fix B: Validate pool.add() Success**
```python
# In MempoolService.submit() after line 234
result = self.pool.add(pool_tx, meta, is_local=local)
# Verify tx is actually in pool
if not self.has_hash(tx_hash_hex):
    raise AdmissionError(
        "pool.add succeeded but tx not in pool",
        context={"tx_hash": tx_hash_hex}
    )
```

**Fix C: Never Return Success Without Verification**
```python
# In tx.sendRawTransaction after mempool_service.submit()
mempool_service.submit(tx=tx_obj, raw=raw, tx_hash_hex=tx_hash_hex)

# CRITICAL: Verify tx is actually in mempool before returning success
if not mempool_service.has_hash(tx_hash_hex):
    raise rpc_errors.InternalError(
        "Transaction submitted but not in mempool",
        data={"tx_hash": tx_hash_hex, "reason": "verification_failed"}
    )

log.info("tx.sendRawTransaction: VERIFIED tx in mempool, hash=%s", tx_hash_hex)
```

**Fix D: Unified Pool Access Helper**
```python
# Create a single helper that returns the canonical pool instance
def get_canonical_mempool():
    """Get the canonical mempool instance, with fallback to _PEND."""
    try:
        ctx = deps.get_ctx()
        if hasattr(ctx, "mempool") and ctx.mempool is not None:
            return ctx.mempool
    except Exception:
        pass
    return _PEND  # or None

# Use this helper everywhere instead of inline checks
```

### Phase 3: Improve Error Handling

1. **In tx.sendRawTransaction**: Never swallow AdmissionError/NonceGap/FeeTooLow
2. **Return structured errors** with reason, details, and tx_hash (optional)
3. **Add metrics** for submission success/failure by reason

### Phase 4: Integration Tests

Create test that reproduces the bug:

```python
# tests/integration/test_tx_send_mempool_visibility.py
def test_tx_send_appears_in_mempool(running_node, funded_wallet):
    """
    Submit a valid signed tx and verify it appears in mempool immediately.
    """
    # 1. Create and sign tx
    tx_hash = rpc_call("tx.sendRawTransaction", [raw_hex])
    assert tx_hash.startswith("0x")
    
    # 2. CRITICAL: Verify tx appears in mempool
    pending = rpc_call("mempool.getPending", [])
    assert tx_hash in pending, f"tx {tx_hash} not in mempool after submission"
    
    # 3. Verify tx appears in miner.getBlockTemplate
    template = rpc_call("miner.getBlockTemplate", [{"address": miner_addr}])
    assert template["mempoolTotal"] > 0
    assert len(template["transactions"]) > 0
    
    # 4. Mine block and verify inclusion
    block = mine_block()
    assert tx_hash in block["transactions"]
    
    # 5. Verify tx removed from mempool after mining
    pending_after = rpc_call("mempool.getPending", [])
    assert tx_hash not in pending_after
```

## Files to Modify

### Primary Changes
1. **rpc/methods/tx.py** (lines 1467-1481)
   - Add verification after mempool_service.submit()
   - Enhanced logging for diagnostics
   - Ensure errors are never swallowed

2. **rpc/mempool_service.py** (lines 146-235)
   - Add logging at entry/exit of submit()
   - Validate pool.add() result
   - Log all validation failures

3. **rpc/methods/mempool.py** (lines 39-66)
   - Log which pool source is used
   - Add debug diagnostics

### Supporting Changes
4. **tests/integration/test_tx_send_mempool_visibility.py** (new file)
   - Comprehensive integration test

5. **rpc/__init__.py** or **rpc/deps.py**
   - Add mempool singleton initialization (if needed)

## Data Model / API Changes

**No breaking changes**. All modifications are internal to RPC methods.

**New logging fields**:
- `mempool_service_available`: bool
- `mempool_path_taken`: "service.submit" | "pending_put" | "fallback"
- `pool_source`: "ctx.mempool" | "_PEND" | "_FALLBACK_PENDING"
- `verified_in_mempool`: bool (after submission)

## Verification Approach

### Manual Testing
```bash
# 1. Start fresh node
animica node down --volumes && animica node up

# 2. Fund wallet
animica miner mine-blocks --address $SENDER --count 5

# 3. Enable debug logging
export ANIMICA_RPC_DEBUG=1
export ANIMICA_MEMPOOL_DEBUG=1

# 4. Send tx and capture logs
animica tx send --from $SENDER --to $RECIP --value 100 2>&1 | tee /tmp/tx_send.log

# 5. Verify in mempool immediately
animica mempool list

# Expected: tx_hash appears in list
# Logs should show:
# - "mempool_service_available: true"
# - "mempool_path_taken: service.submit"
# - "verified_in_mempool: true"

# 6. Mine block
animica miner mine-blocks --address $SENDER --count 1

# Expected: block contains tx, mempool is empty afterward
```

### Automated Testing
```bash
# Run new integration test
pytest tests/integration/test_tx_send_mempool_visibility.py -xvs

# Run existing mempool tests
pytest rpc/tests/test_mempool*.py -xvs
pytest rpc/tests/test_mining_mempool_integration.py -xvs
pytest tests/integration/test_tx_flow_mempool_to_block.py -xvs
```

### Acceptance Criteria
✅ After `animica tx send`, `animica mempool list` shows the tx  
✅ After mining 1 block, the block contains the tx  
✅ Receiver balance increases by transfer amount  
✅ Mempool is empty after block is mined  
✅ If tx is rejected, user sees a clear error with reason (not silent success)  
✅ Logs show clear path taken (service.submit vs fallback)  
✅ Tests pass: test_tx_send_mempool_visibility.py

## Risk Assessment

**High Risk Areas**:
- Changing exception handling in tx.sendRawTransaction could break existing error reporting
- Adding verification step after submit() could introduce race conditions
- Modifying mempool_service.submit() affects all tx submission paths (RPC, P2P gossip)

**Mitigation**:
- Comprehensive logging before making changes (Phase 1)
- Incremental changes with tests after each step
- Preserve all existing exception handling behavior
- Run full test suite after each change

## Dependencies

- **Python**: fastapi, uvicorn, cbor2, pq.py (already in use)
- **Internal**: rpc.mempool_service, mempool.pool, core.types.tx
- **Tests**: pytest, existing test harness

## Related Issues

Git history shows 15+ related commits:
- "Fix mempool tx inclusion in mined blocks" (commit c200bf76)
- "Fix mempool mining inclusion wiring" (commit 741c0450)
- "Fix mempool eviction on block mining" (PR #694)
- "Fix pending pool exposure for mempool RPC" (commit 39234b02)

This suggests the issue is architectural and requires a definitive fix with strong guarantees.
