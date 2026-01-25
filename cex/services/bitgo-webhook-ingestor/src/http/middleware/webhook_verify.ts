/**
 * BitGo Webhook Signature Verification Middleware
 * 
 * Implements HMAC-SHA256 signature verification for webhook security
 */

import crypto from "crypto";
import type { Request, Response, NextFunction } from "express";
import type { Logger } from "@cex/observability";

export interface WebhookVerifyConfig {
  /**
   * Webhook secret for HMAC verification
   */
  webhookSecret?: string;

  /**
   * Replay attack prevention window in seconds
   */
  replayWindowSeconds: number;

  /**
   * Whether to require authentication (can be disabled for dev/testing)
   */
  requireAuth: boolean;
}

/**
 * Verify BitGo webhook signature using HMAC-SHA256
 */
export function verifyBitGoSignature(
  payload: string,
  signature: string,
  secret: string
): boolean {
  if (!secret || !signature) {
    return false;
  }

  try {
    // Compute HMAC-SHA256 signature
    const expectedSignature = crypto
      .createHmac("sha256", secret)
      .update(payload)
      .digest("hex");

    // Ensure both strings are the same length before comparison
    if (signature.length !== expectedSignature.length) {
      return false;
    }

    // Constant-time comparison to prevent timing attacks
    return crypto.timingSafeEqual(
      Buffer.from(signature, "hex"),
      Buffer.from(expectedSignature, "hex")
    );
  } catch (error) {
    // timingSafeEqual will throw if buffers have different lengths
    // or if hex decoding fails
    return false;
  }
}

/**
 * Verify webhook timestamp to prevent replay attacks
 */
export function verifyWebhookTimestamp(
  timestamp: string | undefined,
  windowSeconds: number,
  logger: Logger
): boolean {
  if (!timestamp) {
    logger.warn("No timestamp in webhook payload");
    return false;
  }

  try {
    const webhookTime = new Date(timestamp).getTime();
    const now = Date.now();
    const diff = Math.abs(now - webhookTime);
    const windowMs = windowSeconds * 1000;

    if (diff > windowMs) {
      logger.warn(
        { 
          diff_seconds: diff / 1000, 
          window_seconds: windowSeconds,
          timestamp,
        },
        "Webhook timestamp outside replay window"
      );
      return false;
    }

    return true;
  } catch (error) {
    logger.warn({ error, timestamp }, "Invalid webhook timestamp format");
    return false;
  }
}

/**
 * Extract timestamp from BitGo webhook payload
 * Supports multiple timestamp field locations
 */
export function extractWebhookTimestamp(body: any): string | undefined {
  // Try common BitGo timestamp locations
  return body?.transfer?.date || body?.timestamp || body?.createdAt;
}

/**
 * Create middleware for BitGo webhook signature verification
 */
export function createWebhookVerificationMiddleware(
  config: WebhookVerifyConfig,
  logger: Logger
) {
  return async (req: Request, res: Response, next: NextFunction) => {
    const requestLogger = logger.child({ 
      middleware: "webhook_verify",
      path: req.path,
      method: req.method,
    });

    // Skip auth if not required (dev/testing environments)
    if (!config.requireAuth) {
      requestLogger.debug("Webhook verification disabled");
      next();
      return;
    }

    // Verify secret is configured
    if (!config.webhookSecret) {
      requestLogger.error("Webhook secret not configured but auth is required");
      res.status(500).json({
        error: "Internal Server Error",
        message: "Webhook authentication not configured",
      });
      return;
    }

    // Extract signature from header
    const signature = req.header("x-bitgo-signature");
    if (!signature) {
      requestLogger.warn("Missing BitGo signature header");
      res.status(401).json({
        error: "Unauthorized",
        message: "Missing x-bitgo-signature header",
      });
      return;
    }

    // Get raw body for signature verification
    // Note: BitGo computes signature over JSON.stringify() of the payload
    // This matches their signature generation, though JSON.stringify() is
    // not fully deterministic. In practice, this works because BitGo sends
    // the exact payload they used to generate the signature.
    // For production, consider using express.raw() middleware to capture
    // the raw body buffer before JSON parsing.
    const rawBody = JSON.stringify(req.body);

    // Verify HMAC signature
    const isValidSignature = verifyBitGoSignature(
      rawBody,
      signature,
      config.webhookSecret
    );

    if (!isValidSignature) {
      requestLogger.warn(
        { 
          signature_header: signature.substring(0, 10) + "...",
        },
        "Invalid BitGo webhook signature"
      );
      res.status(401).json({
        error: "Unauthorized",
        message: "Invalid webhook signature",
      });
      return;
    }

    // Verify timestamp to prevent replay attacks
    const timestamp = extractWebhookTimestamp(req.body);
    const isValidTimestamp = verifyWebhookTimestamp(
      timestamp,
      config.replayWindowSeconds,
      requestLogger
    );

    if (!isValidTimestamp) {
      requestLogger.warn(
        { timestamp },
        "Webhook timestamp invalid or outside replay window"
      );
      res.status(401).json({
        error: "Unauthorized",
        message: "Webhook timestamp invalid or expired",
      });
      return;
    }

    requestLogger.debug("BitGo webhook signature verified successfully");
    next();
  };
}
