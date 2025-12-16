/**
 * Unit tests for IndexedDB cache layer
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { ExplorerCache, isCacheAvailable } from '../../src/services/cache';

// Mock IndexedDB for testing
import 'fake-indexeddb/auto';

describe('ExplorerCache', () => {
  let cache: ExplorerCache;

  beforeEach(async () => {
    // Create a new cache instance for each test
    cache = new (ExplorerCache as any)();
    await cache.init();
    // Clear any existing data from previous tests
    await cache.clearAll();
  });

  afterEach(() => {
    cache.close();
  });

  describe('initialization', () => {
    it('should initialize successfully', async () => {
      expect(cache.isReady()).toBe(true);
    });

    it('should detect cache availability', () => {
      expect(isCacheAvailable()).toBe(true);
    });
  });

  describe('block operations', () => {
    it('should store and retrieve a block', async () => {
      const blockData = {
        height: 100,
        hash: '0xabc123',
        timeISO: '2024-01-01T00:00:00Z',
        txCount: 5,
      };

      await cache.putBlock(100, '0xabc123', blockData);
      const retrieved = await cache.getBlock(100);

      expect(retrieved).toEqual(blockData);
    });

    it('should retrieve a block by hash', async () => {
      const blockData = {
        height: 101,
        hash: '0xdef456',
        timeISO: '2024-01-01T00:00:00Z',
        txCount: 3,
      };

      await cache.putBlock(101, '0xdef456', blockData);
      const retrieved = await cache.getBlockByHash('0xdef456');

      expect(retrieved).toEqual(blockData);
    });

    it('should return null for non-existent block', async () => {
      const retrieved = await cache.getBlock(999);
      expect(retrieved).toBeNull();
    });

    it('should store multiple blocks', async () => {
      const blocks = [
        { height: 200, hash: '0xblock200', data: { height: 200, hash: '0xblock200', txCount: 1 } },
        { height: 201, hash: '0xblock201', data: { height: 201, hash: '0xblock201', txCount: 2 } },
        { height: 202, hash: '0xblock202', data: { height: 202, hash: '0xblock202', txCount: 3 } },
      ];

      await cache.putBlocks(blocks);

      const block200 = await cache.getBlock(200);
      const block201 = await cache.getBlock(201);
      const block202 = await cache.getBlock(202);

      expect(block200).toEqual(blocks[0].data);
      expect(block201).toEqual(blocks[1].data);
      expect(block202).toEqual(blocks[2].data);
    });

    it('should retrieve blocks in a range', async () => {
      const blocks = [
        { height: 300, hash: '0xblock300', data: { height: 300, hash: '0xblock300' } },
        { height: 301, hash: '0xblock301', data: { height: 301, hash: '0xblock301' } },
        { height: 302, hash: '0xblock302', data: { height: 302, hash: '0xblock302' } },
        { height: 303, hash: '0xblock303', data: { height: 303, hash: '0xblock303' } },
      ];

      await cache.putBlocks(blocks);

      const range = await cache.getBlocksRange(303, 301);
      expect(range).toHaveLength(3);
      expect(range[0].height).toBe(303);
      expect(range[1].height).toBe(302);
      expect(range[2].height).toBe(301);
    });
  });

  describe('transaction operations', () => {
    it('should store and retrieve a transaction', async () => {
      const txData = {
        hash: '0xtx123',
        from: '0xaaa',
        to: '0xbbb',
        value: '1000',
      };

      await cache.putTx('0xtx123', txData, 100);
      const retrieved = await cache.getTx('0xtx123');

      expect(retrieved).toEqual(txData);
    });

    it('should return null for non-existent transaction', async () => {
      const retrieved = await cache.getTx('0xnonexistent');
      expect(retrieved).toBeNull();
    });
  });

  describe('address operations', () => {
    it('should store and retrieve address data', async () => {
      const addrData = {
        address: '0xaddr123',
        balance: '5000',
        nonce: 10,
      };

      await cache.putAddress('0xaddr123', addrData);
      const retrieved = await cache.getAddress('0xaddr123');

      expect(retrieved).toEqual(addrData);
    });

    it('should return null for non-existent address', async () => {
      const retrieved = await cache.getAddress('0xnonexistent');
      expect(retrieved).toBeNull();
    });
  });

  describe('metadata operations', () => {
    it('should store and retrieve last sync height', async () => {
      await cache.setLastSyncHeight(12345);
      const height = await cache.getLastSyncHeight();

      expect(height).toBe(12345);
    });

    it('should return null for unset last sync height', async () => {
      const height = await cache.getLastSyncHeight();
      expect(height).toBeNull();
    });

    it('should store and retrieve last sync time', async () => {
      const now = Date.now();
      await cache.setLastSyncTime(now);
      const time = await cache.getLastSyncTime();

      expect(time).toBe(now);
    });

    it('should store and retrieve custom meta', async () => {
      await cache.setMeta('custom-key', { foo: 'bar', count: 42 });
      const value = await cache.getMeta('custom-key');

      expect(value).toEqual({ foo: 'bar', count: 42 });
    });
  });

  describe('statistics', () => {
    it('should return cache stats', async () => {
      // Add some test data
      await cache.putBlock(1, '0xblock1', { height: 1 });
      await cache.putBlock(2, '0xblock2', { height: 2 });
      await cache.putTx('0xtx1', { hash: '0xtx1' });
      await cache.putAddress('0xaddr1', { address: '0xaddr1' });
      await cache.setLastSyncHeight(2);
      await cache.setLastSyncTime(Date.now());

      const stats = await cache.getStats();

      expect(stats.blocksCount).toBe(2);
      expect(stats.txsCount).toBe(1);
      expect(stats.addressesCount).toBe(1);
      expect(stats.lastSyncHeight).toBe(2);
      expect(stats.lastSyncTime).toBeGreaterThan(0);
      expect(stats.estimatedSize).toBeGreaterThanOrEqual(0);
    });
  });

  describe('cache management', () => {
    it('should clear all data', async () => {
      // Add some data
      await cache.putBlock(1, '0xblock1', { height: 1 });
      await cache.putTx('0xtx1', { hash: '0xtx1' });
      await cache.putAddress('0xaddr1', { address: '0xaddr1' });
      await cache.setLastSyncHeight(1);

      // Clear
      await cache.clearAll();

      // Verify cleared
      const stats = await cache.getStats();
      expect(stats.blocksCount).toBe(0);
      expect(stats.txsCount).toBe(0);
      expect(stats.addressesCount).toBe(0);
      expect(stats.lastSyncHeight).toBeNull();
    });
  });
});
