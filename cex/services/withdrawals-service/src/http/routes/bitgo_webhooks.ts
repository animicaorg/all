/**
 * BitGo Webhook Routes
 */

import type { Router } from "express";
import type { Pool } from "pg";
import type { Logger } from "pino";
import { verifyWebhookSignature } from "../../bitgo/verify.js";
import { normalizeWebhookToObservation } from "../../bitgo/normalize.js";
import { processWebhook } from "../../pipeline/tracker.js";
import type { BitgoConfigStore } from "../../bitgo/config.js";

export function setupBitGoWebhookRoutes(
  router: Router,
  pool: Pool,
  bitgoConfigStore: BitgoConfigStore,
  logger: Logger
): void {
  /**
   * POST /webhooks/bitgo - Receive BitGo webhooks
   */
  router.post("/webhooks/bitgo", async (req, res) => {
    try {
      // Verify webhook signature if secret is configured
      const runtimeConfig = await bitgoConfigStore.getConfig();
      if (runtimeConfig.webhookSecret) {
        const signature = req.headers["x-bitgo-signature"] as string;
        
        if (!signature) {
          logger.warn("Missing BitGo webhook signature");
          return res.status(401).json({
            error: "Unauthorized",
            message: "Missing webhook signature",
          });
        }

        const payload = JSON.stringify(req.body);
        const isValid = verifyWebhookSignature(
          payload,
          signature,
          runtimeConfig.webhookSecret
        );

        if (!isValid) {
          logger.warn("Invalid BitGo webhook signature");
          return res.status(401).json({
            error: "Unauthorized",
            message: "Invalid webhook signature",
          });
        }
      }

      // Normalize webhook to observation
      const observation = normalizeWebhookToObservation(req.body);

      if (!observation) {
        logger.warn({ webhook: req.body }, "Could not normalize webhook");
        return res.status(400).json({
          error: "Bad Request",
          message: "Invalid webhook payload",
        });
      }

      // Process webhook
      const client = await pool.connect();
      try {
        await client.query("BEGIN");

        const result = await processWebhook(client, observation, logger);

        await client.query("COMMIT");

        logger.info(
          {
            providerRef: observation.providerRef,
            state: observation.state,
            success: result.success,
          },
          "BitGo webhook processed"
        );

        return res.json({
          success: result.success,
          message: result.message,
        });
      } catch (error) {
        await client.query("ROLLBACK");
        throw error;
      } finally {
        client.release();
      }
    } catch (error) {
      logger.error({ error, body: req.body }, "Failed to process BitGo webhook");
      return res.status(500).json({
        error: "Internal Server Error",
        message: "Failed to process webhook",
      });
    }
  });
}
