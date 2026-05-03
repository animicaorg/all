import { Router } from "express";
import { Pool } from "pg";

const router = Router();

export function createAssetsRouter(pgPool: Pool): any {
  router.get("/assets", async (_req: any, res) => {
    try {
      const result = await pgPool.query(`
        SELECT
          asset_networks.id AS asset_network_id,
          assets.symbol,
          assets.name,
          assets.decimals,
          assets.active,
          networks.code AS network_code,
          networks.name AS network_name,
          networks.type AS network_type,
          COALESCE(asset_networks.metadata->>'rpc_url', networks.metadata->>'rpc_url') AS rpc_url,
          asset_networks.bitgo_coin,
          asset_networks.deposits_enabled,
          asset_networks.withdrawals_enabled,
          COALESCE(withdrawal_policies.min_withdrawal_atoms::text, asset_networks.min_deposit_atoms::text, '0') AS min_withdrawal_atoms,
          COALESCE(
            withdrawal_policies.metadata->>'withdrawalFeeAtoms',
            asset_networks.metadata->>'flat_withdrawal_fee_atoms',
            '0'
          ) AS withdrawal_fee_atoms,
          COALESCE(
            withdrawal_policies.metadata->>'flatFee',
            asset_networks.metadata->>'flat_fee',
            'true'
          ) AS flat_fee,
          COALESCE(asset_networks.metadata->>'provider',
            CASE
              WHEN asset_networks.bitgo_coin IS NOT NULL THEN 'BITGO'
              WHEN networks.code = 'ANIMICA' OR networks.type IN ('ANIMICA', 'ACCOUNT') THEN 'ANIMICA_NODE'
              WHEN networks.type = 'UTXO' THEN 'BITCOIN_NODE'
              ELSE 'OTHER'
            END
          ) AS provider
        FROM asset_networks
        JOIN assets ON assets.id = asset_networks.asset_id
        JOIN networks ON networks.id = asset_networks.network_id
        LEFT JOIN withdrawal_policies ON withdrawal_policies.asset_network_id = asset_networks.id
        WHERE assets.active = true
          AND networks.active = true
        ORDER BY assets.symbol ASC, networks.code ASC
      `);

      const assets = new Map<string, any>();
      for (const row of result.rows) {
        const symbol = row.symbol;
        if (!assets.has(symbol)) {
          assets.set(symbol, {
            symbol,
            name: row.name,
            decimals: Number(row.decimals),
            isEnabled: Boolean(row.active),
            networks: [],
          });
        }

        assets.get(symbol).networks.push({
          assetNetworkId: row.asset_network_id,
          code: row.network_code,
          name: row.network_name,
          type: row.network_type,
          provider: row.provider,
          bitgoCoin: row.bitgo_coin,
          rpcUrl: row.rpc_url,
          depositsEnabled: Boolean(row.deposits_enabled),
          withdrawalsEnabled: Boolean(row.withdrawals_enabled),
          minWithdrawalAtoms: row.min_withdrawal_atoms,
          withdrawalFeeAtoms: row.withdrawal_fee_atoms,
          flatFee: row.flat_fee === true || row.flat_fee === "true",
        });
      }

      res.json({ assets: [...assets.values()] });
    } catch (error) {
      console.error("Error fetching assets:", error);
      res.status(500).json({ error: "Failed to fetch assets" });
    }
  });

  return router;
}
