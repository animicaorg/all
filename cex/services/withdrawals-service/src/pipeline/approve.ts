/**
 * Approval Pipeline
 */

import type { PoolClient } from "pg";
import type { Logger } from "pino";
import {
  WithdrawalsRepo,
  ApprovalsRepo,
  AuditRepo,
} from "../db/repositories/index.js";
import { enqueueOperation, enqueueSubmissionIfEligible } from "../outbox/outbox.js";

export interface ApprovalRequest {
  withdrawalId: string;
  approverId: string;
  approverRole: string;
  action: "APPROVE" | "REJECT";
  reason?: string;
}

/**
 * Handle approval/rejection of a withdrawal
 */
export async function handleApproval(
  client: PoolClient,
  request: ApprovalRequest,
  logger: Logger
): Promise<{ success: boolean; message: string; newStatus?: string }> {
  const withdrawalsRepo = new WithdrawalsRepo(client);
  const approvalsRepo = new ApprovalsRepo(client);
  const auditRepo = new AuditRepo(client);

  // 1. Get withdrawal
  const withdrawal = await withdrawalsRepo.findById(request.withdrawalId);
  if (!withdrawal) {
    return { success: false, message: "Withdrawal not found" };
  }

  // 2. Check if withdrawal is in a state that can be approved/rejected
  if (
    !["REQUESTED", "RISK_REVIEW"].includes(withdrawal.status)
  ) {
    return {
      success: false,
      message: `Cannot approve withdrawal in ${withdrawal.status} status`,
    };
  }

  // 3. Check if approver has already acted on this withdrawal
  const hasApproved = await approvalsRepo.hasApproved(
    request.withdrawalId,
    request.approverId
  );

  if (hasApproved) {
    return {
      success: false,
      message: "Approver has already acted on this withdrawal",
    };
  }

  // 4. Record approval/rejection
  await approvalsRepo.create({
    withdrawalId: request.withdrawalId,
    approverId: request.approverId,
    approverRole: request.approverRole,
    action: request.action,
    reason: request.reason,
  });

  // 5. Log audit event
  await auditRepo.log({
    eventType: request.action === "APPROVE" ? "WITHDRAWAL_APPROVED" : "WITHDRAWAL_REJECTED",
    withdrawalId: request.withdrawalId,
    userId: withdrawal.userId,
    actorId: request.approverId,
    actorType: "ADMIN",
    changes: {
      action: request.action,
      reason: request.reason,
    },
  });

  // 6. Handle rejection
  if (request.action === "REJECT") {
    await withdrawalsRepo.updateStatus(request.withdrawalId, "REJECTED", {
      failureCode: "ADMIN_REJECTED",
      failureMessage: request.reason || "Rejected by admin",
    });

    // Queue cancellation of ledger lock
    await enqueueOperation(client, request.withdrawalId, "APPLY_LEDGER_CANCEL", {
      withdrawalId: request.withdrawalId,
      userId: withdrawal.userId,
      reason: "REJECTED",
    });

    logger.info(
      { withdrawalId: request.withdrawalId, approverId: request.approverId },
      "Withdrawal rejected"
    );

    return {
      success: true,
      message: "Withdrawal rejected",
      newStatus: "REJECTED",
    };
  }

  // 7. Handle approval - check if we have enough approvals
  const approvalCount = await approvalsRepo.countApprovals(request.withdrawalId);
  
  // Get required approvals from risk decision (stored in risk_flags/metadata)
  // For now, use default of 1
  const requiredApprovals = 1; // TODO: Get this from withdrawal metadata

  if (approvalCount >= requiredApprovals) {
    // Threshold met - approve and queue submission
    await withdrawalsRepo.updateStatus(request.withdrawalId, "APPROVED");

    const submissionOperation = await enqueueSubmissionIfEligible(client, request.withdrawalId);

    logger.info(
      {
        withdrawalId: request.withdrawalId,
        approvalCount,
        requiredApprovals,
      },
      submissionOperation
        ? "Withdrawal approved and queued for submission"
        : "Withdrawal approved; submission waits for ledger lock"
    );

    return {
      success: true,
      message: "Withdrawal approved",
      newStatus: "APPROVED",
    };
  } else {
    logger.info(
      {
        withdrawalId: request.withdrawalId,
        approvalCount,
        requiredApprovals,
      },
      "Approval recorded, waiting for more approvals"
    );

    return {
      success: true,
      message: `Approval recorded (${approvalCount}/${requiredApprovals})`,
    };
  }
}
