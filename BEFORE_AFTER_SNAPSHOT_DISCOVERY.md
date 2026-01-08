# Snapshot Discovery: Before vs After

## Visual Comparison

### Before: One-Shot Discovery ❌

```
┌─────────────────────────────────────────────────┐
│ Node Startup                                    │
└─────────────────┬───────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────┐
│ P2P Service Starts                              │
└─────────────────┬───────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────┐
│ Background Task: Wait for Peers (max 30s)      │
│ ┌─────────────────────────────────────────────┐ │
│ │ t=0s   → No peers yet...                    │ │
│ │ t=5s   → Still no peers...                  │ │
│ │ t=10s  → Still no peers...                  │ │
│ │ t=15s  → Still no peers...                  │ │
│ │ t=20s  → Still no peers...                  │ │
│ │ t=25s  → Still no peers...                  │ │
│ │ t=30s  → Timeout! Give up.                  │ │
│ └─────────────────────────────────────────────┘ │
└─────────────────┬───────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────┐
│ Try Snapshot Bootstrap ONCE                     │
│ - Query peers (but none connected yet!)         │
│ - Query static RPC (if configured)              │
│ - Result: NO SNAPSHOTS FOUND                    │
└─────────────────┬───────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────┐
│ ❌ Fall Back to Block-by-Block Sync             │
│                                                 │
│ NEVER RETRIES - EVEN IF PEERS CONNECT LATER!   │
│                                                 │
│ Height 0 → 1 → 2 → 3 → 4 → 5 → ...             │
│ Slow... Very slow... 😫                         │
└─────────────────────────────────────────────────┘
```

**Problems:**
- ❌ Timing dependent - misses peers that connect after 30s
- ❌ No retry - single chance, then gives up forever
- ❌ Manual fix needed - user must run `animica snapshot discover`
- ❌ Poor UX - slow sync even when snapshots available

---

### After: Continuous Retry ✅

```
┌─────────────────────────────────────────────────┐
│ Node Startup                                    │
└─────────────────┬───────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────┐
│ P2P Service Starts                              │
└─────────────────┬───────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────┐
│ Background Task: Wait for Initial Peers (30s)  │
│ ┌─────────────────────────────────────────────┐ │
│ │ t=0s   → No peers yet...                    │ │
│ │ t=5s   → Still no peers...                  │ │
│ │ t=10s  → Still no peers...                  │ │
│ │ t=15s  → Still no peers...                  │ │
│ │ t=20s  → Still no peers...                  │ │
│ │ t=25s  → Still no peers...                  │ │
│ │ t=30s  → Will retry anyway!                 │ │
│ └─────────────────────────────────────────────┘ │
└─────────────────┬───────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────┐
│ ✨ Continuous Discovery Loop Begins ✨          │
└─────────────────┬───────────────────────────────┘
                  │
       ┌──────────┴──────────┐
       │                     │
       ▼                     │
┌──────────────────────┐     │
│ Attempt 1 (t=30s)    │     │
│ - Query peers        │     │
│ - No snapshots found │     │
└──────────┬───────────┘     │
           │                 │
           ▼                 │
    [Wait 60s]               │
           │                 │
           ▼                 │
┌──────────────────────┐     │
│ Attempt 2 (t=90s)    │     │
│ - Query peers        │     │
│ - No snapshots found │     │
└──────────┬───────────┘     │
           │                 │
           ▼                 │
    [Wait 60s]               │
           │                 │
           ▼                 │
┌──────────────────────┐     │
│ Attempt 3 (t=150s)   │     │
│ - Query peers        │     │
│ - ✅ FOUND SNAPSHOT! │     │
│   Height: 5000       │     │
│   From: peer-3       │     │
└──────────┬───────────┘     │
           │                 │
           ▼                 │
┌──────────────────────┐     │
│ Download & Import    │     │
│ ✅ Success!          │     │
└──────────┬───────────┘     │
           │                 │
           ▼                 │
┌──────────────────────┐     │
│ Continue Sync from   │     │
│ Height 5000          │     │
│                      │     │
│ 5000 → 5001 → 5002  │     │
│ Fast! 🚀             │     │
└──────────────────────┘     │
                             │
           OR if no snapshot found:
           │                 │
           └─────────────────┘
                  │
                  ▼
           ┌──────────────────────┐
           │ Keep Retrying OR     │
           │ Max Retries Reached  │
           │ → Block Sync         │
           └──────────────────────┘
```

**Benefits:**
- ✅ Resilient - keeps trying even if peers connect late
- ✅ Automatic - no manual intervention needed
- ✅ Configurable - tune retry interval and max attempts
- ✅ Smart - stops when snapshot found or node synced
- ✅ Great UX - fast sync whenever snapshots become available

---

## Code Comparison

### Before: One-Shot

```python
async def _background_snapshot_discovery(...):
    # Wait for peers (max 30s)
    waited = 0
    while waited < 30:
        if has_peers():
            break
        await asyncio.sleep(5)
        waited += 5
    
    if not has_peers():
        return  # Give up!
    
    # Try ONCE
    success, error = await try_snapshot_bootstrap(...)
    
    # That's it - never retries!
```

### After: Continuous Retry

```python
async def _background_snapshot_discovery(...):
    # Wait for initial peers (max 30s)
    waited = 0
    while waited < 30:
        if has_peers():
            break
        await asyncio.sleep(5)
        waited += 5
    
    # Start CONTINUOUS discovery
    await continuous_snapshot_discovery(...)  # Keeps retrying!

async def continuous_snapshot_discovery(...):
    retry_count = 0
    max_retries = get_max_retries()  # 0 = unlimited
    retry_interval = get_retry_interval()  # default: 60s
    
    while True:
        # Check if should stop
        if stop_event and stop_event.is_set():
            break
        if max_retries > 0 and retry_count >= max_retries:
            break
        if not should_try_snapshot_bootstrap(current_height):
            break  # Already synced
        
        retry_count += 1
        
        # Try snapshot bootstrap
        success, error = await try_snapshot_bootstrap(...)
        
        if success:
            break  # Found and imported!
        
        # Wait before retry
        await asyncio.sleep(retry_interval)
```

---

## Configuration Comparison

### Before

```bash
# Only these options available:
export ANIMICA_SNAPSHOT_SYNC_ENABLED=true
export ANIMICA_SNAPSHOT_AUTO_DISCOVER=true
export ANIMICA_SNAPSHOT_RPC_URL=http://...  # Optional static source
export ANIMICA_SNAPSHOT_MIN_HEIGHT=1000
```

**Problem:** No way to configure retry behavior!

### After

```bash
# All previous options still work, PLUS:
export ANIMICA_SNAPSHOT_RETRY_INTERVAL=60    # NEW: Retry every 60s
export ANIMICA_SNAPSHOT_MAX_RETRIES=0        # NEW: 0 = unlimited
```

**Benefit:** Full control over retry behavior!

---

## Timeline Comparison

### Before: Missed Opportunity

```
t=0s     Node starts
t=5s     P2P starts, no peers yet
t=30s    Give up waiting, try once → FAIL
t=35s    Start block-by-block sync
t=45s    Peer connects (TOO LATE!)
t=50s    Peer has snapshot at height 5000
         ❌ Never discovered because we gave up!
t=3600s  Still syncing block 800 out of 5000...
```

**Result:** Slow sync even though snapshot was available

### After: Success Despite Delay

```
t=0s     Node starts
t=5s     P2P starts, no peers yet
t=30s    No peers yet, start continuous retry
t=35s    Attempt 1 → No peers → Wait 60s
t=45s    Peer connects!
t=50s    Peer has snapshot at height 5000
t=95s    Attempt 2 → Query peers → FOUND!
t=100s   Download snapshot
t=120s   Import complete
t=121s   ✅ Syncing from height 5000!
```

**Result:** Fast sync despite late peer connection!

---

## User Experience

### Before

```
User: $ animica node up
[waiting...]
Node: Starting up...
Node: P2P service started
Node: Waiting for peers...
Node: No peers found, trying snapshot bootstrap...
Node: No snapshots available
Node: Falling back to block-by-block sync
Node: Syncing block 1/5000...
Node: Syncing block 2/5000...
Node: Syncing block 3/5000...

[30 minutes later, peers finally connect]

Node: Syncing block 150/5000...

[User notices slow sync]

User: $ animica snapshot discover
Node: Found snapshot at height 5000 from peer!

User: 😤 Why didn't it find this automatically?!
```

### After

```
User: $ animica node up
[waiting...]
Node: Starting up...
Node: P2P service started
Node: Starting continuous snapshot discovery...
Node: Attempt 1: No peers connected yet
Node: Waiting 60s before retry...
Node: Attempt 2: No snapshots found
Node: Waiting 60s before retry...
[Peers connect]
Node: Attempt 3: Found snapshot at height 5000!
Node: Downloading snapshot...
Node: Import complete!
Node: ✅ Synced to height 5000, continuing from there

User: 😊 That was easy!
```

---

## Statistics

### Sync Time Comparison

**Scenario:** Node starting from height 0, target height 5000, peers connect after 2 minutes

**Before:**
- Wait for peers: 30s (timeout)
- Block-by-block sync: ~60 minutes (assuming 1.5 blocks/sec)
- **Total: ~60 minutes** 😫

**After:**
- Wait for peers: 30s (timeout)
- Retry 1: 0s + 60s wait
- Retry 2: 0s + 60s wait (peers connect during this)
- Retry 3: 5s (discovery) + 20s (download) + 30s (import)
- Continue sync from 5000
- **Total: ~3 minutes** 🚀

**Improvement:** 20x faster!

---

## Summary

| Aspect | Before | After |
|--------|--------|-------|
| **Retry Logic** | ❌ None (one-shot) | ✅ Continuous (configurable) |
| **Peer Timing** | ❌ Must connect within 30s | ✅ Works whenever peers connect |
| **Late Snapshots** | ❌ Never discovered | ✅ Discovered automatically |
| **User Action** | ❌ Manual intervention needed | ✅ Fully automatic |
| **Sync Speed** | ❌ Slow block-by-block | ✅ Fast snapshot sync |
| **Configuration** | ⚠️ Limited | ✅ Fully configurable |
| **Fallback** | ✅ Block sync (but never retries) | ✅ Block sync (after max retries) |

**Overall:** 🎉 **Massive improvement in reliability and user experience!**

