/**
 * Main Entry Point
 * Starts the admin API server
 */

import 'dotenv/config';
import { loadConfig } from './config.js';
import { createLogger } from './utils/logger.js';
import { createPrismaClient, disconnectPrisma } from './db/prisma.js';
import { createApp } from './http/server.js';

async function main() {
  const config = loadConfig();
  const logger = createLogger(config);
  
  logger.info({ config: { ...config, JWT_SECRET: '***', CSRF_SECRET: '***', SESSION_SECRET: '***' } }, 'Starting admin API service');

  // Initialize database
  const prisma = createPrismaClient(logger);
  
  try {
    await prisma.$connect();
    logger.info('Connected to database');
  } catch (error) {
    logger.error({ error }, 'Failed to connect to database');
    process.exit(1);
  }

  // Create Express app
  const app = createApp({ prisma, config, logger });

  // Start HTTP server
  const server = app.listen(config.HTTP_PORT, config.HTTP_HOST, () => {
    logger.info(
      { host: config.HTTP_HOST, port: config.HTTP_PORT },
      'Admin API server listening'
    );
  });

  // Graceful shutdown
  const shutdown = async (signal: string) => {
    logger.info({ signal }, 'Received shutdown signal');

    server.close(() => {
      logger.info('HTTP server closed');
    });

    await disconnectPrisma();
    logger.info('Database disconnected');

    process.exit(0);
  };

  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));
}

main().catch((error) => {
  console.error('Fatal error:', error);
  process.exit(1);
});
