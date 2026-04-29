/**
 * Networks Repository
 */

import type { PoolClient } from "pg";

export interface AssetNetwork {
  id: string;
  assetSymbol: string;
  networkName: string;
  addressType: string;
  confirmationsRequired: number;
  enabled: boolean;
  metadata: any;
}

export interface Wallet {
  id: string;
  assetNetworkId: string;
  walletType: string;
  provider: string;
  providerWalletId: string;
  enabled: boolean;
  metadata: any;
}

export class NetworksRepo {
  constructor(private client: PoolClient) {}

  async getAssetNetwork(id: string): Promise<AssetNetwork | null> {
    const result = await this.client.query(
      "SELECT * FROM asset_networks WHERE id = $1",
      [id]
    );
    return result.rows.length > 0 ? this.mapAssetNetworkRow(result.rows[0]) : null;
  }

  async getWallet(assetNetworkId: string, walletType: string = "HOT"): Promise<Wallet | null> {
    const result = await this.client.query(
      `SELECT * FROM wallets 
       WHERE asset_network_id = $1 
         AND wallet_type = $2 
         AND enabled = true 
       LIMIT 1`,
      [assetNetworkId, walletType]
    );
    return result.rows.length > 0 ? this.mapWalletRow(result.rows[0]) : null;
  }

  private mapAssetNetworkRow(row: any): AssetNetwork {
    return {
      id: row.id,
      assetSymbol: row.asset_symbol,
      networkName: row.network_name,
      addressType: row.address_type,
      confirmationsRequired: row.confirmations_required,
      enabled: row.enabled,
      metadata: row.metadata,
    };
  }

  private mapWalletRow(row: any): Wallet {
    return {
      id: row.id,
      assetNetworkId: row.asset_network_id,
      walletType: row.wallet_type,
      provider: row.provider,
      providerWalletId: row.provider_wallet_id,
      enabled: row.enabled,
      metadata: row.metadata,
    };
  }
}
