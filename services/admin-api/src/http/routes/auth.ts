/**
 * Authentication Routes
 * Handles login, logout, refresh, and session management
 */

import { Router } from 'express';
import { z } from 'zod';
import type { PrismaClient } from '@prisma/client';
import type { Config } from '../../config.js';
import type { Logger } from '../../utils/logger.js';
import { AuthService } from '../../services/auth.js';
import { AdminBootstrapService } from '../../services/admin_bootstrap.js';
import { validateBody } from '../middleware/validation.js';
import { createAuthMiddleware } from '../middleware/auth.js';
import { createLoginRateLimiter } from '../middleware/rate_limit.js';

const loginSchema = z.object({
  email: z.string().email(),
  password: z.string().min(8),
  totpToken: z.string().length(6).optional(),
  bootstrapSecret: z.string().min(1).optional(),
});

const refreshSchema = z.object({
  refreshToken: z.string(),
  sessionId: z.string().uuid(),
});

export function createAuthRouter(
  prisma: PrismaClient,
  config: Config,
  logger: Logger
): Router {
  const router = Router();
  const authService = new AuthService(prisma, config, logger);
  const bootstrapService = new AdminBootstrapService(prisma, config, logger);
  const authMiddleware = createAuthMiddleware(prisma, config, logger);
  const loginRateLimiter = createLoginRateLimiter(config);

  /**
   * POST /admin/v1/auth/login
   * Login with email, password, and TOTP
   */
  router.post(
    '/login',
    loginRateLimiter,
    validateBody(loginSchema),
    async (req, res, next) => {
      try {
        const { bootstrapSecret, ...loginPayload } = req.body;

        const bootstrapResult = await bootstrapService.bootstrapIfNeeded(
          { email: loginPayload.email, password: loginPayload.password },
          bootstrapSecret,
          req.ip || req.socket.remoteAddress
        );

        const result = await authService.login(
          loginPayload,
          req.ip || req.socket.remoteAddress,
          req.headers['user-agent']
        );

        // Set HTTP-only cookie for web clients
        res.cookie('admin_token', result.accessToken, {
          httpOnly: true,
          secure: config.NODE_ENV === 'production',
          sameSite: 'strict',
          maxAge: 3600000, // 1 hour
        });

        res.cookie('admin_refresh_token', result.refreshToken, {
          httpOnly: true,
          secure: config.NODE_ENV === 'production',
          sameSite: 'strict',
          maxAge: 7 * 24 * 3600000, // 7 days
        });

        res.cookie('admin_session_id', result.sessionId, {
          httpOnly: true,
          secure: config.NODE_ENV === 'production',
          sameSite: 'strict',
          maxAge: 7 * 24 * 3600000, // 7 days
        });

        // Log audit event
        await req.auditLog?.({
          action: 'LOGIN',
          entityType: 'ADMIN',
          entityId: result.admin.id,
        });

        res.json({
          success: true,
          data: {
            admin: result.admin,
            accessToken: result.accessToken,
            refreshToken: result.refreshToken,
            sessionId: result.sessionId,
            bootstrapCreated: bootstrapResult.created,
          },
        });
      } catch (error) {
        logger.error({ error, requestId: req.id }, 'Login error');
        res.status(401).json({
          error: 'AuthenticationFailed',
          message: error instanceof Error ? error.message : 'Authentication failed',
          requestId: req.id,
        });
      }
    }
  );

  /**
   * POST /admin/v1/auth/logout
   * Logout and revoke current session
   */
  router.post('/logout', authMiddleware, async (req, res, next) => {
    try {
      if (req.session) {
        await authService.logout(req.session.id);

        // Log audit event
        await req.auditLog?.({
          action: 'LOGOUT',
          entityType: 'ADMIN',
          entityId: req.admin?.id,
        });
      }

      // Clear cookies
      res.clearCookie('admin_token');
      res.clearCookie('admin_refresh_token');
      res.clearCookie('admin_session_id');

      res.json({ success: true });
    } catch (error) {
      next(error);
    }
  });

  /**
   * POST /admin/v1/auth/refresh
   * Refresh access token using refresh token
   */
  router.post('/refresh', validateBody(refreshSchema), async (req, res, next) => {
    try {
      const { sessionId, refreshToken } = req.body;
      const result = await authService.refresh(sessionId, refreshToken);

      // Update access token cookie
      res.cookie('admin_token', result.accessToken, {
        httpOnly: true,
        secure: config.NODE_ENV === 'production',
        sameSite: 'strict',
        maxAge: 3600000, // 1 hour
      });

      res.json({
        success: true,
        data: {
          accessToken: result.accessToken,
        },
      });
    } catch (error) {
      logger.error({ error, requestId: req.id }, 'Token refresh error');
      res.status(401).json({
        error: 'RefreshFailed',
        message: error instanceof Error ? error.message : 'Token refresh failed',
        requestId: req.id,
      });
    }
  });

  /**
   * GET /admin/v1/auth/me
   * Get current admin info
   */
  router.get('/me', authMiddleware, async (req, res, next) => {
    try {
      if (!req.admin) {
        res.status(401).json({ error: 'Unauthorized', requestId: req.id });
        return;
      }

      const { passwordHash, totpSecretEncrypted, ...safeAdmin } = req.admin;

      res.json({
        success: true,
        data: {
          admin: safeAdmin,
          session: req.session,
        },
      });
    } catch (error) {
      next(error);
    }
  });

  return router;
}
