/**
 * User Deposit Addresses Repository
 */

import type { PoolClient } from "pg";

export interface UserDepositAddress {
  id: string;
  userId: string;
  assetNetworkId: string;
  walletId: string;
  address: string;
  tag: string | null;
  label: string | null;
  status: string;
  assignedAt: Date;
  lastUsedAt: Date | null;
}

export class AddressesRepo {
  constructor(private client: PoolClient) {}

  /**
   * Find user by deposit address
   */
  async findUserByAddress(
    assetNetworkId: string,
    address: string,
    tag?: string
  ): Promise<string | null> {
    const query = `
      SELECT user_id
      FROM user_deposit_addresses
      WHERE asset_network_id = $1
        AND address = $2
        AND (tag = $3 OR (tag IS NULL AND $3 IS NULL))
        AND status = 'ACTIVE'
    `;

    const result = await this.client.query(query, [
      assetNetworkId,
      address,
      tag || null,
    ]);

    return result.rows.length > 0 ? result.rows[0].user_id : null;
  }

  /**
   * Update last used timestamp
   */
  async updateLastUsed(
    assetNetworkId: string,
    address: string,
    tag?: string
  ): Promise<void> {
    const query = `
      UPDATE user_deposit_addresses
      SET last_used_at = NOW()
      WHERE asset_network_id = $1
        AND address = $2
        AND (tag = $3 OR (tag IS NULL AND $3 IS NULL))
    `;

    await this.client.query(query, [assetNetworkId, address, tag || null]);
  }
}
