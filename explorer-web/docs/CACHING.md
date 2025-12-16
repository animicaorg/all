# Explorer Caching Architecture

## Overview

The Animica Explorer implements a persistent local caching layer using IndexedDB to improve performance and enable offline operation. This document describes the caching architecture, implementation details, and operational guidelines.

## Architecture

### Components

1. **Cache Service** (`src/services/cache.ts`)
   - IndexedDB wrapper for storing blockchain data
   - Provides CRUD operations for blocks, transactions, and addresses
   - Implements LRU eviction to manage storage limits
   - Tracks metadata (last sync height, timestamps)

2. **Sync Manager** (`src/services/sync.ts`)
   - Background service that synchronizes data from RPC to cache
   - Implements incremental sync with batch fetching
   - Provides sync status tracking and progress monitoring
   - Supports pause/resume and manual trigger

3. **Enhanced State Hooks** (`src/state/blocksWithCache.ts`)
   - Cache-first data access strategy
   - Automatic fallback to RPC on cache miss
   - Graceful degradation when RPC is unavailable
   - Transparent integration with existing components

4. **UI Components** (`src/components/CacheStatus.tsx`)
   - Displays cache statistics and sync progress
   - Provides cache management controls
   - Shows offline mode indicator

### Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                        User Request                         │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│             Enhanced State Hook (Cache-First)               │
└─────────────────────────────────────────────────────────────┘
                            │
                 ┌──────────┴──────────┐
                 │                     │
                 ▼                     ▼
         ┌──────────────┐      ┌──────────────┐
         │  IndexedDB   │      │   RPC Node   │
         │    Cache     │      │              │
         └──────────────┘      └──────────────┘
                 ▲                     │
                 │                     │
                 └─────────────────────┘
                   Background Sync
```

### Cache Schema

**IndexedDB Database**: `animica-explorer-cache` (v1)

**Object Stores**:

1. **blocks** (keyPath: `height`)
   - Indices: `hash`, `timestamp`
   - Fields: `height`, `hash`, `data`, `timestamp`
   - Capacity: 100,000 blocks (~200MB)

2. **txs** (keyPath: `hash`)
   - Indices: `blockHeight`, `timestamp`
   - Fields: `hash`, `data`, `blockHeight`, `timestamp`
   - Capacity: 500,000 transactions

3. **addresses** (keyPath: `address`)
   - Indices: `timestamp`
   - Fields: `address`, `data`, `timestamp`
   - Capacity: 50,000 addresses

4. **meta** (keyPath: `key`)
   - Stores sync state: `lastSyncHeight`, `lastSyncTime`
   - Configuration and settings

## Features

### 1. Cache-First Strategy

When data is requested:
1. Check IndexedDB cache first
2. Return cached data if available and valid
3. Fetch from RPC on cache miss
4. Update cache with new data (fire-and-forget)

### 2. Background Synchronization

The sync manager runs continuously in the background:
- Fetches missing blocks incrementally
- Prioritizes recent blocks (head-first strategy)
- Enters catchup mode when far behind (>50 blocks)
- Throttles requests to avoid overwhelming RPC (2s interval)

### 3. Offline Operation

When RPC is unavailable:
- Explorer continues to function using cached data
- Users can browse historical blocks and transactions
- Cache status indicator shows offline mode
- Automatic reconnection when RPC becomes available

### 4. LRU Eviction

When storage limits are approached:
- Oldest entries are evicted first (by timestamp)
- Eviction is triggered automatically during sync
- Configurable capacity limits per store

## Configuration

### Environment Variables

No additional environment variables required. The cache uses the existing RPC configuration:

```bash
VITE_RPC_URL=http://localhost:8545  # Used by sync manager
VITE_CHAIN_ID=1337                  # Chain identifier
```

### Cache Limits

Default capacity limits (adjustable in `cache.ts`):

```typescript
const MAX_BLOCKS = 100_000;    // ~200MB
const MAX_TXS = 500_000;       // ~500MB
const MAX_ADDRESSES = 50_000;  // ~25MB
```

Total estimated storage: ~725MB at capacity

### Sync Configuration

Default sync settings (adjustable when creating SyncManager):

```typescript
{
  batchSize: 20,           // blocks per batch
  delayMs: 2000,          // delay between batches (2s)
  maxRetries: 3,          // retry attempts per batch
  catchupThreshold: 50,   // blocks behind to enter catchup mode
}
```

## Usage

### Basic Integration

The caching is automatically enabled for components using `useBlocksWithCache`:

```typescript
import { useBlocksWithCache } from '../state/blocksWithCache';

function MyComponent() {
  const {
    getPage,
    syncStatus,
    cacheAvailable,
    clearCache,
    getCacheStats,
  } = useBlocksWithCache({
    pageSize: 20,
    autoRefresh: true,
    enableSync: true,
  });

  // Fetch blocks (cache-first)
  const blocks = await getPage(0);
  
  // Check sync status
  if (syncStatus?.isSynced) {
    console.log('Cache is up to date');
  }
  
  return <div>...</div>;
}
```

### Manual Cache Management

```typescript
import { getCache } from '../services/cache';
import { getSyncManager } from '../services/sync';

// Get cache stats
const cache = await getCache();
const stats = await cache.getStats();
console.log('Blocks cached:', stats.blocksCount);
console.log('Last sync:', stats.lastSyncHeight);

// Clear cache
await cache.clearAll();

// Control sync
const syncManager = getSyncManager();
syncManager.pause();
syncManager.resume();
await syncManager.triggerSync();
```

### Cache Status UI

Display cache status in your component:

```typescript
import CacheStatus from '../components/CacheStatus';

function MyPage() {
  const { syncStatus, cacheAvailable, clearCache, getCacheStats } = 
    useBlocksWithCache();

  return (
    <div>
      <CacheStatus
        syncStatus={syncStatus}
        cacheAvailable={cacheAvailable}
        onClearCache={clearCache}
        getCacheStats={getCacheStats}
      />
    </div>
  );
}
```

## Performance

### Benchmarks

Typical performance characteristics (on modern browser/hardware):

- **Cache Hit**: 1-5ms (IndexedDB read)
- **Cache Miss + RPC**: 50-200ms (network latency dependent)
- **Batch Sync**: 20 blocks in ~1-2s
- **Full Sync**: 10,000 blocks in ~15-20 minutes

### Optimization Tips

1. **Batch Size**: Increase for faster catchup, decrease for less RPC load
2. **Delay**: Reduce for faster sync, increase to reduce server impact
3. **Capacity**: Increase limits if storage permits
4. **Selective Sync**: Only sync data types you need

## Troubleshooting

### Cache Not Working

**Symptom**: Data not being cached, always fetching from RPC

**Checks**:
1. Browser supports IndexedDB:
   ```javascript
   console.log(typeof indexedDB !== 'undefined'); // should be true
   ```

2. Private browsing disabled (IndexedDB limited in private mode)

3. Check browser console for initialization errors:
   ```
   [blocksWithCache] Cache initialized
   [sync] Starting background sync...
   ```

4. Verify storage quota:
   ```javascript
   navigator.storage.estimate().then(console.log);
   ```

### Sync Stuck or Slow

**Symptom**: Sync progress not advancing

**Checks**:
1. Check sync status:
   ```javascript
   const { syncStatus } = useBlocksWithCache();
   console.log(syncStatus);
   ```

2. Look for sync errors in console:
   ```
   [sync] Sync cycle error: ...
   [blocksWithCache] Sync error: ...
   ```

3. Verify RPC connectivity:
   ```bash
   curl -X POST $VITE_RPC_URL \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}'
   ```

4. Check sync configuration (may need adjustment for fast chains):
   ```typescript
   const syncManager = getSyncManager({
     batchSize: 50,      // increase batch size
     delayMs: 500,       // reduce delay
   });
   ```

### Cache Corruption

**Symptom**: Errors when accessing cached data, inconsistent results

**Solutions**:
1. Clear cache via UI or programmatically:
   ```typescript
   const cache = await getCache();
   await cache.clearAll();
   ```

2. Clear IndexedDB via browser DevTools:
   - Open DevTools (F12)
   - Application tab → Storage → IndexedDB
   - Right-click `animica-explorer-cache` → Delete

3. Clear browser storage completely (nuclear option):
   - Settings → Privacy → Clear browsing data
   - Select "Cached images and files"

### High Storage Usage

**Symptom**: Cache consuming excessive disk space

**Checks**:
1. Check cache stats:
   ```typescript
   const stats = await cache.getStats();
   console.log(`Size: ~${stats.estimatedSize}MB`);
   console.log(`Blocks: ${stats.blocksCount}`);
   ```

2. Verify eviction is working:
   ```typescript
   await cache.evictOldEntries();
   ```

3. Reduce capacity limits in `cache.ts`:
   ```typescript
   const MAX_BLOCKS = 50_000;  // reduce from 100k
   ```

4. Clear old data:
   ```typescript
   await cache.clearAll();
   ```

## Security Considerations

### Data Integrity

- Cache data is **read-only** and cannot modify chain state
- Cache mismatches are resolved by fetching from RPC
- No sensitive data (private keys) stored in cache
- Cache corruption only affects local view, not chain

### Privacy

- Cache stores public blockchain data only
- No user-specific or identifying information
- Cache is isolated per browser profile
- Clearing browser data removes cache

### Storage Quotas

- Browser may limit IndexedDB storage (typically 50% of available disk)
- Cache respects browser storage policies
- Graceful degradation if storage unavailable
- Users can clear cache anytime

## Browser Compatibility

### Supported Browsers

- ✅ Chrome/Edge 87+
- ✅ Firefox 78+
- ✅ Safari 14+
- ✅ Opera 73+

### Not Supported

- ❌ Internet Explorer (IndexedDB v2 required)
- ⚠️ Private/Incognito mode (limited storage)

### Feature Detection

The cache automatically detects IndexedDB availability:

```typescript
import { isCacheAvailable } from './services/cache';

if (!isCacheAvailable()) {
  console.warn('Cache not available, using RPC only');
}
```

## Testing

### Unit Tests

Run cache unit tests:

```bash
cd explorer-web
pnpm test test/unit/cache.test.ts
```

### Integration Testing

Test with live devnet:

```bash
# Start devnet
cd tests/devnet
docker-compose up -d

# Run explorer
cd explorer-web
VITE_RPC_URL=http://localhost:8545 pnpm dev

# Monitor cache status in browser console
```

### Performance Testing

Simulate high block rate:

```bash
# Generate blocks rapidly
python -m tests.e2e.generate_blocks --rate 10 --duration 60

# Monitor sync performance in explorer
# Check: blocks cached, sync delay, cache hit rate
```

## Future Enhancements

Planned improvements:

1. **Smart Prefetching**: Predict user navigation and prefetch data
2. **Compression**: Compress cached data to reduce storage
3. **Partial Sync**: Sync only recent blocks, not full history
4. **Multi-Chain Support**: Separate caches per chain ID
5. **Export/Import**: Backup and restore cache data
6. **Advanced Eviction**: LFU or hybrid strategies
7. **Cache Warming**: Pre-populate cache on first load

## References

- [IndexedDB API](https://developer.mozilla.org/en-US/docs/Web/API/IndexedDB_API)
- [Storage Quotas](https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria)
- [Web Storage Best Practices](https://web.dev/storage-for-the-web/)
