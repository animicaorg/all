# Miner GUI Improvements - Visual Guide

## Dashboard Tab Changes

### Before
```
┌─────────────────────────────────────────────────┐
│ Status                                          │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│ Chain ID:            --                         │
│ Block Height:        --                         │
│ Sync Status:         --                         │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│ Payout Information                              │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│ Payout Address:      anim1abc...def             │
│ Estimated Earnings:  --                         │
└─────────────────────────────────────────────────┘
```

### After
```
┌─────────────────────────────────────────────────┐
│ Status                                          │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│ Chain ID:            1337                       │ ← Now shows actual chain ID
│ Block Height:        1234                       │ ← Now shows actual height (updates every 5s)
│ Sync Status:         Synced                     │ ← Now shows sync status
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│ Payout Information                              │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│ Payout Address:      anim1abc...def             │
│ Balance:             10.500000000 ANM           │ ← NEW: Shows wallet balance
│ ┌─────────────────────────────────────────────┐ │
│ │       Refresh Balance                       │ │ ← NEW: Manual refresh button
│ └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

## Main Menu Changes

### Before
```
┌──────┬────────┬──────┐
│ File │ Mining │ Help │
└──────┴────────┴──────┘

File
├─ Exit (Ctrl+Q)
```

### After
```
┌──────┬────────┬──────┐
│ File │ Mining │ Help │
└──────┴────────┴──────┘

File
├─ Restart Setup Wizard  ← NEW: Rerun wizard
├─────────────────
└─ Exit (Ctrl+Q)
```

## New Wallet Tab

```
┌───────────────────────────────────────────────────────────┐
│ Tabs: [Dashboard] [Devices] [Pools] [Wallet] [Config]... │ ← NEW TAB
└───────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│ Wallet Information                              │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│ Address:             anim1abc...def             │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│ Send Transaction                                │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│ To Address:    ┌──────────────────────────────┐ │
│                │ anim1...                     │ │
│                └──────────────────────────────┘ │
│                                                 │
│ Amount:        ┌──────────────────────────────┐ │
│                │ 0.0                          │ │
│                └──────────────────────────────┘ │
│                Amount in ANM (e.g., 1.5)        │
│                                                 │
│                ┌──────────────────────────────┐ │
│                │    Send Transaction          │ │
│                └──────────────────────────────┘ │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│ Transaction Result                              │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│ ┌─────────────────────────────────────────────┐ │
│ │ Sending transaction...                      │ │
│ │ Running: python -m animica tx send ...     │ │
│ │                                             │ │
│ │ ✓ Transaction sent successfully!            │ │
│ │                                             │ │
│ │ Output:                                     │ │
│ │ Transaction hash: 0xabc123...               │ │
│ │ Status: pending                             │ │
│ └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

## Wizard: Import Wallet Improvements

### Before
```
┌─────────────────────────────────────────────────┐
│ Payout Address                                  │
│ Configure where mining rewards will be sent     │
├─────────────────────────────────────────────────┤
│                                                 │
│ Enter payout address:                           │
│ ┌─────────────────────────────────────────────┐ │
│ │ anim1...                                    │ │
│ └─────────────────────────────────────────────┘ │
│                                                 │
│ ┌──────────────────────────────────────────┐   │
│ │    Import from Wallets                   │   │ ← Only imported first wallet
│ └──────────────────────────────────────────┘   │
│                                                 │
│ Imported: My Wallet                             │
└─────────────────────────────────────────────────┘
```

### After
```
┌─────────────────────────────────────────────────┐
│ Payout Address                                  │
│ Configure your wallet address for receiving     │
│ mining rewards                                  │
├─────────────────────────────────────────────────┤
│                                                 │
│ Enter payout address:                           │
│ ┌─────────────────────────────────────────────┐ │
│ │ anim1...                                    │ │
│ └─────────────────────────────────────────────┘ │
│                                                 │
│ ┌────────────────────┐  ┌────────────────────┐ │
│ │ Create New Wallet  │  │ Import from Wallets│ │ ← Opens file browser
│ └────────────────────┘  └────────────────────┘ │
│                                                 │
│ ✓ Imported: Mining Wallet 1                    │
└─────────────────────────────────────────────────┘

When "Import from Wallets" is clicked:

Step 1: File Browser Opens
┌─────────────────────────────────────────────────┐
│ Select Wallets File                        [X]  │
├─────────────────────────────────────────────────┤
│ Look in: /home/user/.animica                    │
│                                                 │
│ ├─ config.json                                  │
│ ├─ node.db                                      │
│ ├─ wallets.json          ← Select this          │
│ └─ logs/                                        │
│                                                 │
│ File name: wallets.json                         │
│                                                 │
│             ┌────────┐  ┌────────┐              │
│             │  Open  │  │ Cancel │              │
│             └────────┘  └────────┘              │
└─────────────────────────────────────────────────┘

Step 2: Wallet Selection (if multiple wallets)
┌─────────────────────────────────────────────────┐
│ Select Wallet                              [X]  │
├─────────────────────────────────────────────────┤
│ Choose a wallet to import:                      │
│                                                 │
│ ┌─────────────────────────────────────────────┐ │
│ │ Mining Wallet 1 - anim1abc...def            │◄│← Select one
│ │ Personal Wallet - anim1xyz...123            │ │
│ │ Test Wallet - anim1test...456               │ │
│ └─────────────────────────────────────────────┘ │
│                                                 │
│                     ┌────┐  ┌────────┐          │
│                     │ OK │  │ Cancel │          │
│                     └────┘  └────────┘          │
└─────────────────────────────────────────────────┘
```

## Workflow Examples

### Example 1: Mining and Checking Rewards

```
1. Start GUI → Dashboard shows:
   - Block Height: 1000 (actual, updates every 5s)
   - Balance: 0.000000000 ANM

2. Click "Start Mining" → Mining begins

3. After mining a block:
   - Dashboard shows Block Height: 1001
   - Click "Refresh Balance"
   - Balance: 10.000000000 ANM (block reward credited!)

4. Continue mining:
   - Height: 1002, 1003, 1004...
   - Balance increases: 20 ANM, 30 ANM, 40 ANM...
```

### Example 2: Sending a Transaction

```
1. Go to "Wallet" tab

2. Enter recipient:
   To Address: anim1recipient123456789...
   
3. Enter amount:
   Amount: 5.5

4. Click "Send Transaction"
   → Confirmation dialog appears
   → Click "Yes"

5. Result pane shows:
   ✓ Transaction sent successfully!
   Transaction hash: 0xabc...def
   Status: pending

6. Go back to Dashboard
   → Click "Refresh Balance"
   → Balance decreased by 5.5 ANM (+ gas fees)
```

### Example 3: Restarting Wizard

```
1. Currently mining with:
   - Network: Devnet
   - Wallet: anim1oldwallet...

2. Click "File > Restart Setup Wizard"
   → Dialog: "Stop mining and restart wizard?"
   → Click "Yes"

3. Wizard appears:
   - Change network to Testnet
   - Import different wallet: anim1newwallet...
   - Reconfigure devices
   - Choose "Start mining immediately"

4. Click "Finish"
   → Main window reopens with new settings
   → Mining starts automatically on Testnet
   → Dashboard shows new wallet address
```

## Visual Summary

```
┌─────────────────────────────────────────────────────────────┐
│  Animica Miner                                         [_][□][X]│
├──────┬────────┬──────┬──────────────────────────────────────┤
│ File │ Mining │ Help │                                      │
├──────┴────────┴──────┴──────────────────────────────────────┤
│                                                              │
│ [Dashboard] [Devices] [Pools] [Wallet*] [Configuration] ... │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Status                                               │  │
│  │ Chain ID:       1337         ← Real data            │  │
│  │ Block Height:   1234         ← Updates every 5s     │  │
│  │ Sync Status:    Synced       ← Actual status        │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Payout Information                                   │  │
│  │ Payout Address: anim1abc...def                       │  │
│  │ Balance:        10.500000000 ANM    ← NEW!          │  │
│  │ [ Refresh Balance ]             ← NEW!              │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  [ Start Mining ]  [ Stop Mining ]                          │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│ Status: Mining: 1.5 MH/s | Shares: 10 | Blocks: 1          │
└──────────────────────────────────────────────────────────────┘

NEW FEATURES:
✓ Real chain height display
✓ Balance display with refresh
✓ Restart Setup Wizard menu option
✓ New Wallet tab for sending transactions
✓ File browser for importing wallets
✓ Multi-wallet selection support
```
