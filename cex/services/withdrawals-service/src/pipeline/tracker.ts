/**
 * Webhook Tracker Pipeline
 */

import type { PoolClient } from "pg";
import type { Logger } from "pino";
import { WithdrawalsRepo, AuditRepo } from "../db/repositories/index.js";
import type { WithdrawalObservation } from "../bitgo/types.js";
import type { OutboxOperationType } from "../outbox/outbox.js";
import { enqueueOperation } from "../outbox/outbox.js";

/**
 * Process BitGo webhook observation
 */
export async function processWebhook(
  client: PoolClient,
  observation: WithdrawalObservation,
  logger: Logger
): Promise<{ success: boolean; message: string }> {
  const withdrawalsRepo = new WithdrawalsRepo(client);
  const auditRepo = new AuditRepo(client);

  // 1. Find withdrawal by provider reference
  const withdrawal = await withdrawalsRepo.findByProviderRef(
    observation.providerRef
  );

  if (!withdrawal) {
    logger.warn(
      { providerRef: observation.providerRef },
      "Received webhook for unknown withdrawal"
    );
    return { success: false, message: "Withdrawal not found" };
  }

  logger.info(
    {
      withdrawalId: withdrawal.id,
      providerRef: observation.providerRef,
      currentState: withdrawal.status,
      newState: observation.state,
    },
    "Processing withdrawal webhook"
  );

  // 2. Apply state transition based on observation
  let updated = false;
  let queuedOperation: OutboxOperationType | null = null;

  switch (observation.state) {
    case "SIGNING":
      // Only update if current state is earlier
      if (withdrawal.status === "APPROVED") {
        await withdrawalsRepo.updateStatus(withdrawal.id, "SIGNING", {
          providerRef: observation.providerRef,
        });
        updated = true;
      }
      break;

    case "BROADCAST":
      // Update to broadcast if not already confirmed
      if (["APPROVED", "SIGNING"].includes(withdrawal.status)) {
        await withdrawalsRepo.updateStatus(withdrawal.id, "BROADCAST", {
          providerRef: observation.providerRef,
          txid: observation.txid || undefined,
        });
        updated = true;

        // Queue ledger operation to move funds from locked to system
        queuedOperation = "APPLY_LEDGER_BROADCAST";
        await enqueueOperation(client, withdrawal.id, queuedOperation, {
          withdrawalId: withdrawal.id,
          userId: withdrawal.userId,
          txid: observation.txid,
        });
      }
      break;

    case "CONFIRMED":
      // Update to confirmed
      if (withdrawal.status !== "CONFIRMED") {
        await withdrawalsRepo.updateStatus(withdrawal.id, "CONFIRMED", {
          providerRef: observation.providerRef,
          txid: observation.txid || undefined,
        });
        updated = true;
      }
      break;

    case "FAILED":
      // Mark as failed
      if (!["FAILED", "CONFIRMED"].includes(withdrawal.status)) {
        await withdrawalsRepo.updateStatus(withdrawal.id, "FAILED", {
          providerRef: observation.providerRef,
          failureCode: "BITGO_FAILED",
          failureMessage: "Transfer failed on BitGo",
        });
        updated = true;

        // Queue ledger cancellation to release locked funds
        queuedOperation = "APPLY_LEDGER_CANCEL";
        await enqueueOperation(client, withdrawal.id, queuedOperation, {
          withdrawalId: withdrawal.id,
          userId: withdrawal.userId,
          reason: "FAILED",
        });
      }
      break;
  }

  // 3. Log audit event if updated
  if (updated) {
    await auditRepo.log({
      eventType: "WITHDRAWAL_STATE_UPDATED",
      withdrawalId: withdrawal.id,
      userId: withdrawal.userId,
      actorType: "SYSTEM",
      changes: {
        previousState: withdrawal.status,
        newState: observation.state,
        providerRef: observation.providerRef,
        txid: observation.txid,
      },
      metadata: {
        queuedOperation,
        raw: observation.raw,
      },
    });

    logger.info(
      {
        withdrawalId: withdrawal.id,
        newState: observation.state,
        queuedOperation,
      },
      "Withdrawal state updated from webhook"
    );
  } else {
    logger.debug(
      { withdrawalId: withdrawal.id, state: observation.state },
      "No state update needed"
    );
  }

  return { success: true, message: "Webhook processed" };
}
