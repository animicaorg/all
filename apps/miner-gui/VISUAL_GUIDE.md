# GUI Miner Visual Guide

## Application Structure

```
┌─────────────────────────────────────────────────────────────────┐
│ Animica Miner                                          [_][□][×] │
├─────────────────────────────────────────────────────────────────┤
│ File  Mining  Help                                               │
├─────────────────────────────────────────────────────────────────┤
│ ┌─────────┬─────────┬──────────┬──────────┬────┬────────────┐  │
│ │Dashboard│ Devices │Pools/Mode│  Config  │Logs│Stats/Graphs│  │
│ └─────────┴─────────┴──────────┴──────────┴────┴────────────┘  │
│                                                                  │
│  DASHBOARD TAB                                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ Status                                                      │ │
│  │ ┌──────────────────────────────────────────────────────┐   │ │
│  │ │ Chain ID:        1337                                │   │ │
│  │ │ Block Height:    12345                               │   │ │
│  │ │ Sync Status:     Synced                              │   │ │
│  │ └──────────────────────────────────────────────────────┘   │ │
│  │                                                             │ │
│  │ Mining Status                                               │ │
│  │ ┌──────────────────────────────────────────────────────┐   │ │
│  │ │ Status:          Running                             │   │ │
│  │ │                                                       │   │ │
│  │ │ Hashrate:        1.85 MH/s                          │   │ │
│  │ │                                                       │   │ │
│  │ │ Shares Found:    42                                  │   │ │
│  │ │ Blocks Found:    3                                   │   │ │
│  │ │ Last Submit:     14:32:15                            │   │ │
│  │ └──────────────────────────────────────────────────────┘   │ │
│  │                                                             │ │
│  │ Payout Information                                          │ │
│  │ ┌──────────────────────────────────────────────────────┐   │ │
│  │ │ Payout Address:  anim1abc...xyz123                   │   │ │
│  │ │ Estimated Earn:  12.5 ANIM/day                       │   │ │
│  │ └──────────────────────────────────────────────────────┘   │ │
│  │                                                             │ │
│  │  ┌────────────────┐  ┌────────────────┐                   │ │
│  │  │  Start Mining  │  │  Stop Mining   │                   │ │
│  │  └────────────────┘  └────────────────┘                   │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│ Status: Mining: 1.85 MH/s | Shares: 42 | Blocks: 3              │
└─────────────────────────────────────────────────────────────────┘
```

## First-Run Wizard Flow

```
┌─────────────────────────────────────────────┐
│ Animica Miner Setup                  [×]    │
├─────────────────────────────────────────────┤
│                                             │
│  Step 1/6: Network Selection                │
│                                             │
│  Choose the Animica network:                │
│                                             │
│  ○ Mainnet (production)                     │
│  ○ Testnet (testing)                        │
│  ● Devnet (development) ← selected          │
│  ○ Custom (specify your own RPC)            │
│                                             │
│  Custom RPC URL:                            │
│  [_______________________________]          │
│                                             │
│                                             │
│              [Cancel]  [Next >]             │
└─────────────────────────────────────────────┘

        ↓

┌─────────────────────────────────────────────┐
│ Animica Miner Setup                  [×]    │
├─────────────────────────────────────────────┤
│                                             │
│  Step 2/6: RPC Configuration                │
│                                             │
│  RPC URL: http://127.0.0.1:8545             │
│                                             │
│  [Test Connection]  ✓ OK                    │
│                                             │
│  Connection Info:                           │
│  ┌───────────────────────────────────────┐  │
│  │ ✓ Connection Successful               │  │
│  │ Chain ID: 1337                        │  │
│  │ Current Height: 12340                 │  │
│  └───────────────────────────────────────┘  │
│                                             │
│              [< Back]  [Next >]             │
└─────────────────────────────────────────────┘

        ↓

┌─────────────────────────────────────────────┐
│ Animica Miner Setup                  [×]    │
├─────────────────────────────────────────────┤
│                                             │
│  Step 3/6: Payout Address                   │
│                                             │
│  Enter payout address:                      │
│  [anim1abc...xyz123__________________]      │
│                                             │
│  [Import from Wallets]                      │
│                                             │
│  ✓ Valid address format                     │
│                                             │
│                                             │
│              [< Back]  [Next >]             │
└─────────────────────────────────────────────┘

        ↓

┌─────────────────────────────────────────────┐
│ Animica Miner Setup                  [×]    │
├─────────────────────────────────────────────┤
│                                             │
│  Step 4/6: Device Selection                 │
│                                             │
│  [Auto-Detect Devices]                      │
│                                             │
│  Available Devices:                         │
│  ┌───────────────────────────────────────┐  │
│  │ ✓ CPU: AMD EPYC (4 threads)          │  │
│  │ ○ GPU 0: NVIDIA RTX 3080 (10GB)      │  │
│  │ ○ GPU 1: AMD RX 6800 XT (16GB)       │  │
│  └───────────────────────────────────────┘  │
│                                             │
│  Recommendations:                           │
│  • Recommended threads: 3 (leave 1 free)    │
│  • Recommended GPUs: GPU 0, GPU 1           │
│                                             │
│              [< Back]  [Next >]             │
└─────────────────────────────────────────────┘

        ↓

┌─────────────────────────────────────────────┐
│ Animica Miner Setup                  [×]    │
├─────────────────────────────────────────────┤
│                                             │
│  Step 5/6: Performance Preset               │
│                                             │
│  Choose a performance profile:              │
│                                             │
│  ● Recommended (balanced)                   │
│  ○ Maximum Performance (use all resources)  │
│  ○ Safe Mode (minimal usage)                │
│                                             │
│  Description:                               │
│  Balanced performance using detected        │
│  capabilities. Leaves some CPU cores free   │
│  for system tasks. This is the recommended  │
│  option for most users.                     │
│                                             │
│              [< Back]  [Next >]             │
└─────────────────────────────────────────────┘

        ↓

┌─────────────────────────────────────────────┐
│ Animica Miner Setup                  [×]    │
├─────────────────────────────────────────────┤
│                                             │
│  Step 6/6: Summary                          │
│                                             │
│  Configuration Summary:                     │
│                                             │
│  Network:         Devnet                    │
│  Payout Address:  anim1abc...xyz123         │
│  Performance:     Recommended               │
│                                             │
│  [✓] Start mining immediately               │
│                                             │
│  Click Finish to save this configuration    │
│  and start the miner.                       │
│                                             │
│              [< Back]  [Finish]             │
└─────────────────────────────────────────────┘
```

## Tab Layouts

### Devices Tab
```
┌──────────────────────────────────────────────────────────┐
│ CPU Configuration                                         │
│ ┌────────────────────────────────────────────────────┐   │
│ │ [✓] Enable CPU Mining                              │   │
│ │                                                     │   │
│ │ Threads: [3_____] (0 = auto-detect)               │   │
│ │                                                     │   │
│ │ [✓] Enable Hugepages (if available)                │   │
│ │                                                     │   │
│ │ Priority (-20 to 19): [0_____]                     │   │
│ └────────────────────────────────────────────────────┘   │
│                                                           │
│ GPU Configuration                                         │
│ ┌────────────────────────────────────────────────────┐   │
│ │ GPU 0: NVIDIA RTX 3080                             │   │
│ │ ┌──────────────────────────────────────────────┐   │   │
│ │ │ [✓] Enabled                                  │   │   │
│ │ │ Intensity: [0.8_______]                      │   │   │
│ │ │ Worksize:  [256_______]                      │   │   │
│ │ └──────────────────────────────────────────────┘   │   │
│ └────────────────────────────────────────────────────┘   │
│                                                           │
│ ASIC Configuration (Placeholder)                          │
│ ┌────────────────────────────────────────────────────┐   │
│ │ [ ] Enable ASIC Worker (Stub)                      │   │
│ │ Endpoint: [_________________________________]       │   │
│ └────────────────────────────────────────────────────┘   │
│                                                           │
│ [Benchmark Devices]                                       │
└──────────────────────────────────────────────────────────┘
```

### Logs Tab
```
┌──────────────────────────────────────────────────────────┐
│ Level: [All ▼] Search: [___________] [Clear] [Export]    │
├──────────────────────────────────────────────────────────┤
│ [14:30:15] [INFO] miner: Mining cycle 10 completed       │
│ [14:30:17] [INFO] miner: Mining cycle 11 completed       │
│ [14:30:19] [INFO] miner: Mining cycle 12 completed       │
│ [14:30:20] [INFO] miner: Share found! (count: 42)        │
│ [14:30:21] [INFO] miner: Mining cycle 13 completed       │
│ [14:30:23] [INFO] miner: Mining cycle 14 completed       │
│ [14:30:25] [INFO] miner: Mining cycle 15 completed       │
│ [14:30:27] [INFO] miner: Mining cycle 16 completed       │
│ [14:30:29] [INFO] miner: Mining cycle 17 completed       │
│ [14:30:30] [INFO] miner: Share found! (count: 43)        │
│ [14:30:31] [INFO] miner: Mining cycle 18 completed       │
│ [14:30:32] [WARN] rpc: High latency detected (250ms)     │
│ [14:30:33] [INFO] miner: Mining cycle 19 completed       │
│ [14:30:35] [INFO] miner: Block found! Height: 12341      │
│                                                           │
│ [✓] Auto-scroll                                           │
└──────────────────────────────────────────────────────────┘
```

### Stats/Graphs Tab
```
┌──────────────────────────────────────────────────────────┐
│ Statistics Summary                                        │
│ ┌────────────────────────────────────────────────────┐   │
│ │ Average Hashrate: 1.85 MH/s                        │   │
│ │ Total Shares: 42                                   │   │
│ │ Share Rate: 8.4 shares/min                         │   │
│ └────────────────────────────────────────────────────┘   │
│                                                           │
│ Hashrate Graph                                            │
│ ┌────────────────────────────────────────────────────┐   │
│ │    Mining Hashrate                                 │   │
│ │ 2.0│                       ╱─╲                     │   │
│ │    │                    ╱─╯   ╲                    │   │
│ │ 1.5│               ╱─╲╱         ╲                  │   │
│ │    │            ╱─╯               ╲─╲              │   │
│ │ 1.0│       ╱─╯                       ╲╱─╲          │   │
│ │    │  ╱─╯                                 ╲        │   │
│ │ 0.5├────────────────────────────────────────╲──    │   │
│ │    │                                           ╲   │   │
│ │ 0.0└────────────────────────────────────────────┘  │   │
│ │     300   240   180   120    60     0 seconds ago │   │
│ └────────────────────────────────────────────────────┘   │
│                                                           │
│ Template Statistics                                       │
│ ┌────────────────────────────────────────────────────┐   │
│ │ Mempool Total: 156                                 │   │
│ │ Included: 145                                      │   │
│ │ Rejected: 11                                       │   │
│ └────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

## System Tray Integration

```
┌─────────────────────┐
│ Animica Miner       │  ← Tray Icon
├─────────────────────┤
│ Show                │
├─────────────────────┤
│ Start Mining        │
│ Stop Mining         │
├─────────────────────┤
│ Quit                │
└─────────────────────┘
```

## Notification Examples

```
┌──────────────────────────────────────┐
│ Animica Miner                    [×] │
├──────────────────────────────────────┤
│ Block Found!                         │
│                                      │
│ Block #12341 mined successfully!     │
└──────────────────────────────────────┘
```

```
┌──────────────────────────────────────┐
│ Animica Miner                    [×] │
├──────────────────────────────────────┤
│ Mining Error                         │
│                                      │
│ RPC connection lost                  │
└──────────────────────────────────────┘
```

## Dark Theme

The application uses a professional dark theme with:
- Background: #2b2b2b
- Widgets: #3c3c3c
- Borders: #555555
- Text: #ffffff
- Hover: #4a4a4a
- Pressed: #555555

All widgets follow the theme consistently for a cohesive look.
