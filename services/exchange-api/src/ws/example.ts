/**
 * WebSocket Server Example
 * 
 * Example of how to integrate the WebSocket server into the exchange-api service.
 */

import { createWebSocketServer, type ExchangeWebSocketServer } from './index.js';
import { createLogger } from '../utils/logger.js';
import { loadConfig } from '../config.js';
import { PrismaClient } from '@prisma/client';
import { MarketDataCache } from '../services/market_data_cache.js';
import { createClient } from 'redis';

/**
 * Initialize and start the WebSocket server
 */
export async function startWebSocketServer(): Promise<ExchangeWebSocketServer> {
  // Load configuration
  const config = loadConfig();
  const logger = createLogger(config);

  // Initialize database client
  const prisma = new PrismaClient({
    log:
      config.NODE_ENV === 'development'
        ? ['query', 'error', 'warn']
        : ['error'],
  });

  // Initialize Redis (optional - fallback to DB if not available)
  let redis = null;
  if (config.REDIS_URL) {
    try {
      redis = createClient({ url: config.REDIS_URL });
      await redis.connect();
      logger.info('Redis connected for WebSocket server');
    } catch (error) {
      logger.warn({ error }, 'Redis connection failed, using DB fallback');
    }
  }

  // Initialize market data cache
  const marketDataCache = new MarketDataCache();

  // Create and start WebSocket server
  const wsServer = createWebSocketServer({
    prisma,
    redis,
    config,
    logger,
    marketDataCache,
    snapshotOptions: {
      orderbookDepth: config.ORDERBOOK_MAX_DEPTH || 20,
      recentTradesLimit: config.TRADES_MAX_LIMIT || 50,
    },
  });

  logger.info(
    { port: config.WS_PORT, host: config.WS_HOST },
    'WebSocket server started successfully'
  );

  return wsServer;
}

/**
 * Example: Broadcast orderbook update to subscribers
 */
export function exampleBroadcastOrderbookUpdate(
  wsServer: ExchangeWebSocketServer,
  market: string,
  sequence: number
): void {
  const multiplexer = wsServer.getMultiplexer();

  const update = {
    type: 'update' as const,
    channel: 'book' as const,
    market,
    seq: sequence,
    changes: {
      bids: [
        ['50000.00', '1.5'] as [string, string],
        ['49999.00', '2.0'] as [string, string],
      ],
      asks: [
        ['50001.00', '1.2'] as [string, string],
        ['50002.00', '0'] as [string, string], // quantity 0 = remove
      ],
    },
    ts: Date.now(),
  };

  const channelKey = `book:${market}`;
  const sent = multiplexer.broadcast(channelKey, update);

  console.log(`Broadcasted orderbook update to ${sent} subscribers`);
}

/**
 * Example: Broadcast trade to subscribers
 */
export function exampleBroadcastTrade(
  wsServer: ExchangeWebSocketServer,
  market: string,
  tradeId: string,
  price: string,
  size: string,
  side: 'buy' | 'sell'
): void {
  const multiplexer = wsServer.getMultiplexer();

  const trade = {
    type: 'trade' as const,
    market,
    trade_id: tradeId,
    price,
    size,
    side,
    ts: Date.now(),
  };

  const channelKey = `trades:${market}`;
  const sent = multiplexer.broadcast(channelKey, trade);

  console.log(`Broadcasted trade to ${sent} subscribers`);
}

/**
 * Example: Broadcast ticker update to subscribers
 */
export function exampleBroadcastTicker(
  wsServer: ExchangeWebSocketServer,
  market: string,
  ticker: {
    last: string;
    bid: string;
    ask: string;
    volume: string;
    high?: string;
    low?: string;
  }
): void {
  const multiplexer = wsServer.getMultiplexer();

  const tickerMsg = {
    type: 'ticker' as const,
    market,
    ...ticker,
    ts: Date.now(),
  };

  const channelKey = `tickers:${market}`;
  const sent = multiplexer.broadcast(channelKey, tickerMsg);

  console.log(`Broadcasted ticker update to ${sent} subscribers`);
}

/**
 * Example: Get server statistics
 */
export function exampleGetStats(wsServer: ExchangeWebSocketServer): void {
  const stats = wsServer.getStats();

  console.log('WebSocket Server Statistics:');
  console.log(`  Connections: ${stats.connections} (${stats.authenticated} authenticated)`);
  console.log(`  Channels: ${stats.subscriptions.channels}`);
  console.log(`  Total Subscriptions: ${stats.subscriptions.totalSubscriptions}`);
  console.log(`  Queue Messages: ${stats.queues.totalMessages}`);
  console.log(`  Dropped Messages: ${stats.queues.totalDropped}`);
  console.log(`  Critical Queues: ${stats.queues.criticalQueues}`);
  console.log(`  Alive Connections: ${stats.heartbeat.alive}`);
  console.log(`  Dead Connections: ${stats.heartbeat.dead}`);
  console.log(`  Avg Response Time: ${stats.heartbeat.avgResponseTime.toFixed(2)}ms`);
}

/**
 * Example: Graceful shutdown
 */
export async function exampleGracefulShutdown(
  wsServer: ExchangeWebSocketServer,
  prisma: PrismaClient,
  redis: any
): Promise<void> {
  console.log('Shutting down WebSocket server...');

  // Stop WebSocket server
  await wsServer.stop();

  // Disconnect database
  await prisma.$disconnect();

  // Disconnect Redis
  if (redis) {
    await redis.quit();
  }

  console.log('WebSocket server shut down successfully');
}

/**
 * Main function to run the example
 */
async function main() {
  const wsServer = await startWebSocketServer();

  // Setup graceful shutdown
  process.on('SIGTERM', async () => {
    const config = loadConfig();
    const prisma = new PrismaClient();
    await exampleGracefulShutdown(wsServer, prisma, null);
    process.exit(0);
  });

  process.on('SIGINT', async () => {
    const config = loadConfig();
    const prisma = new PrismaClient();
    await exampleGracefulShutdown(wsServer, prisma, null);
    process.exit(0);
  });

  // Example: Log stats every 30 seconds
  setInterval(() => {
    exampleGetStats(wsServer);
  }, 30000);

  console.log('WebSocket server is running. Press Ctrl+C to stop.');
}

// Run if executed directly
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    console.error('Failed to start WebSocket server:', error);
    process.exit(1);
  });
}
