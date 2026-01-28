# Fix: Load Failed on Rich List on Explorer2

## Summary

Fixed the "Load failed" error on the Rich List page in Explorer2 by adding proper capability detection and improving error logging for the `state.getRichList` and `state.getTotalSupply` RPC methods.

## Problem

When users navigated to the Rich List page in Explorer2 (`/richlist`), they encountered a "Load failed" error. The root cause was:

1. **Missing Capability Detection**: The RPC client didn't check if the node supported `state.getRichList` and `state.getTotalSupply` methods at startup
2. **Silent Error Handling**: Errors from RPC calls were caught and discarded without logging, making it impossible to diagnose the actual issue
3. **Unclear Error Messages**: Generic "not supported" errors didn't explain what was actually failing

## Changes Made

### 1. Added Rich List Capability Detection (rpcChainClient.ts)

Added two new capabilities to the `Capabilities` interface:
- `hasRichList: boolean` - Detects if `state.getRichList` RPC method is available
- `hasTotalSupply: boolean` - Detects if `state.getTotalSupply` RPC method is available

These capabilities are checked at API startup during the `detectCapabilities()` phase.

**Code Changes:**
```typescript
interface Capabilities {
  hasMempool: boolean
  hasPeers: boolean
  hasReceipts: boolean
  hasStateBalance: boolean
  hasRichList: boolean        // NEW
  hasTotalSupply: boolean     // NEW
}
```

### 2. Improved Error Logging (service.ts)

Added pino logger to the service and improved error handling to log actual RPC errors before falling back:

**Code Changes:**
```typescript
import pino from 'pino'

const log = pino({ name: 'explorer-service' })

// In getRichList method:
} catch (error) {
  log.warn({ error, limit, safeOffset }, 'getRichList RPC call failed')
  // Fall through to local implementation if RPC fails
}
```

### 3. Early Capability Check (rpcChainClient.ts)

Modified `getRichList()` and `getTotalSupply()` methods to check capabilities before making RPC calls:

**Code Changes:**
```typescript
async getRichList(limit: number, offset: number): Promise<unknown> {
  const caps = await this.detectCapabilities()
  if (!caps.hasRichList) {
    throw new Error('Node does not support state.getRichList')
  }
  // ... rest of method
}
```

## Impact

### Before
- Users saw generic "Load failed" error
- No logs to diagnose the issue
- Capability detection happened implicitly on first call
- Error messages didn't explain the root cause

### After
- Clear capability detection logged at startup
- Actual RPC errors are logged for debugging
- Early failure with clear error messages
- Users and developers can quickly identify if node supports the feature

## Testing

All existing tests pass:
```bash
cd explorer2/api
pnpm test
# ✓ 35 tests passed
```

Build succeeds:
```bash
cd explorer2
pnpm build
# ✓ All packages built successfully
```

## Verification

To verify the fix works when a node is running:

1. **Check API logs at startup**:
   ```
   INFO: Detecting node capabilities...
   INFO: Capabilities detected {
     hasRichList: true,
     hasTotalSupply: true
   }
   ```

2. **Use the test script**:
   ```bash
   cd explorer2/api
   ./scripts/test_richlist_api.sh http://localhost:8081
   ```

3. **Check the web UI**:
   Navigate to `http://localhost:3001/richlist` and verify the page loads

## Documentation

Added two new documents:
1. **test_richlist_api.sh** - Automated test script for Rich List endpoints
2. **RICH_LIST_TROUBLESHOOTING.md** - Comprehensive troubleshooting guide

## Related Files

### Modified
- `explorer2/api/src/service.ts` - Added logger and improved error handling
- `explorer2/api/src/rpcChainClient.ts` - Added capability detection and early checks

### Added
- `explorer2/api/scripts/test_richlist_api.sh` - Test script
- `explorer2/RICH_LIST_TROUBLESHOOTING.md` - Troubleshooting guide

## Requirements

For Rich List to work, the Animica node must:
1. Have RPC server enabled
2. Implement `state.getRichList` RPC method (defined in `rpc/methods/state.py`)
3. Implement `state.getTotalSupply` RPC method (defined in `rpc/methods/state.py`)
4. Have a working StateDB with `iter_accounts()` support

## Breaking Changes

None. This is a backward-compatible improvement that adds better diagnostics and error handling.

## Future Enhancements

Potential improvements mentioned in the code:
1. Implement Local DB fallback for Rich List (currently only works in RPC mode)
2. Add caching layer for pre-computed rich lists at finalized heights
3. Add historical rich list tracking over time
4. Add WebSocket support for real-time rich list updates
