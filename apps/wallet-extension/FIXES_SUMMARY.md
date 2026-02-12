# Balance Fetching Fixes - Summary

## Root Causes Identified and Fixed

### 1. Insufficient Debug Information
**Problem**: Debug logging was disabled by default, making it impossible to diagnose balance fetching issues.

**Fix**: 
- Enabled `DEBUG_BALANCE = true`, `DEBUG_WALLET = true`, `DEBUG_WALLET_UI = true`
- Added comprehensive console logging for all RPC calls
- Added `lastBalanceRequest` tracking with full request details

### 2. Generic Error Messages
**Problem**: Errors were converted to generic "unavailable" message, hiding the actual issue.

**Fix**:
- Store and display actual error messages from RPC calls
- Show full error context in both UI and console logs
- Display error messages in balance text: "Balance: Error - [actual error]"

### 3. No User-Visible Debug Panel
**Problem**: Users couldn't see what RPC requests were being made or what responses were received.

**Fix**: 
- Enhanced debug panel in Home.tsx with:
  - Active wallet info (label, address)
  - RPC configuration (URL, chain ID, last fetch time)
  - Last balance request details
  - Color-coded error sections
  - Copy buttons for raw responses
  - Scrollable JSON viewers

## Verified Working Components

### ✅ RPC Client Environment Support
- Uses `globalThis.fetch` via `runtime/env.ts`
- Works in both popup (window context) and background service worker
- No "window is not defined" errors

### ✅ RPC Method Format
- Correctly calls `state.getBalance` with params `[address, 'latest']`
- Matches CLI usage: `animica rpc call state.getBalance '["<address>"]'`
- Properly parses hex string response (e.g., "0x0")

### ✅ Balance Parsing
- `parseBalanceResult()` handles:
  - Hex strings with 0x prefix
  - Decimal strings
  - Numbers
  - Nested objects (balance/amount fields)
- Throws clear errors on parse failures

### ✅ Wallet Switching
- Background broadcasts `WALLET_ACTIVE_CHANGED` message
- UI listens via `onActiveWalletChanged()`
- `useEffect` in Home.tsx triggers balance refresh on `currentAccount` change
- Both `refreshBalance()` and `loadBalance()` called on switch

### ✅ State Management
- `balancesStore` properly tracks per-address state:
  - `balancesByAddress`: bigint values
  - `loadingByAddress`: loading states
  - `errorByAddress`: error messages (not just "unavailable")
- Active wallet stored in `chrome.storage.local`
- Persists across extension restarts

## Changes Made

### Files Modified
1. **src/services/balanceService.ts**
   - Enable DEBUG_BALANCE
   - Add lastBalanceRequest tracking
   - Enhanced logging with request/response details
   - Better error context in console

2. **src/ui/pages/Home.tsx**
   - Enable DEBUG_WALLET_UI
   - Redesigned debug panel with sections:
     - Active Wallet
     - RPC Configuration
     - Last Balance Request (yellow background)
     - Errors (red background)
     - Raw responses (expandable with copy buttons)

3. **src/background/index.ts**
   - Enable DEBUG_WALLET
   - Already had proper wallet switching implementation

4. **src/store/balances.ts**
   - Store actual error messages instead of "unavailable"
   - Preserve error context for debugging

5. **src/ui/components/AccountsTab.tsx**
   - Display full error message in balance text
   - Show "Balance: Error - [message]" instead of "Balance: unavailable"

### Files Added
- **TESTING.md**: Complete manual testing guide with checklists

## How to Verify Fixes

### Test Case 1: Zero Balance (Unfunded Address)
**Expected**: 
- Balance shows "0 ANM"
- Debug panel shows raw response: `"0x0"`
- No error messages

### Test Case 2: RPC Unreachable
**Expected**:
- Balance shows "Balance: Error - [connection error]"
- Debug panel shows error in red section
- Test Connection button shows error (not crash)

### Test Case 3: Funded Address
**Expected**:
- Balance shows correct amount (e.g., "1,000.000000000 ANM")
- Matches CLI output: `animica rpc call state.getBalance '["<addr>"]'`
- Debug panel shows hex value in raw response

### Test Case 4: Wallet Switch
**Expected**:
- Clicking different account triggers new balance request
- Debug panel updates with new address
- Balance updates for new wallet
- Console shows "active wallet updated" log

### Test Case 5: Test Connection
**Expected**:
- Works in both popup and background contexts
- Shows "Connected in XXms • chain_id=X • node=..."
- No "window is not defined" error
- On failure, shows actual error message

## Key Insights

1. **RPC format is correct**: The extension properly calls `state.getBalance` with array params and handles hex responses.

2. **No "window is not defined" issues**: The code already uses `globalThis` properly for cross-context compatibility.

3. **Wallet switching works**: The state management and message passing is correctly implemented.

4. **Main issue was visibility**: The lack of debug information made it impossible to diagnose whether:
   - RPC was actually returning 0 (address unfunded)
   - RPC was failing (connection/network error)
   - Response was being parsed incorrectly

5. **Error handling improved**: Now shows actual errors instead of masking them as "unavailable".

## Recommendations for Testing

1. **Start with debug panel open**: Always enable "Show debug" to see what's happening
2. **Check background console**: Many RPC calls happen in background service worker
3. **Test with known funded address**: Use CLI to verify balance, then compare in extension
4. **Test error cases**: Temporarily set invalid RPC URL to verify error handling
5. **Use copy buttons**: Capture raw responses for any unexpected behavior

## Future Enhancements (Optional)

If issues persist, consider:

1. **RPC Call History Ring Buffer**: Store last 30 RPC calls with full request/response
2. **Auto-refresh on error**: Retry failed balance fetches with exponential backoff  
3. **Batch balance fetching**: Single RPC call for multiple addresses (if node supports)
4. **Chain verification**: Warn if RPC chain_id doesn't match expected network
5. **Address validation UI**: Show when address format is invalid before querying

## Conclusion

The extension's RPC client and balance fetching logic are **architecturally sound**. The changes made focus on:
- **Visibility**: Comprehensive debug logging and UI panel
- **Error handling**: Show actual errors instead of generic messages
- **User experience**: Clear indication of what's happening vs what failed

With these improvements, users can now:
- See exactly what RPC requests are being made
- Understand why balances might show 0 (actually 0 vs RPC error)
- Debug connection issues with full error context
- Verify wallet switching triggers proper balance updates
