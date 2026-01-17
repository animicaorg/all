# Stratum Mining Fix - signBytes Missing in Header

## Issue
Miners connecting to Animica Stratum server received jobs but refused to mine, displaying:
```
Warning: No signBytes in job header, skipping batch
```

## Root Causes

### Cause 1: Stratum Bridge Using Wrong RPC Method
The stratum bridge (`mining/stratum_bridge.py`) was calling `miner.getBlockTemplate` to fetch mining work, but that RPC method doesn't return `signBytes` or `hints`.

**Problem:**
```python
template = await self._rpc.call("miner.getBlockTemplate", {...})
# template does NOT contain signBytes or hints!
```

**Solution:**
```python
template = await self._rpc.call("miner.getWork", {...})
# template contains signBytes and hints.mixSeed
```

### Cause 2: Stratum Server Not Enriching Header
The stratum server (`mining/stratum_server.py`) stored `signBytes` and `hints` as separate fields in `StratumJob`, but when broadcasting to miners, it only sent the raw `header` dict without enriching it.

**Problem:**
```python
msg = push_notify(
    job_id=job.job_id,
    header=job.header,  # Missing signBytes!
    ...
)
```

**Solution:**
```python
# Enrich header before sending
header = dict(job.header or {})
if job.sign_bytes and "signBytes" not in header:
    header["signBytes"] = job.sign_bytes
if job.hints and "mixSeed" in job.hints and "mixSeed" not in header:
    header["mixSeed"] = job.hints["mixSeed"]

msg = push_notify(
    job_id=job.job_id,
    header=header,  # Now includes signBytes and mixSeed!
    ...
)
```

## RPC Method Comparison

### miner.getWork
Returns:
- `signBytes` (string, top level) - Canonical header bytes for mining
- `hints.mixSeed` (string) - Mix seed for nonce domain evolution
- `header` (object) - Header fields
- `height`, `target`, `thetaMicro`, etc.

**Used by:** Stratum bridge, CPU miners

### miner.getBlockTemplate
Returns:
- `header` (object) - Header fields only
- `target`, `thetaMicro`, etc.
- **Does NOT return `signBytes` or `hints`**

**Used by:** Block assembly, advanced miners that can compute signBytes themselves

## Miner Requirements

For a miner to successfully hash, it needs:
1. **signBytes** - The canonical 80-byte header prefix (excluding nonce)
2. **mixSeed** - 32-byte seed for nonce domain (from hints or header)
3. **height** - Block height for display

Miners compute the PoW hash as:
```python
digest = sha3_256(signBytes || mixSeed || nonce_le8)
```

If any of these are missing, miners skip the batch with warnings.

## Files Changed

1. **mining/stratum_bridge.py**
   - `_poll_template()` - Use miner.getWork instead of getBlockTemplate
   - `get_current_job()` - Extract signBytes from template (not header), include hints
   - `_create_stratum_job()` - Pass hints to StratumJob

2. **mining/stratum_server.py**
   - `_broadcast_job()` - Enrich header with signBytes and mixSeed before broadcasting

3. **Tests**
   - `mining/tests/test_stratum_signbytes_fix.py` - Unit tests for header enrichment
   - `mining/tests/test_miner_receives_signbytes.py` - E2E test simulating real miner

## Verification

To verify the fix works:

1. **Start a node:**
   ```bash
   animica node up
   ```

2. **Start stratum bridge:**
   ```bash
   python -m mining.stratum_bridge --rpc-url http://127.0.0.1:8545 \
       --listen 127.0.0.1:3333 --address anim1...
   ```

3. **Connect a miner:**
   ```bash
   animica miner stratum --address anim1... --url stratum+tcp://127.0.0.1:3333 --count 2
   ```

4. **Expected output (AFTER fix):**
   ```
   ✓ Connected to 127.0.0.1:3333
   ✓ Subscribed (session: ...)
   ✓ Authorized
   ✓ Received initial job
   → New job: ... (height 7)
   Mining started... (Ctrl+C to stop)
   [No warnings about missing signBytes]
   ```

## Related Documentation
- `STRATUM_JOB_FORMAT.md` - Complete stratum job format specification
- `STRATUM_JOB_FORMAT_FIX_SUMMARY.md` - Previous fix for mixSeed location
- `docs/miner-rpc.md` - RPC method documentation
