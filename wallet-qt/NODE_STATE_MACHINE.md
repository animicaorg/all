# Node State Machine Visualization

## State Transitions

```
                    ┌──────────────┐
                    │   Stopped    │ ◄───────────────┐
                    └──────┬───────┘                 │
                           │                          │
                      startNode()                    │
                           │                          │
                           ▼                          │
                    ┌──────────────┐                 │
                    │   Starting   │                 │
                    └──────┬───────┘                 │
                           │                          │
                  RPC responds to                    │
                    getHead()                         │
                           │                          │
                           ▼                          │
                    ┌──────────────┐                 │
                    │   RpcReady   │ ◄──────┐        │
                    └──────┬───────┘        │        │
                           │                 │        │
                    No degradation      Recovers     │
                      detected              │        │
                           │                 │        │
                           ▼                 │        │
                    ┌──────────────┐        │        │
                    │   Healthy    │        │        │
                    └──────┬───────┘        │        │
                           │                 │        │
                    Degradation              │        │
                     pattern in              │        │
                       logs                  │        │
                           │                 │        │
                           ▼                 │        │
                    ┌──────────────┐        │        │
                    │   Degraded   │────────┘        │
                    └──────┬───────┘                 │
                           │                          │
                       Critical                      │
                        error                        │
                           │                          │
                           ▼                          │
                    ┌──────────────┐                 │
                    │    Error     │                 │
                    └──────┬───────┘                 │
                           │                          │
                      Auto restart                   │
                      (with backoff)                 │
                           │                          │
                           └──────────────────────────┘

                    ┌──────────────┐
    Any State ─────►│   Stopping   │────► Stopped
                    └──────────────┘
                         stopNode()
```

## State Characteristics

### Stopped
- **Color**: Gray
- **Node Process**: Not running
- **RPC**: Unavailable
- **UI Actions**: Can start node
- **Description**: Initial state or after clean shutdown

### Starting
- **Color**: Orange
- **Node Process**: Launching
- **RPC**: Connecting (polling every 250ms)
- **UI Actions**: Can stop
- **Description**: Process spawned, waiting for RPC readiness
- **Timeout**: If RPC not ready in 30s → Degraded (but keeps trying)

### RpcReady
- **Color**: Blue
- **Node Process**: Running
- **RPC**: Responding to basic queries
- **UI Actions**: Can stop, restart
- **Description**: RPC endpoint accessible, chain head readable
- **Next**: Immediately transitions to Healthy or Degraded based on log analysis

### Healthy
- **Color**: Green
- **Node Process**: Running normally
- **RPC**: Fully functional
- **P2P**: May or may not be working (not checked)
- **UI Actions**: Can stop, restart
- **Description**: No issues detected, normal operation
- **Monitoring**: Continues checking for degradation patterns

### Degraded
- **Color**: Amber/Yellow
- **Node Process**: Running but impaired
- **RPC**: Still functional for basic operations
- **P2P/Sync**: Likely having issues
- **UI Actions**: Can stop, restart, reset data, view logs
- **Description**: Known issues detected, wallet remains functional
- **Banner**: Visible with recovery actions
- **Monitoring**: Slower checks (15s intervals), can recover to Healthy

### Stopping
- **Color**: Orange
- **Node Process**: Shutting down (SIGTERM sent)
- **RPC**: Unavailable or closing
- **UI Actions**: Wait for completion
- **Description**: Graceful shutdown in progress (5s timeout, then SIGKILL)

### Error
- **Color**: Red
- **Node Process**: Crashed or failed to start
- **RPC**: Unavailable
- **UI Actions**: Can restart
- **Description**: Critical failure, may auto-restart with backoff

## Degradation Triggers

| Pattern | Trigger | Reason |
|---------|---------|--------|
| `UnboundLocalError: cannot access local variable 'asyncio'` | Immediate | Python node bug |
| `NoneType` + `'>='` | Immediate | Snapshot orchestrator bug |
| `sync: reset cursor due to missing head_hash in db` | After 3 occurrences/sec | DB corruption |
| RPC timeout after 30s | Delayed | RPC not responding |

## Health Check Flow

```
Process Started
      │
      ▼
  ┌─────────────────────┐
  │  Poll every 250ms   │
  │  getHead() call     │
  └─────────┬───────────┘
            │
       ┌────▼────┐
       │ Success │
       │   ?     │
       └────┬────┘
            │
     ┌──────┴──────┐
     │             │
    Yes           No
     │             │
     ▼             ▼
 RpcReady    Attempt < 120?
     │             │
     │          ┌──┴──┐
     │         Yes   No
     │          │     │
     │          │     ▼
     │          │  Degraded
     │          │  (keep trying
     │          │   at 2s)
     │          │
     │      (continue)
     │
     ▼
Check logs
     │
  ┌──┴──┐
  │     │
 Issues None
  │     │
  ▼     ▼
Degraded Healthy
```

## Recovery Scenarios

### Scenario 1: P2P Temporarily Down
```
Healthy → (seed connection refused) → Healthy
(not marked as degraded, just logged)
```

### Scenario 2: DB Corruption Detected
```
Healthy → (head_hash spam) → Degraded
User: Click "Reset Local Node Data"
Degraded → Stopping → Stopped → Starting → RpcReady → Healthy
```

### Scenario 3: Node Process Crash
```
Healthy → (process exits) → Error
Wait (backoff: 1s, 2s, 4s, 8s, 16s, 32s, 60s)
Error → Starting → RpcReady → Healthy
```

### Scenario 4: Slow RPC Startup
```
Starting (30s+) → Degraded (RPC not ready)
(keep checking at 2s intervals)
Degraded → (RPC finally ready) → RpcReady → Healthy
```

## UI State Indicators

| State | Status Text | Action Buttons |
|-------|-------------|----------------|
| Stopped | "Stopped" (Gray) | [Start Node] |
| Starting | "Starting..." (Orange) | [Stop Node] |
| RpcReady | "RPC Ready" (Blue) | [Stop] [Restart] |
| Healthy | "Running (Healthy)" (Green) | [Stop] [Restart] [Diagnostics] |
| Degraded | "Running (Degraded)" (Amber) | [Stop] [Restart] [Open Logs] [Reset Data] |
| Stopping | "Stopping..." (Orange) | (none) |
| Error | "Error" (Red) | [Restart] [Diagnostics] |

## Degraded State Banner

When node is Degraded, shows:

```
┌────────────────────────────────────────────────────────────────┐
│ ⚠️ Node degraded: P2P sync error: missing head_hash (DB may   │
│    be corrupt). You can still use local wallet features.       │
│                                                                 │
│ [Open Node Logs] [Reset Local Node Data] [Copy Diagnostics]   │
└────────────────────────────────────────────────────────────────┘
```

Background: Light yellow (#FFF3CD)
Border: Gold (#FFD700)
Text: Dark yellow (#856404)
