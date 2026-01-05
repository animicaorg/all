/**
 * Enhanced RPC Client with schema validation, request deduplication, and tracing
 * 
 * This wraps the existing services/rpc.ts client with:
 * - Zod schema validation for all responses
 * - Request ID tracing for debugging
 * - Request deduplication to avoid redundant calls
 * - Feature detection for optional RPC methods
 */

import { createRpc as createBaseRpc, type ExplorerRpcClient } from '../../services/rpc';
import {
  chainHeadSchema,
  chainStatusSchema,
  blockDetailSchema,
  txDetailSchema,
  addressDetailSchema,
  mempoolStatusSchema,
  peersStatusSchema,
  feePolicySchema,
  safeParse,
  type ChainHead,
  type ChainStatus,
  type BlockDetail,
  type TxDetail,
  type AddressDetail,
  type MempoolStatus,
  type PeersStatus,
  type FeePolicy,
} from './schemas';

export type {
  ChainHead,
  ChainStatus,
  BlockDetail,
  TxDetail,
  AddressDetail,
  MempoolStatus,
  PeersStatus,
  FeePolicy,
};

// ============================================================================
// Feature Detection
// ============================================================================

export interface RpcFeatures {
  hasMempool: boolean;
  hasPeers: boolean;
  hasSyncStatus: boolean;
  hasNodeInfo: boolean;
  hasFeePolicy: boolean;
  hasTxIndex: boolean;
  hasReceipts: boolean;
}

const DEFAULT_FEATURES: RpcFeatures = {
  hasMempool: false,
  hasPeers: false,
  hasSyncStatus: false,
  hasNodeInfo: false,
  hasFeePolicy: false,
  hasTxIndex: true,
  hasReceipts: true,
};

// ============================================================================
// Request Deduplication
// ============================================================================

interface PendingRequest<T> {
  promise: Promise<T>;
  timestamp: number;
}

class RequestDeduplicator {
  private pending = new Map<string, PendingRequest<any>>();
  private readonly ttlMs = 100; // Dedupe window: 100ms

  async dedupe<T>(key: string, fn: () => Promise<T>): Promise<T> {
    // Clean expired entries
    const now = Date.now();
    for (const [k, req] of this.pending.entries()) {
      if (now - req.timestamp > this.ttlMs) {
        this.pending.delete(k);
      }
    }

    // Return existing pending request if found
    const existing = this.pending.get(key);
    if (existing) {
      console.debug(`[RPC] Deduping request: ${key}`);
      return existing.promise;
    }

    // Create new request
    const promise = fn()
      .finally(() => {
        // Remove from pending after completion
        this.pending.delete(key);
      });

    this.pending.set(key, { promise, timestamp: now });
    return promise;
  }
}

// ============================================================================
// Enhanced RPC Client
// ============================================================================

export class EnhancedRpcClient {
  private baseClient: ExplorerRpcClient;
  private deduplicator = new RequestDeduplicator();
  private features: RpcFeatures = { ...DEFAULT_FEATURES };
  private featuresDetected = false;
  private requestIdCounter = 0;

  constructor(private url: string) {
    this.baseClient = createBaseRpc({ url });
  }

  // ------------------------------------------------------------------------
  // Feature Detection
  // ------------------------------------------------------------------------

  async detectFeatures(): Promise<RpcFeatures> {
    if (this.featuresDetected) {
      return this.features;
    }

    console.log('[RPC] Detecting available features...');

    // Test each optional method
    const checks = await Promise.allSettled([
      this.probeMethod('mempool.getStatus'),
      this.probeMethod('network.getPeers'),
      this.probeMethod('node.getSyncStatus'),
      this.probeMethod('node.getInfo'),
      this.probeMethod('chain.getFeePolicy'),
    ]);

    this.features = {
      hasMempool: checks[0]?.status === 'fulfilled',
      hasPeers: checks[1]?.status === 'fulfilled',
      hasSyncStatus: checks[2]?.status === 'fulfilled',
      hasNodeInfo: checks[3]?.status === 'fulfilled',
      hasFeePolicy: checks[4]?.status === 'fulfilled',
      hasTxIndex: true, // Assume true by default
      hasReceipts: true, // Assume true by default
    };

    this.featuresDetected = true;

    console.log('[RPC] Feature detection complete:', this.features);

    return this.features;
  }

  private async probeMethod(method: string): Promise<boolean> {
    try {
      await this.baseClient.call(method, []);
      return true;
    } catch (error: any) {
      // Method not found is expected for unsupported methods
      if (error?.code === -32601) {
        return false;
      }
      // Other errors might indicate the method exists but failed for other reasons
      return true;
    }
  }

  getFeatures(): RpcFeatures {
    return { ...this.features };
  }

  // ------------------------------------------------------------------------
  // Core Chain Methods
  // ------------------------------------------------------------------------

  async getChainId(): Promise<string> {
    const reqId = this.nextRequestId();
    console.debug(`[RPC:${reqId}] getChainId`);

    return this.deduplicator.dedupe('chainId', async () => {
      try {
        const result = await this.baseClient.getChainId();
        console.debug(`[RPC:${reqId}] getChainId result:`, result);
        return result;
      } catch (error) {
        console.error(`[RPC:${reqId}] getChainId failed:`, error);
        throw error;
      }
    });
  }

  async getHead(): Promise<ChainHead> {
    const reqId = this.nextRequestId();
    console.debug(`[RPC:${reqId}] getHead`);

    const result = await this.baseClient.getHead();
    const parsed = safeParse(chainHeadSchema, result, `getHead (req ${reqId})`);

    if (!parsed.success) {
      throw new Error(parsed.error);
    }

    console.debug(`[RPC:${reqId}] getHead result:`, parsed.data);
    return parsed.data;
  }

  async getChainStatus(): Promise<ChainStatus> {
    const reqId = this.nextRequestId();
    console.debug(`[RPC:${reqId}] getChainStatus`);

    return this.deduplicator.dedupe('chainStatus', async () => {
      try {
        // Try to fetch comprehensive status
        const [chainId, head, syncStatus, nodeInfo, feePolicy] = await Promise.allSettled([
          this.getChainId(),
          this.getHead(),
          this.features.hasSyncStatus ? this.getSyncStatus() : Promise.resolve(undefined),
          this.features.hasNodeInfo ? this.getNodeInfo() : Promise.resolve(undefined),
          this.features.hasFeePolicy ? this.getFeePolicy() : Promise.resolve(undefined),
        ]);

        const status: ChainStatus = {
          chainId: chainId.status === 'fulfilled' ? chainId.value : 'unknown',
          head: head.status === 'fulfilled' ? head.value : undefined,
          syncPhase: syncStatus.status === 'fulfilled' ? syncStatus.value?.phase : undefined,
          syncProgress: syncStatus.status === 'fulfilled' ? syncStatus.value?.progress : undefined,
          nodeVersion: nodeInfo.status === 'fulfilled' ? nodeInfo.value?.version : undefined,
          peers: nodeInfo.status === 'fulfilled' ? nodeInfo.value?.peers : undefined,
        };

        console.debug(`[RPC:${reqId}] getChainStatus result:`, status);
        return status;
      } catch (error) {
        console.error(`[RPC:${reqId}] getChainStatus failed:`, error);
        throw error;
      }
    });
  }

  private async getSyncStatus(): Promise<{ phase: string; progress?: number } | undefined> {
    try {
      const result = await this.baseClient.call<any>('node.getSyncStatus', []);
      return {
        phase: result.phase || result.syncPhase || 'unknown',
        progress: result.progress,
      };
    } catch {
      return undefined;
    }
  }

  private async getNodeInfo(): Promise<{ version?: string; peers?: number } | undefined> {
    try {
      const result = await this.baseClient.call<any>('node.getInfo', []);
      return {
        version: result.version,
        peers: result.peers || result.peerCount,
      };
    } catch {
      return undefined;
    }
  }

  private async getFeePolicy(): Promise<FeePolicy | undefined> {
    try {
      const result = await this.baseClient.call<any>('chain.getFeePolicy', []);
      const parsed = safeParse(feePolicySchema, result, 'getFeePolicy');
      return parsed.success ? parsed.data : undefined;
    } catch {
      return undefined;
    }
  }

  // ------------------------------------------------------------------------
  // Block Methods
  // ------------------------------------------------------------------------

  async getBlock(heightOrHash: number | string): Promise<BlockDetail> {
    const reqId = this.nextRequestId();
    const key = `block:${heightOrHash}`;
    console.debug(`[RPC:${reqId}] getBlock(${heightOrHash})`);

    return this.deduplicator.dedupe(key, async () => {
      try {
        const result = typeof heightOrHash === 'number'
          ? await this.baseClient.getBlock(heightOrHash)
          : await this.baseClient.call<any>('chain.getBlockByHash', [heightOrHash]);

        const parsed = safeParse(blockDetailSchema, result, `getBlock(${heightOrHash}) req ${reqId}`);

        if (!parsed.success) {
          throw new Error(parsed.error);
        }

        console.debug(`[RPC:${reqId}] getBlock result:`, parsed.data);
        return parsed.data;
      } catch (error) {
        console.error(`[RPC:${reqId}] getBlock(${heightOrHash}) failed:`, error);
        throw error;
      }
    });
  }

  async getBlocks(fromHeight: number, limit: number): Promise<BlockDetail[]> {
    const reqId = this.nextRequestId();
    console.debug(`[RPC:${reqId}] getBlocks(${fromHeight}, ${limit})`);

    try {
      const results = await this.baseClient.getBlocks!(fromHeight, limit);
      const blocks: BlockDetail[] = [];

      for (const result of results) {
        const parsed = safeParse(blockDetailSchema, result, `getBlocks item (req ${reqId})`);
        if (parsed.success) {
          blocks.push(parsed.data);
        } else {
          console.warn(`[RPC:${reqId}] Skipping invalid block in batch:`, result);
        }
      }

      console.debug(`[RPC:${reqId}] getBlocks result: ${blocks.length} blocks`);
      return blocks;
    } catch (error) {
      console.error(`[RPC:${reqId}] getBlocks failed:`, error);
      throw error;
    }
  }

  // ------------------------------------------------------------------------
  // Transaction Methods
  // ------------------------------------------------------------------------

  async getTx(hash: string): Promise<TxDetail> {
    const reqId = this.nextRequestId();
    const key = `tx:${hash}`;
    console.debug(`[RPC:${reqId}] getTx(${hash})`);

    return this.deduplicator.dedupe(key, async () => {
      try {
        const result = await this.baseClient.getTx!(hash);
        const parsed = safeParse(txDetailSchema, result, `getTx(${hash}) req ${reqId}`);

        if (!parsed.success) {
          throw new Error(parsed.error);
        }

        console.debug(`[RPC:${reqId}] getTx result:`, parsed.data);
        return parsed.data;
      } catch (error) {
        console.error(`[RPC:${reqId}] getTx(${hash}) failed:`, error);
        throw error;
      }
    });
  }

  // ------------------------------------------------------------------------
  // Address Methods
  // ------------------------------------------------------------------------

  async getAddress(address: string): Promise<AddressDetail> {
    const reqId = this.nextRequestId();
    const key = `address:${address}`;
    console.debug(`[RPC:${reqId}] getAddress(${address})`);

    return this.deduplicator.dedupe(key, async () => {
      try {
        const result = await this.baseClient.getAccount!(address);
        const parsed = safeParse(addressDetailSchema, result, `getAddress(${address}) req ${reqId}`);

        if (!parsed.success) {
          throw new Error(parsed.error);
        }

        console.debug(`[RPC:${reqId}] getAddress result:`, parsed.data);
        return parsed.data;
      } catch (error) {
        console.error(`[RPC:${reqId}] getAddress(${address}) failed:`, error);
        throw error;
      }
    });
  }

  // ------------------------------------------------------------------------
  // Mempool Methods
  // ------------------------------------------------------------------------

  async getMempool(): Promise<MempoolStatus | null> {
    if (!this.features.hasMempool) {
      console.debug('[RPC] Mempool not supported');
      return null;
    }

    const reqId = this.nextRequestId();
    console.debug(`[RPC:${reqId}] getMempool`);

    try {
      const result = await this.baseClient.call<any>('mempool.getStatus', []);
      const parsed = safeParse(mempoolStatusSchema, result, `getMempool req ${reqId}`);

      if (!parsed.success) {
        console.warn(`[RPC:${reqId}] Invalid mempool response:`, result);
        return null;
      }

      console.debug(`[RPC:${reqId}] getMempool result:`, parsed.data);
      return parsed.data;
    } catch (error) {
      console.error(`[RPC:${reqId}] getMempool failed:`, error);
      return null;
    }
  }

  // ------------------------------------------------------------------------
  // Peers Methods
  // ------------------------------------------------------------------------

  async getPeers(): Promise<PeersStatus | null> {
    if (!this.features.hasPeers) {
      console.debug('[RPC] Peers API not supported');
      return null;
    }

    const reqId = this.nextRequestId();
    console.debug(`[RPC:${reqId}] getPeers`);

    try {
      const result = await this.baseClient.call<any>('network.getPeers', []);
      const parsed = safeParse(peersStatusSchema, result, `getPeers req ${reqId}`);

      if (!parsed.success) {
        console.warn(`[RPC:${reqId}] Invalid peers response:`, result);
        return null;
      }

      console.debug(`[RPC:${reqId}] getPeers result:`, parsed.data);
      return parsed.data;
    } catch (error) {
      console.error(`[RPC:${reqId}] getPeers failed:`, error);
      return null;
    }
  }

  // ------------------------------------------------------------------------
  // Subscriptions
  // ------------------------------------------------------------------------

  subscribeNewHeads(
    onHead: (head: ChainHead) => void,
    onError?: (error: Error) => void
  ): { unsubscribe: () => void } {
    const reqId = this.nextRequestId();
    console.debug(`[RPC:${reqId}] subscribeNewHeads`);

    return this.baseClient.subscribeNewHeads!((rawHead: any) => {
      const parsed = safeParse(chainHeadSchema, rawHead, `subscribeNewHeads callback (req ${reqId})`);

      if (parsed.success) {
        onHead(parsed.data);
      } else {
        console.warn(`[RPC:${reqId}] Invalid head in subscription:`, rawHead);
        onError?.(new Error(parsed.error));
      }
    });
  }

  // ------------------------------------------------------------------------
  // Utilities
  // ------------------------------------------------------------------------

  private nextRequestId(): number {
    return ++this.requestIdCounter;
  }

  getUrl(): string {
    return this.url;
  }

  close(): void {
    if (this.baseClient.close) {
      this.baseClient.close();
    }
  }
}

// ============================================================================
// Client Factory & Cache
// ============================================================================

const clientCache = new Map<string, EnhancedRpcClient>();

export function getEnhancedRpcClient(url: string): EnhancedRpcClient {
  const cached = clientCache.get(url);
  if (cached) {
    return cached;
  }

  const client = new EnhancedRpcClient(url);
  clientCache.set(url, client);

  // Auto-detect features on first use
  client.detectFeatures().catch((err) => {
    console.warn('[RPC] Feature detection failed:', err);
  });

  return client;
}

export function releaseEnhancedRpcClient(url: string): void {
  const client = clientCache.get(url);
  if (client) {
    client.close();
    clientCache.delete(url);
  }
}
