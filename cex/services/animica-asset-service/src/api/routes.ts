/**
 * API Routes for Animica Asset Service
 */

import type { Express, Request, Response } from "express";
import type { Pool } from "pg";
import type { Logger } from "pino";
import type { Config } from "../config.js";
import type { AnimicaRpcClient } from "../rpc/client.js";
import { assignDepositAddress, getDepositAddress } from "../deposits/address_assign.js";
import { WithdrawalsRepository } from "../db/repositories/withdrawals_repo.js";
import { ScanStateRepository } from "../db/repositories/scan_state_repo.js";
import { estimateFee } from "../withdrawals/fees.js";
import { buildTransaction, getNextNonce } from "../withdrawals/build_tx.js";
import { broadcastTransaction } from "../withdrawals/broadcast.js";
import { transact } from "../db/tx.js";

/**
 * Setup all routes
 */
export function setupRoutes(
  app: Express,
  pool: Pool,
  rpcClient: AnimicaRpcClient,
  config: Config,
  logger: Logger
): void {
  // Middleware for admin auth
  const requireAdminAuth = (req: Request, res: Response, next: any) => {
    const authHeader = req.headers.authorization;
    if (!authHeader || !authHeader.startsWith("Bearer ")) {
      return res.status(401).json({ error: "Unauthorized" });
    }

    const token = authHeader.substring(7);
    if (token !== config.ADMIN_API_KEY) {
      return res.status(403).json({ error: "Forbidden" });
    }

    next();
  };

  /**
   * POST /api/deposits/address
   * Assign a deposit address to a user
   */
  app.post("/api/deposits/address", requireAdminAuth, async (req: Request, res: Response) => {
    try {
      const { user_id, asset_network_id, label } = req.body;

      if (!user_id || !asset_network_id) {
        return res.status(400).json({
          error: "Bad Request",
          message: "user_id and asset_network_id are required",
        });
      }

      const result = await assignDepositAddress(
        { user_id, asset_network_id, label },
        pool,
        rpcClient,
        logger
      );

      res.json({
        address: result.address,
        label: result.label,
        created: result.created,
      });
    } catch (error: any) {
      logger.error({ error }, "Failed to assign deposit address");
      res.status(500).json({
        error: "Internal Server Error",
        message: error.message,
      });
    }
  });

  /**
   * GET /api/deposits/address/:user_id
   * Get deposit address for a user
   */
  app.get("/api/deposits/address/:user_id", requireAdminAuth, async (req: Request, res: Response) => {
    try {
      const { user_id } = req.params;
      const asset_network_id = config.ANIMICA_ASSET_NETWORK_ID;

      const address = await getDepositAddress(user_id, asset_network_id, pool, logger);

      if (!address) {
        return res.status(404).json({
          error: "Not Found",
          message: "No deposit address assigned to this user",
        });
      }

      res.json({ address });
    } catch (error: any) {
      logger.error({ error }, "Failed to get deposit address");
      res.status(500).json({
        error: "Internal Server Error",
        message: error.message,
      });
    }
  });

  /**
   * POST /api/withdrawals/submit
   * Submit a withdrawal request
   */
  app.post("/api/withdrawals/submit", requireAdminAuth, async (req: Request, res: Response) => {
    try {
      const {
        withdrawal_id,
        from_address,
        to_address,
        amount,
      } = req.body;

      if (!withdrawal_id || !from_address || !to_address || !amount) {
        return res.status(400).json({
          error: "Bad Request",
          message: "withdrawal_id, from_address, to_address, and amount are required",
        });
      }

      const withdrawalsRepo = new WithdrawalsRepository(pool);

      // Get withdrawal
      const withdrawal = await withdrawalsRepo.getById(withdrawal_id);
      if (!withdrawal) {
        return res.status(404).json({
          error: "Not Found",
          message: "Withdrawal not found",
        });
      }

      if (withdrawal.status !== "APPROVED") {
        return res.status(400).json({
          error: "Bad Request",
          message: `Withdrawal is in ${withdrawal.status} status, cannot submit`,
        });
      }

      // Estimate fees
      const feeEstimate = await estimateFee(rpcClient, config, logger);

      // Get nonce
      const nonce = await getNextNonce(from_address, rpcClient, logger);

      // Build transaction
      const tx = await buildTransaction(
        {
          from: from_address,
          to: to_address,
          value: amount,
          nonce,
          gas_limit: feeEstimate.gas_limit,
          gas_price: feeEstimate.gas_price,
        },
        rpcClient,
        logger
      );

      // Update withdrawal with tx details
      await transact(pool, logger, async (client) => {
        await withdrawalsRepo.updateTxDetails(
          withdrawal_id,
          tx.txid,
          tx.nonce,
          tx.raw_tx,
          client
        );

        await withdrawalsRepo.updateStatus(
          withdrawal_id,
          "SIGNING",
          {},
          client
        );
      });

      // Broadcast transaction (outside transaction - external operation)
      const broadcastResult = await broadcastTransaction(tx.raw_tx, rpcClient, logger);

      // Update status based on broadcast result (atomic)
      await transact(pool, logger, async (client) => {
        if (broadcastResult.success) {
          await withdrawalsRepo.updateStatus(withdrawal_id, "BROADCAST", {
            txid: broadcastResult.txid,
            broadcast_at: new Date(),
          }, client);
        } else {
          await withdrawalsRepo.updateStatus(withdrawal_id, "FAILED", {
            failure_code: "BROADCAST_FAILED",
            failure_message: broadcastResult.error,
          }, client);
        }
      });

      if (broadcastResult.success) {
        res.json({
          success: true,
          txid: broadcastResult.txid,
          withdrawal_id,
        });
      } else {
        res.status(500).json({
          error: "Broadcast Failed",
          message: broadcastResult.error,
        });
      }
    } catch (error: any) {
      logger.error({ error }, "Failed to submit withdrawal");
      res.status(500).json({
        error: "Internal Server Error",
        message: error.message,
      });
    }
  });

  /**
   * GET /api/scan/status
   * Get scan status
   */
  app.get("/api/scan/status", requireAdminAuth, async (_req: Request, res: Response) => {
    try {
      const scanStateRepo = new ScanStateRepository(pool, logger);
      const scanState = await scanStateRepo.get(config.ANIMICA_ASSET_NETWORK_ID);

      if (!scanState) {
        return res.status(404).json({
          error: "Not Found",
          message: "Scan state not initialized",
        });
      }

      const chainHead = await rpcClient.getHead();

      res.json({
        cursor_height: scanState.cursor_height,
        cursor_hash: scanState.cursor_hash,
        finalized_height: scanState.finalized_height,
        chain_head: chainHead.height,
        blocks_behind: chainHead.height - scanState.cursor_height,
        lock_owner: scanState.lock_owner,
        lock_expires_at: scanState.lock_expires_at,
        updated_at: scanState.updated_at,
      });
    } catch (error: any) {
      logger.error({ error }, "Failed to get scan status");
      res.status(500).json({
        error: "Internal Server Error",
        message: error.message,
      });
    }
  });

  /**
   * GET /api/withdrawals/:id
   * Get withdrawal status
   */
  app.get("/api/withdrawals/:id", requireAdminAuth, async (req: Request, res: Response) => {
    try {
      const { id } = req.params;

      const withdrawalsRepo = new WithdrawalsRepository(pool);
      const withdrawal = await withdrawalsRepo.getById(id);

      if (!withdrawal) {
        return res.status(404).json({
          error: "Not Found",
          message: "Withdrawal not found",
        });
      }

      res.json({
        id: withdrawal.id,
        user_id: withdrawal.user_id,
        destination_address: withdrawal.destination_address,
        amount: withdrawal.amount,
        fee_amount: withdrawal.fee_amount,
        status: withdrawal.status,
        txid: withdrawal.txid,
        nonce: withdrawal.nonce,
        broadcast_at: withdrawal.broadcast_at,
        confirmed_at: withdrawal.confirmed_at,
        failure_code: withdrawal.failure_code,
        failure_message: withdrawal.failure_message,
        created_at: withdrawal.created_at,
        updated_at: withdrawal.updated_at,
      });
    } catch (error: any) {
      logger.error({ error }, "Failed to get withdrawal");
      res.status(500).json({
        error: "Internal Server Error",
        message: error.message,
      });
    }
  });
}
