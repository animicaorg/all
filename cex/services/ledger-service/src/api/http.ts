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
import { handleDepositCredit } from "../consumers/handlers/deposit_credit.js";

function requiredString(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${field} is required`);
  }
  return value.trim();
}

async function getAssetSymbolForNetwork(client: any, assetNetworkId: string): Promise<string> {
  const result = await client.query(
    `SELECT assets.symbol
     FROM asset_networks
     JOIN assets ON assets.id = asset_networks.asset_id
     WHERE asset_networks.id = $1::uuid`,
    [assetNetworkId]
  );
  const symbol = result.rows[0]?.symbol;
  if (!symbol) throw new Error("Asset network not found");
  return symbol;
}

async function getWithdrawalLedgerRow(client: any, withdrawalId: string) {
  const result = await client.query(
    `SELECT
       withdrawals.id,
       withdrawals.user_id::text AS user_id,
       withdrawals.asset_network_id::text AS asset_network_id,
       withdrawals.total_debit_amount::text AS total_debit_amount,
       assets.symbol AS asset_symbol
     FROM withdrawals
     JOIN asset_networks ON asset_networks.id = withdrawals.asset_network_id
     JOIN assets ON assets.id = asset_networks.asset_id
     WHERE withdrawals.id = $1::uuid`,
    [withdrawalId]
  );
  const row = result.rows[0];
  if (!row) throw new Error("Withdrawal not found");
  return row;
}

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

  app.post("/internal/deposit-credit", async (req, res) => {
    const client = await pool.connect();
    try {
      await client.query("BEGIN");
      await handleDepositCredit(req.body, client, logger);
      await client.query("COMMIT");
      res.json({ ok: true });
    } catch (error: any) {
      await client.query("ROLLBACK").catch(() => undefined);
      logger.error({ error }, "Failed to process internal deposit credit");
      res.status(500).json({ error: "Deposit credit failed", message: error.message });
    } finally {
      client.release();
    }
  });

  app.post("/internal/lock", async (req, res) => {
    const client = await pool.connect();
    try {
      const userId = requiredString(req.body?.userId, "userId");
      const assetNetworkId = requiredString(req.body?.assetNetworkId, "assetNetworkId");
      const referenceId = requiredString(req.body?.referenceId, "referenceId");
      const amount = BigInt(requiredString(req.body?.amount, "amount"));
      const idempotencyKey = `withdrawal:lock:${referenceId}`;

      await client.query("BEGIN");

      const idempotencyRepo = new IdempotencyRepo(client);
      const existing = await idempotencyRepo.get(idempotencyKey);
      if (existing) {
        await client.query("COMMIT");
        res.json(existing.result);
        return;
      }

      const assetId = await getAssetSymbolForNetwork(client, assetNetworkId);
      const accountsRepo = new AccountsRepo(client);
      const ledgerRepo = new LedgerRepo(client);
      const balancesRepo = new BalancesRepo(client);
      const accounts = await accountsRepo.ensureUserAccounts(userId, assetId);
      const balance = await balancesRepo.getBalance(userId, assetId);
      const available = balance?.availableAtoms || 0n;
      const locked = balance?.lockedAtoms || 0n;
      if (available < amount) {
        throw new Error("Insufficient available balance");
      }

      const ledgerTx = await ledgerRepo.createTransaction("TRANSFER", null, null, {
        reason: req.body?.reason || "WITHDRAWAL",
        referenceId,
        assetNetworkId,
      });
      await ledgerRepo.addEntry(ledgerTx.id, accounts.available.id, assetId, "CREDIT", amount, `Withdrawal lock ${referenceId}`);
      await ledgerRepo.addEntry(ledgerTx.id, accounts.locked.id, assetId, "DEBIT", amount, `Withdrawal lock ${referenceId}`);
      await balancesRepo.updateBalance(userId, assetId, available - amount, locked + amount);

      const result = { transactionId: ledgerTx.id };
      await idempotencyRepo.set(idempotencyKey, "ledger-withdrawal-lock", result, 7 * 24 * 60 * 60);
      await client.query("COMMIT");
      res.json(result);
    } catch (error: any) {
      await client.query("ROLLBACK").catch(() => undefined);
      logger.error({ error, body: req.body }, "Failed to apply withdrawal ledger lock");
      res.status(500).json({ error: "Ledger lock failed", message: error.message });
    } finally {
      client.release();
    }
  });

  app.post("/internal/broadcast", async (req, res) => {
    const client = await pool.connect();
    try {
      const withdrawalId = requiredString(req.body?.withdrawalId, "withdrawalId");
      const txid = typeof req.body?.txid === "string" ? req.body.txid : null;
      const idempotencyKey = `withdrawal:broadcast:${withdrawalId}`;

      await client.query("BEGIN");

      const idempotencyRepo = new IdempotencyRepo(client);
      const existing = await idempotencyRepo.get(idempotencyKey);
      if (existing) {
        await client.query("COMMIT");
        res.json(existing.result);
        return;
      }

      const withdrawal = await getWithdrawalLedgerRow(client, withdrawalId);
      if (req.body?.userId && req.body.userId !== withdrawal.user_id) {
        throw new Error("Withdrawal user mismatch");
      }

      const amount = BigInt(withdrawal.total_debit_amount);
      const assetId = withdrawal.asset_symbol;
      const accountsRepo = new AccountsRepo(client);
      const ledgerRepo = new LedgerRepo(client);
      const balancesRepo = new BalancesRepo(client);
      const accounts = await accountsRepo.ensureUserAccounts(withdrawal.user_id, assetId);
      const clearing = await accountsRepo.ensureSystemAccount("CLEARING", assetId);
      const balance = await balancesRepo.getBalance(withdrawal.user_id, assetId);
      const available = balance?.availableAtoms || 0n;
      const locked = balance?.lockedAtoms || 0n;
      if (locked < amount) {
        throw new Error("Insufficient locked balance");
      }

      const ledgerTx = await ledgerRepo.createTransaction("WITHDRAWAL", null, null, {
        withdrawalId,
        txid,
      });
      await ledgerRepo.addEntry(ledgerTx.id, accounts.locked.id, assetId, "CREDIT", amount, `Withdrawal broadcast ${withdrawalId}`);
      await ledgerRepo.addEntry(ledgerTx.id, clearing.id, assetId, "DEBIT", amount, `Withdrawal broadcast ${withdrawalId}`);
      await balancesRepo.updateBalance(withdrawal.user_id, assetId, available, locked - amount);

      const result = { transactionId: ledgerTx.id };
      await idempotencyRepo.set(idempotencyKey, "ledger-withdrawal-broadcast", result, 7 * 24 * 60 * 60);
      await client.query("COMMIT");
      res.json(result);
    } catch (error: any) {
      await client.query("ROLLBACK").catch(() => undefined);
      logger.error({ error, body: req.body }, "Failed to apply withdrawal broadcast");
      res.status(500).json({ error: "Ledger broadcast failed", message: error.message });
    } finally {
      client.release();
    }
  });

  app.post("/internal/cancel", async (req, res) => {
    const client = await pool.connect();
    try {
      const withdrawalId = requiredString(req.body?.withdrawalId, "withdrawalId");
      const idempotencyKey = `withdrawal:cancel:${withdrawalId}`;

      await client.query("BEGIN");

      const idempotencyRepo = new IdempotencyRepo(client);
      const existing = await idempotencyRepo.get(idempotencyKey);
      if (existing) {
        await client.query("COMMIT");
        res.json(existing.result);
        return;
      }

      const withdrawal = await getWithdrawalLedgerRow(client, withdrawalId);
      if (req.body?.userId && req.body.userId !== withdrawal.user_id) {
        throw new Error("Withdrawal user mismatch");
      }

      const amount = BigInt(withdrawal.total_debit_amount);
      const assetId = withdrawal.asset_symbol;
      const accountsRepo = new AccountsRepo(client);
      const ledgerRepo = new LedgerRepo(client);
      const balancesRepo = new BalancesRepo(client);
      const accounts = await accountsRepo.ensureUserAccounts(withdrawal.user_id, assetId);
      const balance = await balancesRepo.getBalance(withdrawal.user_id, assetId);
      const available = balance?.availableAtoms || 0n;
      const locked = balance?.lockedAtoms || 0n;
      const releaseAmount = locked < amount ? locked : amount;

      let transactionId: string | null = null;
      if (releaseAmount > 0n) {
        const ledgerTx = await ledgerRepo.createTransaction("TRANSFER", null, null, {
          withdrawalId,
          reason: req.body?.reason || "CANCELED",
        });
        await ledgerRepo.addEntry(ledgerTx.id, accounts.locked.id, assetId, "CREDIT", releaseAmount, `Withdrawal cancel ${withdrawalId}`);
        await ledgerRepo.addEntry(ledgerTx.id, accounts.available.id, assetId, "DEBIT", releaseAmount, `Withdrawal cancel ${withdrawalId}`);
        await balancesRepo.updateBalance(withdrawal.user_id, assetId, available + releaseAmount, locked - releaseAmount);
        transactionId = ledgerTx.id;
      }

      const result = { transactionId };
      await idempotencyRepo.set(idempotencyKey, "ledger-withdrawal-cancel", result, 7 * 24 * 60 * 60);
      await client.query("COMMIT");
      res.json(result);
    } catch (error: any) {
      await client.query("ROLLBACK").catch(() => undefined);
      logger.error({ error, body: req.body }, "Failed to apply withdrawal cancel");
      res.status(500).json({ error: "Ledger cancel failed", message: error.message });
    } finally {
      client.release();
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
