/**
 * HTTP Server
 * Express server with all middleware and routes
 */

import express, { type Express } from 'express';
import helmet from 'helmet';
import cors from 'cors';
import cookieParser from 'cookie-parser';
import type { PrismaClient } from '@prisma/client';
import type { Config } from '../config.js';
import type { Logger } from '../utils/logger.js';
import {
  requestIdMiddleware,
  createAuthMiddleware,
  createOptionalAuthMiddleware,
  createAuditMiddleware,
  createAdminRateLimiter,
  createErrorHandler,
  notFoundHandler,
} from './middleware/index.js';
import { createAuthRouter } from './routes/auth.js';
import { createUsersRouter } from './routes/users.js';
import { createHealthRouter } from './routes/health.js';
import { createSettingsRouter } from './routes/settings.js';

export interface ServerDependencies {
  prisma: PrismaClient;
  config: Config;
  logger: Logger;
}

/**
 * Create and configure Express application
 */
export function createApp(deps: ServerDependencies): Express {
  const { prisma, config, logger } = deps;
  const app = express();

  // Basic middleware
  app.use(helmet({
    contentSecurityPolicy: {
      directives: {
        defaultSrc: ["'self'"],
        styleSrc: ["'self'", "'unsafe-inline'"],
      },
    },
  }));

  app.use(cors({
    origin: config.ADMIN_WEB_URL,
    credentials: config.CORS_CREDENTIALS,
  }));

  app.use(express.json({ limit: '10mb' }));
  app.use(express.urlencoded({ extended: true, limit: '10mb' }));
  app.use(cookieParser(config.SESSION_SECRET));

  // Custom middleware
  app.use(requestIdMiddleware);
  
  // Create middleware instances
  const authMiddleware = createAuthMiddleware(prisma, config, logger);
  const optionalAuthMiddleware = createOptionalAuthMiddleware(prisma, config, logger);
  const auditMiddleware = createAuditMiddleware(prisma, logger);
  const rateLimiter = createAdminRateLimiter(config);

  // Apply audit middleware to all routes
  app.use(auditMiddleware);

  // Health check (no auth required)
  app.get('/health', (req, res) => {
    res.json({ status: 'ok', service: config.SERVICE_NAME });
  });

  app.get('/admin/v1/health', (req, res) => {
    res.json({ status: 'ok', service: config.SERVICE_NAME });
  });

  // Mount routers
  app.use('/admin/v1/auth', createAuthRouter(prisma, config, logger));
  app.use('/admin/v1/health', createHealthRouter(prisma, config, logger));

  // Protected routes require authentication and rate limiting
  app.use('/admin/v1', authMiddleware, rateLimiter);
  
  // Protected route groups
  app.use('/admin/v1/users', createUsersRouter(prisma, config, logger));
  app.use('/admin/v1/settings', createSettingsRouter(prisma, config, logger));

  // TODO: Add more route groups here
  // app.use('/admin/v1/admins', createAdminsRouter(...));
  // app.use('/admin/v1/users', createUsersRouter(...));
  // app.use('/admin/v1/kyc', createKycRouter(...));
  // app.use('/admin/v1/markets', createMarketsRouter(...));
  // app.use('/admin/v1/fees', createFeesRouter(...));
  // app.use('/admin/v1/wallets', createWalletsRouter(...));
  // app.use('/admin/v1/withdrawals', createWithdrawalsRouter(...));
  // app.use('/admin/v1/incidents', createIncidentsRouter(...));
  // app.use('/admin/v1/audit', createAuditRouter(...));

  // Error handlers (must be last)
  app.use(notFoundHandler);
  app.use(createErrorHandler(logger));

  return app;
}
