# Node Startup Robustness Implementation

## Overview

This implementation fixes wallet-qt "embedded node" startup instability and log spam **without modifying any node code**. All changes are in the wallet C++/Qt layer.

## Problem Statement

When the wallet starts the node on mainnet:
- Node RPC starts fine (Uvicorn on 127.0.0.1:8548)
- Node-side exceptions occur (UnboundLocalError, NoneType comparisons)
- P2P logs spam: "sync: reset cursor due to missing head_hash in db"
- Wallet shows "Stopping..." shortly after due to treating P2P issues as fatal

## Solution Architecture

### 1. Enhanced State Machine

```
Stopped → Starting → RpcReady → Healthy
                              → Degraded → (can recover to Healthy)
                   → Error → Stopped
```

**States:**
- **Stopped**: Node not running
- **Starting**: Node process launching, waiting for RPC
- **RpcReady**: RPC endpoint responding, basic chain data accessible
- **Healthy**: RPC + chain operating normally, no issues detected
- **Degraded**: RPC works but P2P/sync issues detected in logs
- **Stopping**: Graceful shutdown in progress
- **Error**: Critical failure, node stopped

### 2. Health Check Strategy

**Initial Phase (0-30s):**
- Poll every 250ms using `chain_getHead`
- Transition to RpcReady when chain head is readable
- Check for chain height availability

**Degraded Phase (30s+):**
- Slow down to 2s intervals
- Mark as Degraded if RPC not ready after 30s
- Continue checking (don't give up)

**Success Criteria:**
- RPC responds to `getHead()` ✓
- Chain height is readable ✓
- Chain ID matches expected (future enhancement)

**NOT Required:**
- P2P connectivity (seed dial can fail)
- Sync progress (can be at height 0)

### 3. Log Management

**Ring Buffer:**
- Stores last 5000 log lines in memory
- Circular buffer, oldest lines dropped

**Deduplication:**
- 2-second deduplication window
- Identical lines within window are counted
- Display format: `"message (repeated N times)"`
- Prevents log spam from freezing UI

**Pattern Detection:**
Automatically detects these patterns and transitions to Degraded:

1. `"UnboundLocalError: cannot access local variable 'asyncio'"`
   - Python node error
   
2. `"NoneType" + "'>='"` 
   - Snapshot orchestrator comparison error
   
3. `"sync: reset cursor due to missing head_hash in db"`
   - DB corruption / missing head_hash

**Non-Fatal Patterns:**
- `"Connection refused" + "seed"` - Expected when no peers available

### 4. Restart Backoff

**Exponential Backoff with Jitter:**
```
Attempt 0: ~1s  ± 20%
Attempt 1: ~2s  ± 20%
Attempt 2: ~4s  ± 20%
Attempt 3: ~8s  ± 20%
Attempt 4: ~16s ± 20%
Attempt 5: ~32s ± 20%
Attempt 6+: ~60s ± 20% (capped)
```

Prevents rapid restart loops that waste resources.

### 5. UI Enhancements

**Degraded State Banner:**
When node enters Degraded state, shows:
```
⚠️ Node degraded: [reason]. You can still use local wallet features.

[Open Node Logs] [Reset Local Node Data] [Copy Diagnostics]
```

**Action Buttons:**
- **Open Node Logs**: Opens log folder in file manager
- **Reset Local Node Data**: Deletes chain DB (after confirmation)
- **Copy Diagnostics**: Copies full diagnostics to clipboard

**State Display:**
- Color-coded state indicator
- Clear descriptions for each state
- Block height and sync status

## API Changes

### NodeManager

**New States:**
```cpp
enum class State {
    Stopped,
    Starting,
    RpcReady,    // NEW: RPC responding
    Healthy,     // NEW: Fully operational
    Degraded,    // NEW: RPC works, issues detected
    Stopping,
    Error
};
```

**New Methods:**
```cpp
bool isDegraded() const;
bool resetChainData(int chainId);
QStringList getDeduplicatedLogs(int maxLines = 100);
```

**New Signals:**
```cpp
void nodeDegraded(const QString& reason);
```

**Modified Behavior:**
- `isRunning()` now returns true for RpcReady, Healthy, and Degraded
- Health checks continue indefinitely in Degraded state
- Default RPC port changed to 8548 (mainnet)

### NodeControlWidget

**New UI Elements:**
- Degraded state banner (QWidget)
- Recovery action buttons (QPushButton)

**New Slots:**
```cpp
void onNodeDegraded(const QString& reason);
void onOpenLogsClicked();
void onResetDataClicked();
```

## Configuration

**Timeouts:**
- Initial health check interval: 250ms
- Backoff health check interval: 2s
- Degraded sync check interval: 15s (vs 5s normal)
- Max restart delay: 60s

**Buffer Sizes:**
- Log ring buffer: 5000 lines
- Log dedupe window: 2000ms

## Testing

Unit tests in `tests/test_node_manager.cpp`:
- Log deduplication behavior
- Degradation pattern detection
- Exponential backoff calculation
- State transition validity
- isRunning() for all states

Run with:
```bash
cd build
ctest -R test_node_manager
```

## Building

Requires Qt6 (or Qt5.15+):
```bash
mkdir build && cd build
cmake ..
cmake --build .
```

## Acceptance Criteria

✅ Launching wallet on macOS no longer results in immediate "Stopping..." due to P2P warnings
✅ Log spam is collapsed and does not freeze UI
✅ Wallet indicates Degraded state when specific errors occur
✅ Wallet provides recovery actions (Open Logs, Reset Data)
✅ Wallet remains usable even if node is partially broken
✅ No node code changes

## Future Enhancements

1. **Remote RPC Fallback**: Allow switching to remote RPC endpoint when embedded node is degraded
2. **Chain ID Validation**: Verify chain_id matches expected network
3. **Seed Configuration**: UI for configuring custom seed nodes
4. **Log Export**: One-click export of recent logs for support
5. **Auto-Recovery**: Attempt automatic data reset after N degraded hours
6. **Metrics**: Track degradation frequency and patterns

## Files Modified

- `wallet-qt/src/node/NodeManager.h` - Enhanced state machine and API
- `wallet-qt/src/node/NodeManager.cpp` - Implementation
- `wallet-qt/src/ui/NodeControlWidget.h` - Degraded banner UI
- `wallet-qt/src/ui/NodeControlWidget.cpp` - UI implementation
- `wallet-qt/tests/test_node_manager.cpp` - Unit tests (new)
- `wallet-qt/tests/CMakeLists.txt` - Test configuration

## Notes

- All changes are wallet-side only
- Node code in `Contents/Resources/node/` is untouched
- Python node bugs remain but are handled gracefully
- Wallet becomes resilient to node partial failures
