/**
 * Animica Explorer — IndexedDB Cache Layer
 * -----------------------------------------------------------------------------
 * Persistent local cache for blockchain data using IndexedDB.
 * - Stores blocks, transactions, and address metadata
 * - Enables offline/degraded mode operation
 * - Supports efficient range queries and batch operations
 * - Implements LRU eviction when storage limits are approached
 *
 * Schema:
 *  - blocks: { height: number, hash: string, data: BlockSummary, timestamp: number }
 *  - txs: { hash: string, data: TxDetail, blockHeight?: number, timestamp: number }
 *  - addresses: { address: string, data: AddressSummary, timestamp: number }
 *  - meta: { key: string, value: any } // sync status, settings
 */

export interface BlockCacheEntry {
  height: number;
  hash: string;
  data: any; // BlockSummary
  timestamp: number; // when cached (ms epoch)
}

export interface TxCacheEntry {
  hash: string;
  data: any; // TxDetail
  blockHeight?: number;
  timestamp: number;
}

export interface AddressCacheEntry {
  address: string;
  data: any; // AddressSummary
  timestamp: number;
}

export interface MetaEntry {
  key: string;
  value: any;
}

export interface CacheStats {
  blocksCount: number;
  txsCount: number;
  addressesCount: number;
  lastSyncHeight: number | null;
  lastSyncTime: number | null;
  estimatedSize: number; // rough MB estimate
}

const DB_NAME = 'animica-explorer-cache';
const DB_VERSION = 1;

// Store names
const BLOCKS_STORE = 'blocks';
const TXS_STORE = 'txs';
const ADDRESSES_STORE = 'addresses';
const META_STORE = 'meta';

// Meta keys
const META_LAST_SYNC_HEIGHT = 'lastSyncHeight';
const META_LAST_SYNC_TIME = 'lastSyncTime';
const META_GENESIS_SYNC_HEIGHT = 'genesisSyncHeight';

// Cache limits (to avoid filling disk)
const MAX_BLOCKS = 100_000; // ~100k blocks at ~2KB each = ~200MB
const MAX_TXS = 500_000;
const MAX_ADDRESSES = 50_000;

/**
 * Explorer cache using IndexedDB.
 * Singleton pattern - use getCache() to obtain instance.
 */
export class ExplorerCache {
  private db: IDBDatabase | null = null;
  private initPromise: Promise<void> | null = null;

  private constructor() {}

  /**
   * Initialize the IndexedDB connection.
   * Safe to call multiple times (returns existing promise).
   */
  async init(): Promise<void> {
    if (this.db) return;
    if (this.initPromise) return this.initPromise;

    this.initPromise = new Promise<void>((resolve, reject) => {
      // IndexedDB not available (e.g., private browsing, very old browser)
      if (typeof indexedDB === 'undefined') {
        reject(new Error('IndexedDB not available'));
        return;
      }

      const req = indexedDB.open(DB_NAME, DB_VERSION);

      req.onerror = () => {
        reject(new Error(`IndexedDB open failed: ${req.error?.message || 'unknown'}`));
      };

      req.onsuccess = () => {
        this.db = req.result;
        resolve();
      };

      req.onupgradeneeded = (e) => {
        const db = (e.target as IDBOpenDBRequest).result;

        // Blocks store: primary key = height, indexed by hash
        if (!db.objectStoreNames.contains(BLOCKS_STORE)) {
          const blocksStore = db.createObjectStore(BLOCKS_STORE, { keyPath: 'height' });
          blocksStore.createIndex('hash', 'hash', { unique: true });
          blocksStore.createIndex('timestamp', 'timestamp', { unique: false });
        }

        // Transactions store: primary key = hash, indexed by blockHeight
        if (!db.objectStoreNames.contains(TXS_STORE)) {
          const txsStore = db.createObjectStore(TXS_STORE, { keyPath: 'hash' });
          txsStore.createIndex('blockHeight', 'blockHeight', { unique: false });
          txsStore.createIndex('timestamp', 'timestamp', { unique: false });
        }

        // Addresses store: primary key = address
        if (!db.objectStoreNames.contains(ADDRESSES_STORE)) {
          const addrStore = db.createObjectStore(ADDRESSES_STORE, { keyPath: 'address' });
          addrStore.createIndex('timestamp', 'timestamp', { unique: false });
        }

        // Meta store: key-value for sync state
        if (!db.objectStoreNames.contains(META_STORE)) {
          db.createObjectStore(META_STORE, { keyPath: 'key' });
        }
      };
    });

    return this.initPromise;
  }

  /**
   * Close the database connection.
   */
  close(): void {
    if (this.db) {
      this.db.close();
      this.db = null;
      this.initPromise = null;
    }
  }

  /**
   * Check if cache is ready.
   */
  isReady(): boolean {
    return this.db !== null;
  }

  // ----------------------------- Blocks -------------------------------------

  /**
   * Get a block by height from cache.
   */
  async getBlock(height: number): Promise<any | null> {
    await this.init();
    if (!this.db) return null;

    return new Promise((resolve, reject) => {
      const tx = this.db!.transaction(BLOCKS_STORE, 'readonly');
      const store = tx.objectStore(BLOCKS_STORE);
      const req = store.get(height);

      req.onsuccess = () => {
        const entry = req.result as BlockCacheEntry | undefined;
        resolve(entry ? entry.data : null);
      };
      req.onerror = () => reject(req.error);
    });
  }

  /**
   * Get a block by hash from cache.
   */
  async getBlockByHash(hash: string): Promise<any | null> {
    await this.init();
    if (!this.db) return null;

    return new Promise((resolve, reject) => {
      const tx = this.db!.transaction(BLOCKS_STORE, 'readonly');
      const store = tx.objectStore(BLOCKS_STORE);
      const index = store.index('hash');
      const req = index.get(hash);

      req.onsuccess = () => {
        const entry = req.result as BlockCacheEntry | undefined;
        resolve(entry ? entry.data : null);
      };
      req.onerror = () => reject(req.error);
    });
  }

  /**
   * Get a range of blocks (descending order).
   * Returns blocks in range [high, low] inclusive, sorted by height descending.
   */
  async getBlocksRange(high: number, low: number): Promise<any[]> {
    await this.init();
    if (!this.db || high < low) return [];

    return new Promise((resolve, reject) => {
      const tx = this.db!.transaction(BLOCKS_STORE, 'readonly');
      const store = tx.objectStore(BLOCKS_STORE);
      const range = IDBKeyRange.bound(low, high);
      const req = store.openCursor(range, 'prev'); // descending

      const results: any[] = [];

      req.onsuccess = () => {
        const cursor = req.result;
        if (cursor) {
          const entry = cursor.value as BlockCacheEntry;
          results.push(entry.data);
          cursor.continue();
        } else {
          resolve(results);
        }
      };
      req.onerror = () => reject(req.error);
    });
  }

  /**
   * Put a block into cache.
   */
  async putBlock(height: number, hash: string, data: any): Promise<void> {
    await this.init();
    if (!this.db) return;

    const entry: BlockCacheEntry = {
      height,
      hash,
      data,
      timestamp: Date.now(),
    };

    return new Promise((resolve, reject) => {
      const tx = this.db!.transaction(BLOCKS_STORE, 'readwrite');
      const store = tx.objectStore(BLOCKS_STORE);
      const req = store.put(entry);

      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error);
    });
  }

  /**
   * Put multiple blocks into cache efficiently.
   */
  async putBlocks(blocks: Array<{ height: number; hash: string; data: any }>): Promise<void> {
    await this.init();
    if (!this.db || blocks.length === 0) return;

    return new Promise((resolve, reject) => {
      const tx = this.db!.transaction(BLOCKS_STORE, 'readwrite');
      const store = tx.objectStore(BLOCKS_STORE);

      let remaining = blocks.length;
      let settled = false;

      const finish = (err?: DOMException | null) => {
        if (settled) return;
        settled = true;
        if (err) {
          reject(err);
        } else {
          resolve();
        }
      };

      tx.onabort = () => finish(tx.error);
      tx.onerror = () => finish(tx.error);
      tx.oncomplete = () => finish();

      for (const b of blocks) {
        const entry: BlockCacheEntry = {
          height: b.height,
          hash: b.hash,
          data: b.data,
          timestamp: Date.now(),
        };

        const req = store.put(entry);
        req.onsuccess = () => {
          remaining -= 1;
          if (remaining === 0) finish();
        };
        req.onerror = (event) => {
          const err = req.error;
          if (err && (err.name === 'ConstraintError' || err.name === 'DataError')) {
            event.preventDefault();
            remaining -= 1;
            if (remaining === 0) finish();
            return;
          }
          finish(err);
        };
      }
    });
  }

  // ----------------------------- Transactions --------------------------------

  /**
   * Get a transaction by hash from cache.
   */
  async getTx(hash: string): Promise<any | null> {
    await this.init();
    if (!this.db) return null;

    return new Promise((resolve, reject) => {
      const tx = this.db!.transaction(TXS_STORE, 'readonly');
      const store = tx.objectStore(TXS_STORE);
      const req = store.get(hash);

      req.onsuccess = () => {
        const entry = req.result as TxCacheEntry | undefined;
        resolve(entry ? entry.data : null);
      };
      req.onerror = () => reject(req.error);
    });
  }

  /**
   * Put a transaction into cache.
   */
  async putTx(hash: string, data: any, blockHeight?: number): Promise<void> {
    await this.init();
    if (!this.db) return;

    const entry: TxCacheEntry = {
      hash,
      data,
      blockHeight,
      timestamp: Date.now(),
    };

    return new Promise((resolve, reject) => {
      const tx = this.db!.transaction(TXS_STORE, 'readwrite');
      const store = tx.objectStore(TXS_STORE);
      const req = store.put(entry);

      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error);
    });
  }

  // ----------------------------- Addresses -----------------------------------

  /**
   * Get address data from cache.
   */
  async getAddress(address: string): Promise<any | null> {
    await this.init();
    if (!this.db) return null;

    return new Promise((resolve, reject) => {
      const tx = this.db!.transaction(ADDRESSES_STORE, 'readonly');
      const store = tx.objectStore(ADDRESSES_STORE);
      const req = store.get(address);

      req.onsuccess = () => {
        const entry = req.result as AddressCacheEntry | undefined;
        resolve(entry ? entry.data : null);
      };
      req.onerror = () => reject(req.error);
    });
  }

  /**
   * Put address data into cache.
   */
  async putAddress(address: string, data: any): Promise<void> {
    await this.init();
    if (!this.db) return;

    const entry: AddressCacheEntry = {
      address,
      data,
      timestamp: Date.now(),
    };

    return new Promise((resolve, reject) => {
      const tx = this.db!.transaction(ADDRESSES_STORE, 'readwrite');
      const store = tx.objectStore(ADDRESSES_STORE);
      const req = store.put(entry);

      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error);
    });
  }

  // ----------------------------- Meta ----------------------------------------

  /**
   * Get last synced block height.
   */
  async getLastSyncHeight(): Promise<number | null> {
    const val = await this.getMeta(META_LAST_SYNC_HEIGHT);
    return typeof val === 'number' ? val : null;
  }

  /**
   * Set last synced block height.
   */
  async setLastSyncHeight(height: number): Promise<void> {
    await this.setMeta(META_LAST_SYNC_HEIGHT, height);
  }

  /**
   * Get last sync timestamp (ms epoch).
   */
  async getLastSyncTime(): Promise<number | null> {
    const val = await this.getMeta(META_LAST_SYNC_TIME);
    return typeof val === 'number' ? val : null;
  }

  /**
   * Set last sync timestamp.
   */
  async setLastSyncTime(time: number): Promise<void> {
    await this.setMeta(META_LAST_SYNC_TIME, time);
  }

  /**
   * Get last synced block height from genesis bootstrap (ascending).
   */
  async getGenesisSyncHeight(): Promise<number | null> {
    const val = await this.getMeta(META_GENESIS_SYNC_HEIGHT);
    return typeof val === 'number' ? val : null;
  }

  /**
   * Set last synced block height from genesis bootstrap (ascending).
   */
  async setGenesisSyncHeight(height: number): Promise<void> {
    await this.setMeta(META_GENESIS_SYNC_HEIGHT, height);
  }

  /**
   * Generic meta getter.
   */
  async getMeta(key: string): Promise<any | null> {
    await this.init();
    if (!this.db) return null;

    return new Promise((resolve, reject) => {
      const tx = this.db!.transaction(META_STORE, 'readonly');
      const store = tx.objectStore(META_STORE);
      const req = store.get(key);

      req.onsuccess = () => {
        const entry = req.result as MetaEntry | undefined;
        resolve(entry ? entry.value : null);
      };
      req.onerror = () => reject(req.error);
    });
  }

  /**
   * Generic meta setter.
   */
  async setMeta(key: string, value: any): Promise<void> {
    await this.init();
    if (!this.db) return;

    const entry: MetaEntry = { key, value };

    return new Promise((resolve, reject) => {
      const tx = this.db!.transaction(META_STORE, 'readwrite');
      const store = tx.objectStore(META_STORE);
      const req = store.put(entry);

      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error);
    });
  }

  // ----------------------------- Stats & Management --------------------------

  /**
   * Get cache statistics.
   */
  async getStats(): Promise<CacheStats> {
    await this.init();
    if (!this.db) {
      return {
        blocksCount: 0,
        txsCount: 0,
        addressesCount: 0,
        lastSyncHeight: null,
        lastSyncTime: null,
        estimatedSize: 0,
      };
    }

    const [blocksCount, txsCount, addressesCount, lastSyncHeight, lastSyncTime] = await Promise.all([
      this.countStore(BLOCKS_STORE),
      this.countStore(TXS_STORE),
      this.countStore(ADDRESSES_STORE),
      this.getLastSyncHeight(),
      this.getLastSyncTime(),
    ]);

    // Rough size estimate: 2KB per block, 1KB per tx, 500B per address
    const estimatedSize = Math.round((blocksCount * 2 + txsCount + addressesCount * 0.5) / 1024);

    return {
      blocksCount,
      txsCount,
      addressesCount,
      lastSyncHeight,
      lastSyncTime,
      estimatedSize,
    };
  }

  /**
   * Count entries in a store.
   */
  private async countStore(storeName: string): Promise<number> {
    if (!this.db) return 0;

    return new Promise((resolve, reject) => {
      const tx = this.db!.transaction(storeName, 'readonly');
      const store = tx.objectStore(storeName);
      const req = store.count();

      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  /**
   * Evict old entries when limits are exceeded (LRU by timestamp).
   */
  async evictOldEntries(): Promise<void> {
    await this.init();
    if (!this.db) return;

    await Promise.all([
      this.evictStoreByTimestamp(BLOCKS_STORE, MAX_BLOCKS),
      this.evictStoreByTimestamp(TXS_STORE, MAX_TXS),
      this.evictStoreByTimestamp(ADDRESSES_STORE, MAX_ADDRESSES),
    ]);
  }

  /**
   * Evict oldest entries from a store if count exceeds limit.
   */
  private async evictStoreByTimestamp(storeName: string, maxCount: number): Promise<void> {
    if (!this.db) return;

    const count = await this.countStore(storeName);
    if (count <= maxCount) return;

    const toDelete = count - maxCount;

    return new Promise((resolve, reject) => {
      const tx = this.db!.transaction(storeName, 'readwrite');
      const store = tx.objectStore(storeName);
      const index = store.index('timestamp');
      const req = index.openCursor(null, 'next'); // oldest first

      let deleted = 0;

      req.onsuccess = () => {
        const cursor = req.result;
        if (cursor && deleted < toDelete) {
          cursor.delete();
          deleted++;
          cursor.continue();
        } else {
          resolve();
        }
      };
      req.onerror = () => reject(req.error);
    });
  }

  /**
   * Clear all cached data (for reset/troubleshooting).
   */
  async clearAll(): Promise<void> {
    await this.init();
    if (!this.db) return;

    return new Promise((resolve, reject) => {
      const tx = this.db!.transaction([BLOCKS_STORE, TXS_STORE, ADDRESSES_STORE, META_STORE], 'readwrite');

      const promises: Promise<void>[] = [
        this.clearStore(tx, BLOCKS_STORE),
        this.clearStore(tx, TXS_STORE),
        this.clearStore(tx, ADDRESSES_STORE),
        this.clearStore(tx, META_STORE),
      ];

      Promise.all(promises)
        .then(() => resolve())
        .catch((err) => reject(err));
    });
  }

  private clearStore(tx: IDBTransaction, storeName: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const store = tx.objectStore(storeName);
      const req = store.clear();
      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error);
    });
  }
}

// ----------------------------- Singleton Pattern ----------------------------

let _cacheInstance: ExplorerCache | null = null;
let cacheDisabled = false;
let cacheDisabledReason: unknown = null;

function isDomException(err: unknown): boolean {
  return typeof DOMException !== 'undefined' && err instanceof DOMException;
}

export function isCacheAccessError(err: unknown): boolean {
  if (!err) return false;
  if (isDomException(err)) return true;
  const name = (err as { name?: string }).name;
  const message = (err as { message?: string }).message ?? String(err);
  return name === 'SecurityError' || name === 'InvalidStateError' || /indexeddb/i.test(message);
}

/**
 * Get the singleton cache instance.
 * Initializes on first call.
 */
export async function getCache(): Promise<ExplorerCache> {
  if (cacheDisabled) {
    throw cacheDisabledReason ?? new Error('Explorer cache disabled');
  }
  if (!_cacheInstance) {
    _cacheInstance = new ExplorerCache();
    try {
      await _cacheInstance.init();
    } catch (err) {
      cacheDisabled = true;
      cacheDisabledReason = err;
      _cacheInstance = null;
      throw err;
    }
  }
  return _cacheInstance;
}

/**
 * Check if cache is available (IndexedDB supported).
 */
export function isCacheAvailable(): boolean {
  return typeof indexedDB !== 'undefined' && !cacheDisabled;
}
