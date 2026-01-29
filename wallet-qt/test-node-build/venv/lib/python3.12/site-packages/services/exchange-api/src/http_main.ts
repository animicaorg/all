/**
 * Main Entry Point for HTTP Server
 * Start the exchange API HTTP server
 */

import { loadConfig } from './config.js';
import { createLogger } from './utils/logger.js';
import { createRedisClient } from './utils/redis.js';
import { prisma } from './db/client.js';
import { startServer } from './http/server.js';

async function main() {
  // Load configuration
  const config = loadConfig();
  const logger = createLogger(config);

  logger.info('Starting exchange-api HTTP server...');

  try {
    // Connect to Redis
    const redis = await createRedisClient(config, logger);

    // Start HTTP server
    await startServer({
      prisma,
      redis,
      config,
      logger,
    });
  } catch (error) {
    logger.fatal({ error }, 'Failed to start server');
    process.exit(1);
  }
}

// Run if executed directly
if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}

export { main };
