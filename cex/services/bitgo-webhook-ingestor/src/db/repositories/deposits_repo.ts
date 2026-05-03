/**
 * Deposits Repository
 */

import type { PoolClient } from "pg";
import type { DepositObservation, DepositStatus } from "../../bitgo/types.js";

export interface Deposit {
  id: string;
  userId: string | null;
  assetNetworkId: string;
  provider: string;
  providerEventId: string | null;
  walletId: string;
  transferId: string | null;
  txid: string;
  vout: string | null;
  address: string;
  tag: string | null;
  amountAtoms: bigint;
  confirmations: number;
  confirmationsRequired: number;
  blockHeight: number | null;
  blockHash: string | null;
  status: DepositStatus;
  detectedAt: Date;
  confirmedAt: Date | null;
  creditedAt: Date | null;
  unassigned: boolean;
  riskHold: boolean;
  riskReason: string | null;
  raw: any;
  metadata: any;
  createdAt: Date;
  updatedAt: Date;
}

export class DepositsRepo {
  constructor(private client: PoolClient) {}

  /**
   * Upsert deposit by unique keys
   */
  async upsert(
    observation: DepositObservation,
    assetNetworkId: string,
    userId: string | null,
    confirmationsRequired: number
  ): Promise<Deposit> {
    const normalizedTag = observation.tag || "";
    const normalizedVout = observation.voutOrLogIndex || "0";
    const query = `
      INSERT INTO deposits (
        user_id, asset_network_id, provider, provider_event_id,
        wallet_id, transfer_id, txid, vout, address, tag,
        amount_atoms, confirmations, confirmations_required,
        block_height, block_hash, status, detected_at,
        unassigned, raw, metadata
      ) VALUES (
        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
        $11, $12, $13, $14, $15, $16, $17, $18, $19, $20
      )
      ON CONFLICT (asset_network_id, txid, address, tag, vout)
      DO UPDATE SET
        confirmations = GREATEST(deposits.confirmations, EXCLUDED.confirmations),
        block_height = COALESCE(EXCLUDED.block_height, deposits.block_height),
        block_hash = COALESCE(EXCLUDED.block_hash, deposits.block_hash),
        status = CASE
          WHEN EXCLUDED.status = 'FAILED' THEN 'FAILED'
          WHEN (EXCLUDED.status = 'CONFIRMED' OR EXCLUDED.confirmations >= deposits.confirmations_required)
            AND deposits.status = 'DETECTED' THEN 'CONFIRMED'
          ELSE deposits.status
        END,
        confirmed_at = CASE
          WHEN (EXCLUDED.status = 'CONFIRMED' OR EXCLUDED.confirmations >= deposits.confirmations_required)
            AND deposits.status = 'DETECTED' THEN NOW()
          ELSE deposits.confirmed_at
        END,
        updated_at = NOW()
      RETURNING *
    `;

    const values = [
      userId,
      assetNetworkId,
      observation.provider,
      observation.providerEventId || null,
      observation.walletId,
      observation.transferId || null,
      observation.txid,
      normalizedVout,
      observation.address,
      normalizedTag,
      observation.amountAtoms.toString(),
      observation.confirmations,
      confirmationsRequired,
      observation.blockHeight || null,
      observation.blockHash || null,
      observation.status,
      observation.observedAt,
      userId === null, // unassigned if no user
      JSON.stringify(observation.raw),
      JSON.stringify({}),
    ];

    const result = await this.client.query(query, values);
    return this.mapRow(result.rows[0]);
  }

  /**
   * Get deposit by ID
   */
  async getById(id: string): Promise<Deposit | null> {
    const result = await this.client.query(
      "SELECT * FROM deposits WHERE id = $1",
      [id]
    );
    return result.rows.length > 0 ? this.mapRow(result.rows[0]) : null;
  }

  /**
   * Get deposits by status
   */
  async getByStatus(
    status: DepositStatus,
    limit: number = 100
  ): Promise<Deposit[]> {
    const result = await this.client.query(
      `SELECT * FROM deposits
       WHERE status = $1
       ORDER BY created_at DESC
       LIMIT $2`,
      [status, limit]
    );
    return result.rows.map(this.mapRow);
  }

  /**
   * Get deposits needing confirmation update (old DETECTED deposits)
   */
  async getNeedingConfirmationUpdate(
    minutesOld: number,
    limit: number = 50
  ): Promise<Deposit[]> {
    const result = await this.client.query(
      `SELECT * FROM deposits
       WHERE status = 'DETECTED'
         AND created_at < NOW() - INTERVAL '1 minute' * $1
       ORDER BY created_at ASC
       LIMIT $2`,
      [minutesOld, limit]
    );
    return result.rows.map(this.mapRow);
  }

  /**
   * Update deposit status
   */
  async updateStatus(
    id: string,
    status: DepositStatus,
    timestampField?: "confirmed_at" | "credited_at"
  ): Promise<void> {
    const setClause = timestampField
      ? `status = $2, ${timestampField} = NOW(), updated_at = NOW()`
      : `status = $2, updated_at = NOW()`;

    await this.client.query(
      `UPDATE deposits SET ${setClause} WHERE id = $1`,
      [id, status]
    );
  }

  /**
   * Update confirmations
   */
  async updateConfirmations(
    id: string,
    confirmations: number,
    blockHeight?: number,
    blockHash?: string
  ): Promise<Deposit> {
    const query = `
      UPDATE deposits
      SET confirmations = GREATEST(confirmations, $2),
          block_height = COALESCE($3, block_height),
          block_hash = COALESCE($4, block_hash),
          status = CASE
            WHEN $2 >= confirmations_required AND status = 'DETECTED' THEN 'CONFIRMED'
            ELSE status
          END,
          confirmed_at = CASE
            WHEN $2 >= confirmations_required AND status = 'DETECTED' THEN NOW()
            ELSE confirmed_at
          END,
          updated_at = NOW()
      WHERE id = $1
      RETURNING *
    `;

    const result = await this.client.query(query, [
      id,
      confirmations,
      blockHeight || null,
      blockHash || null,
    ]);

    return this.mapRow(result.rows[0]);
  }

  /**
   * Set risk hold
   */
  async setRiskHold(id: string, reason: string): Promise<void> {
    await this.client.query(
      `UPDATE deposits
       SET risk_hold = true, risk_reason = $2, updated_at = NOW()
       WHERE id = $1`,
      [id, reason]
    );
  }

  /**
   * Map database row to Deposit
   */
  private mapRow(row: any): Deposit {
    return {
      id: row.id,
      userId: row.user_id,
      assetNetworkId: row.asset_network_id,
      provider: row.provider,
      providerEventId: row.provider_event_id,
      walletId: row.wallet_id,
      transferId: row.transfer_id,
      txid: row.txid,
      vout: row.vout,
      address: row.address,
      tag: row.tag,
      amountAtoms: BigInt(row.amount_atoms),
      confirmations: row.confirmations,
      confirmationsRequired: row.confirmations_required,
      blockHeight: row.block_height,
      blockHash: row.block_hash,
      status: row.status,
      detectedAt: row.detected_at,
      confirmedAt: row.confirmed_at,
      creditedAt: row.credited_at,
      unassigned: row.unassigned,
      riskHold: row.risk_hold,
      riskReason: row.risk_reason,
      raw: row.raw,
      metadata: row.metadata,
      createdAt: row.created_at,
      updatedAt: row.updated_at,
    };
  }
}
