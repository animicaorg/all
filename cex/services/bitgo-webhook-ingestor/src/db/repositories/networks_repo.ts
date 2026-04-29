/**
 * Networks Repository
 */

import type { PoolClient } from "pg";

export interface Network {
  id: string;
  code: string;
  name: string;
  type: string;
  confirmationsRequired: number;
  active: boolean;
  metadata: any;
}

export interface AssetNetwork {
  id: string;
  assetId: string;
  networkId: string;
  contractAddress: string | null;
  bitgoCoin: string | null;
  depositsEnabled: boolean;
  withdrawalsEnabled: boolean;
  minDepositAtoms: bigint | null;
  confirmationsOverride: number | null;
  metadata: any;
}

export class NetworksRepo {
  constructor(private client: PoolClient) {}

  /**
   * Get asset network by ID
   */
  async getAssetNetwork(id: string): Promise<AssetNetwork | null> {
    const result = await this.client.query(
      "SELECT * FROM asset_networks WHERE id = $1",
      [id]
    );
    return result.rows.length > 0 ? this.mapAssetNetworkRow(result.rows[0]) : null;
  }

  /**
   * Get confirmations required for an asset network
   */
  async getConfirmationsRequired(assetNetworkId: string): Promise<number> {
    const query = `
      SELECT
        COALESCE(an.confirmations_override, n.confirmations_required) as confirmations
      FROM asset_networks an
      JOIN networks n ON n.id = an.network_id
      WHERE an.id = $1
    `;

    const result = await this.client.query(query, [assetNetworkId]);
    return result.rows.length > 0 ? result.rows[0].confirmations : 6;
  }

  /**
   * Get asset symbol for asset network
   */
  async getAssetSymbol(assetNetworkId: string): Promise<string | null> {
    const query = `
      SELECT a.symbol
      FROM asset_networks an
      JOIN assets a ON a.id = an.asset_id
      WHERE an.id = $1
    `;

    const result = await this.client.query(query, [assetNetworkId]);
    return result.rows.length > 0 ? result.rows[0].symbol : null;
  }

  /**
   * Map row to AssetNetwork
   */
  private mapAssetNetworkRow(row: any): AssetNetwork {
    return {
      id: row.id,
      assetId: row.asset_id,
      networkId: row.network_id,
      contractAddress: row.contract_address,
      bitgoCoin: row.bitgo_coin,
      depositsEnabled: row.deposits_enabled,
      withdrawalsEnabled: row.withdrawals_enabled,
      minDepositAtoms: row.min_deposit_atoms ? BigInt(row.min_deposit_atoms) : null,
      confirmationsOverride: row.confirmations_override,
      metadata: row.metadata,
    };
  }
}
