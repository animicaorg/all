/**
 * Withdrawals Repository for Animica
 * 
 * Uses the shared withdrawals table with provider="ANIMICA_NODE"
 */

import type { Pool, PoolClient } from "pg";

export type WithdrawalStatus =
  | "REQUESTED"
  | "APPROVED"
  | "SIGNING"
  | "BROADCAST"
  | "CONFIRMED"
  | "FAILED"
  | "CANCELED";

export interface Withdrawal {
  id: string;
  user_id: string;
  asset_network_id: string;
  destination_address: string;
  destination_tag: string | null;
  amount: string;
  fee_amount: string;
  total_debit_amount: string;
  status: WithdrawalStatus;
  idempotency_key: string;
  provider: string;
  provider_ref: string | null;
  txid: string | null;
  nonce: number | null;
  raw_tx: string | null;
  broadcast_at: Date | null;
  confirmed_at: Date | null;
  failure_code: string | null;
  failure_message: string | null;
  attempt_count: number;
  next_retry_at: Date | null;
  created_at: Date;
  updated_at: Date;
}

export class WithdrawalsRepository {
  constructor(private pool: Pool) {}

  /**
   * Get pending withdrawals for this provider
   */
  async getPendingForProvider(
    provider: string = "ANIMICA_NODE",
    limit: number = 50
  ): Promise<Withdrawal[]> {
    const query = `
      SELECT * FROM withdrawals
      WHERE provider = $1
        AND status IN ('APPROVED', 'SIGNING', 'BROADCAST')
        AND (next_retry_at IS NULL OR next_retry_at <= NOW())
      ORDER BY created_at ASC
      LIMIT $2
    `;

    const result = await this.pool.query(query, [provider, limit]);
    return result.rows;
  }

  /**
   * Get withdrawal by ID
   */
  async getById(id: string): Promise<Withdrawal | null> {
    const query = `SELECT * FROM withdrawals WHERE id = $1`;
    const result = await this.pool.query(query, [id]);
    return result.rows[0] || null;
  }

  /**
   * Update withdrawal status
   */
  async updateStatus(
    id: string,
    status: WithdrawalStatus,
    updates: {
      txid?: string;
      provider_ref?: string;
      failure_code?: string;
      failure_message?: string;
      broadcast_at?: Date;
      confirmed_at?: Date;
    } = {},
    client?: PoolClient
  ): Promise<void> {
    const executor = client || this.pool;

    const fields: string[] = ["status = $2", "updated_at = NOW()"];
    const values: any[] = [id, status];
    let paramIndex = 3;

    if (updates.txid !== undefined) {
      fields.push(`txid = $${paramIndex}`);
      values.push(updates.txid);
      paramIndex++;
    }

    if (updates.provider_ref !== undefined) {
      fields.push(`provider_ref = $${paramIndex}`);
      values.push(updates.provider_ref);
      paramIndex++;
    }

    if (updates.failure_code !== undefined) {
      fields.push(`failure_code = $${paramIndex}`);
      values.push(updates.failure_code);
      paramIndex++;
    }

    if (updates.failure_message !== undefined) {
      fields.push(`failure_message = $${paramIndex}`);
      values.push(updates.failure_message);
      paramIndex++;
    }

    if (updates.broadcast_at !== undefined) {
      fields.push(`broadcast_at = $${paramIndex}`);
      values.push(updates.broadcast_at);
      paramIndex++;
    }

    if (updates.confirmed_at !== undefined) {
      fields.push(`confirmed_at = $${paramIndex}`);
      values.push(updates.confirmed_at);
      paramIndex++;
    }

    const query = `
      UPDATE withdrawals
      SET ${fields.join(", ")}
      WHERE id = $1
    `;

    await executor.query(query, values);
  }

  /**
   * Update transaction details
   */
  async updateTxDetails(
    id: string,
    txid: string,
    nonce: number,
    rawTx: string,
    client?: PoolClient
  ): Promise<void> {
    const executor = client || this.pool;

    const query = `
      UPDATE withdrawals
      SET txid = $2, nonce = $3, raw_tx = $4, updated_at = NOW()
      WHERE id = $1
    `;

    await executor.query(query, [id, txid, nonce, rawTx]);
  }

  /**
   * Increment attempt count and set retry time
   */
  async incrementAttempt(
    id: string,
    retryDelaySeconds: number,
    client?: PoolClient
  ): Promise<void> {
    const executor = client || this.pool;

    const query = `
      UPDATE withdrawals
      SET 
        attempt_count = attempt_count + 1,
        next_retry_at = NOW() + INTERVAL '${retryDelaySeconds} seconds',
        updated_at = NOW()
      WHERE id = $1
    `;

    await executor.query(query, [id]);
  }
}
