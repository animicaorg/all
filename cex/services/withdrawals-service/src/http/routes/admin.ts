/**
 * Admin Routes
 */

import type { Router } from "express";
import type { Pool } from "pg";
import type { Logger } from "pino";
import { z } from "zod";
import type { AdminRequest } from "../middleware/admin_auth.js";
import {
  WithdrawalsRepo,
  ApprovalsRepo,
  AuditRepo,
} from "../../db/repositories/index.js";
import { handleApproval, type ApprovalRequest } from "../../pipeline/approve.js";

const approvalSchema = z.object({
  action: z.enum(["APPROVE", "REJECT"]),
  reason: z.string().optional(),
});

export function setupAdminRoutes(
  router: Router,
  pool: Pool,
  logger: Logger
): void {
  /**
   * GET /admin/withdrawals - List all withdrawals
   */
  router.get("/admin/withdrawals", async (req: AdminRequest, res) => {
    try {
      const limit = Math.min(
        parseInt(req.query.limit as string) || 50,
        100
      );
      const offset = parseInt(req.query.offset as string) || 0;
      const status = req.query.status as string | undefined;
      const userId = req.query.userId as string | undefined;
      const assetNetworkId = req.query.assetNetworkId as string | undefined;

      const client = await pool.connect();
      try {
        const repo = new WithdrawalsRepo(client);
        const withdrawals = await repo.list({
          userId,
          status: status as any,
          assetNetworkId,
          limit,
          offset,
        });

        return res.json({
          withdrawals: withdrawals.map((w) => ({
            id: w.id,
            userId: w.userId,
            status: w.status,
            assetNetworkId: w.assetNetworkId,
            amount: w.amount.toString(),
            feeAmount: w.feeAmount.toString(),
            totalDebitAmount: w.totalDebitAmount.toString(),
            destinationAddress: w.destinationAddress,
            destinationTag: w.destinationTag,
            providerRef: w.providerRef,
            txid: w.txid,
            riskScore: w.riskScore,
            riskFlags: w.riskFlags,
            riskReason: w.riskReason,
            requestedAt: w.requestedAt.toISOString(),
            approvedAt: w.approvedAt?.toISOString(),
            broadcastAt: w.broadcastAt?.toISOString(),
            confirmedAt: w.confirmedAt?.toISOString(),
            failureCode: w.failureCode,
            failureMessage: w.failureMessage,
            attemptCount: w.attemptCount,
            createdAt: w.createdAt.toISOString(),
            updatedAt: w.updatedAt.toISOString(),
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
   * GET /admin/withdrawals/:id - Get withdrawal details
   */
  router.get("/admin/withdrawals/:id", async (req: AdminRequest, res) => {
    try {
      const withdrawalId = req.params.id;

      const client = await pool.connect();
      try {
        const withdrawalsRepo = new WithdrawalsRepo(client);
        const approvalsRepo = new ApprovalsRepo(client);
        const auditRepo = new AuditRepo(client);

        const withdrawal = await withdrawalsRepo.findById(withdrawalId);
        if (!withdrawal) {
          return res.status(404).json({
            error: "Not Found",
            message: "Withdrawal not found",
          });
        }

        const approvals = await approvalsRepo.listByWithdrawal(withdrawalId);
        const auditLog = await auditRepo.listByWithdrawal(withdrawalId, 50);

        return res.json({
          withdrawal: {
            id: withdrawal.id,
            userId: withdrawal.userId,
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
          },
          approvals: approvals.map((a) => ({
            id: a.id,
            approverId: a.approverId,
            approverRole: a.approverRole,
            action: a.action,
            reason: a.reason,
            createdAt: a.createdAt.toISOString(),
          })),
          auditLog: auditLog.map((e) => ({
            id: e.id,
            eventType: e.eventType,
            actorId: e.actorId,
            actorType: e.actorType,
            changes: e.changes,
            metadata: e.metadata,
            createdAt: e.createdAt.toISOString(),
          })),
        });
      } finally {
        client.release();
      }
    } catch (error) {
      logger.error({ error }, "Failed to get withdrawal details");
      return res.status(500).json({
        error: "Internal Server Error",
        message: "Failed to get withdrawal details",
      });
    }
  });

  /**
   * POST /admin/withdrawals/:id/approve - Approve withdrawal
   */
  router.post("/admin/withdrawals/:id/approve", async (req: AdminRequest, res) => {
    try {
      const withdrawalId = req.params.id;
      const admin = req.admin;

      if (!admin) {
        return res.status(401).json({
          error: "Unauthorized",
          message: "Admin not authenticated",
        });
      }

      const parseResult = approvalSchema.safeParse(req.body);
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

        const approvalRequest: ApprovalRequest = {
          withdrawalId,
          approverId: admin.id,
          approverRole: admin.role,
          action: data.action,
          reason: data.reason,
        };

        const result = await handleApproval(client, approvalRequest, logger);

        await client.query("COMMIT");

        if (!result.success) {
          return res.status(400).json({
            error: "Bad Request",
            message: result.message,
          });
        }

        return res.json({
          success: true,
          message: result.message,
          newStatus: result.newStatus,
        });
      } catch (error) {
        await client.query("ROLLBACK");
        throw error;
      } finally {
        client.release();
      }
    } catch (error) {
      logger.error({ error }, "Failed to process approval");
      return res.status(500).json({
        error: "Internal Server Error",
        message: "Failed to process approval",
      });
    }
  });

  /**
   * POST /admin/withdrawals/:id/reject - Reject withdrawal
   */
  router.post("/admin/withdrawals/:id/reject", async (req: AdminRequest, res) => {
    // Reuse approve endpoint logic with REJECT action
    req.body.action = "REJECT";
    return router.stack
      .find((layer) => layer.route?.path === "/admin/withdrawals/:id/approve")
      ?.route?.stack[0]?.handle(req, res, () => {});
  });

  /**
   * POST /admin/withdrawals/:id/cancel - Cancel withdrawal
   */
  router.post("/admin/withdrawals/:id/cancel", async (req: AdminRequest, res) => {
    try {
      const withdrawalId = req.params.id;

      const client = await pool.connect();
      try {
        await client.query("BEGIN");

        const repo = new WithdrawalsRepo(client);
        const withdrawal = await repo.findById(withdrawalId);

        if (!withdrawal) {
          return res.status(404).json({
            error: "Not Found",
            message: "Withdrawal not found",
          });
        }

        if (["CONFIRMED", "FAILED", "CANCELED", "REJECTED"].includes(withdrawal.status)) {
          return res.status(400).json({
            error: "Bad Request",
            message: `Cannot cancel withdrawal in ${withdrawal.status} status`,
          });
        }

        await repo.updateStatus(withdrawalId, "CANCELED", {
          failureCode: "ADMIN_CANCELED",
          failureMessage: req.body.reason || "Canceled by admin",
        });

        await client.query("COMMIT");

        return res.json({
          success: true,
          message: "Withdrawal canceled",
        });
      } catch (error) {
        await client.query("ROLLBACK");
        throw error;
      } finally {
        client.release();
      }
    } catch (error) {
      logger.error({ error }, "Failed to cancel withdrawal");
      return res.status(500).json({
        error: "Internal Server Error",
        message: "Failed to cancel withdrawal",
      });
    }
  });

  /**
   * POST /admin/withdrawals/:id/retry - Force retry
   */
  router.post("/admin/withdrawals/:id/retry", async (req: AdminRequest, res) => {
    try {
      const withdrawalId = req.params.id;

      const client = await pool.connect();
      try {
        await client.query("BEGIN");

        const repo = new WithdrawalsRepo(client);
        const withdrawal = await repo.findById(withdrawalId);

        if (!withdrawal) {
          return res.status(404).json({
            error: "Not Found",
            message: "Withdrawal not found",
          });
        }

        if (!["SIGNING", "BROADCAST", "FAILED"].includes(withdrawal.status)) {
          return res.status(400).json({
            error: "Bad Request",
            message: `Cannot retry withdrawal in ${withdrawal.status} status`,
          });
        }

        await repo.updateStatus(withdrawalId, "APPROVED", {
          nextRetryAt: new Date(),
        });

        await client.query("COMMIT");

        return res.json({
          success: true,
          message: "Withdrawal queued for retry",
        });
      } catch (error) {
        await client.query("ROLLBACK");
        throw error;
      } finally {
        client.release();
      }
    } catch (error) {
      logger.error({ error }, "Failed to retry withdrawal");
      return res.status(500).json({
        error: "Internal Server Error",
        message: "Failed to retry withdrawal",
      });
    }
  });
}
