# Transaction History UI Guide

## Main Transactions Tab

```
╔════════════════════════════════════════════════════════════════════════════╗
║                          Animica Wallet                                    ║
╠════════════════════════════════════════════════════════════════════════════╣
║                                                                            ║
║  Accounts:                                                                 ║
║  ┌────────────────────────────────────────────────────────────────────┐  ║
║  │ Label          │ Address                                           │  ║
║  │ Main Account   │ anim1qyfe6g57j7n9vqqw4n0pxq9eukxm4s7q8xw8z  ◀───┤  ║
║  │ Savings        │ anim1qyfe6g57j7n9vqqw4n0pxq9eukxm4s7q8xw9a       │  ║
║  └────────────────────────────────────────────────────────────────────┘  ║
║                                                                            ║
║  ┌──────────────────────────────────────────────────────────────────┐    ║
║  │ [Overview] [Send] [Receive] [Transactions] [Node]              │    ║
║  ├──────────────────────────────────────────────────────────────────┤    ║
║  │                                                                  │    ║
║  │  Transaction History                                             │    ║
║  │                                                                  │    ║
║  │  Filter: [All ▼]        Search: [________________] [Search]     │    ║
║  │                                      [Refresh] [Resync]          │    ║
║  │                                                                  │    ║
║  │  ┌───────────────────────────────────────────────────────────┐  │    ║
║  │  │ Hash          │ From      │ To        │ Value   │ Status │ Block │ ║
║  │  ├───────────────────────────────────────────────────────────┤  │    ║
║  │  │ 0x1234...cdef │ anim1...8z│ anim1...9a│ 1.5 ANIM│ ✓ CONFIRMED│12345│ ║
║  │  │ 0x5678...abcd │ anim1...8z│ anim1...9b│ 0.5 ANIM│ ⏱ PENDING  │  —  │ ║
║  │  │ 0x9abc...1234 │ anim1...9a│ anim1...8z│ 2.0 ANIM│ ✓ CONFIRMED│12340│ ║
║  │  │ 0xdef0...5678 │ anim1...8z│ (contract)│ 0.0 ANIM│ ✗ FAILED   │  —  │ ║
║  │  └───────────────────────────────────────────────────────────┘  │    ║
║  │                                                                  │    ║
║  │  Showing 4 transactions                                          │    ║
║  └──────────────────────────────────────────────────────────────────┘    ║
╚════════════════════════════════════════════════════════════════════════════╝
```

## Transaction Details Dialog

When you double-click a transaction or search for one:

```
╔══════════════════════════════════════════════════════════════╗
║                   Transaction Details                        ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  Hash:                                                       ║
║  0x1234567890abcdef1234567890abcdef1234567890abcdef12345678 ║
║                                                              ║
║  Status:         ✓ CONFIRMED                                ║
║                                                              ║
║  Block:          12345                                       ║
║                                                              ║
║  From:                                                       ║
║  anim1qyfe6g57j7n9vqqw4n0pxq9eukxm4s7q8xw8z                ║
║                                                              ║
║  To:                                                         ║
║  anim1qyfe6g57j7n9vqqw4n0pxq9eukxm4s7q8xw9a                ║
║                                                              ║
║  Value:          1.500000 ANIM                              ║
║                                                              ║
║  Gas Limit:      21000                                       ║
║                                                              ║
║  Max Fee:        1000000000 wei                             ║
║                                                              ║
║  Nonce:          5                                           ║
║                                                              ║
║  Time:           2024-01-11 10:30:45                        ║
║                                                              ║
║                                         [OK]                 ║
╚══════════════════════════════════════════════════════════════╝
```

## Status Indicators

### In Transaction List

```
✓ CONFIRMED  (Green)   - Transaction included in a block
⏱ PENDING    (Yellow)  - Transaction submitted but not confirmed
✗ FAILED     (Red)     - Transaction failed with error
```

### Filter Options

```
┌─────────────┐
│ All      ▼ │  ← Shows all transactions
├─────────────┤
│ All         │
│ Pending     │  ← Only unconfirmed transactions
│ Confirmed   │  ← Only transactions in blocks
│ Failed      │  ← Only failed transactions
└─────────────┘
```

## User Interactions

### 1. Viewing Transactions
- Select account from list
- Click "Transactions" tab
- See all transactions for that account
- Auto-refreshes every 5 seconds

### 2. Filtering
- Click status dropdown
- Select filter (All/Pending/Confirmed/Failed)
- List updates immediately

### 3. Searching
- Type/paste transaction hash in search box
- Click "Search" button
- Transaction details dialog opens if found
- "Not Found" message if not found

### 4. Viewing Details
- Double-click any transaction row
- Transaction details dialog opens
- All fields are read-only
- Hash and addresses can be selected/copied

### 5. Refreshing
- Click "Refresh" to manually update list
- Click "Resync" to scan blockchain for updates
- Status message shows progress and results

## Example Workflow: Sending and Tracking

```
1. User sends 1.5 ANIM to address B
   ↓
2. Transaction immediately appears in list as "PENDING"
   Hash: 0x1234...cdef
   Status: ⏱ PENDING
   ↓
3. Auto-refresh updates every 5 seconds
   ↓
4. Once confirmed (or user clicks Resync):
   Status: ✓ CONFIRMED
   Block: 12345
   ↓
5. User can double-click to see full details
```

## Error Handling

### Failed Transaction Example

```
╔══════════════════════════════════════════════════════════════╗
║                   Transaction Details                        ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  Hash:          0xdef0...5678                               ║
║                                                              ║
║  Status:        ✗ FAILED                                    ║
║                                                              ║
║  Error:         Insufficient funds                          ║
║                                                              ║
║  From:          anim1qyfe...8xw8z                           ║
║  To:            (contract)                                   ║
║  Value:         0.000000 ANIM                               ║
║  Gas Limit:     100000                                       ║
║  Max Fee:       5000000000 wei                              ║
║                                                              ║
║                                         [OK]                 ║
╚══════════════════════════════════════════════════════════════╝
```

## Keyboard Shortcuts

- **Enter** on search field: Trigger search
- **Double-click** transaction row: Open details
- **Ctrl+R** (future): Refresh list
- **Esc** in details dialog: Close dialog

## Responsive Behavior

- Table columns resize to fit content
- Long hashes are truncated with "..." in the middle
- Addresses show prefix...suffix format
- Values formatted with 6 decimal places
- All text is selectable for copying

## Data Persistence

```
Transaction History Storage:
~/.animica-wallet/tx_history.json

{
  "version": 1,
  "transactions": [
    {
      "tx_hash": "0x1234...",
      "from": "anim1...",
      "to": "anim1...",
      "value": 1500000000000000000,
      "status": "confirmed",
      "timestamp": 1704988800.0,
      "block_number": 12345,
      "gas_limit": 21000,
      "max_fee": 1000000000,
      "nonce": 5
    }
  ]
}
```

## Performance Characteristics

- **Initial Load**: < 100ms (for 100 transactions)
- **Filter**: Instant (in-memory)
- **Search**: < 50ms (local) or network latency (remote)
- **Refresh**: Network latency
- **Resync**: ~1-5 seconds (for 1000 blocks)

## Security Considerations

✅ No private keys in transaction history  
✅ Local storage only (not sent to servers)  
✅ File permissions match wallet file (600)  
✅ Read-only display (no transaction editing)  
✅ Hash validation before search  
