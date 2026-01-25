/**
 * Outbox Pattern - Enqueue Operations
 */

import type { PoolClient } from "pg";

export type OutboxOperationType =
  | "APPLY_LEDGER_LOCK"
  | "SUBMIT_TO_BITGO"
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
         last_error = $2,
         updated_at = NOW()
     WHERE id = $1`,
    [operationId, JSON.stringify(error)]
  );
}

function mapRow(row: any): OutboxOperation {
  return {
    id: row.id,
    withdrawalId: row.withdrawal_id,
    type: row.type,
    payload: row.payload,
    status: row.status,
    attemptCount: row.attempt_count,
    nextRetryAt: row.next_retry_at,
    lastError: row.last_error,
    createdAt: row.created_at,
    processedAt: row.processed_at,
    updatedAt: row.updated_at,
  };
}
