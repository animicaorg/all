# Rich List Troubleshooting Guide

## Problem: "Load failed on rich list on explorer2"

This document explains how to diagnose and fix the "Load failed" error on the Rich List page in Explorer2.

## Root Cause

The Rich List feature requires two RPC methods to be available on the Animica node:
- `state.getRichList(limit, offset)` - Returns paginated list of addresses sorted by balance
- `state.getTotalSupply()` - Returns total supply and address count

When these methods are not available or throw errors, the Explorer2 API returns a 501 error.

## What Was Fixed

### 1. Capability Detection (rpcChainClient.ts)

**Before**: The RPC client didn't check if `getRichList` and `getTotalSupply` methods were available on the node.

**After**: Added capability detection that checks for these methods at startup:
```typescript
interface Capabilities {
  // ... other capabilities
  hasRichList: boolean
  hasTotalSupply: boolean
}
```

The detection runs when the API starts and logs the results.

### 2. Error Logging (service.ts)

**Before**: Errors were caught and silently discarded, making it impossible to diagnose the real issue.

**After**: Actual RPC errors are now logged before falling back:
```typescript
} catch (error) {
  log.warn({ error, limit, safeOffset }, 'getRichList RPC call failed')
  // Fall through to local implementation if RPC fails
}
```

### 3. Early Error Detection (rpcChainClient.ts)

**Before**: Methods would fail with generic "Failed to get rich list from RPC" error.

**After**: Methods check capabilities first and fail fast with clear error messages:
```typescript
const caps = await this.detectCapabilities()
if (!caps.hasRichList) {
  throw new Error('Node does not support state.getRichList')
}
```

## How to Diagnose

### Step 1: Check API Logs

When the API starts, it logs capability detection results:
```
INFO: Detecting node capabilities...
INFO: Capabilities detected { 
  capabilities: {
    hasMempool: true,
    hasPeers: true,
    hasReceipts: true,
    hasStateBalance: true,
    hasRichList: false,    <-- Look for this!
    hasTotalSupply: false  <-- And this!
  }
}
```

If `hasRichList` or `hasTotalSupply` is `false`, the Rich List won't work.

### Step 2: Check Diagnostics Page

Visit `http://localhost:3001/diagnostics` in the web UI to see:
- Connection mode (RPC or Local DB)
- RPC URL
- Detected capabilities

### Step 3: Test API Endpoints Directly

Use curl or the test script:
```bash
# Using the test script
cd explorer2/api
./scripts/test_richlist_api.sh http://localhost:8081

# Or manually with curl
curl http://localhost:8081/api/richlist?limit=10
curl http://localhost:8081/api/richlist/summary
```

### Step 4: Check RPC Method Registration

Verify the RPC methods are registered in the Python node:
```bash
# Check if methods exist in the RPC server
cd /path/to/animica
grep -r "state.getRichList\|state.getTotalSupply" rpc/methods/state.py
```

You should see:
```python
@method(
    "state.getRichList",
    desc="Return addresses sorted by balance (descending). Supports pagination with limit/offset.",
)
def state_get_rich_list(limit: int = 100, offset: int = 0) -> dict:
    ...

@method(
    "state.getTotalSupply",
    desc="Return the total supply (sum of all account balances).",
)
def state_get_total_supply() -> dict:
    ...
```

## How to Fix

### If Running in RPC Mode

1. **Ensure the node has the RPC methods implemented**
   
   Check that `rpc/methods/state.py` contains `state_get_rich_list` and `state_get_total_supply` functions with the `@method` decorator.

2. **Restart the node**
   
   If the methods were recently added, restart the node to register them:
   ```bash
   # Stop the node
   pkill -f "python.*rpc"
   
   # Start the node with RPC enabled
   python -m rpc.server --host 0.0.0.0 --port 8545
   ```

3. **Restart the Explorer2 API**
   
   The API detects capabilities at startup, so restart it after fixing the node:
   ```bash
   cd explorer2/api
   pnpm start
   ```

4. **Verify the fix**
   
   Check the API logs for successful capability detection, then test the endpoints.

### If Running in Local DB Mode

The Rich List feature is **not currently supported in Local DB mode**. You must use RPC mode.

To switch to RPC mode:
1. Start an Animica node with RPC enabled
2. Set `EXPLORER2_RPC_URL` environment variable
3. Restart the Explorer2 API

### If RPC Methods Fail

If the methods exist but throw errors, check:

1. **State DB availability**: The methods require access to `StateDB.iter_accounts()`
   ```python
   # In state.py
   if hasattr(sdb, "iter_accounts"):
       for addr_bytes, account in sdb.iter_accounts():
           ...
   else:
       raise rpc_errors.InternalError("State DB does not support account iteration")
   ```

2. **Bech32 encoding**: The methods need the `pq.py.utils.bech32` module for address encoding
   ```python
   from pq.py.utils import bech32 as _bech32
   ```

3. **Chain head availability**: The methods query the chain head height
   ```python
   from rpc.methods.chain import chain_get_head
   head = chain_get_head()
   height = head.get("height", 0)
   ```

## Common Error Messages

### "Node does not support state.getRichList RPC method"

**Cause**: The RPC method is not registered or fails during capability detection.

**Fix**: 
1. Check `rpc/methods/state.py` has the method
2. Restart the node
3. Restart the API

### "State DB not available"

**Cause**: The node's state database is not accessible.

**Fix**:
1. Ensure the node has a valid state database
2. Check that the RPC context includes `state_db`
3. Verify the node is synced

### "State DB does not support account iteration"

**Cause**: The StateDB implementation doesn't have the `iter_accounts()` method.

**Fix**:
1. Check `core/db/state_db.py` or equivalent has `iter_accounts()`
2. Update the StateDB implementation if needed
3. Restart the node

## Testing

Use the provided test script to verify the fix:
```bash
cd explorer2/api
./scripts/test_richlist_api.sh http://localhost:8081
```

Or use the existing verification script:
```bash
cd explorer2/api
node scripts/verify_richlist.js --sample 10
```

## Next Steps

If the issue persists after following this guide:

1. **Enable debug logging**:
   ```bash
   EXPLORER2_LOG_LEVEL=debug pnpm -C explorer2/api start
   ```

2. **Check the full error stack**:
   Look for `WARN` or `ERROR` messages in the API logs that show the actual RPC error.

3. **Test the RPC method directly**:
   ```bash
   curl -X POST http://localhost:8545/rpc \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"state.getRichList","params":[10, 0],"id":1}'
   ```

4. **File an issue** with:
   - API logs showing the capability detection
   - RPC error messages
   - Node version and configuration
   - Whether the node has any accounts with balances
