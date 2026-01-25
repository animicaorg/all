/**
 * Webhook Routes
 * 
 * Handles BitGo webhook callbacks for deposit detection
 */

import type { Router } from "express";
import type { Pool } from "pg";
import type { Logger } from "pino";
import { normalizeBitGoWebhook } from "../../bitgo/normalize.js";
import { ingestDeposit } from "../../pipeline/ingest.js";

/**
 * Setup webhook routes
 */
export function setupWebhookRoutes(
  router: Router,
  pool: Pool,
  logger: Logger
): void {
  /**
   * POST /webhook
   * 
   * Main webhook endpoint for BitGo callbacks
   * 
   * Expected headers:
   * - x-bitgo-signature: HMAC signature for verification
   * 
   * Expected body: BitGoWebhookPayload
   */
  router.post("/webhook", async (req, res) => {
    const requestId = req.header("x-request-id") || Math.random().toString(36).slice(2);
    const webhookLogger = logger.child({ requestId, endpoint: "webhook" });

    try {
      webhookLogger.info(
        {
          type: req.body?.type,
          walletId: req.body?.walletId,
          coin: req.body?.coin,
          transferId: req.body?.transfer?.id,
        },
        "Received BitGo webhook"
      );

      // Normalize webhook payload
      const client = await pool.connect();
      let observations;
      
      try {
        observations = await normalizeBitGoWebhook(
          req.body,
          client,
          webhookLogger
        );
      } finally {
        client.release();
      }

      if (observations.length === 0) {
        webhookLogger.debug("No valid deposit observations in webhook");
        res.status(200).json({
          status: "ok",
          message: "No deposits to process",
          processed: 0,
        });
        return;
      }

      // Process each observation
      const results = [];
      for (const observation of observations) {
        try {
          const result = await ingestDeposit(pool, observation, webhookLogger);
          results.push({
            depositId: result.depositId,
            status: result.status,
            isNew: result.isNew,
            userId: result.userId,
          });

          webhookLogger.info(
            {
              depositId: result.depositId,
              status: result.status,
              isNew: result.isNew,
              userId: result.userId,
              unassigned: result.unassigned,
              riskHold: result.riskHold,
              txid: observation.txid,
              address: observation.address,
              amountAtoms: observation.amountAtoms.toString(),
            },
            result.isNew ? "Deposit detected" : "Deposit updated"
          );
        } catch (error) {
          webhookLogger.error(
            {
              error,
              txid: observation.txid,
              address: observation.address,
            },
            "Failed to ingest deposit observation"
          );

          // Continue processing other observations
          results.push({
            error: "Failed to process",
            txid: observation.txid,
          });
        }
      }

      // Return success even if some observations failed
      // BitGo will retry the webhook if we return an error
      res.status(200).json({
        status: "ok",
        message: "Webhook processed",
        processed: results.length,
        results,
      });
    } catch (error) {
      webhookLogger.error({ error }, "Webhook processing error");

      // Return 500 so BitGo will retry
      res.status(500).json({
        error: "Internal Server Error",
        message: "Failed to process webhook",
      });
    }
  });

  /**
   * GET /webhook/health
   * 
   * Health check for webhook endpoint
   */
  router.get("/webhook/health", async (_req, res) => {
    try {
      // Check database connection
      const result = await pool.query("SELECT 1");
      const dbOk = result.rowCount === 1;

      if (!dbOk) {
        res.status(503).json({
          status: "unhealthy",
          message: "Database connection failed",
        });
        return;
      }

      res.status(200).json({
        status: "healthy",
        service: "bitgo-webhook-ingestor",
        webhook: "ready",
      });
    } catch (error) {
      logger.error({ error }, "Health check failed");
      res.status(503).json({
        status: "unhealthy",
        message: "Service unavailable",
      });
    }
  });
}
