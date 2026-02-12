# Wallet Extension Testing Guide

## Build and Load Extension

### Build
```bash
cd apps/wallet-extension
npm install
npx vite build
```

The extension will be built to `dist/` directory.

### Load in Chrome/Chromium
1. Open Chrome and navigate to `chrome://extensions/`
2. Enable "Developer mode" (toggle in top right)
3. Click "Load unpacked"
4. Select the `apps/wallet-extension/dist` directory
5. The Animica Wallet extension should now appear

## Manual Test Checklist

### 1. Initial Setup
- [ ] Click extension icon in toolbar
- [ ] Create new wallet with password
- [ ] Verify first account is created

### 2. Balance Display (Main Test)
- [ ] Open extension popup
- [ ] Click "Show debug" button
- [ ] Verify "Debug details" section appears
- [ ] Check "RPC Configuration" shows correct URL
- [ ] Note the "Last Balance Request" section

**Expected**: If balance is 0, debug panel should show:
- Raw response from RPC (likely `"0x0"` for zero balance)
- No error in the error section
- Request details showing the address queried

**If error occurs**: 
- Error section should be red with the actual error message
- Raw response should show the RPC error details
- Use "Copy Response" button to capture full error

### 3. Test Connection Button
- [ ] Go to Settings tab
- [ ] Verify default RPC URL is shown
- [ ] Click "Test Connection" button
- [ ] Verify NO "window is not defined" error
- [ ] Should show: `Connected in XXms • chain_id=X • node=...`

**If RPC is unreachable**:
- Should show error message (not crash)
- Should not show "window is not defined"

### 4. Wallet Switching
- [ ] Create a second account (Accounts tab → "+ New Account")
- [ ] Click on the new account to select it
- [ ] Verify the active wallet indicator changes
- [ ] Check that balance request is triggered for new account (in debug panel)
- [ ] Switch back to first account
- [ ] Verify balance updates again

### 5. Balance with Funded Address
If you have a funded address on testnet/devnet:

- [ ] Import wallet with funded address or send funds to existing address
- [ ] Verify balance shows > 0 ANM
- [ ] Verify "Confirmed:" balance matches CLI query:
  ```bash
  animica rpc call state.getBalance '["<address>"]'
  ```

### 6. RPC Configuration
- [ ] Go to Settings tab
- [ ] Change RPC URL to a different endpoint
- [ ] Click "Save"
- [ ] Verify "RPC endpoint saved" message
- [ ] Click "Test Connection" again
- [ ] Go back to Accounts tab
- [ ] Verify balances are refreshed with new RPC endpoint

### 7. Error States (Negative Test)
- [ ] Go to Settings tab
- [ ] Set RPC URL to invalid endpoint: `http://localhost:9999`
- [ ] Click "Test Connection"
- [ ] Verify error message is displayed (not crash)
- [ ] Click "Save" to persist bad URL
- [ ] Go to Accounts tab
- [ ] Verify balances show "Balance: Error - ..." with actual error
- [ ] Check debug panel shows connection error
- [ ] Reset RPC to default

## Console Logs to Capture

### Background Service Worker Logs
1. Go to `chrome://extensions/`
2. Find "Animica Wallet"
3. Click "service worker" link (or "background page" in MV2)
4. Open DevTools console
5. Look for `[balance-service]` and `[wallet-bg]` prefixed logs

### Popup Logs
1. Open extension popup
2. Right-click anywhere → "Inspect"
3. Look for `[wallet-ui]` prefixed logs

## Common Issues and Solutions

### Issue: All balances show 0
**Check**:
1. Debug panel → Raw Balance Response
   - If shows `"0x0"` → Balance is actually 0, address needs funding
   - If shows error → RPC connection issue
2. Debug panel → RPC Configuration
   - Verify RPC URL is correct and reachable
3. Settings → Test Connection
   - Should succeed without errors

### Issue: "window is not defined"
**Check**:
1. Verify `runtime/env.ts` uses `globalThis` not `window`
2. Verify `RpcClient` uses `fetchFn` from `runtime/env`
3. Check background service worker console for the error

### Issue: Balance stuck after wallet switch
**Check**:
1. Background console logs for `active wallet updated`
2. Popup console for balance refresh calls
3. Debug panel should show new request with switched address

## Debug Panel Data Points

The debug panel shows:
- **Active Wallet**: Current wallet label and address
- **RPC Configuration**: URL, chain ID, last fetch time
- **Last Balance Request**: Address, RPC URL, chain ID, timestamp
- **Errors**: Balance or ping errors (red background)
- **Raw Balance Response**: Full JSON response from RPC
- **Raw Ping Response**: Full JSON from test connection

Use "Copy Response" buttons to capture data for bug reports.

## Expected Console Output

### Successful Balance Fetch
```
[balance-service] Calling state.getBalance { address: 'anim1...', rpcUrl: 'http://...', chainId: 1337 }
[balance-service] state.getBalance raw response { address: 'anim1...', raw: '0x0', rawType: 'string' }
[balance-service] state.getBalance parsed result { address: 'anim1...', parsed: '0' }
```

### Failed Balance Fetch
```
[balance-service] Calling state.getBalance { address: 'anim1...', rpcUrl: 'http://...', chainId: 1337 }
[balance-service] getBalance failed: { address: 'anim1...', error: 'Request timed out...', raw: undefined }
```

### Wallet Switch
```
[wallet-bg] active wallet updated { walletId: 'anim1...', label: 'Account 2' }
[wallet-ui] loadBalance { address: 'anim1...', active: 'anim1...', ... }
```
