# Explorer Error Handling Guide

## Overview

The Animica Explorer now includes comprehensive error handling to provide users with actionable feedback when issues occur, particularly around RPC connectivity and data loading.

## Key Features

### 1. Global Error Boundary

A React Error Boundary wraps the entire application to catch any unhandled errors in React components:

```typescript
<ErrorBoundary>
  <App />
</ErrorBoundary>
```

**Features:**
- Catches React component errors before they crash the entire app
- Displays user-friendly error UI with error details
- Provides "Reload Page" and "Reset & Reload" options
- Emits error toasts for visibility

**Location:** `src/components/ErrorBoundary.tsx`

### 2. Global Error Handlers

Unhandled promise rejections and global errors are caught and displayed to users:

```typescript
installGlobalErrorHandlers()
```

**Handles:**
- `unhandledrejection` events (promise rejections)
- `error` events (global JavaScript errors)
- Categorizes errors and provides context
- Shows toast notifications with troubleshooting steps

**Location:** `src/utils/errorHandler.ts`

### 3. Enhanced Network Error Messages

Network connection errors now provide detailed, actionable feedback:

**Before:**
```
Failed to connect to RPC at http://localhost:8545: fetch failed
```

**After:**
```
Network error: Unable to reach RPC server at http://localhost:8545

💡 Troubleshooting:
• Check that the RPC server is running
• Verify the URL is correct
• Ensure your internet connection is stable
• Check firewall settings
```

### 4. Error Categorization

Errors are automatically categorized to provide relevant troubleshooting:

- **Network errors:** Connection failures, fetch errors
- **Timeout errors:** Slow or unresponsive servers
- **RPC errors:** JSON-RPC protocol errors
- **Parse errors:** Invalid JSON responses
- **CORS errors:** Cross-origin request blocked
- **Chain ID mismatch:** Configuration mismatch

**Example:**
```typescript
const context = categorizeError(error);
// Returns: { kind: 'network', message: '...', troubleshooting: [...] }
```

## Usage Examples

### Wrapping Async Operations

Use `withErrorHandling` to automatically handle errors in async functions:

```typescript
import { withErrorHandling } from './utils/errorHandler';

const fetchData = withErrorHandling(async () => {
  const response = await fetch('/api/data');
  return response.json();
}, 'Fetch Data');

// Errors are automatically caught and displayed as toasts
await fetchData();
```

### Safe Async Calls

Use `safeAsync` for operations where you want to handle errors without throwing:

```typescript
import { safeAsync } from './utils/errorHandler';

const data = await safeAsync(
  rpc.getBlock(height),
  'Failed to fetch block'
);

if (data === null) {
  // Handle error case (toast already shown)
}
```

### Manual Error Display

Show a specific error to the user:

```typescript
import { showErrorToast } from './utils/errorHandler';

try {
  await someOperation();
} catch (error) {
  showErrorToast(error, 'Operation Failed');
}
```

## Error UI States

### 1. Connection Error (Network Tab)

When the RPC connection fails, users see:

**Top Bar:**
- Red dot status indicator
- "Disconnected" label
- Chain ID shows "Unknown" if not fetched

**Home Page:**
- Large info icon
- "Unable to Connect to RPC Node" heading
- Detailed troubleshooting checklist:
  - RPC node URL and status
  - CORS configuration check
  - Network connectivity
  - Chain ID verification
- Link to browser console for technical details

### 2. Error Toast Notifications

All errors generate toast notifications with:
- **Error icon** (red color scheme)
- **Title:** Categorized error type
- **Message:** Detailed error with troubleshooting
- **Duration:** 10-12 seconds (longer for errors)
- **Dismissible:** Users can close manually

### 3. Error Boundary Fallback

If a React component crashes:
- Full-screen error UI
- "Something went wrong" heading
- Collapsible error details (message + stack trace)
- Two action buttons:
  - **Reload Page:** Simple refresh
  - **Reset & Reload:** Clear localStorage and refresh
- Troubleshooting hint about browser console

## Testing Error Scenarios

### Simulate Network Failure

1. Stop your RPC node
2. Refresh the explorer
3. **Expected:** Network error with troubleshooting steps

### Simulate Timeout

1. Add network throttling in DevTools (Network tab)
2. Set to "Slow 3G" or similar
3. Refresh the explorer
4. **Expected:** Timeout error with appropriate message

### Simulate Chain ID Mismatch

1. Edit `.env.local`: `VITE_CHAIN_ID=999999`
2. Refresh the explorer
3. **Expected:** Chain ID mismatch error with resolution steps

### Simulate CORS Error

1. Configure RPC server to block your origin
2. Refresh the explorer
3. **Expected:** CORS error with server configuration hint

### Simulate Component Error

In React DevTools, throw an error from any component:
```javascript
throw new Error('Test error');
```

**Expected:** Error boundary catches it and shows fallback UI

## Best Practices

### 1. Always Handle Promises

```typescript
// ❌ Bad: Unhandled promise
someAsyncFunction();

// ✅ Good: Handled with catch
someAsyncFunction().catch(handleError);

// ✅ Better: Wrapped with error handling
const wrappedFunction = withErrorHandling(someAsyncFunction);
await wrappedFunction();
```

### 2. Provide Context in Error Messages

```typescript
// ❌ Bad: Generic error
throw new Error('Failed');

// ✅ Good: Contextual error
throw new Error(`Failed to fetch block ${height}: ${reason}`);
```

### 3. Log Errors for Debugging

```typescript
// Always log errors before showing to user
console.error('[Component] Operation failed:', error);
showErrorToast(error, 'Operation Failed');
```

### 4. Use Appropriate Toast Duration

```typescript
// Short (4-5s): Info messages
emitToast({ kind: 'info', message: 'Block fetched', durationMs: 4500 });

// Medium (8-10s): Warnings
emitToast({ kind: 'warning', message: 'Slow connection', durationMs: 8000 });

// Long (10-12s): Errors with troubleshooting
emitToast({ kind: 'error', message: 'Detailed error...', durationMs: 12000 });
```

## Debugging

### Enable Verbose Logging

All network operations log to console with `[network]` prefix:

```
[network] Connecting to RPC: http://localhost:8545
[network] RPC client created successfully
[network] Fetching chain ID...
[network] Chain ID: 1337
[network] Connection established successfully
```

### Check Error Context

Errors include detailed context in console:

```typescript
console.error('[network] Connection error:', {
  name: 'NetworkError',
  message: 'fetch failed',
  url: 'http://localhost:8545',
  chainId: '1337',
  stack: '...'
});
```

### Browser Console (F12)

Look for:
- `[errorHandler]` - Global error handling
- `[network]` - Network/RPC operations
- `[blocksWithCache]` - Block caching
- `[sync]` - Background sync
- `[ws]` - WebSocket operations

## Error Codes Reference

### RPC Error Codes

Standard JSON-RPC 2.0 error codes:

| Code | Meaning | Handling |
|------|---------|----------|
| -32700 | Parse error | Shows parse error toast |
| -32600 | Invalid request | Shows RPC error toast |
| -32601 | Method not found | Shows RPC error toast |
| -32602 | Invalid params | Shows RPC error toast |
| -32603 | Internal error | Retries automatically (if configured) |
| -32000 to -32099 | Server error | Retries automatically |

### HTTP Error Codes

| Code | Meaning | Handling |
|------|---------|----------|
| 429 | Too many requests | Retries with exponential backoff |
| 500-599 | Server errors | Retries with exponential backoff |
| 404 | Not found | Shows error (no retry) |
| 403 | Forbidden | Shows error (no retry) |

## Future Improvements

- [ ] Offline mode with cached data
- [ ] Retry with user confirmation
- [ ] Error reporting/telemetry (opt-in)
- [ ] Context-sensitive help links
- [ ] Network diagnostics tool
- [ ] Error history/log viewer

## Related Files

- `src/components/ErrorBoundary.tsx` - React error boundary
- `src/utils/errorHandler.ts` - Error handling utilities
- `src/state/network.ts` - Network state & RPC connection
- `src/services/rpc.ts` - RPC client with retry logic
- `src/services/ws.ts` - WebSocket client with reconnect
- `test/unit/errorHandler.test.ts` - Error handling tests
