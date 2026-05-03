/**
 * Withdrawals Repository
 */

import type { PoolClient } from "pg";

export type WithdrawalStatus = 
  | "REQUESTED" 
  | "RISK_REVIEW" 
  | "APPROVED" 
  | "SIGNING" 
  | "BROADCAST" 
  | "CONFIRMED"
  | "CANCELED"
  | "REJECTED"
  | "FAILED";

export interface Withdrawal {
  id: string;
  userId: string;
  assetNetworkId: string;
  destinationAddress: string;
  destinationTag: string | null;
  amount: bigint;
  feeAmount: bigint;
  totalDebitAmount: bigint;
  status: WithdrawalStatus;
  idempotencyKey: string;
  clientWithdrawalId: string | null;
  provider: string;
  providerRef: string | null;
  txid: string | null;
  riskScore: number | null;
  riskFlags: string[];
  riskReason: string | null;
  requestedAt: Date;
  approvedAt: Date | null;
  broadcastAt: Date | null;
  confirmedAt: Date | null;
  failureCode: string | null;
  failureMessage: string | null;
  attemptCount: number;
  nextRetryAt: Date | null;
  createdAt: Date;
  updatedAt: Date;
}

export interface CreateWithdrawalParams {
  userId: string;
  assetNetworkId: string;
  destinationAddress: string;
  destinationTag?: string;
  amount: bigint;
  feeAmount: bigint;
  provider?: string;
  idempotencyKey: string;
  clientWithdrawalId?: string;
  riskScore?: number;
  riskFlags?: string[];
  riskReason?: string;
}

export interface ListWithdrawalsFilters {
  userId?: string;
  status?: WithdrawalStatus;
  assetNetworkId?: string;
  limit?: number;
  offset?: number;
}

export class WithdrawalsRepo {
  constructor(private client: PoolClient) {}

  async create(params: CreateWithdrawalParams): Promise<Withdrawal> {
    const query = `
      INSERT INTO withdrawals (
        user_id, asset_network_id, destination_address, destination_tag,
        amount, fee_amount, total_debit_amount, idempotency_key, client_withdrawal_id,
        provider, risk_score, risk_flags, risk_reason
      ) VALUES (
        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
      )
      RETURNING *
    `;

    const totalDebitAmount = params.amount + params.feeAmount;

    const values = [
      params.userId,
      params.assetNetworkId,
      params.destinationAddress,
      params.destinationTag || null,
      params.amount.toString(),
      params.feeAmount.toString(),
      totalDebitAmount.toString(),
      params.idempotencyKey,
      params.clientWithdrawalId || null,
      params.provider || "BITGO",
      params.riskScore || null,
      JSON.stringify(params.riskFlags || []),
      params.riskReason || null,
    ];

    const result = await this.client.query(query, values);
    return this.mapRow(result.rows[0]);
  }

  async findById(id: string): Promise<Withdrawal | null> {
    const result = await this.client.query(
      "SELECT * FROM withdrawals WHERE id = $1",
      [id]
    );
    return result.rows.length > 0 ? this.mapRow(result.rows[0]) : null;
  }

  async findByProviderRef(providerRef: string): Promise<Withdrawal | null> {
    const result = await this.client.query(
      "SELECT * FROM withdrawals WHERE provider_ref = $1",
      [providerRef]
    );
    return result.rows.length > 0 ? this.mapRow(result.rows[0]) : null;
  }

  async updateStatus(
    id: string,
    status: WithdrawalStatus,
    updates?: {
      providerRef?: string;
      txid?: string;
      failureCode?: string;
      failureMessage?: string;
      incrementAttempt?: boolean;
      nextRetryAt?: Date;
    }
  ): Promise<Withdrawal> {
    const setClauses: string[] = ["status = $2", "updated_at = NOW()"];
    const values: any[] = [id, status];
    let paramIndex = 3;

    // Set timestamp based on status
    if (status === "APPROVED") {
      setClauses.push("approved_at = NOW()");
    } else if (status === "BROADCAST") {
      setClauses.push("broadcast_at = NOW()");
    } else if (status === "CONFIRMED") {
      setClauses.push("confirmed_at = NOW()");
    }

    if (updates?.providerRef !== undefined) {
      setClauses.push(`provider_ref = $${paramIndex++}`);
      values.push(updates.providerRef);
    }

    if (updates?.txid !== undefined) {
      setClauses.push(`txid = $${paramIndex++}`);
      values.push(updates.txid);
    }

    if (updates?.failureCode !== undefined) {
      setClauses.push(`failure_code = $${paramIndex++}`);
      values.push(updates.failureCode);
    }

    if (updates?.failureMessage !== undefined) {
      setClauses.push(`failure_message = $${paramIndex++}`);
      values.push(updates.failureMessage);
    }

    if (updates?.incrementAttempt) {
      setClauses.push("attempt_count = attempt_count + 1");
    }

    if (updates?.nextRetryAt !== undefined) {
      setClauses.push(`next_retry_at = $${paramIndex++}`);
      values.push(updates.nextRetryAt);
    }

    const query = `
      UPDATE withdrawals
      SET ${setClauses.join(", ")}
      WHERE id = $1
      RETURNING *
    `;

    const result = await this.client.query(query, values);
    return this.mapRow(result.rows[0]);
  }

  async list(filters: ListWithdrawalsFilters = {}): Promise<Withdrawal[]> {
    const whereClauses: string[] = [];
    const values: any[] = [];
    let paramIndex = 1;

    if (filters.userId) {
      whereClauses.push(`user_id = $${paramIndex++}`);
      values.push(filters.userId);
    }

    if (filters.status) {
      whereClauses.push(`status = $${paramIndex++}`);
      values.push(filters.status);
    }

    if (filters.assetNetworkId) {
      whereClauses.push(`asset_network_id = $${paramIndex++}`);
      values.push(filters.assetNetworkId);
    }

    const whereClause = whereClauses.length > 0 
      ? `WHERE ${whereClauses.join(" AND ")}` 
      : "";

    const limit = filters.limit || 50;
    const offset = filters.offset || 0;

    const query = `
      SELECT * FROM withdrawals
      ${whereClause}
      ORDER BY created_at DESC
      LIMIT $${paramIndex++} OFFSET $${paramIndex}
    `;

    values.push(limit, offset);

    const result = await this.client.query(query, values);
    return result.rows.map(this.mapRow);
  }

  async getPendingForRetry(limit: number = 50): Promise<Withdrawal[]> {
    const query = `
      SELECT * FROM withdrawals
      WHERE status IN ('SIGNING', 'BROADCAST')
        AND next_retry_at IS NOT NULL
        AND next_retry_at <= NOW()
        AND attempt_count < 10
      ORDER BY next_retry_at ASC
      LIMIT $1
    `;

    const result = await this.client.query(query, [limit]);
    return result.rows.map(this.mapRow);
  }

  private mapRow(row: any): Withdrawal {
    return {
      id: row.id,
      userId: row.user_id,
      assetNetworkId: row.asset_network_id,
      destinationAddress: row.destination_address,
      destinationTag: row.destination_tag,
      amount: BigInt(row.amount),
      feeAmount: BigInt(row.fee_amount),
      totalDebitAmount: BigInt(row.total_debit_amount),
      status: row.status,
      idempotencyKey: row.idempotency_key,
      clientWithdrawalId: row.client_withdrawal_id,
      provider: row.provider,
      providerRef: row.provider_ref,
      txid: row.txid,
      riskScore: row.risk_score ? parseFloat(row.risk_score) : null,
      riskFlags: row.risk_flags || [],
      riskReason: row.risk_reason,
      requestedAt: row.requested_at,
      approvedAt: row.approved_at,
      broadcastAt: row.broadcast_at,
      confirmedAt: row.confirmed_at,
      failureCode: row.failure_code,
      failureMessage: row.failure_message,
      attemptCount: row.attempt_count,
      nextRetryAt: row.next_retry_at,
      createdAt: row.created_at,
      updatedAt: row.updated_at,
    };
  }
}
