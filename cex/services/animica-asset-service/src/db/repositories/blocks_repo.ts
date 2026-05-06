/**
 * Blocks Repository
 * 
 * Manages block hash chain for reorg detection
 */

import type { Pool, PoolClient } from "pg";
import type { Logger } from "pino";

export interface AnimicaBlock {
  height: number;
  asset_network_id: string;
  hash: string;
  parent_hash: string;
  canonical: boolean;
  seen_at: Date;
}

function toSafeHeight(value: unknown, field: string): number {
  const height = typeof value === "number" ? value : Number(value);
  if (!Number.isSafeInteger(height) || height < 0) {
    throw new Error(`Invalid ${field}: ${String(value)}`);
  }
  return height;
}

export class BlocksRepository {
  constructor(
    private pool: Pool,
    private logger: Logger
  ) {}
  
  /**
   * Upsert a block
   */
  async upsert(
    assetNetworkId: string,
    height: number,
    hash: string,
    parentHash: string,
    canonical: boolean = true,
    client?: PoolClient
  ): Promise<void> {
    const executor = client || this.pool;
    const normalizedHeight = toSafeHeight(height, "height");
    
    const query = `
      INSERT INTO animica_blocks (asset_network_id, height, hash, parent_hash, canonical)
      VALUES ($1, $2, $3, $4, $5)
      ON CONFLICT (asset_network_id, height)
      DO UPDATE SET hash = $3, parent_hash = $4, canonical = $5, seen_at = NOW()
    `;
    
    await executor.query(query, [assetNetworkId, normalizedHeight, hash, parentHash, canonical]);
  }
  
  /**
   * Get block by height
   */
  async getByHeight(assetNetworkId: string, height: number): Promise<AnimicaBlock | null> {
    const query = `
      SELECT * FROM animica_blocks
      WHERE asset_network_id = $1 AND height = $2
    `;
    
    const result = await this.pool.query(query, [assetNetworkId, toSafeHeight(height, "height")]);
    return result.rows[0] ? this.mapRow(result.rows[0]) : null;
  }

  /**
   * Get canonical block by height.
   */
  async getCanonicalByHeight(assetNetworkId: string, height: number): Promise<AnimicaBlock | null> {
    const query = `
      SELECT * FROM animica_blocks
      WHERE asset_network_id = $1 AND height = $2 AND canonical = true
    `;

    const result = await this.pool.query(query, [assetNetworkId, toSafeHeight(height, "height")]);
    return result.rows[0] ? this.mapRow(result.rows[0]) : null;
  }
  
  /**
   * Mark blocks as non-canonical (for reorg)
   */
  async markNonCanonical(
    assetNetworkId: string,
    fromHeight: number,
    toHeight: number,
    client?: PoolClient
  ): Promise<void> {
    const executor = client || this.pool;
    const normalizedFromHeight = toSafeHeight(fromHeight, "fromHeight");
    const normalizedToHeight = toSafeHeight(toHeight, "toHeight");
    if (normalizedFromHeight > normalizedToHeight) return;
    
    const query = `
      UPDATE animica_blocks
      SET canonical = false
      WHERE asset_network_id = $1 AND height >= $2 AND height <= $3
    `;
    
    await executor.query(query, [assetNetworkId, normalizedFromHeight, normalizedToHeight]);
    this.logger.warn(
      { assetNetworkId, fromHeight: normalizedFromHeight, toHeight: normalizedToHeight },
      "Blocks marked as non-canonical due to reorg"
    );
  }
  
  /**
   * Get blocks in range
   */
  async getRange(
    assetNetworkId: string,
    fromHeight: number,
    toHeight: number
  ): Promise<AnimicaBlock[]> {
    const query = `
      SELECT * FROM animica_blocks
      WHERE asset_network_id = $1 AND height >= $2 AND height <= $3
      ORDER BY height ASC
    `;
    
    const result = await this.pool.query(query, [
      assetNetworkId,
      toSafeHeight(fromHeight, "fromHeight"),
      toSafeHeight(toHeight, "toHeight"),
    ]);
    return result.rows.map((row) => this.mapRow(row));
  }

  private mapRow(row: any): AnimicaBlock {
    return {
      height: toSafeHeight(row.height, "height"),
      asset_network_id: row.asset_network_id,
      hash: row.hash,
      parent_hash: row.parent_hash,
      canonical: row.canonical,
      seen_at: row.seen_at,
    };
  }
}
