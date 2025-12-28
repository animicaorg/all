# Investigation: `animica tx send` Success Without Mempool Inclusion

## Bug Summary

**Symptom**: `animica tx send` reports "Transaction Sent" with a tx hash, but:
- `animica mempool list` shows empty mempool
- Miner builds templates with `mempool_total=0`
- Recipient balance unchanged after mining blocks
- Transaction never appears on-chain

**Expected**: CLI should only report success if tx is actually accepted into mempool, and mempool should return the tx when queried.

---

## Code Path Analysis

### 1. CLI Command (`python/animica/cli/tx.py:275-440`)

```python
# Line 423: Submit via RPC
tx_hash = _rpc(rpc, "tx.sendRawTransaction", [raw_hex])

# Lines 424-433: Only catches specific errors (insufficient funds, method not found)
except RpcError as e:
    if e.code == -32013:  # INSUFFICIENT_FUNDS
        _format_insufficient_funds_error(e)
        raise typer.Exit(code=1)
    if e.code in (-32601,):  # method not found fallback
        tx_hash = _rpc(rpc, "tx_sendRawTransaction", [raw_hex])
    else:
        raise

# Lines 435-436: UNCONDITIONALLY prints success if no exception
console.print("\n[bold green]=== Transaction Sent ===[/bold green]")
console.print({"tx_hash": tx_hash})
```

**Problem**: CLI assumes if RPC returns a hash, the tx is accepted. **No verification** that tx is in mempool.

---

### 2. RPC Handler (`rpc/methods/tx.py:1331-1544`)

```python
# Lines 1467-1473: Submit to mempool service
mempool_service.submit(tx=tx_obj, raw=raw, tx_hash_hex=tx_hash_hex)

# Lines 1475-1493: VERIFICATION (added in previous fix attempt)
if not mempool_service.has_hash(tx_hash_hex):
    log.error(
        "tx.sendRawTransaction: VERIFICATION FAILED - tx not in mempool after submit(), hash=%s",
        tx_hash_hex,
    )
    raise rpc_errors.InternalError(
        "Transaction submitted but not in mempool",
        data={
            "tx_hash": tx_hash_hex,
            "reason": "verification_failed",
            "hint": "pool.add() may have silently failed",
        },
    )
```

**Good**: RPC handler **does verify** tx is in mempool after submit.
**Question**: Why isn't this error reaching the CLI?

---

### 3. MempoolService (`rpc/mempool_service.py:146-270`)

```python
# Line 252: Add to pool
self.pool.add(pool_tx, meta, is_local=local)

# Lines 254-263: VERIFICATION (also added in previous fix attempt)
if not self.has_hash(tx_hash_hex):
    log.error(
        "MempoolService.submit: CRITICAL - pool.add() succeeded but tx not in pool, tx_hash=%s",
        tx_hash_hex,
    )
    raise AdmissionError(
        "pool.add succeeded but tx not in pool",
        context={"tx_hash": tx_hash_hex},
    )
```

**Good**: Service **also verifies** tx is in pool.
**Double verification** suggests this bug was encountered before.

---

### 4. Pool (`mempool/pool.py:352-451`)

```python
def add(self, ...) -> AddResult:
    """
    Raises:
        DuplicateTx, FeeTooLow, NonceGap, Oversize, AdmissionError
    """
    # Lines 420, 428, 432: Raises exceptions on failure
    if existing_hash == h:
        raise DuplicateTx("transaction already in pool")
    if self.index.get(h) is not None:
        raise DuplicateTx("transaction already in pool")
    if not self._admit_floor_ok(meta, is_local=is_local):
        raise FeeTooLow("effective fee below current admit floor")
```

**Good**: Pool raises exceptions on failure, doesn't swallow errors.

---

## Root Cause Hypotheses

### Hypothesis 1: Mempool Instance Mismatch ⚠️ **MOST LIKELY**

**Symptom matches perfectly**: 
- RPC `tx.sendRawTransaction` inserts into mempool instance A
- RPC `mempool.list` reads from mempool instance B
- Miner template builder uses mempool instance C

**Evidence**:
1. Double verification in both RPC and Service suggests previous fix attempts
2. If verification is working, errors would reach CLI - but they don't
3. User reports `mempool list` shows empty immediately after send

**How to diagnose**:
- Check `rpc/deps.py` - how is mempool singleton managed?
- Check if multiple `MempoolService` instances are created
- Check if mempool is stored in app state vs module-level variable

### Hypothesis 2: RPC Exception Not Propagating

**If** `mempool_service.submit()` raises `AdmissionError`, it should:
1. Convert to `rpc_errors.RpcError` 
2. Return as JSON-RPC error response
3. CLI receives error and displays it

**Possible failure points**:
- Exception caught and swallowed in RPC middleware
- `AdmissionError` not properly mapped to RPC error code
- CLI not receiving/displaying the error

**How to diagnose**:
- Add logging before/after `mempool_service.submit()`
- Check RPC error mapping for `AdmissionError`
- Run CLI with verbose logging

### Hypothesis 3: CLI Talking to Wrong RPC Endpoint

**Less likely** but possible:
- CLI resolves to `http://127.0.0.1:8545/rpc` (endpoint A)
- `mempool list` uses a different endpoint (endpoint B)
- Miner uses yet another endpoint (endpoint C)

**How to diagnose**:
- CLI should print resolved RPC URL
- Check `_resolve_rpc_url()` logic
- Verify all commands use same endpoint

### Hypothesis 4: Silent Pool Rejection Due to Nonce/Fee ⚠️ **POSSIBLE**

**Scenario**: Transaction passes all RPC validation but fails mempool admission:
- Nonce too high → `NonceGap` → tx held in "held queue", not "ready queue"
- Fee too low → `FeeTooLow` → rejected
- Gas limit exceeds block limit → rejected

**But**: Both Pool and Service raise exceptions on these cases.

**Unless**: There's a code path where pool.add() returns success but tx goes to "held queue" instead of "ready queue", and verification only checks ready queue.

**How to diagnose**:
- Check if `has_hash()` searches both ready and held queues
- Check if held txs are included in `mempool list`
- Look at pool.index vs pool ready_heap

---

## Affected Components

1. **CLI** (`python/animica/cli/tx.py`): Needs post-send verification
2. **RPC Deps** (`rpc/deps.py` or wherever mempool singleton is managed): May have instance mismatch
3. **Mempool Service** (`rpc/mempool_service.py`): Already has verification
4. **Block Template Builder** (`mining/` or `miner/`): Must use same mempool instance

---

## Proposed Investigation Steps

### Step 1: Add Diagnostic Logging
Add server-side logs to track:
- When `tx.sendRawTransaction` is called
- When `mempool_service.submit()` succeeds/fails
- When `pool.add()` succeeds/fails
- Mempool instance ID at each call site

### Step 2: Verify Mempool Singleton
Check how mempool is created and stored:
```python
# Search for MempoolService instantiation
grep -r "MempoolService(" rpc/ core/
# Check if it's stored in app state
grep -r "ctx.mempool" rpc/
```

### Step 3: Check has_hash() Implementation
Verify `has_hash()` searches all pool structures:
- `pool.index` (all txs)
- `pool.ready_heap` (ready txs)
- `pool.seqs` (per-sender nonce queues, including held txs)

### Step 4: Run Existing Integration Test
```bash
pytest tests/integration/test_mempool_template_regression.py -v
```
This test does exactly what the user reports:
1. Sends tx via `tx.sendRawTransaction`
2. Verifies tx in `mempool.getPending`
3. Mines block
4. Verifies tx included and mempool cleared

If this test passes, the **RPC path works** → problem is CLI-specific or endpoint mismatch.

### Step 5: Reproduce with CLI
Replicate exact user flow:
```bash
animica wallet list
animica miner mine-blocks --address <FROM> --count 5
animica tx send --from <FROM> --to <TO> --value 19 --verbose
# Immediately after:
animica mempool list
# Check server logs for "MempoolService.submit" messages
```

---

## Proposed Solution (Preliminary)

### Fix 1: CLI Post-Send Verification ✅ **REQUIRED**

Update `python/animica/cli/tx.py:send()` to verify tx in mempool after RPC call:

```python
# After line 423: tx_hash = _rpc(rpc, "tx.sendRawTransaction", [raw_hex])

# Verify tx is in mempool
try:
    pending = _rpc(rpc, "mempool.getPending", [])
    if tx_hash not in pending:
        console.print("[bold red]ERROR: Transaction submitted but not in mempool[/bold red]")
        console.print(f"TX hash: {tx_hash}")
        console.print("This may indicate a mempool rejection (nonce gap, fee too low, etc.)")
        raise typer.Exit(code=1)
except RpcError as e:
    # mempool.getPending may not be available on all nodes
    log.debug(f"Could not verify mempool inclusion: {e}")
    # Fallback: try mempool.explain
    try:
        explain = _rpc(rpc, "mempool.explain", [tx_hash])
        if explain.get("status") != "eligible":
            console.print(f"[bold yellow]WARNING: TX may not be eligible: {explain}[/bold yellow]")
    except RpcError:
        pass  # Best-effort verification

# Only print success if verified
console.print("\n[bold green]=== Transaction Sent ===[/bold green]")
console.print({"tx_hash": tx_hash})
```

### Fix 2: Ensure Mempool Singleton ✅ **REQUIRED**

Audit mempool instantiation in RPC server setup:
- Mempool should be created once and stored in app state
- All RPC methods should reference the **same instance**
- Template builder should also use the same instance

**Check**:
```python
# In rpc/server.py or main.py:
ctx.mempool = MempoolService.create(...)  # Single instance

# In rpc/methods/tx.py and rpc/methods/mempool.py:
def _get_mempool_service():
    return deps.get_ctx().mempool  # Same instance
```

### Fix 3: Enhanced Error Reporting ✅ **NICE TO HAVE**

Update RPC `tx.sendRawTransaction` to return structured result:

```python
# Instead of returning just tx_hash string:
return tx_hash_hex

# Return structured object:
return {
    "tx_hash": tx_hash_hex,
    "accepted": True,
    "mempool_size": len(mempool_service.pool),
}
```

Then CLI can check `result["accepted"]` explicitly.

---

## Edge Cases to Consider

1. **Nonce gap**: TX with future nonce → held in mempool but not ready → should still show in `mempool list`
2. **Fee too low**: TX rejected → should return error, not success
3. **Duplicate TX**: Resubmitting same TX → should return original hash (idempotent)
4. **Chain ID mismatch**: Wrong network → should return error before mempool admission
5. **Signature invalid**: Bad PQ signature → should fail verification before mempool

---

## Testing Strategy

### Regression Tests (must fail before fix, pass after)

1. **Test: CLI tx send failure modes**
   - Send tx with insufficient balance → CLI shows error + reason
   - Send tx with nonce gap → CLI shows error or warning
   - Send tx with fee too low → CLI shows error
   
2. **Test: CLI tx send success flow**
   - Mine funds to sender
   - Send valid tx
   - **Verify CLI success AND `mempool list` contains tx**
   - Mine block
   - Verify tx included and balance updated

3. **Test: Mempool service verification**
   - Mock `pool.add()` to silently fail (return without raising)
   - Call `mempool_service.submit()`
   - Assert it raises `AdmissionError` due to verification failure

4. **Test: Multiple algorithm support**
   - SPHINCS+ wallet (stateful signature)
   - Dilithium wallet (stateless signature)
   - Both should have same success/failure behavior

---

## Next Steps

1. ✅ Run existing integration test to confirm RPC path works
2. ⚠️ Add diagnostic logging to identify mempool instance IDs
3. ⚠️ Reproduce exact user flow with logging enabled
4. ✅ Implement CLI verification (Fix 1)
5. ✅ Audit mempool singleton pattern (Fix 2)
6. ✅ Write regression tests
7. ⏭️ Run full test suite including new tests
