/**
 * Admin Authentication Middleware
 */

import type { Request, Response, NextFunction } from "express";
import type { Logger } from "pino";

export interface AdminRequest extends Request {
  admin?: {
    id: string;
    role: string;
  };
}

/**
 * Admin API key authentication middleware
 */
export function createAdminAuthMiddleware(adminKey: string, logger: Logger) {
  return (req: AdminRequest, res: Response, next: NextFunction) => {
    try {
      const apiKey = req.headers["x-admin-api-key"];

      if (!apiKey || apiKey !== adminKey) {
        return res.status(403).json({
          error: "Forbidden",
          message: "Invalid admin API key",
        });
      }

      // Set admin user (in production, this would come from a proper auth system)
      req.admin = {
        id: "admin",
        role: "ADMIN",
      };

      next();
    } catch (error) {
      logger.error({ error }, "Admin auth middleware error");
      return res.status(500).json({
        error: "Internal Server Error",
        message: "Authentication failed",
      });
    }
  };
}
