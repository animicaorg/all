/**
 * Deposit Outbox Repository
 */

import type { PoolClient } from "pg";

export interface DepositOutboxItem {
  id: string;
  depositId: string;
  idempotencyKey: string;
  payload: any;
  createdAt: Date;
  processedAt: Date | null;
  retryCount: number;
  lastRetryAt: Date | null;
  lastError: any;
}

export class OutboxRepo {
  constructor(private client: PoolClient) {}

  /**
   * Create outbox entry for deposit credit
   */
  async create(
    depositId: string,
    userId: string,
    assetId: string,
    amountAtoms: bigint,
    source: Record<string, unknown>
  ): Promise<DepositOutboxItem> {
    const idempotencyKey = `deposit:${depositId}`;
    const payload = {
      idempotencyKey,
      userId,
      assetId,
      amountAtoms: amountAtoms.toString(),
      source,
      depositId,
    };

    const query = `
      INSERT INTO deposit_outbox (
        deposit_id, idempotency_key, payload
      ) VALUES ($1, $2, $3)
      ON CONFLICT (idempotency_key) DO NOTHING
      RETURNING *
    `;

    const result = await this.client.query(query, [
      depositId,
      idempotencyKey,
      JSON.stringify(payload),
    ]);

    if (result.rows.length === 0) {
      // Already exists, fetch it
      const existing = await this.client.query(
        "SELECT * FROM deposit_outbox WHERE idempotency_key = $1",
        [idempotencyKey]
      );
      return this.mapRow(existing.rows[0]);
    }

    return this.mapRow(result.rows[0]);
  }

  /**
   * Get pending outbox items
   */
  async getPending(limit: number = 100): Promise<DepositOutboxItem[]> {
    const query = `
      SELECT deposit_outbox.*
      FROM deposit_outbox
      JOIN deposits ON deposits.id = deposit_outbox.deposit_id
      WHERE deposit_outbox.processed_at IS NULL
        AND deposits.provider = 'BITGO'
        AND (
          deposit_outbox.last_retry_at IS NULL
          OR deposit_outbox.last_retry_at < NOW() - INTERVAL '30 seconds'
        )
      ORDER BY deposit_outbox.created_at ASC
      LIMIT $1
    `;

    const result = await this.client.query(query, [limit]);
    return result.rows.map(this.mapRow);
  }

  /**
   * Mark item as processed
   */
  async markProcessed(id: string): Promise<void> {
    await this.client.query(
      "UPDATE deposit_outbox SET processed_at = NOW(), last_error = NULL WHERE id = $1",
      [id]
    );
  }

  /**
   * Record retry attempt
   */
  async recordRetry(id: string, error: any): Promise<void> {
    await this.client.query(
      `UPDATE deposit_outbox
       SET retry_count = retry_count + 1,
           last_retry_at = NOW(),
           last_error = $2
       WHERE id = $1`,
      [id, JSON.stringify(error)]
    );
  }

  /**
   * Map database row to DepositOutboxItem
   */
  private mapRow(row: any): DepositOutboxItem {
    return {
      id: row.id,
      depositId: row.deposit_id,
      idempotencyKey: row.idempotency_key,
      payload: row.payload,
      createdAt: row.created_at,
      processedAt: row.processed_at,
      retryCount: row.retry_count,
      lastRetryAt: row.last_retry_at,
      lastError: row.last_error,
    };
  }
}
