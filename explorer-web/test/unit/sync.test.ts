/**
 * Unit tests for sync manager
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { SyncManager } from '../../src/services/sync';
import type { RpcClient } from '../../src/state/blocks';
import 'fake-indexeddb/auto';

describe('SyncManager', () => {
  let syncManager: SyncManager;
  let mockRpcClient: RpcClient;

  beforeEach(() => {
    // Create mock RPC client
    mockRpcClient = {
      getBlock: vi.fn(async (height: number) => ({
        height,
        hash: `0xblock${height}`,
        parentHash: `0xblock${height - 1}`,
        timeISO: new Date().toISOString(),
        txCount: Math.floor(Math.random() * 10),
      })),
      close: vi.fn(),
    };

    syncManager = new SyncManager({
      batchSize: 5,
      delayMs: 100,
      maxRetries: 2,
      catchupThreshold: 10,
    });

    syncManager.setRpcClient(mockRpcClient);
  });

  afterEach(() => {
    syncManager.stop();
  });

  describe('initialization', () => {
    it('should create a sync manager with default options', () => {
      const sm = new SyncManager();
      expect(sm).toBeDefined();
      expect(sm.getStatus().isRunning).toBe(false);
    });

    it('should create a sync manager with custom options', () => {
      const sm = new SyncManager({ batchSize: 10 });
      expect(sm).toBeDefined();
      expect(sm.getStatus().isRunning).toBe(false);
    });
  });

  describe('status', () => {
    it('should return initial status', () => {
      const status = syncManager.getStatus();
      expect(status.isRunning).toBe(false);
      expect(status.isSynced).toBe(false);
      expect(status.lastSyncHeight).toBeNull();
      expect(status.currentHeight).toBeNull();
      expect(status.progress).toBe(0);
      expect(status.blocksToSync).toBe(0);
      expect(status.error).toBeNull();
    });

    it('should update status when sync starts', async () => {
      const promise = syncManager.start();
      
      // Give it a moment to initialize
      await new Promise(resolve => setTimeout(resolve, 50));
      
      const status = syncManager.getStatus();
      expect(status.isRunning).toBe(true);
      
      syncManager.stop();
      await promise.catch(() => {}); // Ignore errors from stopping
    });
  });

  describe('pause and resume', () => {
    it('should pause synchronization', async () => {
      await syncManager.start();
      
      syncManager.pause();
      
      const status = syncManager.getStatus();
      expect(status.isRunning).toBe(true); // Still running but paused
    });

    it('should resume synchronization', async () => {
      await syncManager.start();
      
      syncManager.pause();
      syncManager.resume();
      
      const status = syncManager.getStatus();
      expect(status.isRunning).toBe(true);
    });
  });

  describe('status listeners', () => {
    it('should notify listeners of status changes', async () => {
      const listener = vi.fn();
      const unsubscribe = syncManager.onStatusChange(listener);
      
      await syncManager.start();
      
      // Give it time to make some progress
      await new Promise(resolve => setTimeout(resolve, 50));
      
      expect(listener).toHaveBeenCalled();
      
      unsubscribe();
      syncManager.stop();
    });

    it('should stop notifying after unsubscribe', async () => {
      const listener = vi.fn();
      const unsubscribe = syncManager.onStatusChange(listener);
      
      unsubscribe();
      
      await syncManager.start();
      await new Promise(resolve => setTimeout(resolve, 50));
      
      expect(listener).not.toHaveBeenCalled();
      
      syncManager.stop();
    });
  });

  describe('error handling', () => {
    it('should throw error if RPC client not set', async () => {
      const sm = new SyncManager();
      
      await expect(sm.start()).rejects.toThrow('RPC client not set');
    });

    it('should handle RPC errors gracefully', async () => {
      // Mock RPC client that throws errors
      const errorRpcClient: RpcClient = {
        getBlock: vi.fn(async () => {
          throw new Error('RPC error');
        }),
        close: vi.fn(),
      };
      
      const sm = new SyncManager({ batchSize: 2, delayMs: 50 });
      sm.setRpcClient(errorRpcClient);
      
      await sm.start();
      
      // Give it time to fail
      await new Promise(resolve => setTimeout(resolve, 200));
      
      const status = sm.getStatus();
      // Should handle errors and continue running
      expect(status.isRunning).toBe(true);
      
      sm.stop();
    });
  });

  describe('manual trigger', () => {
    it('should allow manual sync trigger', async () => {
      await syncManager.start();
      
      // Trigger manual sync
      await syncManager.triggerSync();
      
      // Verify RPC was called
      expect(mockRpcClient.getBlock).toHaveBeenCalled();
      
      syncManager.stop();
    });
  });
});
