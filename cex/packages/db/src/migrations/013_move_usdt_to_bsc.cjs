/**
 * Correct USDT rail from Ethereum ERC-20 to BNB Smart Chain BEP-20.
 *
 * The exchange displays the asset as USDT, but the BNB Smart Chain token is
 * Binance-Peg BSC-USD at 0x55d398326f99059ff775485246999027b3197955.
 */

const BSC_NETWORK_ID = "bcbcbcbc-bcbc-4bcb-8bcb-bcbcbcbcbcbc";
const USDT_ASSET_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const USDT_ASSET_NETWORK_ID = "ffffffff-0003-0003-0003-000000000003";
const USDT_CONTRACT_ADDRESS = "0x55d398326f99059ff775485246999027b3197955";
const USDT_WITHDRAWAL_FEE_ATOMS = "1000000000000000000"; // 1 USDT
const USDT_MIN_WITHDRAWAL_ATOMS = "5000000000000000000"; // 5 USDT

async function getIdByColumn(knex, table, column, value) {
  const row = await knex(table).whereRaw(`UPPER(${column}) = UPPER(?)`, [value]).first("id");
  if (!row?.id) {
    throw new Error(`Expected ${table}.${column}=${value} to exist`);
  }
  return row.id;
}

exports.up = async function up(knex) {
  await knex("networks")
    .insert({
      id: BSC_NETWORK_ID,
      code: "BSC",
      name: "BNB Smart Chain",
      type: "EVM",
      confirmations_required: 15,
      active: true,
      metadata: JSON.stringify({ chain_id: 56, explorer_url: "https://bscscan.com" }),
    })
    .onConflict("code")
    .merge(["name", "type", "confirmations_required", "active", "metadata"]);

  await knex("assets")
    .insert({
      id: USDT_ASSET_ID,
      symbol: "USDT",
      name: "Tether USD",
      decimals: 18,
      active: true,
      metadata: JSON.stringify({}),
    })
    .onConflict("symbol")
    .merge(["name", "decimals", "active", "metadata"]);

  const usdtAssetId = await getIdByColumn(knex, "assets", "symbol", "USDT");
  const bscNetworkId = await getIdByColumn(knex, "networks", "code", "BSC");

  await knex("asset_networks")
    .insert({
      id: USDT_ASSET_NETWORK_ID,
      asset_id: usdtAssetId,
      network_id: bscNetworkId,
      contract_address: USDT_CONTRACT_ADDRESS,
      bitgo_coin: "bsc:bsc-usd",
      deposits_enabled: true,
      withdrawals_enabled: true,
      min_deposit_atoms: "1000000000000000000",
      confirmations_override: 15,
      metadata: JSON.stringify({
        provider: "BITGO",
        address_coin: "bsc",
        display_symbol: "USDT",
        token_symbol: "BSC-USD",
        token_standard: "BEP20",
        flat_withdrawal_fee_atoms: USDT_WITHDRAWAL_FEE_ATOMS,
        flat_withdrawal_fee: "1",
      }),
    })
    .onConflict("id")
    .merge([
      "asset_id",
      "network_id",
      "contract_address",
      "bitgo_coin",
      "deposits_enabled",
      "withdrawals_enabled",
      "min_deposit_atoms",
      "confirmations_override",
      "metadata",
    ]);

  const hasWithdrawalPolicies = await knex.schema.hasTable("withdrawal_policies");
  if (hasWithdrawalPolicies) {
    await knex("withdrawal_policies")
      .insert({
        asset_network_id: USDT_ASSET_NETWORK_ID,
        min_withdrawal_atoms: USDT_MIN_WITHDRAWAL_ATOMS,
        required_approvals: 1,
        high_risk_approvals: 2,
        enabled: true,
        metadata: JSON.stringify({
          withdrawalFeeAtoms: USDT_WITHDRAWAL_FEE_ATOMS,
          withdrawalFee: "1",
          flatFee: true,
          feeAsset: "USDT",
          rationale: "Flat fee for BNB Smart Chain BEP-20 USDT withdrawals through BitGo.",
        }),
      })
      .onConflict("asset_network_id")
      .merge({
        min_withdrawal_atoms: knex.raw("EXCLUDED.min_withdrawal_atoms"),
        required_approvals: knex.raw("EXCLUDED.required_approvals"),
        high_risk_approvals: knex.raw("EXCLUDED.high_risk_approvals"),
        enabled: knex.raw("EXCLUDED.enabled"),
        metadata: knex.raw("EXCLUDED.metadata"),
        updated_at: knex.fn.now(),
      });
  }

  await knex.raw(`
    UPDATE networks AS n
       SET active = false
     WHERE n.code = 'ETH'
       AND NOT EXISTS (
         SELECT 1
           FROM asset_networks AS an
           JOIN assets AS a ON a.id = an.asset_id
          WHERE an.network_id = n.id
            AND a.active = true
            AND (an.deposits_enabled = true OR an.withdrawals_enabled = true)
       )
  `);
};

exports.down = async function down(knex) {
  const ethNetworkId = await getIdByColumn(knex, "networks", "code", "ETH");
  const usdtAssetId = await getIdByColumn(knex, "assets", "symbol", "USDT");

  await knex("assets").where({ symbol: "USDT" }).update({ decimals: 6 });
  await knex("asset_networks")
    .where({ id: USDT_ASSET_NETWORK_ID })
    .update({
      asset_id: usdtAssetId,
      network_id: ethNetworkId,
      contract_address: "0xdac17f958d2ee523a2206206994597c13d831ec7",
      bitgo_coin: "usdt",
      deposits_enabled: true,
      withdrawals_enabled: true,
      min_deposit_atoms: "1000000",
      confirmations_override: 12,
      metadata: JSON.stringify({
        provider: "BITGO",
        address_coin: "eth",
        token_standard: "ERC20",
        flat_withdrawal_fee_atoms: "10000000",
        flat_withdrawal_fee: "10",
      }),
    });

  const hasWithdrawalPolicies = await knex.schema.hasTable("withdrawal_policies");
  if (hasWithdrawalPolicies) {
    await knex("withdrawal_policies")
      .where({ asset_network_id: USDT_ASSET_NETWORK_ID })
      .update({
        min_withdrawal_atoms: "20000000",
        metadata: JSON.stringify({
          withdrawalFeeAtoms: "10000000",
          withdrawalFee: "10",
          flatFee: true,
          feeAsset: "USDT",
          rationale: "Flat fee for Ethereum ERC-20 USDT withdrawals through BitGo.",
        }),
        updated_at: knex.fn.now(),
      });
  }
};
