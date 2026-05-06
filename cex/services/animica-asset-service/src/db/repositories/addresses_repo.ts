/**
 * Addresses Repository
 * 
 * Manages deposit addresses using existing user_deposit_addresses table
 */

import type { Pool } from "pg";
import type { Logger } from "pino";

export interface DepositAddress {
  id: string;
  user_id: string;
  asset_network_id: string;
  wallet_id: string;
  address: string;
  tag: string | null;
  label: string | null;
  status: string;
  assigned_at: Date;
  last_used_at: Date | null;
}

export class AddressesRepository {
  constructor(
    private pool: Pool,
    private logger: Logger
  ) {}
  
  /**
   * Get or create deposit address for a user
   */
  async getOrCreate(
    userId: string,
    assetNetworkId: string,
    walletId: string,
    address: string,
    tag: string | null = null
  ): Promise<DepositAddress> {
    // First, try to get existing address
    const existingQuery = `
      SELECT * FROM user_deposit_addresses
      WHERE user_id = $1 AND asset_network_id = $2 AND status = 'ACTIVE'
      LIMIT 1
    `;
    
    const existing = await this.pool.query(existingQuery, [userId, assetNetworkId]);
    
    if (existing.rows.length > 0) {
      return existing.rows[0];
    }
    
    // Create new address
    const insertQuery = `
      INSERT INTO user_deposit_addresses (
        user_id, asset_network_id, wallet_id, address, tag, status
      )
      VALUES ($1, $2, $3, $4, $5, 'ACTIVE')
      ON CONFLICT (asset_network_id, address, tag) DO NOTHING
      RETURNING *
    `;
    
    const result = await this.pool.query(insertQuery, [
      userId,
      assetNetworkId,
      walletId,
      address,
      tag,
    ]);
    
    if (result.rows.length === 0) {
      // Address already exists but for different user - fetch it
      const conflictQuery = `
        SELECT * FROM user_deposit_addresses
        WHERE asset_network_id = $1 AND address = $2 AND tag IS NOT DISTINCT FROM $3
      `;
      const conflict = await this.pool.query(conflictQuery, [assetNetworkId, address, tag]);
      
      if (conflict.rows.length > 0) {
        throw new Error(`Address ${address} already assigned to another user`);
      }
      
      throw new Error("Failed to create deposit address due to unknown conflict");
    }
    
    this.logger.info({ userId, address, assetNetworkId }, "New deposit address assigned");
    return result.rows[0];
  }
  
  /**
   * Get user ID by address
   */
  async getUserIdByAddress(
    assetNetworkId: string,
    address: string,
    tag: string | null = null
  ): Promise<string | null> {
    const query = `
      SELECT user_id FROM user_deposit_addresses
      WHERE asset_network_id = $1
        AND LOWER(address) = LOWER($2)
        AND tag IS NOT DISTINCT FROM $3
        AND status = 'ACTIVE'
    `;
    
    const result = await this.pool.query(query, [assetNetworkId, address, tag]);
    return result.rows[0]?.user_id || null;
  }
  
  /**
   * Get all active addresses for an asset network
   */
  async getActiveAddresses(assetNetworkId: string): Promise<Set<string>> {
    const query = `
      SELECT address FROM user_deposit_addresses
      WHERE asset_network_id = $1 AND status = 'ACTIVE'
    `;
    
    const result = await this.pool.query(query, [assetNetworkId]);
    return new Set(result.rows.map((row) => String(row.address).toLowerCase()));
  }
  
  /**
   * Update last used timestamp
   */
  async updateLastUsed(addressId: string): Promise<void> {
    const query = `
      UPDATE user_deposit_addresses
      SET last_used_at = NOW()
      WHERE id = $1
    `;
    
    await this.pool.query(query, [addressId]);
  }
}
