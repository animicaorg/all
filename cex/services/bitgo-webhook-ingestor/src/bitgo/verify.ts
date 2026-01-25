/**
 * BitGo Webhook Signature Verification
 */

import crypto from "crypto";
import type { Logger } from "pino";

/**
 * Verify BitGo webhook signature using HMAC
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
    const expectedSignature = crypto
      .createHmac("sha256", secret)
      .update(payload)
      .digest("hex");

    // Constant-time comparison to prevent timing attacks
    return crypto.timingSafeEqual(
      Buffer.from(signature),
      Buffer.from(expectedSignature)
    );
  } catch (error) {
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
    logger.warn("No timestamp in webhook");
    return false;
  }

  try {
    const webhookTime = new Date(timestamp).getTime();
    const now = Date.now();
    const diff = Math.abs(now - webhookTime);

    if (diff > windowSeconds * 1000) {
      logger.warn(
        { diff: diff / 1000, windowSeconds },
        "Webhook timestamp outside replay window"
      );
      return false;
    }

    return true;
  } catch (error) {
    logger.warn({ error, timestamp }, "Invalid webhook timestamp");
    return false;
  }
}
