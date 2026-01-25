/**
 * Authentication Middleware
 * Verifies JWT tokens and loads admin session
 */

import jwt from 'jsonwebtoken';
import type { Request, Response, NextFunction } from 'express';
import type { PrismaClient, Admin, AdminRole } from '@prisma/client';
import type { Config } from '../../config.js';
import type { Logger } from '../../utils/logger.js';

export interface AdminJwtPayload {
  adminId: string;
  email: string;
  role: AdminRole;
  sessionId: string;
}

declare global {
  namespace Express {
    interface Request {
      admin?: Admin;
      session?: {
        id: string;
        adminId: string;
      };
    }
  }
}

export function createAuthMiddleware(
  prisma: PrismaClient,
  config: Config,
  logger: Logger
) {
  return async (req: Request, res: Response, next: NextFunction): Promise<void> => {
    try {
      // Get token from Authorization header or cookie
      const authHeader = req.headers.authorization;
      const token = authHeader?.startsWith('Bearer ')
        ? authHeader.substring(7)
        : req.cookies?.admin_token;

      if (!token) {
        res.status(401).json({ error: 'Unauthorized', message: 'No authentication token provided' });
        return;
      }

      // Verify JWT
      let payload: AdminJwtPayload;
      try {
        payload = jwt.verify(token, config.JWT_SECRET) as AdminJwtPayload;
      } catch (error) {
        logger.debug({ error }, 'Invalid JWT token');
        res.status(401).json({ error: 'Unauthorized', message: 'Invalid or expired token' });
        return;
      }

      // Check session exists and is not revoked
      const session = await prisma.adminSession.findUnique({
        where: { id: payload.sessionId },
      });

      if (!session || session.revokedAt || session.expiresAt < new Date()) {
        res.status(401).json({ error: 'Unauthorized', message: 'Session expired or revoked' });
        return;
      }

      // Load admin
      const admin = await prisma.admin.findUnique({
        where: { id: payload.adminId },
      });

      if (!admin || admin.status !== 'ACTIVE') {
        res.status(401).json({ error: 'Unauthorized', message: 'Admin account not found or disabled' });
        return;
      }

      // Attach admin and session to request
      req.admin = admin;
      req.session = {
        id: session.id,
        adminId: admin.id,
      };

      next();
    } catch (error) {
      logger.error({ error, requestId: req.id }, 'Authentication middleware error');
      res.status(500).json({ error: 'InternalError', message: 'Authentication error' });
    }
  };
}

/**
 * Optional auth middleware - doesn't fail if no token provided
 */
export function createOptionalAuthMiddleware(
  prisma: PrismaClient,
  config: Config,
  logger: Logger
) {
  const authMiddleware = createAuthMiddleware(prisma, config, logger);
  
  return (req: Request, res: Response, next: NextFunction): void => {
    const authHeader = req.headers.authorization;
    const token = authHeader?.startsWith('Bearer ')
      ? authHeader.substring(7)
      : req.cookies?.admin_token;

    if (!token) {
      next();
      return;
    }

    authMiddleware(req, res, next);
  };
}
