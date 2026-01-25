/**
 * Finalization Pipeline
 */

import type { PoolClient } from "pg";
import type { Logger } from "pino";
import { WithdrawalsRepo, AuditRepo } from "../db/repositories/index.js";

/**
 * Finalize withdrawal to terminal state
 */
export async function finalizeWithdrawal(
  client: PoolClient,
  withdrawalId: string,
  finalStatus: "CONFIRMED" | "FAILED" | "CANCELED",
  logger: Logger
): Promise<{ success: boolean; message: string }> {
  const withdrawalsRepo = new WithdrawalsRepo(client);
  const auditRepo = new AuditRepo(client);

  // 1. Get withdrawal
  const withdrawal = await withdrawalsRepo.findById(withdrawalId);
  if (!withdrawal) {
    return { success: false, message: "Withdrawal not found" };
  }

  // 2. Check if already in terminal state
  if (["CONFIRMED", "FAILED", "CANCELED", "REJECTED"].includes(withdrawal.status)) {
    return {
      success: true,
      message: `Withdrawal already in terminal state: ${withdrawal.status}`,
    };
  }

  // 3. Update to final status
  await withdrawalsRepo.updateStatus(withdrawalId, finalStatus);

  // 4. Log audit event
  await auditRepo.log({
    eventType: `WITHDRAWAL_${finalStatus}`,
    withdrawalId,
    userId: withdrawal.userId,
    actorType: "SYSTEM",
    changes: {
      previousState: withdrawal.status,
      finalState: finalStatus,
    },
  });

  logger.info(
    {
      withdrawalId,
      previousStatus: withdrawal.status,
      finalStatus,
    },
    "Withdrawal finalized"
  );

  return {
    success: true,
    message: `Withdrawal finalized as ${finalStatus}`,
  };
}
