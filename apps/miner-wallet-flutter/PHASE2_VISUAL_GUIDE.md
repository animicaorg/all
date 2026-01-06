# Flutter Wallet Miner - Phase 2 Visual Guide

This document provides ASCII-based mockups and descriptions of all the UI pages implemented in Phase 2.

## Application Structure

```
┌─────────────────────────────────────────────────┐
│  Animica Miner-Wallet                           │
├────┬────────────────────────────────────────────┤
│    │                                            │
│ M  │  Content Area (Pages)                      │
│ I  │                                            │
│ N  │  • Dashboard                               │
│ I  │  • Devices                                 │
│ N  │  • Pools                                   │
│ G  │  • Logs                                    │
│    │  • Stats                                   │
│ W  │  • Wallet                                  │
│ A  │  • Settings                                │
│ L  │    - Config Editor                         │
│ L  │    - Wizard                                │
│ E  │                                            │
│ T  │                                            │
│    │                                            │
│ S  │                                            │
│ E  │                                            │
│ T  │                                            │
│ T  │                                            │
│ I  │                                            │
│ N  │                                            │
│ G  │                                            │
│ S  │                                            │
└────┴────────────────────────────────────────────┘
```

## Dashboard Page (Updated)

```
┌────────────────────────────────────────────────────┐
│ Mining Dashboard                      [Refresh]    │
├────────────────────────────────────────────────────┤
│                                                    │
│  ┌─ Chain Status ─────────────────────────────┐  │
│  │ Chain ID:          2                       │  │
│  │ Block Height:      12,456                  │  │
│  │ Sync Status:       Synced                  │  │
│  └────────────────────────────────────────────┘  │
│                                                    │
│  ┌─ Mining Status ────────────────────────────┐  │
│  │ Status:            Mining (green)          │  │
│  │ Hashrate:          125.5 MH/s             │  │
│  │ Blocks Found:      3                       │  │
│  │                                            │  │
│  │ [Stop]                                     │  │
│  └────────────────────────────────────────────┘  │
│                                                    │
└────────────────────────────────────────────────────┘
```

## Devices Page (New)

```
┌────────────────────────────────────────────────────┐
│ Mining Devices                        [Refresh]    │
├────────────────────────────────────────────────────┤
│                                                    │
│  ┌────────────────────────────────────────────┐  │
│  │ [CPU] Intel Core i7-9700K                  │  │
│  │       8 cores, 8 threads                   │  │
│  │                          [⚙️] [ON/OFF] ☑️  │  │
│  └────────────────────────────────────────────┘  │
│                                                    │
│  ┌────────────────────────────────────────────┐  │
│  │ [GPU] NVIDIA GeForce RTX 3070              │  │
│  │       8192 MB, 46 CUs                      │  │
│  │                          [⚙️] [ON/OFF] ☐  │  │
│  └────────────────────────────────────────────┘  │
│                                                    │
│  ┌────────────────────────────────────────────┐  │
│  │ [GPU] AMD Radeon RX 6800                   │  │
│  │       16384 MB, 60 CUs                     │  │
│  │                          [⚙️] [ON/OFF] ☐  │  │
│  └────────────────────────────────────────────┘  │
│                                                    │
└────────────────────────────────────────────────────┘

Config Dialog (CPU):
┌────────────────────────────────────┐
│ CPU Configuration                  │
├────────────────────────────────────┤
│ Device: Intel Core i7-9700K        │
│ Cores: 8                           │
│                                    │
│ Threads: 6                         │
│ ├───●────────┤ (slider)            │
│ 1           8                      │
│                                    │
│                        [Close]     │
└────────────────────────────────────┘
```

## Pools Page (New)

```
┌────────────────────────────────────────────────────┐
│ Pool Settings                                      │
├────────────────────────────────────────────────────┤
│                                                    │
│  ┌─ Pool Mining ──────────────────────────────┐  │
│  │ Pool Mining                     [ON/OFF] ☑️ │  │
│  │ Pool mining enabled - shares will be       │  │
│  │ submitted to the configured pool           │  │
│  └────────────────────────────────────────────┘  │
│                                                    │
│  ┌─ Pool Configuration ───────────────────────┐  │
│  │                                            │  │
│  │ Pool URL:                                  │  │
│  │ ┌──────────────────────────────────────┐  │  │
│  │ │ stratum+tcp://pool.animica.org:3333 │  │  │
│  │ └──────────────────────────────────────┘  │  │
│  │                                            │  │
│  │ Username / Wallet Address:                 │  │
│  │ ┌──────────────────────────────────────┐  │  │
│  │ │ anim1q...                            │  │  │
│  │ └──────────────────────────────────────┘  │  │
│  │                                            │  │
│  │        [Save Pool Settings]                │  │
│  └────────────────────────────────────────────┘  │
│                                                    │
│  ┌─ Popular Pools ────────────────────────────┐  │
│  │ Official Animica Pool            [+]       │  │
│  │ stratum+tcp://pool.animica.org:3333        │  │
│  │                                            │  │
│  │ Community Pool 1                 [+]       │  │
│  │ stratum+tcp://pool1.animica.community...   │  │
│  └────────────────────────────────────────────┘  │
│                                                    │
└────────────────────────────────────────────────────┘
```

## Logs Page (New)

```
┌────────────────────────────────────────────────────┐
│ Mining Logs                                        │
├────────────────────────────────────────────────────┤
│ 1,234 log entries      [📋] [⬇️] [🗑️]             │
├────────────────────────────────────────────────────┤
│ 2025-01-06 23:15:23 | Starting miner...          │
│ 2025-01-06 23:15:24 | Connected to RPC           │
│ 2025-01-06 23:15:25 | Hashrate: 125.5 MH/s       │
│ 2025-01-06 23:15:26 | Share found!               │
│ 2025-01-06 23:15:27 | Share accepted             │
│ 2025-01-06 23:15:28 | Template updated           │
│ 2025-01-06 23:15:29 | Hashrate: 127.2 MH/s       │
│ [ERROR] 23:15:30 | Connection timeout            │
│ 2025-01-06 23:15:31 | Reconnecting...            │
│ 2025-01-06 23:15:32 | Connected to RPC           │
│ ...                                                │
│ ...                                                │
│ ...                                                │
│                                                    │
└────────────────────────────────────────────────────┘
```

## Stats Page (New)

```
┌────────────────────────────────────────────────────┐
│ Mining Statistics                     [Refresh]    │
├────────────────────────────────────────────────────┤
│                                                    │
│ ┌────────────────┐  ┌────────────────┐           │
│ │ Current        │  │ Blocks         │           │
│ │ Hashrate       │  │ Found          │           │
│ │ 125.5 MH/s    │  │ 3              │           │
│ └────────────────┘  └────────────────┘           │
│                                                    │
│ ┌────────────────┐  ┌────────────────┐           │
│ │ Shares         │  │ Avg Hashrate   │           │
│ │ Found          │  │                │           │
│ │ 156            │  │ 123.4 MH/s    │           │
│ └────────────────┘  └────────────────┘           │
│                                                    │
│  ┌─ Hashrate History ─────────────────────────┐  │
│  │                                            │  │
│  │  140│         ╭─╮                          │  │
│  │  120│    ╭────╯ ╰╮                         │  │
│  │  100│   ╭╯       ╰──╮                      │  │
│  │   80│  ╭╯           ╰─╮                    │  │
│  │   60│ ╭╯              ╰╮                   │  │
│  │   40│╭╯                ╰╮                  │  │
│  │   20│╯                  ╰─                 │  │
│  │    0└──────────────────────────────────    │  │
│  │     0    10   20   30   40   50   60      │  │
│  └────────────────────────────────────────────┘  │
│                                                    │
│  ┌─ Session Statistics ──────────────────────┐   │
│  │ Data Points:        60                    │   │
│  │ Peak Hashrate:      142.3 MH/s           │   │
│  │ Min Hashrate:       98.7 MH/s            │   │
│  └────────────────────────────────────────────┘  │
│                                                    │
└────────────────────────────────────────────────────┘
```

## Config Editor Page (New)

```
┌────────────────────────────────────────────────────┐
│ Configuration Editor        [↻ Reset] [💾 Save]   │
├────────────────────────────────────────────────────┤
│                                                    │
│ ┌──────────────────────────────────────────────┐ │
│ │ {                                            │ │
│ │   "network": {                               │ │
│ │     "rpcUrl": "https://rpc.clearblocker.com",│ │
│ │     "chainId": 2,                            │ │
│ │     "networkName": "Animica Network"         │ │
│ │   },                                         │ │
│ │   "miner": {                                 │ │
│ │     "payoutAddress": "anim1q...",            │ │
│ │     "autoStart": false,                      │ │
│ │     "blocksPerBatch": 10,                    │ │
│ │     "mode": "solo"                           │ │
│ │   },                                         │ │
│ │   "cpu": {                                   │ │
│ │     "enabled": true,                         │ │
│ │     "threads": 6                             │ │
│ │   },                                         │ │
│ │   "gpus": [],                                │ │
│ │   "pool": null,                              │ │
│ │   "ui": {                                    │ │
│ │     "systemTray": true,                      │ │
│ │     "notifications": true,                   │ │
│ │     "logLevel": "info"                       │ │
│ │   }                                          │ │
│ │ }                                            │ │
│ └──────────────────────────────────────────────┘ │
│                                                    │
│  [Validate]          [Save & Apply]               │
│                                                    │
└────────────────────────────────────────────────────┘
```

## Wizard Page (New)

```
Step 1: Network Configuration
┌────────────────────────────────────────────────────┐
│ Setup Wizard                            Step 1/4   │
├────────────────────────────────────────────────────┤
│                                                    │
│  ● Network Configuration                           │
│  ○ Wallet Setup                                    │
│  ○ Mining Settings                                 │
│  ○ Complete                                        │
│                                                    │
│  Configure your connection to the Animica network  │
│                                                    │
│  RPC URL:                                          │
│  ┌──────────────────────────────────────────┐     │
│  │ https://rpc.clearblocker.com            │     │
│  └──────────────────────────────────────────┘     │
│                                                    │
│  Chain ID:                                         │
│  ┌──────────────────────────────────────────┐     │
│  │ 2                                        │     │
│  └──────────────────────────────────────────┘     │
│                                                    │
│                           [Cancel] [Continue]      │
└────────────────────────────────────────────────────┘

Step 4: Complete
┌────────────────────────────────────────────────────┐
│ Setup Wizard                            Step 4/4   │
├────────────────────────────────────────────────────┤
│                                                    │
│  ● Network Configuration                           │
│  ● Wallet Setup                                    │
│  ● Mining Settings                                 │
│  ● Complete                                        │
│                                                    │
│  Setup complete! Review your configuration:        │
│                                                    │
│  ┌────────────────────────────────────────────┐   │
│  │ RPC URL:         https://rpc.clearblock... │   │
│  │ Chain ID:        2                         │   │
│  │ Payout Address:  anim1q...                 │   │
│  │ CPU Mining:      Enabled (6 threads)       │   │
│  └────────────────────────────────────────────┘   │
│                                                    │
│           [✓ Finish Setup]                         │
│                                                    │
└────────────────────────────────────────────────────┘
```

## Settings Page (Updated)

```
┌────────────────────────────────────────────────────┐
│ Settings                                           │
├────────────────────────────────────────────────────┤
│                                                    │
│  [🖥️] Devices                              [›]    │
│       Configure CPU and GPU devices                │
│  ─────────────────────────────────────────────     │
│  [🏊] Pool Settings                         [›]    │
│       Mining pool configuration                    │
│  ─────────────────────────────────────────────     │
│  [📄] View Logs                             [›]    │
│       Mining logs and debug info                   │
│  ─────────────────────────────────────────────     │
│  [📊] Statistics                            [›]    │
│       Mining stats and charts                      │
│  ─────────────────────────────────────────────     │
│  [</>] JSON Configuration                   [›]    │
│       Advanced: Edit raw config                    │
│  ─────────────────────────────────────────────     │
│  [📱] System Tray                     [ON/OFF] ☑️  │
│       Minimize to system tray                      │
│  ─────────────────────────────────────────────     │
│  [🔔] Notifications                   [ON/OFF] ☑️  │
│       Block found, errors, etc.                    │
│  ─────────────────────────────────────────────     │
│  [ℹ️] About                                 [›]    │
│       Version 0.1.0+1                              │
│                                                    │
└────────────────────────────────────────────────────┘
```

## Responsive Layouts

### Desktop (Wide Screen)
```
┌────────────────────────────────────────────────────────────┐
│  Animica Miner-Wallet                                      │
├───────┬────────────────────────────────────────────────────┤
│  [M] │                                                     │
│  [W] │  Dashboard / Devices / Pools / etc.                │
│  [S] │  (Full content area)                               │
│       │                                                     │
│       │                                                     │
└───────┴────────────────────────────────────────────────────┘
```

### Mobile (Narrow Screen)
```
┌─────────────────────┐
│ Animica Miner       │
├─────────────────────┤
│                     │
│  Content Area       │
│                     │
│                     │
│                     │
│                     │
│                     │
├─────────────────────┤
│ [M]  [W]  [S]      │ ← Bottom Nav
└─────────────────────┘
```

## Color Scheme

**Primary Colors:**
- Teal: #5EEAD4 (Primary actions, highlights)
- Indigo: #818CF8 (Secondary elements)
- Dark: #0B0D12 (Background)
- Card: #1A1D24 (Surface)

**Status Colors:**
- Success: #34D399 (Mining active, success states)
- Error: #F87171 (Errors, critical states)
- Warning: #FBBF24 (Warnings, attention needed)

**Typography:**
- Font Family: Inter (Variable)
- Display: 32px, Weight 600
- Headline: 24px, Weight 600
- Title: 20px, Weight 600
- Body: 16px, Weight 400
- Label: 14px, Weight 400

## Key Interactions

### Start/Stop Mining
```
[Start Mining] → Shows loading → [Stop] (enabled)
[Stop] → Shows stopping → Mining stopped → [Start Mining]
```

### Device Configuration
```
Device Card [⚙️] → Opens dialog → Configure → Save → Updates state
Device Card [ON/OFF] → Toggles immediately → Saves to config
```

### Pool Toggle
```
Pool Mining [OFF] → [ON] → Shows config form
Pool Mining [ON] → [OFF] → Hides config, saves null pool
```

### Log Auto-Scroll
```
New log arrives → User at bottom → Auto scroll
New log arrives → User scrolled up → Show [⬇️] button
User clicks [⬇️] → Scroll to bottom → Resume auto-scroll
```

### Stats Chart
```
New hashrate data → Add to chart → Slide window if > 60 points
[Refresh] → Clear chart data → Start fresh
```

## Accessibility

- All interactive elements have tooltips
- Color is not the only indicator (icons, text)
- Keyboard navigation supported
- Screen reader labels on all controls
- Sufficient contrast ratios (WCAG AA)

## Performance

- **Logs**: Max 1000 entries to prevent memory bloat
- **Charts**: Rolling window of 60 data points
- **State**: Efficient Riverpod providers with automatic cleanup
- **Lists**: Lazy loading with ListView.builder

---

**Created**: January 6, 2025  
**Phase**: Phase 2 UI Implementation
