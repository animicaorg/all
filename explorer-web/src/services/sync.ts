/**
 * Animica Explorer — Background Sync Manager
 * -----------------------------------------------------------------------------
 * Manages incremental synchronization of blockchain data from RPC to cache.
 * - Fetches missing blocks in batches
 * - Tracks sync progress and handles interruptions
 * - Prioritizes recent blocks (head-first strategy)
 * - Throttles requests to avoid overwhelming RPC
 * - Supports pause/resume and manual trigger
 */

import { getCache, type ExplorerCache } from './cache';
import type { RpcClient } from '../state/blocks';

export interface SyncStatus {
  isRunning: boolean;
  isSynced: boolean;
  lastSyncHeight: number | null;
  currentHeight: number | null;
  progress: number; // 0-1
  blocksToSync: number;
  error: string | null;
}

export interface SyncOptions {
  batchSize?: number; // blocks per batch (default: 20)
  delayMs?: number; // delay between batches (default: 500)
  maxRetries?: number; // retries per batch (default: 3)
  catchupThreshold?: number; // blocks behind before entering catchup mode (default: 100)
  bootstrapFromGenesis?: boolean; // sync from genesis upward (default: true)
  bootstrapBatchSize?: number; // blocks per bootstrap batch (default: 20)
  bootstrapDelayMs?: number; // delay between bootstrap batches (default: 200)
}

const DEFAULT_BATCH_SIZE = 20;
const DEFAULT_DELAY_MS = 500;
const DEFAULT_MAX_RETRIES = 3;
const DEFAULT_CATCHUP_THRESHOLD = 100;
const DEFAULT_BOOTSTRAP_ENABLED = true;
const DEFAULT_BOOTSTRAP_BATCH_SIZE = 20;
const DEFAULT_BOOTSTRAP_DELAY_MS = 200;

/**
 * Background sync manager.
 */
export class SyncManager {
  private cache: ExplorerCache | null = null;
  private rpcClient: RpcClient | null = null;
  private running = false;
  private paused = false;
  private abortController: AbortController | null = null;

  private status: SyncStatus = {
    isRunning: false,
    isSynced: false,
    lastSyncHeight: null,
    currentHeight: null,
    progress: 0,
    blocksToSync: 0,
    error: null,
  };

  private options: Required<SyncOptions> = {
    batchSize: DEFAULT_BATCH_SIZE,
    delayMs: DEFAULT_DELAY_MS,
    maxRetries: DEFAULT_MAX_RETRIES,
    catchupThreshold: DEFAULT_CATCHUP_THRESHOLD,
    bootstrapFromGenesis: DEFAULT_BOOTSTRAP_ENABLED,
    bootstrapBatchSize: DEFAULT_BOOTSTRAP_BATCH_SIZE,
    bootstrapDelayMs: DEFAULT_BOOTSTRAP_DELAY_MS,
  };

  private listeners: Array<(status: SyncStatus) => void> = [];

  constructor(opts?: SyncOptions) {
    if (opts) {
      this.options = { ...this.options, ...opts };
    }
  }

  /**
   * Set the RPC client to use for fetching data.
   */
  setRpcClient(client: RpcClient): void {
    this.rpcClient = client;
  }

  /**
   * Start background synchronization.
   * Safe to call multiple times (no-op if already running).
   */
  async start(): Promise<void> {
    if (this.running) {
      console.debug('[sync] Already running');
      return;
    }

    if (!this.rpcClient) {
      throw new Error('[sync] RPC client not set. Call setRpcClient() first.');
    }

    console.log('[sync] Starting background sync...');
    this.running = true;
    this.paused = false;
    this.abortController = new AbortController();

    this.updateStatus({ isRunning: true, error: null });

    // Initialize cache
    try {
      this.cache = await getCache();
    } catch (err: any) {
      this.running = false;
      this.updateStatus({
        isRunning: false,
        error: `Failed to initialize cache: ${err?.message || String(err)}`,
      });
      console.error('[sync] Cache init failed:', err);
      return;
    }

    // Run sync loop
    this.syncLoop().catch((err) => {
      console.error('[sync] Sync loop error:', err);
      this.updateStatus({
        isRunning: false,
        error: String(err?.message || err),
      });
    });
  }

  /**
   * Stop synchronization.
   */
  stop(): void {
    if (!this.running) return;

    console.log('[sync] Stopping sync...');
    this.running = false;
    this.paused = false;
    if (this.abortController) {
      this.abortController.abort();
      this.abortController = null;
    }
    this.updateStatus({ isRunning: false });
  }

  /**
   * Pause synchronization (can be resumed).
   */
  pause(): void {
    if (!this.running) return;
    console.log('[sync] Pausing sync...');
    this.paused = true;
  }

  /**
   * Resume synchronization.
   */
  resume(): void {
    if (!this.running) return;
    console.log('[sync] Resuming sync...');
    this.paused = false;
  }

  /**
   * Get current sync status.
   */
  getStatus(): SyncStatus {
    return { ...this.status };
  }

  /**
   * Subscribe to status updates.
   * Returns unsubscribe function.
   */
  onStatusChange(listener: (status: SyncStatus) => void): () => void {
    this.listeners.push(listener);
    return () => {
      this.listeners = this.listeners.filter((l) => l !== listener);
    };
  }

  /**
   * Manually trigger a sync cycle (fetch latest blocks).
   */
  async triggerSync(): Promise<void> {
    if (!this.rpcClient || !this.cache) {
      throw new Error('[sync] Not initialized');
    }

    console.log('[sync] Manual sync triggered');
    const currentHeight = await this.getHeadHeight();
    if (currentHeight == null) return;
    await this.syncLatest(currentHeight);
  }

  // ----------------------------- Internal ------------------------------------

  private async syncLoop(): Promise<void> {
    const signal = this.abortController!.signal;

    while (this.running && !signal.aborted) {
      if (this.paused) {
        // Wait a bit and check again
        await this.sleep(1000);
        continue;
      }

      try {
        const currentHeight = await this.getHeadHeight();
        if (currentHeight == null) {
          await this.sleep(this.options.delayMs);
          continue;
        }

        if (this.options.bootstrapFromGenesis) {
          await this.syncGenesis(currentHeight);
        }

        await this.syncLatest(currentHeight);

        // Check if we need catchup
        const behind = this.status.blocksToSync;
        if (behind > this.options.catchupThreshold) {
          console.log(`[sync] Behind by ${behind} blocks, entering catchup mode`);
          await this.syncCatchup();
        }

        this.updateStatus({ error: null });
      } catch (err: any) {
        console.error('[sync] Sync cycle error:', err);
        this.updateStatus({ error: String(err?.message || err) });
      }

      // Wait before next cycle
      await this.sleep(this.options.delayMs);
    }

    this.running = false;
    this.updateStatus({ isRunning: false });
  }

  /**
   * Sync the latest blocks (head-first strategy).
   */
  private async syncLatest(currentHeightInput?: number): Promise<void> {
    if (!this.rpcClient || !this.cache) return;

    // Get current head height from RPC
    const currentHeight = currentHeightInput ?? (await this.getHeadHeight());
    if (currentHeight == null) return;

    // Get last synced height from cache
    const lastSyncHeight = (await this.cache.getLastSyncHeight()) ?? 0;

    this.updateStatus({
      currentHeight,
      lastSyncHeight,
      blocksToSync: Math.max(0, currentHeight - lastSyncHeight),
    });

    // If already synced, just check if there are new blocks
    if (lastSyncHeight >= currentHeight) {
      this.updateStatus({ isSynced: true, progress: 1 });
      return;
    }

    // Fetch missing blocks in descending order (newest first)
    const fromHeight = currentHeight;
    const toHeight = lastSyncHeight + 1;
    const count = Math.min(this.options.batchSize, fromHeight - toHeight + 1);

    if (count <= 0) {
      this.updateStatus({ isSynced: true, progress: 1 });
      return;
    }

    console.debug(`[sync] Fetching blocks ${fromHeight} to ${fromHeight - count + 1}`);

    try {
      // Fetch batch
      const blocks: any[] = [];
      for (let h = fromHeight; h >= toHeight && blocks.length < count; h--) {
        try {
          const block = await this.rpcClient.getBlock(h);
          if (block) {
            blocks.push({ height: h, hash: block.hash, data: block });
          }
        } catch (err) {
          console.warn(`[sync] Failed to fetch block ${h}:`, err);
          // Continue with other blocks
        }
      }

      // Store in cache
      if (blocks.length > 0) {
        await this.cache.putBlocks(blocks);
        const newLastHeight = Math.max(...blocks.map((b) => b.height));
        await this.cache.setLastSyncHeight(newLastHeight);
        await this.cache.setLastSyncTime(Date.now());

        console.debug(`[sync] Cached ${blocks.length} blocks, last height: ${newLastHeight}`);

        this.updateStatus({
          lastSyncHeight: newLastHeight,
          progress: newLastHeight / currentHeight,
          isSynced: newLastHeight >= currentHeight,
        });
      }
    } catch (err) {
      console.error('[sync] Batch fetch error:', err);
      throw err;
    }
  }

  /**
   * Catchup mode: aggressively fetch missing blocks.
   */
  private async syncCatchup(): Promise<void> {
    if (!this.rpcClient || !this.cache) return;

    const currentHeight = this.status.currentHeight ?? 0;
    const lastSyncHeight = this.status.lastSyncHeight ?? 0;

    if (lastSyncHeight >= currentHeight) {
      this.updateStatus({ isSynced: true, progress: 1 });
      return;
    }

    const totalToSync = currentHeight - lastSyncHeight;
    let synced = 0;

    console.log(`[sync] Catchup: syncing ${totalToSync} blocks...`);

    // Fetch in larger batches during catchup
    const catchupBatchSize = this.options.batchSize * 2;

    while (synced < totalToSync && this.running && !this.paused) {
      const fromHeight = currentHeight - synced;
      const toHeight = Math.max(lastSyncHeight + 1, fromHeight - catchupBatchSize + 1);
      const batchCount = fromHeight - toHeight + 1;

      try {
        const blocks: any[] = [];
        for (let h = fromHeight; h >= toHeight && blocks.length < batchCount; h--) {
          try {
            const block = await this.rpcClient!.getBlock(h);
            if (block) {
              blocks.push({ height: h, hash: block.hash, data: block });
            }
          } catch (err) {
            console.warn(`[sync] Catchup: failed to fetch block ${h}:`, err);
          }
        }

        if (blocks.length > 0) {
          await this.cache!.putBlocks(blocks);
          synced += blocks.length;

          const newLastHeight = Math.max(...blocks.map((b) => b.height));
          await this.cache!.setLastSyncHeight(newLastHeight);
          await this.cache!.setLastSyncTime(Date.now());

          this.updateStatus({
            lastSyncHeight: newLastHeight,
            progress: newLastHeight / currentHeight,
            blocksToSync: totalToSync - synced,
          });

          console.debug(`[sync] Catchup progress: ${synced}/${totalToSync} blocks`);
        } else {
          break; // No more blocks fetched
        }

        // Small delay between catchup batches
        await this.sleep(200);
      } catch (err) {
        console.error('[sync] Catchup error:', err);
        break;
      }
    }

    this.updateStatus({ isSynced: synced >= totalToSync });
  }

  /**
   * Bootstrap from genesis (ascending).
   */
  private async syncGenesis(currentHeight: number): Promise<void> {
    if (!this.rpcClient || !this.cache) return;
    if (currentHeight <= 0) return;

    const batchSize = Math.max(1, this.options.bootstrapBatchSize);
    const delayMs = Math.max(0, this.options.bootstrapDelayMs);

    while (this.running && !this.paused) {
      const genesisSyncHeight = (await this.cache.getGenesisSyncHeight()) ?? 0;
      const startHeight = Math.max(1, genesisSyncHeight + 1);
      if (startHeight > currentHeight) {
        return;
      }

      const endHeight = Math.min(currentHeight, startHeight + batchSize - 1);
      const heights = Array.from({ length: endHeight - startHeight + 1 }, (_, i) => startHeight + i);

      const blocks = await Promise.all(
        heights.map(async (height) => {
          try {
            const block = await this.rpcClient!.getBlock(height);
            if (!block) return null;
            return { height, hash: block.hash ?? '', data: block };
          } catch (err) {
            console.warn(`[sync] Genesis bootstrap failed to fetch block ${height}:`, err);
            return null;
          }
        })
      );

      const filtered = blocks.filter((b): b is { height: number; hash: string; data: any } => Boolean(b));
      if (filtered.length > 0) {
        await this.cache.putBlocks(filtered);
        const newGenesisHeight = Math.max(...filtered.map((b) => b.height));
        await this.cache.setGenesisSyncHeight(newGenesisHeight);
        await this.cache.setLastSyncTime(Date.now());
        console.debug(`[sync] Genesis bootstrap cached ${filtered.length} blocks up to ${newGenesisHeight}`);
      }

      if (filtered.length < heights.length) {
        // If we failed to fetch some blocks, pause briefly to avoid tight loops.
        await this.sleep(Math.max(delayMs, 200));
      } else if (delayMs > 0) {
        await this.sleep(delayMs);
      }
    }
  }

  private updateStatus(patch: Partial<SyncStatus>): void {
    this.status = { ...this.status, ...patch };
    // Notify listeners
    for (const listener of this.listeners) {
      try {
        listener(this.getStatus());
      } catch (err) {
        console.error('[sync] Listener error:', err);
      }
    }
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  private async getHeadHeight(): Promise<number | null> {
    if (!this.rpcClient) return null;
    try {
      const head = await this.rpcClient.getBlock(0); // Assume 0 or 'latest' gives head
      const height = head?.height ?? 0;
      return Number.isFinite(height) ? height : null;
    } catch (err) {
      console.warn('[sync] Failed to fetch head:', err);
      return null;
    }
  }
}

// ----------------------------- Singleton ------------------------------------

let _syncManager: SyncManager | null = null;

/**
 * Get the singleton sync manager instance.
 */
export function getSyncManager(opts?: SyncOptions): SyncManager {
  if (!_syncManager) {
    _syncManager = new SyncManager(opts);
  }
  return _syncManager;
}
