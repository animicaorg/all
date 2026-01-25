/**
 * Audit Logging Middleware
 * Automatically logs all admin actions to audit_log
 */

import type { Request, Response, NextFunction } from 'express';
import type { PrismaClient } from '@prisma/client';
import type { Logger } from '../../utils/logger.js';

export interface AuditLogOptions {
  action: string;
  entityType?: string;
  entityId?: string;
  beforeSnapshot?: any;
  afterSnapshot?: any;
  metadata?: any;
}

declare global {
  namespace Express {
    interface Request {
      auditLog?: (options: AuditLogOptions) => Promise<void>;
    }
  }
}

/**
 * Create audit logging middleware
 */
export function createAuditMiddleware(prisma: PrismaClient, logger: Logger) {
  return (req: Request, res: Response, next: NextFunction): void => {
    // Attach audit log function to request
    req.auditLog = async (options: AuditLogOptions) => {
      try {
        const admin = req.admin;
        if (!admin) {
          logger.warn({ requestId: req.id, options }, 'Attempted audit log without admin context');
          return;
        }

        await prisma.auditLog.create({
          data: {
            actorType: 'ADMIN',
            actorAdminId: admin.id,
            action: options.action,
            entityType: options.entityType || 'UNKNOWN',
            entityId: options.entityId,
            requestId: req.id,
            ip: req.ip || req.socket.remoteAddress || null,
            userAgent: req.headers['user-agent'] || null,
            before: options.beforeSnapshot || null,
            after: options.afterSnapshot || null,
            metadata: options.metadata || null,
          },
        });

        logger.info(
          {
            requestId: req.id,
            adminId: admin.id,
            action: options.action,
            entityType: options.entityType,
            entityId: options.entityId,
          },
          'Admin action logged'
        );
      } catch (error) {
        logger.error({ error, requestId: req.id, options }, 'Failed to create audit log');
        // Don't throw - audit logging should not break the request
      }
    };

    next();
  };
}

/**
 * Middleware to automatically log actions for specific routes
 * Use this after the action is completed successfully
 */
export function autoAuditLog(options: Partial<AuditLogOptions>) {
  return async (req: Request, res: Response, next: NextFunction): Promise<void> => {
    const originalJson = res.json.bind(res);

    res.json = function (body: any) {
      // Only log on success (2xx status codes)
      if (res.statusCode >= 200 && res.statusCode < 300 && req.auditLog) {
        req.auditLog({
          action: options.action || `${req.method} ${req.path}`,
          entityType: options.entityType,
          entityId: body?.id || body?.data?.id,
          metadata: {
            method: req.method,
            path: req.path,
            params: req.params,
            ...options.metadata,
          },
        }).catch((error) => {
          // Already logged in auditLog function
        });
      }

      return originalJson(body);
    };

    next();
  };
}
