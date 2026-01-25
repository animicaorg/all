/**
 * Idempotency Middleware
 */

import type { Response, NextFunction } from "express";
import type { Pool } from "pg";
import type { Logger } from "pino";
import type { AuthenticatedRequest } from "./auth.js";
import { IdempotencyRepo } from "../../db/repositories/index.js";

/**
 * Idempotency middleware for withdrawal requests
 */
export function createIdempotencyMiddleware(pool: Pool, logger: Logger) {
  return async (
    req: AuthenticatedRequest,
    res: Response,
    next: NextFunction
  ) => {
    try {
      const idempotencyKey = req.headers["idempotency-key"] as string;

      // Idempotency key is required for POST requests
      if (!idempotencyKey) {
        return res.status(400).json({
          error: "Bad Request",
          message: "Idempotency-Key header is required",
        });
      }

      const userId = req.user?.id;
      if (!userId) {
        return res.status(401).json({
          error: "Unauthorized",
          message: "User not authenticated",
        });
      }

      const endpoint = req.path;

      // Check for existing idempotency record
      const client = await pool.connect();
      try {
        const repo = new IdempotencyRepo(client);
        const existing = await repo.check(idempotencyKey, userId, endpoint);

        if (existing) {
          // Return cached response
          logger.info(
            { idempotencyKey, userId, withdrawalId: existing.withdrawalId },
            "Returning cached idempotent response"
          );

          return res
            .status(existing.responseStatus)
            .json(existing.responseBody);
        }

        // Store idempotency key in request for later use
        (req as any).idempotencyKey = idempotencyKey;

        next();
      } finally {
        client.release();
      }
    } catch (error) {
      logger.error({ error }, "Idempotency middleware error");
      return res.status(500).json({
        error: "Internal Server Error",
        message: "Failed to check idempotency",
      });
    }
  };
}
