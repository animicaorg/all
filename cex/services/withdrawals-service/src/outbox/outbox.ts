/**
 * Outbox Pattern - Enqueue Operations
 */

import type { PoolClient } from "pg";

export type OutboxOperationType =
  | "APPLY_LEDGER_LOCK"
  | "SUBMIT_TO_BITGO"
  | "SUBMIT_TO_ANIMICA_NODE"
  | "SUBMIT_TO_BITCOIN_NODE"
  | "APPLY_LEDGER_BROADCAST"
  | "APPLY_LEDGER_CANCEL";

export interface OutboxOperation {
  id: string;
  withdrawalId: string;
  type: OutboxOperationType;
  payload: any;
  status: "PENDING" | "PROCESSING" | "COMPLETED" | "FAILED";
  attemptCount: number;
  nextRetryAt: Date;
  lastError: any;
  createdAt: Date;
  processedAt: Date | null;
  updatedAt: Date;
}

export function submissionOperationForProvider(provider: string): OutboxOperationType | null {
  if (provider === "BITGO") return "SUBMIT_TO_BITGO";
  if (provider === "ANIMICA_NODE") return "SUBMIT_TO_ANIMICA_NODE";
  if (provider === "BITCOIN_NODE") return "SUBMIT_TO_BITCOIN_NODE";
  return null;
}

/**
 * Enqueue an outbox operation
 */
export async function enqueueOperation(
  client: PoolClient,
  withdrawalId: string,
  type: OutboxOperationType,
  payload: any
): Promise<OutboxOperation> {
  const query = `
    INSERT INTO withdrawal_outbox (
      withdrawal_id, type, payload
    ) VALUES (
      $1, $2, $3
    )
    RETURNING *
  `;

  const result = await client.query(query, [
    withdrawalId,
    type,
    JSON.stringify(payload),
  ]);

  return mapRow(result.rows[0]);
}

export async function hasCompletedLedgerLock(
  client: PoolClient,
  withdrawalId: string
): Promise<boolean> {
  const result = await client.query(
    `SELECT 1
     FROM withdrawal_outbox
     WHERE withdrawal_id = $1
       AND type = 'APPLY_LEDGER_LOCK'
       AND status = 'COMPLETED'
     LIMIT 1`,
    [withdrawalId]
  );
  return (result.rowCount || 0) > 0;
}

export async function enqueueOperationIfMissing(
  client: PoolClient,
  withdrawalId: string,
  type: OutboxOperationType,
  payload: any
): Promise<OutboxOperation | null> {
  const existing = await client.query(
    `SELECT *
     FROM withdrawal_outbox
     WHERE withdrawal_id = $1
       AND type = $2
     ORDER BY created_at DESC
     LIMIT 1`,
    [withdrawalId, type]
  );
  if ((existing.rowCount || 0) > 0) {
    return null;
  }

  return enqueueOperation(client, withdrawalId, type, payload);
}

export async function enqueueSubmissionIfEligible(
  client: PoolClient,
  withdrawalId: string
): Promise<OutboxOperation | null> {
  const withdrawalResult = await client.query(
    `SELECT status, provider
     FROM withdrawals
     WHERE id = $1`,
    [withdrawalId]
  );
  const withdrawal = withdrawalResult.rows[0];
  if (!withdrawal || withdrawal.status !== "APPROVED") {
    return null;
  }

  if (!(await hasCompletedLedgerLock(client, withdrawalId))) {
    return null;
  }

  const type = submissionOperationForProvider(withdrawal.provider);
  if (!type) return null;

  return enqueueOperationIfMissing(client, withdrawalId, type, { withdrawalId });
}

/**
 * Get pending operations (with lock)
 */
export async function getPendingOperations(
  client: PoolClient,
  limit: number = 10
): Promise<OutboxOperation[]> {
  const query = `
    SELECT * FROM withdrawal_outbox
    WHERE status = 'PENDING'
      AND next_retry_at <= NOW()
      AND attempt_count < 10
    ORDER BY next_retry_at ASC
    LIMIT $1
    FOR UPDATE SKIP LOCKED
  `;

  const result = await client.query(query, [limit]);
  return result.rows.map(mapRow);
}

/**
 * Mark operation as processing
 */
export async function markProcessing(
  client: PoolClient,
  operationId: string
): Promise<void> {
  await client.query(
    `UPDATE withdrawal_outbox 
     SET status = 'PROCESSING', updated_at = NOW()
     WHERE id = $1`,
    [operationId]
  );
}

/**
 * Mark operation as completed
 */
export async function markCompleted(
  client: PoolClient,
  operationId: string
): Promise<void> {
  await client.query(
    `UPDATE withdrawal_outbox 
     SET status = 'COMPLETED', 
         processed_at = NOW(), 
         updated_at = NOW()
     WHERE id = $1`,
    [operationId]
  );
}

/**
 * Mark operation as failed and schedule retry
 */
export async function markFailed(
  client: PoolClient,
  operationId: string,
  error: any,
  retryDelayMs: number = 60000
): Promise<void> {
  await client.query(
    `UPDATE withdrawal_outbox 
     SET status = 'PENDING',
         attempt_count = attempt_count + 1,
         last_error = $2,
         next_retry_at = NOW() + INTERVAL '1 millisecond' * $3,
         updated_at = NOW()
     WHERE id = $1`,
    [operationId, JSON.stringify(error), retryDelayMs]
  );
}

/**
 * Mark operation as permanently failed
 */
export async function markPermanentlyFailed(
  client: PoolClient,
  operationId: string,
  error: any
): Promise<void> {
  await client.query(
    `UPDATE withdrawal_outbox 
     SET status = 'FAILED',
         attempt_count = attempt_count + 1,
         last_error = $2,
         updated_at = NOW()
     WHERE id = $1`,
    [operationId, JSON.stringify(error)]
  );
}

function mapRow(row: any): OutboxOperation {
  const payload =
    typeof row.payload === "string"
      ? JSON.parse(row.payload)
      : row.payload;

  return {
    id: row.id,
    withdrawalId: row.withdrawal_id,
    type: row.type,
    payload,
    status: row.status,
    attemptCount: row.attempt_count,
    nextRetryAt: row.next_retry_at,
    lastError: row.last_error,
    createdAt: row.created_at,
    processedAt: row.processed_at,
    updatedAt: row.updated_at,
  };
}
