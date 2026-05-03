/**
 * Deposits Repository
 * 
 * Manages deposit records using existing deposits table
 */

import type { Pool, PoolClient } from "pg";
import type { Logger } from "pino";

export interface Deposit {
  id: string;
  user_id: string | null;
  asset_network_id: string;
  provider: string;
  provider_event_id: string | null;
  wallet_id: string;
  transfer_id: string | null;
  txid: string;
  vout: string | null;
  address: string;
  tag: string | null;
  amount_atoms: string;
  confirmations: number;
  confirmations_required: number;
  block_height: number | null;
  block_hash: string | null;
  status: string;
  detected_at: Date;
  confirmed_at: Date | null;
  credited_at: Date | null;
  unassigned: boolean;
  risk_hold: boolean;
  risk_reason: string | null;
  raw: any;
  metadata: any;
  created_at: Date;
  updated_at: Date;
}

export interface CreateDepositParams {
  userId: string | null;
  assetNetworkId: string;
  walletId: string;
  txid: string;
  vout: string | null;
  address: string;
  tag: string | null;
  amountAtoms: string;
  confirmationsRequired: number;
  blockHeight: number;
  blockHash: string;
}

export class DepositsRepository {
  constructor(
    private pool: Pool,
    private logger: Logger
  ) {}
  
  /**
   * Upsert deposit (idempotent)
   */
  async upsert(params: CreateDepositParams, client?: PoolClient): Promise<Deposit> {
    const executor = client || this.pool;
    const normalizedTag = params.tag || "";
    const normalizedVout = params.vout || "0";
    
    const query = `
      INSERT INTO deposits (
        user_id, asset_network_id, provider, wallet_id, txid, vout, address, tag,
        amount_atoms, confirmations, confirmations_required, block_height, block_hash,
        status, unassigned
      )
      VALUES (
        $1, $2, 'ANIMICA_NODE', $3, $4, $5, $6, $7,
        $8, 0, $9, $10, $11,
        'DETECTED', $12
      )
      ON CONFLICT (asset_network_id, txid, address, tag, vout)
      DO UPDATE SET
        confirmations = deposits.confirmations,
        block_height = EXCLUDED.block_height,
        block_hash = EXCLUDED.block_hash,
        updated_at = NOW()
      RETURNING *
    `;
    
    const values = [
      params.userId,
      params.assetNetworkId,
      params.walletId,
      params.txid,
      normalizedVout,
      params.address,
      normalizedTag,
      params.amountAtoms,
      params.confirmationsRequired,
      params.blockHeight,
      params.blockHash,
      params.userId === null, // unassigned
    ];
    
    const result = await executor.query(query, values);
    return result.rows[0];
  }

  /**
   * Queue a confirmed deposit for ledger credit.
   */
  async createCreditOutbox(deposit: Deposit, client?: PoolClient): Promise<void> {
    if (!deposit.user_id || deposit.unassigned || deposit.risk_hold) return;

    const executor = client || this.pool;
    const assetResult = await executor.query(
      `SELECT assets.symbol
       FROM asset_networks
       JOIN assets ON assets.id = asset_networks.asset_id
       WHERE asset_networks.id = $1`,
      [deposit.asset_network_id]
    );
    const assetSymbol = assetResult.rows[0]?.symbol;
    if (!assetSymbol) return;

    const idempotencyKey = `deposit:${deposit.id}`;
    const payload = {
      idempotencyKey,
      userId: deposit.user_id,
      assetId: assetSymbol,
      amountAtoms: deposit.amount_atoms,
      source: {
        provider: deposit.provider,
        txid: deposit.txid,
        address: deposit.address,
        network: "ANIMICA",
      },
      depositId: deposit.id,
    };

    await executor.query(
      `INSERT INTO deposit_outbox (deposit_id, idempotency_key, payload)
       VALUES ($1, $2, $3)
       ON CONFLICT (idempotency_key) DO NOTHING`,
      [deposit.id, idempotencyKey, JSON.stringify(payload)]
    );
  }
  
  /**
   * Update confirmations
   */
  async updateConfirmations(
    depositId: string,
    confirmations: number,
    client?: PoolClient
  ): Promise<void> {
    const executor = client || this.pool;
    
    const query = `
      UPDATE deposits
      SET confirmations = $2, updated_at = NOW()
      WHERE id = $1 AND confirmations < $2
    `;
    
    await executor.query(query, [depositId, confirmations]);
  }
  
  /**
   * Update status
   */
  async updateStatus(
    depositId: string,
    status: string,
    client?: PoolClient
  ): Promise<void> {
    const executor = client || this.pool;
    
    const query = `
      UPDATE deposits
      SET status = $2, updated_at = NOW(),
          confirmed_at = CASE WHEN $2 = 'CONFIRMED' AND confirmed_at IS NULL THEN NOW() ELSE confirmed_at END,
          credited_at = CASE WHEN $2 = 'CREDITED' AND credited_at IS NULL THEN NOW() ELSE credited_at END
      WHERE id = $1
    `;
    
    await executor.query(query, [depositId, status]);
  }
  
  /**
   * Get deposits by status
   */
  async getByStatus(
    assetNetworkId: string,
    status: string,
    limit: number = 100
  ): Promise<Deposit[]> {
    const query = `
      SELECT * FROM deposits
      WHERE asset_network_id = $1 AND status = $2
      ORDER BY created_at ASC
      LIMIT $3
    `;
    
    const result = await this.pool.query(query, [assetNetworkId, status, limit]);
    return result.rows;
  }
  
  /**
   * Get deposits in height range (for reorg handling)
   */
  async getByHeightRange(
    assetNetworkId: string,
    fromHeight: number,
    toHeight: number
  ): Promise<Deposit[]> {
    const query = `
      SELECT * FROM deposits
      WHERE asset_network_id = $1 
        AND block_height >= $2 
        AND block_height <= $3
    `;
    
    const result = await this.pool.query(query, [assetNetworkId, fromHeight, toHeight]);
    return result.rows;
  }
  
  /**
   * Mark deposits as reorged
   */
  async markReorged(
    depositIds: string[],
    client?: PoolClient
  ): Promise<void> {
    if (depositIds.length === 0) return;
    
    const executor = client || this.pool;
    
    const query = `
      UPDATE deposits
      SET status = CASE
        WHEN status = 'CREDITED' THEN 'REORGED_CREDITED'
        ELSE 'REORGED'
      END,
      updated_at = NOW()
      WHERE id = ANY($1::uuid[])
    `;
    
    await executor.query(query, [depositIds]);
    this.logger.warn({ count: depositIds.length }, "Deposits marked as reorged");
  }
}
