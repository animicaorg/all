/**
 * Seen Transactions Repository
 * 
 * Manages deduplication of processed transactions
 */

import type { Pool, PoolClient } from "pg";
import type { Logger } from "pino";

export interface SeenTx {
  key: string;
  asset_network_id: string;
  txid: string;
  height: number;
  address: string;
  amount_atoms: string;
  created_at: Date;
}

export class SeenTxsRepository {
  constructor(
    private pool: Pool,
    private logger: Logger
  ) {}
  
  /**
   * Check if transaction has been seen
   */
  async hasSeen(key: string): Promise<boolean> {
    const query = `SELECT 1 FROM animica_seen_txs WHERE key = $1`;
    const result = await this.pool.query(query, [key]);
    return (result.rowCount ?? 0) > 0;
  }
  
  /**
   * Mark transaction as seen
   */
  async markSeen(
    key: string,
    assetNetworkId: string,
    txid: string,
    height: number,
    address: string,
    amountAtoms: string,
    client?: PoolClient
  ): Promise<void> {
    const executor = client || this.pool;
    
    const query = `
      INSERT INTO animica_seen_txs (key, asset_network_id, txid, height, address, amount_atoms)
      VALUES ($1, $2, $3, $4, $5, $6)
      ON CONFLICT (key) DO NOTHING
    `;
    
    await executor.query(query, [key, assetNetworkId, txid, height, address, amountAtoms]);
  }
  
  /**
   * Get seen transactions in height range (for reorg cleanup)
   */
  async getByHeightRange(
    assetNetworkId: string,
    fromHeight: number,
    toHeight: number
  ): Promise<SeenTx[]> {
    const query = `
      SELECT * FROM animica_seen_txs
      WHERE asset_network_id = $1 AND height >= $2 AND height <= $3
    `;
    
    const result = await this.pool.query(query, [assetNetworkId, fromHeight, toHeight]);
    return result.rows;
  }
  
  /**
   * Delete seen transactions (for reorg cleanup)
   */
  async deleteByHeightRange(
    assetNetworkId: string,
    fromHeight: number,
    toHeight: number,
    client?: PoolClient
  ): Promise<void> {
    const executor = client || this.pool;
    
    const query = `
      DELETE FROM animica_seen_txs
      WHERE asset_network_id = $1 AND height >= $2 AND height <= $3
    `;
    
    await executor.query(query, [assetNetworkId, fromHeight, toHeight]);
    this.logger.warn(
      { assetNetworkId, fromHeight, toHeight },
      "Seen transactions deleted due to reorg"
    );
  }
}
