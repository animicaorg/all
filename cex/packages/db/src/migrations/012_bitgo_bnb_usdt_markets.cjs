/**
 * Add BitGo-backed BNB and USDT rails and markets.
 *
 * BNB is configured as native BNB on BNB Smart Chain through BitGo coin "bsc".
 * USDT is configured as BNB Smart Chain BEP-20 Binance-Peg BSC-USD,
 * displayed to exchange users as USDT.
 */

const BSC_NETWORK_ID = "bcbcbcbc-bcbc-4bcb-8bcb-bcbcbcbcbcbc";
const BNB_ASSET_ID = "babababa-baba-4aba-8aba-babababababa";
const USDT_ASSET_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const BNB_ASSET_NETWORK_ID = "ffffffff-000b-000b-000b-00000000000b";
const USDT_ASSET_NETWORK_ID = "ffffffff-0003-0003-0003-000000000003";

const USDT_CONTRACT_ADDRESS = "0x55d398326f99059ff775485246999027b3197955";

const BNB_WITHDRAWAL_FEE_ATOMS = "1000000000000000"; // 0.001 BNB
const BNB_MIN_WITHDRAWAL_ATOMS = "10000000000000000"; // 0.01 BNB
const USDT_WITHDRAWAL_FEE_ATOMS = "1000000000000000000"; // 1 USDT
const USDT_MIN_WITHDRAWAL_ATOMS = "5000000000000000000"; // 5 USDT

const NEW_ASSETS = ["BNB", "USDT"];

const MIN_ORDER_SIZE_BY_BASE = {
  ANM: "1",
  BTC: "0.0001",
  BNB: "0.001",
  DOGE: "10",
  LTC: "0.001",
  USDT: "1",
  ZEC: "0.001",
};

function marketForPair(assetA, assetB) {
  const normalized = [assetA, assetB].map((asset) => String(asset).toUpperCase());
  const quote =
    ["USDT", "ANM", "BNB"].find((candidate) => normalized.includes(candidate)) ?? normalized[1];
  const base = normalized.find((asset) => asset !== quote) ?? normalized[0];

  return {
    symbol: `${base}-${quote}`,
    baseAsset: base,
    quoteAsset: quote,
  };
}

async function getIdByColumn(knex, table, column, value) {
  const row = await knex(table).whereRaw(`UPPER(${column}) = UPPER(?)`, [value]).first("id");
  if (!row?.id) {
    throw new Error(`Expected ${table}.${column}=${value} to exist`);
  }
  return row.id;
}

exports.up = async function up(knex) {
  await knex("networks")
    .insert([
      {
        id: BSC_NETWORK_ID,
        code: "BSC",
        name: "BNB Smart Chain",
        type: "EVM",
        confirmations_required: 15,
        active: true,
        metadata: JSON.stringify({ chain_id: 56, explorer_url: "https://bscscan.com" }),
      },
    ])
    .onConflict("code")
    .merge(["name", "type", "confirmations_required", "active", "metadata"]);

  await knex("assets")
    .insert([
      {
        id: BNB_ASSET_ID,
        symbol: "BNB",
        name: "BNB",
        decimals: 18,
        active: true,
        metadata: JSON.stringify({}),
      },
      {
        id: USDT_ASSET_ID,
        symbol: "USDT",
        name: "Tether USD",
        decimals: 18,
        active: true,
        metadata: JSON.stringify({}),
      },
    ])
    .onConflict("symbol")
    .merge(["name", "decimals", "active", "metadata"]);

  const bnbAssetId = await getIdByColumn(knex, "assets", "symbol", "BNB");
  const usdtAssetId = await getIdByColumn(knex, "assets", "symbol", "USDT");
  const bscNetworkId = await getIdByColumn(knex, "networks", "code", "BSC");

  await knex("asset_networks")
    .insert([
      {
        id: BNB_ASSET_NETWORK_ID,
        asset_id: bnbAssetId,
        network_id: bscNetworkId,
        contract_address: null,
        bitgo_coin: "bsc",
        deposits_enabled: true,
        withdrawals_enabled: true,
        min_deposit_atoms: "1000000000000000",
        confirmations_override: null,
        metadata: JSON.stringify({
          provider: "BITGO",
          address_coin: "bsc",
          flat_withdrawal_fee_atoms: BNB_WITHDRAWAL_FEE_ATOMS,
          flat_withdrawal_fee: "0.001",
        }),
      },
      {
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
      },
    ])
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
      .insert([
        {
          asset_network_id: BNB_ASSET_NETWORK_ID,
          min_withdrawal_atoms: BNB_MIN_WITHDRAWAL_ATOMS,
          required_approvals: 1,
          high_risk_approvals: 2,
          enabled: true,
          metadata: JSON.stringify({
            withdrawalFeeAtoms: BNB_WITHDRAWAL_FEE_ATOMS,
            withdrawalFee: "0.001",
            flatFee: true,
            feeAsset: "BNB",
            rationale: "Flat fee for native BNB Smart Chain withdrawals through BitGo.",
          }),
        },
        {
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
        },
      ])
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

  const hasMarkets = await knex.schema.hasTable("markets");
  if (hasMarkets) {
    const activeAssets = await knex("assets")
      .where({ active: true })
      .pluck("symbol");

    const markets = new Map();
    for (const newAsset of NEW_ASSETS) {
      for (const otherAsset of activeAssets) {
        const other = String(otherAsset).toUpperCase();
        if (other === newAsset) continue;
        const market = marketForPair(newAsset, other);
        markets.set(market.symbol, {
          id: knex.raw("gen_random_uuid()"),
          symbol: market.symbol,
          base_asset: market.baseAsset,
          quote_asset: market.quoteAsset,
          active: true,
          price_tick: "0.00000001",
          size_step: "0.00000001",
          min_order_size: MIN_ORDER_SIZE_BY_BASE[market.baseAsset] ?? "0.001",
          maker_fee_bps: 10,
          taker_fee_bps: 20,
          fee_asset: market.quoteAsset,
        });
      }
    }

    const marketRows = Array.from(markets.values()).sort((a, b) => a.symbol.localeCompare(b.symbol));
    if (marketRows.length > 0) {
      await knex("markets")
        .insert(marketRows)
        .onConflict("symbol")
        .merge([
          "base_asset",
          "quote_asset",
          "active",
          "price_tick",
          "size_step",
          "min_order_size",
          "maker_fee_bps",
          "taker_fee_bps",
          "fee_asset",
        ]);

      await knex.raw(
        `
          INSERT INTO market_sequence (market_id, last_seq)
          SELECT id, 0
          FROM markets
          WHERE symbol = ANY(?::text[])
          ON CONFLICT (market_id) DO NOTHING
        `,
        [marketRows.map((market) => market.symbol)]
      );
    }
  }
};

exports.down = async function down(knex) {
  const hasMarkets = await knex.schema.hasTable("markets");
  if (hasMarkets) {
    await knex("markets")
      .whereIn("base_asset", NEW_ASSETS)
      .orWhereIn("quote_asset", NEW_ASSETS)
      .update({ active: false });
  }

  const hasWithdrawalPolicies = await knex.schema.hasTable("withdrawal_policies");
  if (hasWithdrawalPolicies) {
    await knex("withdrawal_policies")
      .whereIn("asset_network_id", [BNB_ASSET_NETWORK_ID, USDT_ASSET_NETWORK_ID])
      .update({ enabled: false, updated_at: knex.fn.now() });
  }

  await knex("asset_networks")
    .whereIn("id", [BNB_ASSET_NETWORK_ID, USDT_ASSET_NETWORK_ID])
    .update({ deposits_enabled: false, withdrawals_enabled: false });

  await knex("assets").whereIn("symbol", NEW_ASSETS).update({ active: false });
  await knex("networks").where({ code: "BSC" }).update({ active: false });
};
