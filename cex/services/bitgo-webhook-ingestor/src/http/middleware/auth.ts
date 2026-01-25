/**
 * Authentication Middleware
 * 
 * Provides authentication for admin and internal service endpoints
 */

import type { Request, Response, NextFunction } from "express";
import type { Logger } from "@cex/observability";

/**
 * Admin API key authentication
 * 
 * Verifies Bearer token against configured admin key
 */
export function createAdminAuthMiddleware(
  adminKey: string | undefined,
  logger: Logger
) {
  return (req: Request, res: Response, next: NextFunction) => {
    const requestLogger = logger.child({
      middleware: "admin_auth",
      path: req.path,
      method: req.method,
    });

    if (!adminKey) {
      requestLogger.warn("Admin key not configured");
      res.status(503).json({
        error: "Service Unavailable",
        message: "Admin endpoints not configured",
      });
      return;
    }

    const authHeader = req.header("Authorization");
    if (!authHeader) {
      requestLogger.warn("Missing Authorization header");
      res.status(401).json({
        error: "Unauthorized",
        message: "Authorization header required",
      });
      return;
    }

    // Extract token from "Bearer <token>" format
    const providedKey = authHeader.replace(/^Bearer\s+/i, "");

    if (!providedKey || providedKey !== adminKey) {
      requestLogger.warn("Invalid admin key provided");
      res.status(401).json({
        error: "Unauthorized",
        message: "Invalid admin key",
      });
      return;
    }

    requestLogger.debug("Admin authenticated successfully");
    next();
  };
}

