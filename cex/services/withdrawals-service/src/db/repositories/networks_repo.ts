/**
 * Networks Repository
 */

import type { PoolClient } from "pg";

export interface AssetNetwork {
  id: string;
  assetSymbol: string;
  assetDecimals: number;
  networkName: string;
  addressType: string;
  provider: string;
  bitgoCoin: string | null;
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
      `SELECT
        asset_networks.id,
        assets.symbol AS asset_symbol,
        assets.decimals AS asset_decimals,
        networks.code AS network_name,
        networks.type AS address_type,
        asset_networks.bitgo_coin,
        COALESCE(
          asset_networks.metadata->>'provider',
          CASE
            WHEN asset_networks.bitgo_coin IS NOT NULL THEN 'BITGO'
            WHEN networks.code = 'ANIMICA' OR networks.type IN ('ANIMICA', 'ACCOUNT') THEN 'ANIMICA_NODE'
            WHEN networks.type = 'UTXO' THEN 'BITCOIN_NODE'
            ELSE 'BITGO'
          END
        ) AS provider,
        COALESCE(asset_networks.confirmations_override, networks.confirmations_required) AS confirmations_required,
        asset_networks.withdrawals_enabled AS enabled,
        asset_networks.metadata
      FROM asset_networks
      JOIN assets ON assets.id = asset_networks.asset_id
      JOIN networks ON networks.id = asset_networks.network_id
      WHERE asset_networks.id = $1`,
      [id]
    );
    return result.rows.length > 0 ? this.mapAssetNetworkRow(result.rows[0]) : null;
  }

  async getWallet(assetNetworkId: string, walletType: string = "HOT"): Promise<Wallet | null> {
    const direct = await this.client.query(
      `SELECT
        id,
        asset_network_id,
        COALESCE(metadata->>'purpose', $2) AS wallet_type,
        provider,
        wallet_id AS provider_wallet_id,
        COALESCE(LOWER(status) NOT IN ('disabled', 'inactive', 'closed', 'paused'), true) AS enabled,
        metadata
      FROM wallets
      WHERE asset_network_id = $1
        AND COALESCE(metadata->>'purpose', $2) = $2
        AND COALESCE(LOWER(status) NOT IN ('disabled', 'inactive', 'closed', 'paused'), true)
      ORDER BY created_at ASC
      LIMIT 1`,
      [assetNetworkId, walletType]
    );
    if (direct.rows.length > 0) {
      return this.mapWalletRow(direct.rows[0]);
    }

    const shared = await this.client.query(
      `WITH target_network AS (
        SELECT LOWER(COALESCE(NULLIF(metadata->>'address_coin', ''), bitgo_coin)) AS address_coin
        FROM asset_networks
        WHERE id = $1
      )
      SELECT
        wallets.id,
        wallets.asset_network_id,
        COALESCE(wallets.metadata->>'purpose', $2) AS wallet_type,
        wallets.provider,
        wallets.wallet_id AS provider_wallet_id,
        COALESCE(LOWER(wallets.status) NOT IN ('disabled', 'inactive', 'closed', 'paused'), true) AS enabled,
        wallets.metadata
      FROM wallets
      JOIN asset_networks wallet_asset_networks
        ON wallet_asset_networks.id = wallets.asset_network_id
      JOIN target_network
        ON target_network.address_coin = LOWER(COALESCE(NULLIF(wallet_asset_networks.metadata->>'address_coin', ''), wallet_asset_networks.bitgo_coin))
      WHERE wallets.asset_network_id <> $1
        AND wallets.provider = 'BITGO'
        AND COALESCE(wallets.metadata->>'purpose', $2) = $2
        AND COALESCE(LOWER(wallets.status) NOT IN ('disabled', 'inactive', 'closed', 'paused'), true)
      ORDER BY wallets.created_at ASC
      LIMIT 1`,
      [assetNetworkId, walletType]
    );

    return shared.rows.length > 0 ? this.mapWalletRow(shared.rows[0]) : null;
  }

  private mapAssetNetworkRow(row: any): AssetNetwork {
    return {
      id: row.id,
      assetSymbol: row.asset_symbol,
      assetDecimals: Number(row.asset_decimals ?? 0),
      networkName: row.network_name,
      addressType: row.address_type,
      provider: row.provider,
      bitgoCoin: row.bitgo_coin,
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
