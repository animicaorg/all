# Wallet Chain Integration Feature

## Overview

The qt-wallet-py application now includes chain integration via the walletd proxy server. This allows the wallet UI to display real-time blockchain information and account balances.

## Features

### 1. Chain RPC Proxying in walletd

The walletd server now proxies safe read-only RPC calls to the node:

- `chain.getHead` - Get current blockchain head (height, hash)
- `state.getBalance` - Get account balance for an address
- `net.peers` - Get list of connected peers
- `net.peerCount` - Get count of connected peers

These methods are available through the walletd JSON-RPC interface and automatically forward requests to the node RPC endpoint when the node is running.

### 2. Overview Tab in UI

The wallet UI now includes two tabs:

#### Overview Tab
Displays real-time chain status information:
- **Height**: Current blockchain height
- **Best Hash**: Hash of the current head block (truncated for display)
- **Sync Status**: Shows "Synced", "Not connected", or "Error"
- **Peers**: Number of connected peers

Selected Account Balance:
- **Address**: Currently selected account address (auto-selected from accounts table)
- **Balance**: Current balance in ANM (formatted with 9 decimal places)

#### Node Tab
Contains the existing node control interface:
- Network selector (mainnet/testnet)
- Start/Stop node buttons
- Node logs viewer

### 3. Auto-refresh Timers

The UI automatically refreshes data at regular intervals:
- **Chain info**: Updates every 3 seconds (height, hash, sync status, peers)
- **Node status**: Updates every 2 seconds (running state, network)
- **Node logs**: Updates every 2 seconds
- **Accounts**: Updates every 5 seconds

### 4. Error Handling

When the node is not running:
- Chain status displays clear message: "Node is not running. Start the node to see chain status."
- All chain-related fields show "—" placeholder
- Balance shows "—" when node unavailable
- No UI freezing or crashes

When RPC calls fail:
- Error messages displayed in the UI
- Graceful fallback to default values
- Proper async handling prevents blocking

## Implementation Details

### walletd Server Changes
- Added `_proxy_to_node()` function to forward RPC calls
- Added handlers for chain.getHead, state.getBalance, net.peers, net.peerCount
- Uses 5-second timeout for node RPC calls
- Returns proper error messages on failure

### walletd_manager Changes
- Added `chain_get_head()` method
- Added `state_get_balance(address)` method
- Added `net_peers()` method
- Added `net_peer_count()` method

### UI Changes
- Added QTabWidget with Overview and Node tabs
- Added chain status labels (height, hash, sync, peers)
- Added balance display for selected account
- Added `_chain_info_timer` for periodic updates
- Added `_refresh_chain_info()` and `_update_chain_info()` methods
- Added `_refresh_selected_balance()` for balance updates
- Added account selection handler to update balance on selection change

## Usage

1. **Start the wallet application**
   ```bash
   cd apps/qt-wallet-py
   ./run.sh
   ```

2. **Start the node**
   - Go to the "Node" tab
   - Select network (mainnet or testnet)
   - Click "Start Node"
   - Wait for node to start (status bar shows "Node: running")

3. **View chain status**
   - Go to the "Overview" tab
   - Chain status updates automatically every 3 seconds
   - Select an account in the accounts table to see its balance

4. **Monitor the network**
   - Height increases as new blocks are mined
   - Peer count shows number of connected peers
   - Balance updates reflect confirmed transactions

## Testing

### Manual Testing Steps

1. **Test with node stopped**
   - Open wallet
   - Go to Overview tab
   - Verify message: "Node is not running..."
   - All fields should show "—"

2. **Test with node starting**
   - Go to Node tab
   - Click "Start Node"
   - Go back to Overview tab
   - Wait for chain info to appear (3-5 seconds)

3. **Test with node running**
   - Verify height updates (matches actual chain height)
   - Verify hash changes when new blocks arrive
   - Verify peer count is reasonable (>0 on testnet/mainnet)
   - Select different accounts and verify balance updates

4. **Test account selection**
   - Unlock wallet (if locked)
   - Create or select an account
   - Verify balance displays for selected account
   - Change selection and verify balance updates

5. **Test error handling**
   - Stop the node while on Overview tab
   - Verify graceful degradation (no crashes)
   - Restart node and verify recovery

## Future Enhancements

Possible improvements:
- WebSocket subscription for real-time updates (instead of polling)
- More detailed sync progress (percentage, blocks behind)
- Inbound vs outbound peer count breakdown
- Network health indicators
- Transaction history for selected account
- Mempool transaction count and size
