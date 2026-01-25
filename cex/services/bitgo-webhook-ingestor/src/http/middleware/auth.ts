/**
 * Authentication Middleware
 * 
 * Verifies BitGo webhook signatures and prevents replay attacks
 */

import type { Request, Response, NextFunction } from "express";
import type { Logger } from "pino";
import { verifyBitGoSignature, verifyWebhookTimestamp } from "../../bitgo/verify.js";

export interface AuthConfig {
  webhookSecret?: string;
  replayWindowSeconds: number;
  requireAuth: boolean;
}

/**
 * Verify BitGo webhook signature
 */
export function createBitGoAuthMiddleware(
  config: AuthConfig,
  logger: Logger
) {
  return async (req: Request, res: Response, next: NextFunction) => {
    // Skip auth if not required (dev/testing)
    if (!config.requireAuth) {
      logger.debug("Auth disabled, skipping verification");
      next();
      return;
    }

    // Check if secret is configured
    if (!config.webhookSecret) {
      logger.error("Webhook secret not configured but auth is required");
      res.status(500).json({
        error: "Internal Server Error",
        message: "Webhook authentication not configured",
      });
      return;
    }

    // Get signature from header
    const signature = req.header("x-bitgo-signature");
    if (!signature) {
      logger.warn("Missing BitGo signature header");
      res.status(401).json({
        error: "Unauthorized",
        message: "Missing signature header",
      });
      return;
    }

    // Verify signature
    const rawBody = JSON.stringify(req.body);
    const isValidSignature = verifyBitGoSignature(
      rawBody,
      signature,
      config.webhookSecret
    );

    if (!isValidSignature) {
      logger.warn({ signature }, "Invalid BitGo signature");
      res.status(401).json({
        error: "Unauthorized",
        message: "Invalid signature",
      });
      return;
    }

    // Verify timestamp to prevent replay attacks
    const timestamp = req.body?.transfer?.date || req.body?.timestamp;
    const isValidTimestamp = verifyWebhookTimestamp(
      timestamp,
      config.replayWindowSeconds,
      logger
    );

    if (!isValidTimestamp) {
      logger.warn({ timestamp }, "Invalid or expired webhook timestamp");
      res.status(401).json({
        error: "Unauthorized",
        message: "Webhook timestamp invalid or expired",
      });
      return;
    }

    logger.debug("BitGo webhook authenticated successfully");
    next();
  };
}

/**
 * Admin API key authentication
 */
export function createAdminAuthMiddleware(
  adminKey: string | undefined,
  logger: Logger
) {
  return (req: Request, res: Response, next: NextFunction) => {
    if (!adminKey) {
      logger.warn("Admin key not configured");
      res.status(503).json({
        error: "Service Unavailable",
        message: "Admin endpoints not configured",
      });
      return;
    }

    const authHeader = req.header("Authorization");
    const providedKey = authHeader?.replace(/^Bearer\s+/i, "");

    if (!providedKey || providedKey !== adminKey) {
      logger.warn("Invalid admin authentication");
      res.status(401).json({
        error: "Unauthorized",
        message: "Invalid admin key",
      });
      return;
    }

    next();
  };
}
