/**
 * Scan State Repository
 * 
 * Manages the scan cursor with leader election
 */

import type { Pool, PoolClient } from "pg";
import type { Logger } from "pino";

export interface ScanState {
  id: string;
  asset_network_id: string;
  cursor_height: number;
  cursor_hash: string | null;
  finalized_height: number | null;
  updated_at: Date;
  lock_owner: string | null;
  lock_expires_at: Date | null;
}

function toSafeHeight(value: unknown, field: string): number {
  const height = typeof value === "number" ? value : Number(value);
  if (!Number.isSafeInteger(height)) {
    throw new Error(`Invalid ${field}: ${String(value)}`);
  }
  return height;
}

export class ScanStateRepository {
  constructor(
    private pool: Pool,
    private logger: Logger
  ) {}
  
  /**
   * Initialize scan state for an asset network
   */
  async initialize(assetNetworkId: string, startHeight: number = 0): Promise<void> {
    const normalizedStartHeight = Math.max(0, toSafeHeight(startHeight, "startHeight"));
    const query = `
      INSERT INTO animica_scan_state (asset_network_id, cursor_height, cursor_hash)
      VALUES ($1, $2, NULL)
      ON CONFLICT (asset_network_id) DO NOTHING
    `;
    
    await this.pool.query(query, [assetNetworkId, normalizedStartHeight]);
    this.logger.info({ assetNetworkId, startHeight: normalizedStartHeight }, "Scan state initialized");
  }
  
  /**
   * Acquire scan lock with leader election
   * Returns true if lock acquired, false otherwise
   */
  async acquireLock(
    assetNetworkId: string,
    lockOwner: string,
    ttlMs: number
  ): Promise<boolean> {
    const lockExpiresAt = new Date(Date.now() + ttlMs);
    
    const query = `
      UPDATE animica_scan_state
      SET lock_owner = $2, lock_expires_at = $3
      WHERE asset_network_id = $1
        AND (lock_expires_at IS NULL OR lock_expires_at < NOW() OR lock_owner = $2)
      RETURNING id
    `;
    
    const result = await this.pool.query(query, [assetNetworkId, lockOwner, lockExpiresAt]);
    
    const acquired = (result.rowCount ?? 0) > 0;
    if (acquired) {
      this.logger.debug({ assetNetworkId, lockOwner }, "Scan lock acquired");
    }
    
    return acquired;
  }
  
  /**
   * Renew scan lock
   */
  async renewLock(
    assetNetworkId: string,
    lockOwner: string,
    ttlMs: number
  ): Promise<boolean> {
    const lockExpiresAt = new Date(Date.now() + ttlMs);
    
    const query = `
      UPDATE animica_scan_state
      SET lock_expires_at = $3
      WHERE asset_network_id = $1 AND lock_owner = $2
      RETURNING id
    `;
    
    const result = await this.pool.query(query, [assetNetworkId, lockOwner, lockExpiresAt]);
    return (result.rowCount ?? 0) > 0;
  }
  
  /**
   * Release scan lock
   */
  async releaseLock(assetNetworkId: string, lockOwner: string): Promise<void> {
    const query = `
      UPDATE animica_scan_state
      SET lock_owner = NULL, lock_expires_at = NULL
      WHERE asset_network_id = $1 AND lock_owner = $2
    `;
    
    await this.pool.query(query, [assetNetworkId, lockOwner]);
    this.logger.debug({ assetNetworkId, lockOwner }, "Scan lock released");
  }
  
  /**
   * Get current scan state
   */
  async get(assetNetworkId: string): Promise<ScanState | null> {
    const query = `
      SELECT * FROM animica_scan_state
      WHERE asset_network_id = $1
    `;
    
    const result = await this.pool.query(query, [assetNetworkId]);
    const row = result.rows[0];
    if (!row) return null;

    return {
      ...row,
      cursor_height: toSafeHeight(row.cursor_height, "cursor_height"),
      finalized_height:
        row.finalized_height === null || row.finalized_height === undefined
          ? null
          : toSafeHeight(row.finalized_height, "finalized_height"),
    };
  }
  
  /**
   * Update scan cursor atomically
   */
  async updateCursor(
    assetNetworkId: string,
    height: number,
    hash: string,
    client?: PoolClient
  ): Promise<void> {
    const executor = client || this.pool;
    const normalizedHeight = Math.max(0, toSafeHeight(height, "height"));
    
    const query = `
      UPDATE animica_scan_state
      SET cursor_height = $2, cursor_hash = $3, updated_at = NOW()
      WHERE asset_network_id = $1
    `;
    
    await executor.query(query, [assetNetworkId, normalizedHeight, hash]);
  }
  
  /**
   * Rollback cursor to a previous height (for reorg handling)
   */
  async rollbackCursor(
    assetNetworkId: string,
    height: number,
    hash: string,
    client?: PoolClient
  ): Promise<void> {
    const executor = client || this.pool;
    const normalizedHeight = Math.max(0, toSafeHeight(height, "height"));
    
    const query = `
      UPDATE animica_scan_state
      SET cursor_height = $2, cursor_hash = $3, updated_at = NOW()
      WHERE asset_network_id = $1
    `;
    
    await executor.query(query, [assetNetworkId, normalizedHeight, hash]);
    this.logger.warn({ assetNetworkId, height: normalizedHeight, hash }, "Scan cursor rolled back");
  }
}
