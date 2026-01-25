/**
 * Withdrawal Routes
 */

import type { Router } from "express";
import type { Pool } from "pg";
import type { Logger } from "pino";
import { z } from "zod";
import type { AuthenticatedRequest } from "../middleware/auth.js";
import { WithdrawalsRepo, IdempotencyRepo } from "../../db/repositories/index.js";
import { validateAndCreateWithdrawal, type WithdrawalRequest } from "../../pipeline/request.js";

const createWithdrawalSchema = z.object({
  assetNetworkId: z.string().uuid(),
  destinationAddress: z.string().min(1).max(200),
  destinationTag: z.string().optional(),
  amount: z.string().regex(/^\d+$/),
  clientWithdrawalId: z.string().optional(),
});

export function setupWithdrawalRoutes(
  router: Router,
  pool: Pool,
  logger: Logger
): void {
  /**
   * POST /withdrawals - Create withdrawal request
   */
  router.post("/withdrawals", async (req: AuthenticatedRequest, res) => {
    try {
      const userId = req.user?.id;
      if (!userId) {
        return res.status(401).json({
          error: "Unauthorized",
          message: "User not authenticated",
        });
      }

      const idempotencyKey = (req as any).idempotencyKey;
      if (!idempotencyKey) {
        return res.status(400).json({
          error: "Bad Request",
          message: "Idempotency-Key header required",
        });
      }

      // Validate request body
      const parseResult = createWithdrawalSchema.safeParse(req.body);
      if (!parseResult.success) {
        return res.status(400).json({
          error: "Bad Request",
          message: "Invalid request body",
          details: parseResult.error.flatten().fieldErrors,
        });
      }

      const data = parseResult.data;

      const client = await pool.connect();
      try {
        await client.query("BEGIN");

        const request: WithdrawalRequest = {
          assetNetworkId: data.assetNetworkId,
          destinationAddress: data.destinationAddress,
          destinationTag: data.destinationTag,
          amount: BigInt(data.amount),
          clientWithdrawalId: data.clientWithdrawalId,
        };

        const result = await validateAndCreateWithdrawal(
          client,
          userId,
          request,
          idempotencyKey,
          logger
        );

        const withdrawal = await new WithdrawalsRepo(client).findById(
          result.withdrawalId
        );

        if (!withdrawal) {
          throw new Error("Withdrawal not found after creation");
        }

        const responseBody = {
          id: withdrawal.id,
          status: withdrawal.status,
          amount: withdrawal.amount.toString(),
          feeAmount: withdrawal.feeAmount.toString(),
          totalDebitAmount: withdrawal.totalDebitAmount.toString(),
          destinationAddress: withdrawal.destinationAddress,
          destinationTag: withdrawal.destinationTag,
          riskScore: withdrawal.riskScore,
          riskFlags: withdrawal.riskFlags,
          requestedAt: withdrawal.requestedAt.toISOString(),
          createdAt: withdrawal.createdAt.toISOString(),
        };

        // Record idempotency
        await new IdempotencyRepo(client).record(
          idempotencyKey,
          userId,
          "/withdrawals",
          withdrawal.id,
          req.body,
          responseBody,
          201
        );

        await client.query("COMMIT");

        return res.status(201).json(responseBody);
      } catch (error: any) {
        await client.query("ROLLBACK");
        throw error;
      } finally {
        client.release();
      }
    } catch (error: any) {
      logger.error({ error }, "Failed to create withdrawal");
      return res.status(500).json({
        error: "Internal Server Error",
        message: error.message || "Failed to create withdrawal",
      });
    }
  });

  /**
   * GET /withdrawals - List user's withdrawals
   */
  router.get("/withdrawals", async (req: AuthenticatedRequest, res) => {
    try {
      const userId = req.user?.id;
      if (!userId) {
        return res.status(401).json({
          error: "Unauthorized",
          message: "User not authenticated",
        });
      }

      const limit = Math.min(
        parseInt(req.query.limit as string) || 50,
        100
      );
      const offset = parseInt(req.query.offset as string) || 0;
      const status = req.query.status as string | undefined;

      const client = await pool.connect();
      try {
        const repo = new WithdrawalsRepo(client);
        const withdrawals = await repo.list({
          userId,
          status: status as any,
          limit,
          offset,
        });

        return res.json({
          withdrawals: withdrawals.map((w) => ({
            id: w.id,
            status: w.status,
            assetNetworkId: w.assetNetworkId,
            amount: w.amount.toString(),
            feeAmount: w.feeAmount.toString(),
            totalDebitAmount: w.totalDebitAmount.toString(),
            destinationAddress: w.destinationAddress,
            destinationTag: w.destinationTag,
            txid: w.txid,
            riskScore: w.riskScore,
            riskFlags: w.riskFlags,
            requestedAt: w.requestedAt.toISOString(),
            approvedAt: w.approvedAt?.toISOString(),
            broadcastAt: w.broadcastAt?.toISOString(),
            confirmedAt: w.confirmedAt?.toISOString(),
            failureCode: w.failureCode,
            failureMessage: w.failureMessage,
            createdAt: w.createdAt.toISOString(),
          })),
          pagination: {
            limit,
            offset,
            hasMore: withdrawals.length === limit,
          },
        });
      } finally {
        client.release();
      }
    } catch (error) {
      logger.error({ error }, "Failed to list withdrawals");
      return res.status(500).json({
        error: "Internal Server Error",
        message: "Failed to list withdrawals",
      });
    }
  });

  /**
   * GET /withdrawals/:id - Get single withdrawal
   */
  router.get("/withdrawals/:id", async (req: AuthenticatedRequest, res) => {
    try {
      const userId = req.user?.id;
      if (!userId) {
        return res.status(401).json({
          error: "Unauthorized",
          message: "User not authenticated",
        });
      }

      const withdrawalId = req.params.id;

      const client = await pool.connect();
      try {
        const repo = new WithdrawalsRepo(client);
        const withdrawal = await repo.findById(withdrawalId);

        if (!withdrawal) {
          return res.status(404).json({
            error: "Not Found",
            message: "Withdrawal not found",
          });
        }

        // Check ownership
        if (withdrawal.userId !== userId) {
          return res.status(403).json({
            error: "Forbidden",
            message: "Access denied",
          });
        }

        return res.json({
          id: withdrawal.id,
          status: withdrawal.status,
          assetNetworkId: withdrawal.assetNetworkId,
          amount: withdrawal.amount.toString(),
          feeAmount: withdrawal.feeAmount.toString(),
          totalDebitAmount: withdrawal.totalDebitAmount.toString(),
          destinationAddress: withdrawal.destinationAddress,
          destinationTag: withdrawal.destinationTag,
          providerRef: withdrawal.providerRef,
          txid: withdrawal.txid,
          riskScore: withdrawal.riskScore,
          riskFlags: withdrawal.riskFlags,
          riskReason: withdrawal.riskReason,
          requestedAt: withdrawal.requestedAt.toISOString(),
          approvedAt: withdrawal.approvedAt?.toISOString(),
          broadcastAt: withdrawal.broadcastAt?.toISOString(),
          confirmedAt: withdrawal.confirmedAt?.toISOString(),
          failureCode: withdrawal.failureCode,
          failureMessage: withdrawal.failureMessage,
          attemptCount: withdrawal.attemptCount,
          createdAt: withdrawal.createdAt.toISOString(),
          updatedAt: withdrawal.updatedAt.toISOString(),
        });
      } finally {
        client.release();
      }
    } catch (error) {
      logger.error({ error }, "Failed to get withdrawal");
      return res.status(500).json({
        error: "Internal Server Error",
        message: "Failed to get withdrawal",
      });
    }
  });
}
