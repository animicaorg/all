# Snapshot Automation System - Architecture

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         Animica Node                             │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                    RPC Server (deps.py)                     │ │
│  │                                                              │ │
│  │  on_startup():                                               │ │
│  │    ├─ Initialize RpcContext                                 │ │
│  │    ├─ Create SnapshotOrchestrator                           │ │
│  │    └─ Start orchestrator.start()                            │ │
│  │                                                              │ │
│  │  on_shutdown():                                              │ │
│  │    └─ Stop orchestrator.stop()                              │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              │                                    │
│                              ▼                                    │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │          SnapshotOrchestrator (orchestrator.py)            │ │
│  │                                                              │ │
│  │  Background Tasks:                                           │ │
│  │  ┌──────────────────────┐  ┌──────────────────────┐        │ │
│  │  │  Snapshot Monitor    │  │   Health Check       │        │ │
│  │  │  (every 10 seconds)  │  │  (every 5 minutes)   │        │ │
│  │  │                      │  │                      │        │ │
│  │  │ • Check chain height │  │ • Verify snapshots   │        │ │
│  │  │ • Create at interval │  │ • Check disk space   │        │ │
│  │  │ • Retry on failure   │  │ • Detect missing     │        │ │
│  │  └──────────────────────┘  │ • Report warnings    │        │ │
│  │                             └──────────────────────┘        │ │
│  │                                                              │ │
│  │  State Management:                                           │ │
│  │  • SnapshotConfig  (from environment)                       │ │
│  │  • SnapshotStatus  (statistics, health, errors)             │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              │                                    │
│                              ▼                                    │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │              Snapshot Storage & Operations                  │ │
│  │                                                              │ │
│  │  ~/.animica/snapshots/                                       │ │
│  │  ├─ chain-1-height-2000/                                    │ │
│  │  │  ├─ manifest.json                                        │ │
│  │  │  ├─ blocks.chunk                                         │ │
│  │  │  └─ state.chunk                                          │ │
│  │  ├─ chain-1-height-4000/                                    │ │
│  │  └─ chain-1-height-6000/                                    │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      External Interfaces                         │
│                                                                   │
│  ┌────────────┐          ┌────────────┐       ┌───────────┐    │
│  │    CLI     │          │    RPC     │       │   Logs    │    │
│  │            │          │            │       │           │    │
│  │ animica    │          │ snapshot.  │       │ tail -f   │    │
│  │ snapshot   │◄────────►│ status     │◄─────►│ *.log     │    │
│  │ status     │          │            │       │           │    │
│  └────────────┘          └────────────┘       └───────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow

### 1. Snapshot Creation Flow

```
Chain Height Increases
        │
        ▼
Monitor detects interval (e.g., 2000, 4000)
        │
        ▼
Create snapshot directory
        │
        ▼
Export blocks & state to chunks
        │
        ▼
Compress & sign chunks
        │
        ▼
Write manifest.json
        │
        ▼
Verify (optional)
        │
        ▼
Update statistics
```

### 2. Health Check Flow

```
Timer triggers (every 5 minutes)
        │
        ▼
Check snapshots directory exists
        │
        ▼
List all snapshots
        │
        ▼
Check disk space
        │
        ├─ Low? → Cleanup old snapshots
        └─ OK → Continue
        │
        ▼
Check chain height
        │
        ├─ Behind? → Generate warning
        └─ OK → Continue
        │
        ▼
Update health status
```

### 3. Status Query Flow

```
CLI/RPC: snapshot.status
        │
        ▼
Get RpcContext
        │
        ▼
Check if orchestrator exists?
        │
        ├─ Yes → Get orchestrator.get_status()
        │         ├─ Configuration
        │         ├─ Health status
        │         ├─ Statistics
        │         ├─ Warnings/errors
        │         └─ Snapshot list
        │
        └─ No → Return manual mode info
        │
        ▼
Format & return response
```

## Component Interactions

### Configuration Flow

```
Environment Variables
        │
        ▼
SnapshotConfig.from_env()
        │
        ├─ ANIMICA_SNAPSHOT_INTERVAL → interval
        ├─ ANIMICA_SNAPSHOT_AUTO_CREATE → auto_create
        ├─ ANIMICA_SNAPSHOT_MAX_KEEP → max_snapshots
        ├─ ANIMICA_SNAPSHOT_MIN_DISK_GB → min_disk_space_gb
        └─ ... (more settings)
        │
        ▼
SnapshotOrchestrator.__init__(config=config)
```

### Lifecycle Management

```
Node Startup
    │
    ▼
rpc.deps.startup()
    │
    ├─ build_context()
    │  └─ Create SnapshotOrchestrator
    │
    └─ orchestrator.start()
       ├─ Start snapshot monitor task
       └─ Start health check task
    
Node Shutdown
    │
    ▼
rpc.deps.shutdown()
    │
    └─ orchestrator.stop()
       ├─ Cancel all tasks
       └─ Wait for completion
```

## Key Design Decisions

### 1. Background Tasks
- **Why**: Non-blocking operation, doesn't slow down block import
- **How**: asyncio.create_task() with independent loops

### 2. Health Checks
- **Why**: Proactive issue detection before failures occur
- **How**: Periodic checks with warning/error thresholds

### 3. Retry Logic
- **Why**: Transient failures shouldn't cause permanent issues
- **How**: Exponential backoff with max retries

### 4. Disk Management
- **Why**: Prevent disk full scenarios
- **How**: Automatic cleanup based on free space threshold

### 5. Unified Orchestrator
- **Why**: Single point of control, easier to reason about
- **How**: Encapsulates all snapshot lifecycle logic

## Environment Variables Hierarchy

```
Default Values (in code)
        │
        ▼
Environment Variables (user override)
        │
        ▼
SnapshotConfig instance
        │
        ▼
Runtime configuration
```

## Error Handling Strategy

```
Operation Attempted
        │
        ▼
Try with timeout
        │
        ├─ Success → Log info, update stats
        │
        └─ Failure
           │
           ├─ Attempt 1/3 → Log warning, retry after 60s
           ├─ Attempt 2/3 → Log warning, retry after 60s
           ├─ Attempt 3/3 → Log error, give up
           │
           └─ Update statistics (snapshots_failed++)
```

## Integration Points

1. **RPC Context** (`rpc/deps.py`)
   - Creates orchestrator during context initialization
   - Stores in `ctx.snapshot_orchestrator`
   - Lifecycle tied to node lifecycle

2. **RPC Methods** (`rpc/methods/snapshot.py`)
   - `snapshot.status` queries orchestrator
   - Other methods (create, list, etc.) work independently
   - Orchestrator enhances but doesn't replace existing methods

3. **CLI** (`python/animica/cli/snapshot.py`)
   - `status` command calls `snapshot.status` RPC
   - Formats output for human readability
   - Provides JSON mode for scripting

4. **Core DB** (`core/db/snapshot.py`)
   - Orchestrator uses existing export/import functions
   - No changes to core snapshot format
   - Maintains backward compatibility

## Performance Characteristics

| Operation | Frequency | Duration | Impact |
|-----------|-----------|----------|--------|
| Height check | 10s | ~1ms | Negligible |
| Snapshot creation | 2000 blocks | 30-60s | Background thread |
| Health check | 5 min | ~100ms | Negligible |
| Disk cleanup | On demand | ~1s | Rare event |

## Security Considerations

1. **Snapshots are signed** with node's PQ keys
2. **Hash verification** on all chunks
3. **No privilege escalation** - runs with node permissions
4. **No network access** in orchestrator itself
5. **Deterministic behavior** - no random elements

## Observability

### Metrics Tracked
- `snapshots_created`: Total created
- `snapshots_deleted`: Total deleted
- `snapshots_failed`: Total failures
- `sync_attempts`: Snapshot sync attempts
- `sync_successes`: Successful syncs

### Health Indicators
- ✅ Healthy: All checks pass
- ⚠️ Warning: Minor issues (e.g., behind schedule)
- ❌ Error: Critical issues (e.g., disk full)

### Logging Levels
- **INFO**: Normal operations (creation, cleanup)
- **WARNING**: Retries, minor issues
- **ERROR**: Permanent failures
- **DEBUG**: Detailed diagnostic info

## Future Enhancements

Possible additions (not in scope for this PR):

1. **Prometheus Metrics** - Export stats for monitoring
2. **Distributed Coordination** - Share snapshots across clusters
3. **Incremental Snapshots** - Only delta from last snapshot
4. **Compression Tuning** - Optimize for speed vs size
5. **Web Dashboard** - Real-time visualization
