# Qt Wallet UI Layout - After Chain Integration

## Window Structure

```
┌─────────────────────────────────────────────────────────────────┐
│ Animica Wallet                                        [_][□][×] │
├─────────────────────────────────────────────────────────────────┤
│ File    Settings    Help                                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Animica Wallet                                                 │
│                                                                  │
│  ┌─────────────────── Accounts ────────────────────────────┐   │
│  │ Wallet: Unlocked               [Unlock] [Lock]          │   │
│  │ ┌─────────────────────────────────────────────────────┐ │   │
│  │ │ Label      │ Address                                │ │   │
│  │ ├────────────┼──────────────────────────────────────┤ │   │
│  │ │ Main       │ anim1qyfe...xw8z                     │ │   │
│  │ │ Savings    │ anim1qz7m...4pkq                     │ │   │
│  │ └─────────────────────────────────────────────────────┘ │   │
│  │ [Create Account] [Import Account]    [Show Secret]     │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─[ Overview ]──[ Node ]────────────────────────────────┐     │
│  │                                                        │     │
│  │  ┌─────────────── Chain Status ───────────────────┐   │     │
│  │  │                                                 │   │     │
│  │  │  Height:         12,453                        │   │     │
│  │  │  Best Hash:      0x7a3f2b...8c4d                │   │     │
│  │  │  Sync Status:    Synced                        │   │     │
│  │  │  Peers:          8                             │   │     │
│  │  │                                                 │   │     │
│  │  └─────────────────────────────────────────────────┘   │     │
│  │                                                        │     │
│  │  ┌────────── Selected Account Balance ─────────────┐  │     │
│  │  │                                                  │  │     │
│  │  │  Address:  anim1qyfe...xw8z                    │  │     │
│  │  │                                                  │  │     │
│  │  │  Balance:  1,234.567890000 ANM                 │  │     │
│  │  │                                                  │  │     │
│  │  └──────────────────────────────────────────────────┘  │     │
│  │                                                        │     │
│  │  Chain status updates automatically every 3 seconds.  │     │
│  │                                                        │     │
│  └────────────────────────────────────────────────────────┘     │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│ Node: running (mainnet)              Walletd: OK                │
└─────────────────────────────────────────────────────────────────┘
```

## Node Tab Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  ┌─[ Overview ]──[ Node ]────────────────────────────────┐     │
│  │                                                        │     │
│  │  Control your local node.                             │     │
│  │                                                        │     │
│  │  Network: [mainnet ▼]              [Start Node] [Stop Node] │
│  │                                                        │     │
│  │  Node Logs                                            │     │
│  │  ┌────────────────────────────────────────────────┐   │     │
│  │  │ [2024-01-11 05:30:12] Starting node on port  │   │     │
│  │  │ [2024-01-11 05:30:13] P2P service started     │   │     │
│  │  │ [2024-01-11 05:30:14] Connected to 3 peers    │   │     │
│  │  │ [2024-01-11 05:30:15] Syncing blocks...       │   │     │
│  │  │ [2024-01-11 05:30:20] Synced to height 12453  │   │     │
│  │  │                                                │   │     │
│  │  └────────────────────────────────────────────────┘   │     │
│  │                                                        │     │
│  └────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘
```

## States

### State 1: Node Stopped
```
┌─────────────── Chain Status ───────────────────┐
│                                                 │
│  Height:         —                             │
│  Best Hash:      —                             │
│  Sync Status:    Not connected                 │
│  Peers:          —                             │
│                                                 │
└─────────────────────────────────────────────────┘

┌────────── Selected Account Balance ─────────────┐
│                                                  │
│  Address:  —                                    │
│                                                  │
│  Balance:  —                                    │
│                                                  │
└──────────────────────────────────────────────────┘

Node is not running. Start the node to see chain status.
```

### State 2: Node Starting
```
┌─────────────── Chain Status ───────────────────┐
│                                                 │
│  Height:         —                             │
│  Best Hash:      —                             │
│  Sync Status:    Synced                        │
│  Peers:          —                             │
│                                                 │
└─────────────────────────────────────────────────┘

Waiting for node to be ready...
```

### State 3: Node Running (Synced)
```
┌─────────────── Chain Status ───────────────────┐
│                                                 │
│  Height:         12,453                        │
│  Best Hash:      0x7a3f2b...8c4d                │
│  Sync Status:    Synced                        │
│  Peers:          8                             │
│                                                 │
└─────────────────────────────────────────────────┘

┌────────── Selected Account Balance ─────────────┐
│                                                  │
│  Address:  anim1qyfe...xw8z                    │
│                                                  │
│  Balance:  1,234.567890000 ANM                 │
│                                                  │
└──────────────────────────────────────────────────┘

Chain status updates automatically every 3 seconds.
```

### State 4: RPC Error
```
┌─────────────── Chain Status ───────────────────┐
│                                                 │
│  Height:         —                             │
│  Best Hash:      —                             │
│  Sync Status:    Error                         │
│  Peers:          —                             │
│                                                 │
└─────────────────────────────────────────────────┘

Chain RPC error: Connection refused
```

## Key UI Features

1. **Tab Navigation**: Easy switch between Overview and Node tabs
2. **Auto-refresh**: Chain data updates every 3 seconds without user action
3. **Selected Account**: Balance shown for currently selected account in table
4. **Graceful Degradation**: Clear messages when node is unavailable
5. **Status Bar**: Shows node and walletd status at bottom
6. **Non-blocking**: All updates happen asynchronously, UI remains responsive
