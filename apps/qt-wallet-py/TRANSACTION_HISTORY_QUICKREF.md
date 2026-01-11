# Transaction History Quick Reference

## For Wallet Users

### View Transaction History
1. Open wallet and unlock
2. Select an account from the list
3. Click "Transactions" tab
4. View your transaction history

### Search for a Transaction
1. Go to Transactions tab
2. Enter transaction hash in search box
3. Click "Search" button
4. View transaction details in popup

### Filter Transactions
- Use status dropdown to filter:
  - **All**: Show all transactions
  - **Pending**: Only unconfirmed transactions
  - **Confirmed**: Only transactions in blocks
  - **Failed**: Only failed transactions

### Resync with Blockchain
- Click "Resync" button to scan recent blocks
- Useful if pending transactions aren't updating

## For Developers

### Using the RPC API

#### List Transactions
```python
# Via Python
result = await walletd_manager.call_rpc("wallet.txs.list", {
    "address": "anim1qyfe...",  # optional: filter by address
    "limit": 100,                # optional: max results (default: 100)
    "cursor": 0,                 # optional: offset for pagination
    "status": "pending"          # optional: filter by status
})

# Returns
{
    "transactions": [...],
    "next_cursor": 100 or None
}
```

#### Lookup Transaction by Hash
```python
result = await walletd_manager.call_rpc("wallet.txs.lookup", {
    "hash": "0x1234..."
})

# Returns transaction details or None if not found
```

#### Resync Transactions
```python
result = await walletd_manager.call_rpc("wallet.txs.resync", {
    "address": "anim1qyfe...",
    "window": 1000  # optional: number of blocks to scan (default: 1000)
})

# Returns
{
    "scanned_from": 11345,
    "scanned_to": 12345,
    "updated": 3
}
```

### Adding Transaction Tracking to New Features

When sending a transaction, pass `tx_details` to enable automatic tracking:

```python
# In UI code
tx_details = {
    "from": from_addr,
    "to": to_addr,
    "value": value,
    "gas_limit": gas_limit,
    "max_fee": max_fee,
    "nonce": nonce,
}

result = await walletd_manager.tx_send(
    signed_tx=signed_tx,
    tx_details=tx_details  # This enables history tracking
)
```

### Accessing Transaction History Directly

```python
from animica_qt_wallet.walletd.tx_history import TxHistory
from pathlib import Path

# Initialize
history = TxHistory(Path("~/.animica-wallet/tx_history.json"))

# Add pending transaction
history.add_pending(
    tx_hash="0x...",
    from_addr="anim1...",
    to_addr="anim1...",
    value=1000000000000000000,
    gas_limit=21000,
    max_fee=1000000000,
    nonce=0
)

# Update status
history.update_status(
    "0x...",
    "confirmed",
    block_number=12345
)

# Query
entry = history.get("0x...")
entries = history.list(address="anim1...", status_filter="pending")
```

## Transaction Status Values

- **pending**: Transaction submitted but not yet confirmed
- **confirmed**: Transaction included in a block
- **failed**: Transaction failed (with error message)

## File Locations

- Transaction history: `~/.animica-wallet/tx_history.json`
- Format: JSON array of transaction objects
- Auto-saves on every change

## Troubleshooting

### Transactions not appearing
- Check if wallet is unlocked
- Verify correct account is selected
- Click "Refresh" button
- Try "Resync" to scan blockchain

### Search not finding transaction
- Ensure transaction hash is correct (with or without 0x prefix)
- Transaction may not be on chain yet (check pending)
- Transaction may be in a different network (mainnet vs testnet)

### Status not updating
- Pending transactions update every 5 seconds automatically
- Click "Resync" to force update from blockchain
- May take time for blocks to be mined

## Implementation Notes

### Performance
- History is loaded in memory on startup
- All operations are fast (< 1ms)
- Pagination prevents loading too many transactions at once

### Security
- Transaction history is stored locally only
- No sensitive data (private keys) in history
- History file has same permissions as wallet file

### Limitations
- Only tracks transactions sent through this wallet
- Cannot track incoming transactions unless rescanned
- Resync limited to recent blocks (configurable window)
- Full blockchain scan not implemented (would be slow)

## API Compatibility

All RPC methods follow the walletd JSON-RPC 2.0 format:

```json
{
  "jsonrpc": "2.0",
  "method": "wallet.txs.list",
  "params": {
    "address": "anim1...",
    "limit": 100
  },
  "id": 1
}
```

Response:
```json
{
  "jsonrpc": "2.0",
  "result": {
    "transactions": [...],
    "next_cursor": 100
  },
  "id": 1
}
```
