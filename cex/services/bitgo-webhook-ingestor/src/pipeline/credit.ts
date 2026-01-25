/**
 * Credit Service - Interfaces with Ledger Service
 * 
 * Sends deposit credit commands to the ledger service via NATS
 */

import type { NatsConnection } from "nats";
import type { Logger } from "pino";
import type { DepositOutboxItem } from "../db/repositories/outbox_repo.js";

/**
 * Send deposit credit command to ledger service
 */
export async function sendDepositCredit(
  nats: NatsConnection,
  outboxItem: DepositOutboxItem,
  subject: string,
  logger: Logger
): Promise<void> {
  const payload = outboxItem.payload;

  logger.info(
    {
      depositId: payload.depositId,
      userId: payload.userId,
      assetId: payload.assetId,
      amountAtoms: payload.amountAtoms,
    },
    "Sending deposit credit to ledger service"
  );

  try {
    // Publish to NATS with the ledger service subject
    await nats.publish(
      subject,
      JSON.stringify({
        type: "DEPOSIT_CREDIT",
        idempotencyKey: payload.idempotencyKey,
        userId: payload.userId,
        assetId: payload.assetId,
        amountAtoms: payload.amountAtoms,
        source: payload.source,
        depositId: payload.depositId,
        timestamp: new Date().toISOString(),
      })
    );

    logger.info(
      { depositId: payload.depositId, idempotencyKey: payload.idempotencyKey },
      "Deposit credit sent successfully"
    );
  } catch (error) {
    logger.error(
      { error, depositId: payload.depositId },
      "Failed to send deposit credit"
    );
    throw error;
  }
}

/**
 * Send deposit credit via HTTP (alternative to NATS)
 */
export async function sendDepositCreditHttp(
  ledgerServiceUrl: string,
  outboxItem: DepositOutboxItem,
  logger: Logger
): Promise<void> {
  const payload = outboxItem.payload;

  logger.info(
    {
      depositId: payload.depositId,
      userId: payload.userId,
      assetId: payload.assetId,
      amountAtoms: payload.amountAtoms,
      ledgerServiceUrl,
    },
    "Sending deposit credit to ledger service via HTTP"
  );

  try {
    const response = await fetch(`${ledgerServiceUrl}/internal/deposit-credit`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        idempotencyKey: payload.idempotencyKey,
        userId: payload.userId,
        assetId: payload.assetId,
        amountAtoms: payload.amountAtoms,
        source: payload.source,
        depositId: payload.depositId,
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Ledger service returned ${response.status}: ${errorText}`);
    }

    const result = await response.json();
    
    logger.info(
      { depositId: payload.depositId, result },
      "Deposit credit sent successfully via HTTP"
    );
  } catch (error) {
    logger.error(
      { error, depositId: payload.depositId },
      "Failed to send deposit credit via HTTP"
    );
    throw error;
  }
}
