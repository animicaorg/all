/**
 * Authentication Middleware
 */

import type { Request, Response, NextFunction } from "express";
import type { Logger } from "pino";

export interface AuthenticatedRequest extends Request {
  user?: {
    id: string;
    role: string;
  };
}

/**
 * Simple bearer token authentication middleware
 * In production, this should verify JWT tokens or use a proper auth service
 */
export function createAuthMiddleware(logger: Logger) {
  return async (
    req: AuthenticatedRequest,
    res: Response,
    next: NextFunction
  ) => {
    try {
      const authHeader = req.headers.authorization;

      if (!authHeader || !authHeader.startsWith("Bearer ")) {
        return res.status(401).json({
          error: "Unauthorized",
          message: "Missing or invalid authorization header",
        });
      }

      const token = authHeader.substring(7);

      // TODO: In production, verify JWT token and extract user info
      // For now, we'll extract user ID from a simple token format
      // This is a placeholder - implement proper JWT verification
      
      // Placeholder: Extract user ID from token (format: "user-{id}")
      if (token.startsWith("user-")) {
        const userId = token.substring(5);
        req.user = {
          id: userId,
          role: "USER",
        };
        return next();
      }

      return res.status(401).json({
        error: "Unauthorized",
        message: "Invalid token",
      });
    } catch (error) {
      logger.error({ error }, "Auth middleware error");
      return res.status(500).json({
        error: "Internal Server Error",
        message: "Authentication failed",
      });
    }
  };
}
