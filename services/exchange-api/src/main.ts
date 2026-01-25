/**
 * Main Entry Point
 * Starts both HTTP and WebSocket servers along with background jobs
 */

import { loadConfig } from './config.js';
import { createLogger } from './utils/logger.js';
import { createRedisClient } from './utils/redis.js';
import { prisma } from './db/client.js';
import { startServer as startHttpServer } from './http/server.js';
import { startWebSocketServer } from './ws/server.js';
import { MarketDataCache } from './services/market_data_cache.js';
import { startCandlesAggregator } from './jobs/candles_aggregator.js';
import { startSnapshotsPublisher } from './jobs/snapshots_publisher.js';

async function main() {
  // Load configuration
  const config = loadConfig();
  const logger = createLogger(config);

  logger.info(
    {
      service: config.SERVICE_NAME,
      env: config.NODE_ENV,
      httpPort: config.HTTP_PORT,
      wsPort: config.WS_PORT,
    },
    'Starting exchange-api services'
  );

  try {
    // Connect to Redis
    const redis = await createRedisClient(config, logger);

    // Initialize market data cache
    const marketDataCache = new MarketDataCache(config, logger);

    // Start HTTP server
    logger.info('Starting HTTP server...');
    await startHttpServer({
      prisma,
      redis,
      config,
      logger,
    });

    // Start WebSocket server
    logger.info('Starting WebSocket server...');
    await startWebSocketServer({
      prisma,
      redis,
      marketDataCache,
      config,
      logger,
    });

    // Start background jobs
    logger.info('Starting background jobs...');
    
    // Start candles aggregator
    const candlesAggregator = await startCandlesAggregator(
      prisma,
      config,
      logger
    );
    logger.info('Candles aggregator started');

    // Start snapshots publisher
    const snapshotsPublisher = await startSnapshotsPublisher(
      marketDataCache,
      config,
      logger
    );
    logger.info('Snapshots publisher started');

    logger.info('🚀 All services started successfully');

    // Graceful shutdown handler
    const shutdown = async (signal: string) => {
      logger.info({ signal }, 'Received shutdown signal');

      try {
        // Stop background jobs
        logger.info('Stopping background jobs...');
        await candlesAggregator.stop();
        snapshotsPublisher.stop();

        // Disconnect from services
        if (redis) {
          await redis.quit();
          logger.info('Redis disconnected');
        }

        await prisma.$disconnect();
        logger.info('Prisma disconnected');

        logger.info('Graceful shutdown complete');
        process.exit(0);
      } catch (error) {
        logger.error({ error }, 'Error during shutdown');
        process.exit(1);
      }
    };

    // Register shutdown handlers
    process.on('SIGTERM', () => shutdown('SIGTERM'));
    process.on('SIGINT', () => shutdown('SIGINT'));

  } catch (error) {
    logger.fatal({ error }, 'Failed to start services');
    process.exit(1);
  }
}

// Run if executed directly
if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}

export { main };
