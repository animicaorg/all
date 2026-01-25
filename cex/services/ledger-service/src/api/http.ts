/**
 * Admin HTTP API for Ledger Service
 * Protected endpoints for querying ledger data and triggering reconciliation
 */

import type { Express } from "express";
import type { Pool } from "pg";
import type { Logger } from "pino";
import { runReconciliation, checkHealth } from "../jobs/index.js";
import {
  AccountsRepo,
  LedgerRepo,
  BalancesRepo,
  IdempotencyRepo
} from "../db/repositories/index.js";

export function setupAdminAPI(app: Express, pool: Pool, logger: Logger, adminKey?: string): void {
  // Middleware to check admin key if configured
  const requireAdmin = (req: any, res: any, next: any) => {
    if (adminKey) {
      const providedKey = req.headers["x-admin-key"];
      if (providedKey !== adminKey) {
        return res.status(401).json({ error: "Unauthorized" });
      }
    }
    next();
  };

  /**
   * GET /health - Health check endpoint
   */
  app.get("/health", async (_req, res) => {
    try {
      const health = await checkHealth(pool, logger);
      const statusCode = health.ok ? 200 : 503;
      res.status(statusCode).json(health);
    } catch (error) {
      logger.error({ error }, "Health check failed");
      res.status(500).json({ error: "Health check failed" });
    }
  });

  /**
   * GET /balances/:userId - Get user balances
   */
  app.get("/balances/:userId", requireAdmin, async (req, res) => {
    try {
      const { userId } = req.params;
      const client = await pool.connect();
      try {
        const balancesRepo = new BalancesRepo(client);
        const balances = await balancesRepo.getUserBalances(userId);
        
        // Format balances for readability
        const formatted = balances.map((b) => ({
          assetId: b.assetId,
          available: b.availableAtoms.toString(),
          locked: b.lockedAtoms.toString(),
          total: (b.availableAtoms + b.lockedAtoms).toString()
        }));

        res.json({
          userId,
          balances: formatted
        });
      } finally {
        client.release();
      }
    } catch (error) {
      logger.error({ error, userId: req.params.userId }, "Failed to get user balances");
      res.status(500).json({ error: "Failed to get balances" });
    }
  });

  /**
   * POST /reconcile/run - Trigger reconciliation job
   */
  app.post("/reconcile/run", requireAdmin, async (_req, res) => {
    try {
      logger.info("Starting reconciliation job via API");
      const report = await runReconciliation(pool, logger);
      
      res.json({
        ok: report.ok,
        runAt: report.runAt,
        mismatchCount: report.mismatches.length,
        mismatches: report.mismatches.slice(0, 100), // Limit to first 100
        summary: report.summary
      });
    } catch (error) {
      logger.error({ error }, "Reconciliation job failed");
      res.status(500).json({ error: "Reconciliation failed" });
    }
  });

  /**
   * GET /reconcile/latest - Get latest reconciliation report
   */
  app.get("/reconcile/latest", requireAdmin, async (_req, res) => {
    try {
      const client = await pool.connect();
      try {
        const result = await client.query(
          `SELECT id, job_type, ok, mismatches, summary, run_at
           FROM reconciliation_reports
           WHERE job_type = 'BALANCE_RECOMPUTE'
           ORDER BY run_at DESC
           LIMIT 1`
        );

        if (result.rowCount === 0) {
          return res.status(404).json({ error: "No reconciliation reports found" });
        }

        const report = result.rows[0];
        res.json({
          id: report.id,
          ok: report.ok,
          runAt: report.run_at,
          mismatchCount: report.mismatches.length,
          mismatches: report.mismatches.slice(0, 100),
          summary: report.summary
        });
      } finally {
        client.release();
      }
    } catch (error) {
      logger.error({ error }, "Failed to get latest reconciliation report");
      res.status(500).json({ error: "Failed to get report" });
    }
  });

  /**
   * GET /ledger/tx/:id - Get ledger transaction with entries
   */
  app.get("/ledger/tx/:id", requireAdmin, async (req, res) => {
    try {
      const { id } = req.params;
      const client = await pool.connect();
      try {
        const ledgerRepo = new LedgerRepo(client);
        const transaction = await ledgerRepo.getTransaction(id);

        if (!transaction) {
          return res.status(404).json({ error: "Transaction not found" });
        }

        // Format entries for readability
        const formattedEntries = transaction.entries?.map((e) => ({
          id: e.id,
          accountId: e.accountId,
          assetId: e.assetId,
          direction: e.direction,
          amount: e.amountAtoms.toString(),
          description: e.description,
          createdAt: e.createdAt
        }));

        res.json({
          id: transaction.transaction.id,
          txType: transaction.transaction.txType,
          marketId: transaction.transaction.marketId,
          seq: transaction.transaction.seq?.toString(),
          metadata: transaction.transaction.metadata,
          entries: formattedEntries,
          createdAt: transaction.transaction.createdAt
        });
      } finally {
        client.release();
      }
    } catch (error) {
      logger.error({ error, txId: req.params.id }, "Failed to get transaction");
      res.status(500).json({ error: "Failed to get transaction" });
    }
  });

  /**
   * GET /ledger/account/:accountId/entries - Get entries for an account
   */
  app.get("/ledger/account/:accountId/entries", requireAdmin, async (req, res) => {
    try {
      const { accountId } = req.params;
      const limit = parseInt(req.query.limit as string) || 100;
      const offset = parseInt(req.query.offset as string) || 0;

      const client = await pool.connect();
      try {
        const ledgerRepo = new LedgerRepo(client);
        const entries = await ledgerRepo.getEntriesByAccount(accountId, limit, offset);

        // Format entries
        const formatted = entries.map((e) => ({
          id: e.id,
          transactionId: e.transactionId,
          assetId: e.assetId,
          direction: e.direction,
          amount: e.amountAtoms.toString(),
          description: e.description,
          createdAt: e.createdAt
        }));

        res.json({
          accountId,
          entries: formatted,
          limit,
          offset
        });
      } finally {
        client.release();
      }
    } catch (error) {
      logger.error({ error, accountId: req.params.accountId }, "Failed to get entries");
      res.status(500).json({ error: "Failed to get entries" });
    }
  });

  /**
   * GET /ledger/accounts/:userId - Get all accounts for a user
   */
  app.get("/ledger/accounts/:userId", requireAdmin, async (req, res) => {
    try {
      const { userId } = req.params;
      const client = await pool.connect();
      try {
        const accountsRepo = new AccountsRepo(client);
        const accounts = await accountsRepo.getUserAccounts(userId);

        const formatted = accounts.map((a) => ({
          id: a.id,
          accountType: a.accountType,
          accountName: a.accountName,
          assetId: a.assetId,
          createdAt: a.createdAt
        }));

        res.json({
          userId,
          accounts: formatted
        });
      } finally {
        client.release();
      }
    } catch (error) {
      logger.error({ error, userId: req.params.userId }, "Failed to get accounts");
      res.status(500).json({ error: "Failed to get accounts" });
    }
  });

  logger.info("Admin API endpoints registered");
}
