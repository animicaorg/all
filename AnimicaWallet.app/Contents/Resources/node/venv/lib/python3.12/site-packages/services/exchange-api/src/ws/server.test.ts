/**
 * WebSocket Server Tests
 * 
 * Integration tests for the WebSocket server.
 * Run with: npm test ws/server.test.ts
 */

import { describe, it, expect, beforeAll, afterAll, vi } from 'vitest';
import WebSocket from 'ws';
import { createWebSocketServer, type ExchangeWebSocketServer } from './index.js';
import { createLogger } from '../utils/logger.js';
import { loadConfig } from '../config.js';
import { PrismaClient } from '@prisma/client';
import { MarketDataCache } from '../services/market_data_cache.js';
import crypto from 'node:crypto';

describe('WebSocket Server', () => {
  let wsServer: ExchangeWebSocketServer;
  let config: ReturnType<typeof loadConfig>;
  let prisma: PrismaClient;
  let marketDataCache: MarketDataCache;
  let logger: ReturnType<typeof createLogger>;
  let wsUrl: string;

  beforeAll(async () => {
    config = loadConfig();
    logger = createLogger(config);
    prisma = new PrismaClient();
    marketDataCache = new MarketDataCache();

    // Use different port for tests
    config.WS_PORT = 13001;

    wsServer = createWebSocketServer({
      prisma,
      redis: null,
      config,
      logger,
      marketDataCache,
    });

    wsUrl = `ws://localhost:${config.WS_PORT}`;

    // Wait for server to be ready
    await new Promise((resolve) => setTimeout(resolve, 100));
  });

  afterAll(async () => {
    await wsServer.stop();
    await prisma.$disconnect();
  });

  it('should accept WebSocket connections', (done) => {
    const ws = new WebSocket(wsUrl);

    ws.on('open', () => {
      expect(ws.readyState).toBe(WebSocket.OPEN);
      ws.close();
      done();
    });

    ws.on('error', done);
  });

  it('should handle ping/pong', (done) => {
    const ws = new WebSocket(wsUrl);

    ws.on('open', () => {
      ws.send(JSON.stringify({ op: 'ping', ts: Date.now() }));
    });

    ws.on('message', (data) => {
      const msg = JSON.parse(data.toString());
      if (msg.op === 'pong') {
        expect(msg.ts).toBeDefined();
        ws.close();
        done();
      }
    });

    ws.on('error', done);
  });

  it('should reject invalid JSON messages', (done) => {
    const ws = new WebSocket(wsUrl);

    ws.on('open', () => {
      ws.send('invalid json{');
    });

    ws.on('message', (data) => {
      const msg = JSON.parse(data.toString());
      if (msg.type === 'error') {
        expect(msg.code).toBe('INVALID_MESSAGE');
        ws.close();
        done();
      }
    });

    ws.on('error', done);
  });

  it('should handle subscribe without auth for public channels', (done) => {
    const ws = new WebSocket(wsUrl);

    ws.on('open', () => {
      ws.send(
        JSON.stringify({
          op: 'subscribe',
          id: 1,
          channels: [{ name: 'book', market: 'BTC_USD' }],
        })
      );
    });

    ws.on('message', (data) => {
      const msg = JSON.parse(data.toString());
      if (msg.op === 'subscribed') {
        expect(msg.id).toBe(1);
        expect(msg.channels).toHaveLength(1);
        expect(msg.channels[0].name).toBe('book');
        ws.close();
        done();
      }
    });

    ws.on('error', done);
  });

  it('should reject private channels without auth', (done) => {
    const ws = new WebSocket(wsUrl);

    ws.on('open', () => {
      ws.send(
        JSON.stringify({
          op: 'subscribe',
          id: 2,
          channels: [{ name: 'orders' }],
        })
      );
    });

    ws.on('message', (data) => {
      const msg = JSON.parse(data.toString());
      if (msg.type === 'error') {
        expect(msg.code).toBe('INVALID_CHANNEL');
        expect(msg.message).toContain('Authentication required');
        ws.close();
        done();
      }
    });

    ws.on('error', done);
  });

  it('should handle unsubscribe', (done) => {
    const ws = new WebSocket(wsUrl);
    let subscribed = false;

    ws.on('open', () => {
      ws.send(
        JSON.stringify({
          op: 'subscribe',
          id: 3,
          channels: [{ name: 'book', market: 'BTC_USD' }],
        })
      );
    });

    ws.on('message', (data) => {
      const msg = JSON.parse(data.toString());

      if (msg.op === 'subscribed' && !subscribed) {
        subscribed = true;
        ws.send(
          JSON.stringify({
            op: 'unsubscribe',
            id: 4,
            channels: [{ name: 'book', market: 'BTC_USD' }],
          })
        );
      } else if (msg.op === 'unsubscribed') {
        expect(msg.id).toBe(4);
        ws.close();
        done();
      }
    });

    ws.on('error', done);
  });

  it('should enforce max subscriptions limit', (done) => {
    const ws = new WebSocket(wsUrl);
    const maxSubs = config.WS_MAX_SUBSCRIPTIONS_PER_CLIENT;

    // Create channels exceeding the limit
    const channels = Array.from({ length: maxSubs + 5 }, (_, i) => ({
      name: 'tickers',
      market: `PAIR_${i}`,
    }));

    ws.on('open', () => {
      ws.send(
        JSON.stringify({
          op: 'subscribe',
          id: 5,
          channels,
        })
      );
    });

    ws.on('message', (data) => {
      const msg = JSON.parse(data.toString());

      if (msg.op === 'subscribed') {
        // Should succeed for some channels
        expect(msg.channels.length).toBeLessThanOrEqual(maxSubs);
        ws.close();
        done();
      } else if (msg.type === 'error' && msg.code === 'INVALID_CHANNEL') {
        // Some channels should fail
        expect(msg.message).toContain('Max subscriptions');
      }
    });

    ws.on('error', done);
  });

  it('should broadcast messages to subscribers', (done) => {
    const ws1 = new WebSocket(wsUrl);
    const ws2 = new WebSocket(wsUrl);
    let ws1Ready = false;
    let ws2Ready = false;
    let receivedCount = 0;

    const checkReady = () => {
      if (ws1Ready && ws2Ready) {
        // Both subscribed, now broadcast a message
        const multiplexer = wsServer.getMultiplexer();
        const update = {
          type: 'update' as const,
          channel: 'book' as const,
          market: 'TEST_USD',
          seq: 1,
          changes: { bids: [['100.00', '1.0'] as [string, string]] },
          ts: Date.now(),
        };

        setTimeout(() => {
          multiplexer.broadcast('book:TEST_USD', update);
        }, 100);
      }
    };

    ws1.on('open', () => {
      ws1.send(
        JSON.stringify({
          op: 'subscribe',
          id: 6,
          channels: [{ name: 'book', market: 'TEST_USD' }],
        })
      );
    });

    ws1.on('message', (data) => {
      const msg = JSON.parse(data.toString());
      if (msg.op === 'subscribed') {
        ws1Ready = true;
        checkReady();
      } else if (msg.type === 'update' && msg.market === 'TEST_USD') {
        receivedCount++;
        if (receivedCount === 2) {
          ws1.close();
          ws2.close();
          done();
        }
      }
    });

    ws2.on('open', () => {
      ws2.send(
        JSON.stringify({
          op: 'subscribe',
          id: 7,
          channels: [{ name: 'book', market: 'TEST_USD' }],
        })
      );
    });

    ws2.on('message', (data) => {
      const msg = JSON.parse(data.toString());
      if (msg.op === 'subscribed') {
        ws2Ready = true;
        checkReady();
      } else if (msg.type === 'update' && msg.market === 'TEST_USD') {
        receivedCount++;
        if (receivedCount === 2) {
          ws1.close();
          ws2.close();
          done();
        }
      }
    });

    ws1.on('error', done);
    ws2.on('error', done);
  }, 10000);

  it('should provide server statistics', () => {
    const stats = wsServer.getStats();
    expect(stats).toHaveProperty('connections');
    expect(stats).toHaveProperty('authenticated');
    expect(stats).toHaveProperty('subscriptions');
    expect(stats).toHaveProperty('queues');
    expect(stats).toHaveProperty('heartbeat');
  });

  it('should close connections gracefully', (done) => {
    const ws = new WebSocket(wsUrl);

    ws.on('open', () => {
      ws.close(1000, 'Normal closure');
    });

    ws.on('close', (code, reason) => {
      expect(code).toBe(1000);
      done();
    });

    ws.on('error', done);
  });
});

describe('Protocol Validation', () => {
  it('should validate channel configurations', async () => {
    const { validateChannel } = await import('./subscriptions.js');

    // Valid public channel
    expect(
      validateChannel({ name: 'book', market: 'BTC_USD' }, false).valid
    ).toBe(true);

    // Invalid channel name
    expect(validateChannel({ name: 'invalid' }, false).valid).toBe(false);

    // Missing market parameter
    expect(validateChannel({ name: 'book' }, false).valid).toBe(false);

    // Private channel without auth
    expect(validateChannel({ name: 'orders' }, false).valid).toBe(false);

    // Private channel with auth
    expect(validateChannel({ name: 'orders' }, true).valid).toBe(true);
  });

  it('should build and parse channel keys', async () => {
    const { buildChannelKey, parseChannelKey } = await import('./protocol.js');

    const config = { name: 'book', market: 'BTC_USD' };
    const key = buildChannelKey(config);
    expect(key).toBe('book:BTC_USD');

    const parsed = parseChannelKey(key);
    expect(parsed.name).toBe('book');
    expect(parsed.market).toBe('BTC_USD');
  });

  it('should determine message priority', async () => {
    const { getMessagePriority, MessagePriority } = await import('./protocol.js');

    const snapshot = {
      type: 'snapshot' as const,
      channel: 'book' as const,
      market: 'BTC_USD',
      seq: 1,
      bids: [],
      asks: [],
      ts: Date.now(),
    };
    expect(getMessagePriority(snapshot)).toBe(MessagePriority.CRITICAL);

    const trade = {
      type: 'trade' as const,
      market: 'BTC_USD',
      trade_id: '1',
      price: '100',
      size: '1',
      side: 'buy' as const,
      ts: Date.now(),
    };
    expect(getMessagePriority(trade)).toBe(MessagePriority.NORMAL);

    const ticker = {
      type: 'ticker' as const,
      market: 'BTC_USD',
      last: '100',
      bid: '99',
      ask: '101',
      volume: '1000',
      ts: Date.now(),
    };
    expect(getMessagePriority(ticker)).toBe(MessagePriority.LOW);
  });
});

describe('Backpressure Management', () => {
  it('should handle queue overflow', async () => {
    const { MessageQueue } = await import('./backpressure.js');
    const { MessagePriority } = await import('./protocol.js');
    const logger = createLogger(loadConfig());

    const queue = new MessageQueue('test-conn', 10, logger);

    // Fill queue with low priority messages
    for (let i = 0; i < 10; i++) {
      const msg = {
        type: 'ticker' as const,
        market: 'BTC_USD',
        last: '100',
        bid: '99',
        ask: '101',
        volume: '1000',
        ts: Date.now(),
      };
      expect(queue.enqueue(msg)).toBe(true);
    }

    expect(queue.size()).toBe(10);
    expect(queue.isFull()).toBe(true);

    // Try to add critical message - should drop a low priority message
    const critical = {
      type: 'snapshot' as const,
      channel: 'book' as const,
      market: 'BTC_USD',
      seq: 1,
      bids: [],
      asks: [],
      ts: Date.now(),
    };
    expect(queue.enqueue(critical)).toBe(true);

    // Queue should still be full but contain the critical message
    expect(queue.size()).toBe(10);
  });

  it('should dequeue messages in priority order', async () => {
    const { MessageQueue } = await import('./backpressure.js');
    const logger = createLogger(loadConfig());

    const queue = new MessageQueue('test-conn', 100, logger);

    // Add messages with different priorities
    queue.enqueue({
      type: 'ticker' as const,
      market: 'BTC_USD',
      last: '100',
      bid: '99',
      ask: '101',
      volume: '1000',
      ts: Date.now(),
    });

    queue.enqueue({
      type: 'snapshot' as const,
      channel: 'book' as const,
      market: 'BTC_USD',
      seq: 1,
      bids: [],
      asks: [],
      ts: Date.now(),
    });

    // Dequeue - should get critical (snapshot) first
    const first = queue.dequeue();
    expect(first?.message.type).toBe('snapshot');

    const second = queue.dequeue();
    expect(second?.message.type).toBe('ticker');
  });
});
