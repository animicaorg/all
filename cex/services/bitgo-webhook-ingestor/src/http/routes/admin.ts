/**
 * Admin Routes
 * 
 * Administrative endpoints for managing deposits
 */

import type { Router } from "express";
import type { Pool } from "pg";
import type { Logger } from "@cex/observability";
import { DepositsRepo, OutboxRepo, AuditRepo } from "../../db/repositories/index.js";

/**
 * Setup admin routes
 */
export function setupAdminRoutes(
  router: Router,
  pool: Pool,
  logger: Logger
): void {
  /**
   * GET /admin/deposits/:depositId
   * 
   * Get deposit details
   */
  router.get("/admin/deposits/:depositId", async (req, res) => {
    try {
      const client = await pool.connect();
      try {
        const depositsRepo = new DepositsRepo(client);
        const deposit = await depositsRepo.getById(req.params.depositId);

        if (!deposit) {
          res.status(404).json({
            error: "Not Found",
            message: "Deposit not found",
          });
          return;
        }

        // Convert bigint to string for JSON serialization
        const depositJson = {
          ...deposit,
          amountAtoms: deposit.amountAtoms.toString(),
        };

        res.status(200).json(depositJson);
      } finally {
        client.release();
      }
    } catch (error) {
      logger.error({ error }, "Failed to get deposit");
      res.status(500).json({
        error: "Internal Server Error",
        message: "Failed to retrieve deposit",
      });
    }
  });

  /**
   * GET /admin/deposits
   * 
   * List deposits with filtering
   */
  router.get("/admin/deposits", async (req, res) => {
    try {
      const status = req.query.status as string | undefined;
      const limit = parseInt(req.query.limit as string) || 100;

      const client = await pool.connect();
      try {
        const depositsRepo = new DepositsRepo(client);
        
        let deposits;
        if (status) {
          deposits = await depositsRepo.getByStatus(status as any, limit);
        } else {
          // Get recent deposits
          const result = await client.query(
            `SELECT * FROM deposits
             ORDER BY created_at DESC
             LIMIT $1`,
            [limit]
          );
          deposits = result.rows.map((row) => ({
            id: row.id,
            userId: row.user_id,
            assetNetworkId: row.asset_network_id,
            provider: row.provider,
            txid: row.txid,
            address: row.address,
            amountAtoms: row.amount_atoms,
            confirmations: row.confirmations,
            status: row.status,
            detectedAt: row.detected_at,
            confirmedAt: row.confirmed_at,
            creditedAt: row.credited_at,
            unassigned: row.unassigned,
            riskHold: row.risk_hold,
            createdAt: row.created_at,
          }));
        }

        // Convert bigints to strings
        const depositsJson = deposits.map((d) => ({
          ...d,
          amountAtoms: d.amountAtoms?.toString(),
        }));

        res.status(200).json({
          deposits: depositsJson,
          count: depositsJson.length,
        });
      } finally {
        client.release();
      }
    } catch (error) {
      logger.error({ error }, "Failed to list deposits");
      res.status(500).json({
        error: "Internal Server Error",
        message: "Failed to list deposits",
      });
    }
  });

  /**
   * POST /admin/deposits/:depositId/release-hold
   * 
   * Release a deposit from risk hold
   */
  router.post("/admin/deposits/:depositId/release-hold", async (req, res) => {
    try {
      const { depositId } = req.params;

      const client = await pool.connect();
      try {
        await client.query("BEGIN");

        const depositsRepo = new DepositsRepo(client);
        const auditRepo = new AuditRepo(client);

        const deposit = await depositsRepo.getById(depositId);
        if (!deposit) {
          await client.query("ROLLBACK");
          res.status(404).json({
            error: "Not Found",
            message: "Deposit not found",
          });
          return;
        }

        if (!deposit.riskHold) {
          await client.query("ROLLBACK");
          res.status(400).json({
            error: "Bad Request",
            message: "Deposit is not on hold",
          });
          return;
        }

        // Release hold
        await client.query(
          `UPDATE deposits
           SET risk_hold = false, risk_reason = NULL, updated_at = NOW()
           WHERE id = $1`,
          [depositId]
        );

        // Log audit event
        await auditRepo.logDeposit(
          "DEPOSIT_HOLD_RELEASED",
          depositId,
          deposit.userId,
          { reason: deposit.riskReason },
          { releasedBy: "admin" }
        );

        // If deposit is CONFIRMED and has user, create outbox entry
        if (deposit.status === "CONFIRMED" && deposit.userId) {
          const outboxRepo = new OutboxRepo(client);
          
          // Get asset symbol
          const assetResult = await client.query(
            `SELECT a.symbol
             FROM asset_networks an
             JOIN assets a ON a.id = an.asset_id
             WHERE an.id = $1`,
            [deposit.assetNetworkId]
          );

          if (assetResult.rows.length > 0) {
            const assetSymbol = assetResult.rows[0].symbol;
            await outboxRepo.create(
              depositId,
              deposit.userId,
              assetSymbol,
              deposit.amountAtoms,
              {
                provider: deposit.provider,
                txid: deposit.txid,
                address: deposit.address,
              }
            );
          }
        }

        await client.query("COMMIT");

        logger.info({ depositId }, "Deposit hold released");

        res.status(200).json({
          status: "ok",
          message: "Hold released",
          depositId,
        });
      } catch (error) {
        await client.query("ROLLBACK");
        throw error;
      } finally {
        client.release();
      }
    } catch (error) {
      logger.error({ error }, "Failed to release hold");
      res.status(500).json({
        error: "Internal Server Error",
        message: "Failed to release hold",
      });
    }
  });

  /**
   * GET /admin/outbox
   * 
   * Get pending outbox items
   */
  router.get("/admin/outbox", async (req, res) => {
    try {
      const limit = parseInt(req.query.limit as string) || 100;

      const client = await pool.connect();
      try {
        const outboxRepo = new OutboxRepo(client);
        const items = await outboxRepo.getPending(limit);

        // Convert bigints in payload
        const itemsJson = items.map((item) => ({
          ...item,
          payload: {
            ...item.payload,
            amountAtoms: item.payload.amountAtoms?.toString(),
          },
        }));

        res.status(200).json({
          items: itemsJson,
          count: itemsJson.length,
        });
      } finally {
        client.release();
      }
    } catch (error) {
      logger.error({ error }, "Failed to get outbox items");
      res.status(500).json({
        error: "Internal Server Error",
        message: "Failed to retrieve outbox items",
      });
    }
  });

  /**
   * GET /admin/stats
   * 
   * Get deposit statistics
   */
  router.get("/admin/stats", async (req, res) => {
    try {
      const client = await pool.connect();
      try {
        const result = await client.query(`
          SELECT 
            status,
            COUNT(*) as count,
            COUNT(CASE WHEN unassigned = true THEN 1 END) as unassigned_count,
            COUNT(CASE WHEN risk_hold = true THEN 1 END) as risk_hold_count
          FROM deposits
          WHERE created_at > NOW() - INTERVAL '24 hours'
          GROUP BY status
        `);

        const stats = result.rows.map((row) => ({
          status: row.status,
          count: parseInt(row.count),
          unassignedCount: parseInt(row.unassigned_count),
          riskHoldCount: parseInt(row.risk_hold_count),
        }));

        // Get outbox stats
        const outboxResult = await client.query(`
          SELECT 
            COUNT(*) as pending,
            COUNT(CASE WHEN processed_at IS NOT NULL THEN 1 END) as processed,
            MAX(retry_count) as max_retries
          FROM deposit_outbox
          WHERE created_at > NOW() - INTERVAL '24 hours'
        `);

        const outboxStats = {
          pending: parseInt(outboxResult.rows[0].pending),
          processed: parseInt(outboxResult.rows[0].processed),
          maxRetries: parseInt(outboxResult.rows[0].max_retries) || 0,
        };

        res.status(200).json({
          deposits: stats,
          outbox: outboxStats,
          timestamp: new Date().toISOString(),
        });
      } finally {
        client.release();
      }
    } catch (error) {
      logger.error({ error }, "Failed to get stats");
      res.status(500).json({
        error: "Internal Server Error",
        message: "Failed to retrieve statistics",
      });
    }
  });
}
