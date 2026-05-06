/**
 * Update BitGo-backed asset lineup.
 *
 * Keep existing ETH/SOL rows for historical data integrity, but disable them
 * for new deposits, withdrawals, and markets. Add LTC, DOGE, and ZEC as
 * active BitGo-backed native UTXO assets.
 */

const BTC_ASSET_NETWORK_ID = "ffffffff-0001-0001-0001-000000000001";
const ETH_ASSET_NETWORK_ID = "ffffffff-0002-0002-0002-000000000002";
const ETH_SEPOLIA_ASSET_NETWORK_ID = "ffffffff-0005-0005-0005-000000000005";
const SOL_ASSET_NETWORK_ID = "ffffffff-0007-0007-0007-000000000007";
const LTC_ASSET_NETWORK_ID = "ffffffff-0008-0008-0008-000000000008";
const DOGE_ASSET_NETWORK_ID = "ffffffff-0009-0009-0009-000000000009";
const ZEC_ASSET_NETWORK_ID = "ffffffff-000a-000a-000a-00000000000a";

const BTC_LOW_FEE_ATOMS = "5000"; // 0.00005 BTC
const BTC_LOW_MIN_WITHDRAWAL_ATOMS = "10000"; // 0.0001 BTC

exports.up = async function up(knex) {
  await knex("networks")
    .insert([
      {
        id: "66666666-6666-6666-6666-666666666666",
        code: "LTC",
        name: "Litecoin Mainnet",
        type: "UTXO",
        confirmations_required: 6,
        active: true,
        metadata: JSON.stringify({ explorer_url: "https://blockchair.com/litecoin" }),
      },
      {
        id: "77777777-7777-7777-7777-777777777777",
        code: "DOGE",
        name: "Dogecoin Mainnet",
        type: "UTXO",
        confirmations_required: 20,
        active: true,
        metadata: JSON.stringify({ explorer_url: "https://blockchair.com/dogecoin" }),
      },
      {
        id: "88888888-8888-8888-8888-888888888888",
        code: "ZEC",
        name: "Zcash Mainnet",
        type: "UTXO",
        confirmations_required: 24,
        active: true,
        metadata: JSON.stringify({ explorer_url: "https://blockchair.com/zcash" }),
      },
    ])
    .onConflict("code")
    .merge(["name", "type", "confirmations_required", "active", "metadata"]);

  await knex("assets")
    .insert([
      {
        id: "abababab-abab-abab-abab-abababababab",
        symbol: "LTC",
        name: "Litecoin",
        decimals: 8,
        active: true,
        metadata: JSON.stringify({}),
      },
      {
        id: "cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd",
        symbol: "DOGE",
        name: "Dogecoin",
        decimals: 8,
        active: true,
        metadata: JSON.stringify({}),
      },
      {
        id: "efefefef-efef-efef-efef-efefefefefef",
        symbol: "ZEC",
        name: "Zcash",
        decimals: 8,
        active: true,
        metadata: JSON.stringify({}),
      },
    ])
    .onConflict("symbol")
    .merge(["name", "decimals", "active", "metadata"]);

  await knex("assets").whereIn("symbol", ["ETH", "SOL"]).update({ active: false });
  await knex("networks").whereIn("code", ["SOL", "ETH_SEPOLIA"]).update({ active: false });

  await knex("asset_networks")
    .insert([
      {
        id: LTC_ASSET_NETWORK_ID,
        asset_id: "abababab-abab-abab-abab-abababababab",
        network_id: "66666666-6666-6666-6666-666666666666",
        contract_address: null,
        bitgo_coin: "ltc",
        deposits_enabled: true,
        withdrawals_enabled: true,
        min_deposit_atoms: "100000",
        confirmations_override: null,
        metadata: JSON.stringify({
          provider: "BITGO",
          flat_withdrawal_fee_atoms: "10000",
          flat_withdrawal_fee: "0.0001",
        }),
      },
      {
        id: DOGE_ASSET_NETWORK_ID,
        asset_id: "cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd",
        network_id: "77777777-7777-7777-7777-777777777777",
        contract_address: null,
        bitgo_coin: "doge",
        deposits_enabled: true,
        withdrawals_enabled: true,
        min_deposit_atoms: "1000000000",
        confirmations_override: null,
        metadata: JSON.stringify({
          provider: "BITGO",
          flat_withdrawal_fee_atoms: "100000000",
          flat_withdrawal_fee: "1",
        }),
      },
      {
        id: ZEC_ASSET_NETWORK_ID,
        asset_id: "efefefef-efef-efef-efef-efefefefefef",
        network_id: "88888888-8888-8888-8888-888888888888",
        contract_address: null,
        bitgo_coin: "zec",
        deposits_enabled: true,
        withdrawals_enabled: true,
        min_deposit_atoms: "100000",
        confirmations_override: null,
        metadata: JSON.stringify({
          provider: "BITGO",
          flat_withdrawal_fee_atoms: "10000",
          flat_withdrawal_fee: "0.0001",
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

  await knex("asset_networks")
    .where({ id: BTC_ASSET_NETWORK_ID })
    .update({
      metadata: knex.raw(
        "metadata || ?::jsonb",
        [JSON.stringify({
          flat_withdrawal_fee_atoms: BTC_LOW_FEE_ATOMS,
          flat_withdrawal_fee: "0.00005",
        })]
      ),
    });

  await knex("asset_networks")
    .whereIn("id", [ETH_ASSET_NETWORK_ID, ETH_SEPOLIA_ASSET_NETWORK_ID, SOL_ASSET_NETWORK_ID])
    .update({
      deposits_enabled: false,
      withdrawals_enabled: false,
    });

  const hasWithdrawalPolicies = await knex.schema.hasTable("withdrawal_policies");
  if (hasWithdrawalPolicies) {
    await knex("withdrawal_policies")
      .insert([
        {
          asset_network_id: BTC_ASSET_NETWORK_ID,
          min_withdrawal_atoms: BTC_LOW_MIN_WITHDRAWAL_ATOMS,
          required_approvals: 1,
          high_risk_approvals: 2,
          enabled: true,
          metadata: JSON.stringify({
            withdrawalFeeAtoms: BTC_LOW_FEE_ATOMS,
            withdrawalFee: "0.00005",
            flatFee: true,
            feeAsset: "BTC",
            rationale: "Lower flat BTC withdrawal fee and minimum for the current product policy.",
          }),
        },
        {
          asset_network_id: LTC_ASSET_NETWORK_ID,
          min_withdrawal_atoms: "100000",
          required_approvals: 1,
          high_risk_approvals: 2,
          enabled: true,
          metadata: JSON.stringify({
            withdrawalFeeAtoms: "10000",
            withdrawalFee: "0.0001",
            flatFee: true,
            feeAsset: "LTC",
            rationale: "Flat fee for Litecoin BitGo withdrawals.",
          }),
        },
        {
          asset_network_id: DOGE_ASSET_NETWORK_ID,
          min_withdrawal_atoms: "1000000000",
          required_approvals: 1,
          high_risk_approvals: 2,
          enabled: true,
          metadata: JSON.stringify({
            withdrawalFeeAtoms: "100000000",
            withdrawalFee: "1",
            flatFee: true,
            feeAsset: "DOGE",
            rationale: "Flat fee for Dogecoin BitGo withdrawals.",
          }),
        },
        {
          asset_network_id: ZEC_ASSET_NETWORK_ID,
          min_withdrawal_atoms: "100000",
          required_approvals: 1,
          high_risk_approvals: 2,
          enabled: true,
          metadata: JSON.stringify({
            withdrawalFeeAtoms: "10000",
            withdrawalFee: "0.0001",
            flatFee: true,
            feeAsset: "ZEC",
            rationale: "Flat fee for Zcash BitGo withdrawals.",
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

    await knex("withdrawal_policies")
      .whereIn("asset_network_id", [ETH_ASSET_NETWORK_ID, ETH_SEPOLIA_ASSET_NETWORK_ID, SOL_ASSET_NETWORK_ID])
      .update({ enabled: false, updated_at: knex.fn.now() });
  }

  const hasMarkets = await knex.schema.hasTable("markets");
  if (hasMarkets) {
    await knex("markets").whereIn("symbol", ["ETH-ANM", "SOL-ANM"]).update({ active: false });

    await knex("markets")
      .insert([
        {
          id: knex.raw("gen_random_uuid()"),
          symbol: "LTC-ANM",
          base_asset: "LTC",
          quote_asset: "ANM",
          active: true,
          price_tick: "0.00000001",
          size_step: "0.00000001",
          min_order_size: "0.001",
          maker_fee_bps: 10,
          taker_fee_bps: 20,
          fee_asset: "ANM",
        },
        {
          id: knex.raw("gen_random_uuid()"),
          symbol: "DOGE-ANM",
          base_asset: "DOGE",
          quote_asset: "ANM",
          active: true,
          price_tick: "0.00000001",
          size_step: "0.00000001",
          min_order_size: "10",
          maker_fee_bps: 10,
          taker_fee_bps: 20,
          fee_asset: "ANM",
        },
        {
          id: knex.raw("gen_random_uuid()"),
          symbol: "ZEC-ANM",
          base_asset: "ZEC",
          quote_asset: "ANM",
          active: true,
          price_tick: "0.00000001",
          size_step: "0.00000001",
          min_order_size: "0.001",
          maker_fee_bps: 10,
          taker_fee_bps: 20,
          fee_asset: "ANM",
        },
      ])
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

    await knex.raw(`
      INSERT INTO market_sequence (market_id, last_seq)
      SELECT id, 0
      FROM markets
      WHERE symbol IN ('LTC-ANM', 'DOGE-ANM', 'ZEC-ANM')
      ON CONFLICT (market_id) DO NOTHING
    `);
  }
};

exports.down = async function down(knex) {
  await knex("asset_networks")
    .where({ id: BTC_ASSET_NETWORK_ID })
    .update({
      metadata: knex.raw(
        "metadata || ?::jsonb",
        [JSON.stringify({
          flat_withdrawal_fee_atoms: "30000",
          flat_withdrawal_fee: "0.0003",
        })]
      ),
    });

  await knex("assets").whereIn("symbol", ["ETH", "SOL"]).update({ active: true });
  await knex("networks").whereIn("code", ["SOL", "ETH_SEPOLIA"]).update({ active: true });
  await knex("asset_networks")
    .whereIn("id", [ETH_ASSET_NETWORK_ID, ETH_SEPOLIA_ASSET_NETWORK_ID, SOL_ASSET_NETWORK_ID])
    .update({ deposits_enabled: true, withdrawals_enabled: true });

  await knex("assets").whereIn("symbol", ["LTC", "DOGE", "ZEC"]).update({ active: false });
  await knex("networks").whereIn("code", ["LTC", "DOGE", "ZEC"]).update({ active: false });
  await knex("asset_networks")
    .whereIn("id", [LTC_ASSET_NETWORK_ID, DOGE_ASSET_NETWORK_ID, ZEC_ASSET_NETWORK_ID])
    .update({ deposits_enabled: false, withdrawals_enabled: false });

  const hasWithdrawalPolicies = await knex.schema.hasTable("withdrawal_policies");
  if (hasWithdrawalPolicies) {
    await knex("withdrawal_policies")
      .where({ asset_network_id: BTC_ASSET_NETWORK_ID })
      .update({
        min_withdrawal_atoms: "100000",
        enabled: true,
        metadata: JSON.stringify({
          withdrawalFeeAtoms: "30000",
          withdrawalFee: "0.0003",
          flatFee: true,
          feeAsset: "BTC",
          rationale: "Flat fee set above normal network fee targets to cover miner fees and exchange operations.",
        }),
        updated_at: knex.fn.now(),
      });

    await knex("withdrawal_policies")
      .whereIn("asset_network_id", [ETH_ASSET_NETWORK_ID, ETH_SEPOLIA_ASSET_NETWORK_ID, SOL_ASSET_NETWORK_ID])
      .update({ enabled: true, updated_at: knex.fn.now() });

    await knex("withdrawal_policies")
      .whereIn("asset_network_id", [LTC_ASSET_NETWORK_ID, DOGE_ASSET_NETWORK_ID, ZEC_ASSET_NETWORK_ID])
      .update({ enabled: false, updated_at: knex.fn.now() });
  }

  const hasMarkets = await knex.schema.hasTable("markets");
  if (hasMarkets) {
    await knex("markets").whereIn("symbol", ["ETH-ANM", "SOL-ANM"]).update({ active: true });
    await knex("markets").whereIn("symbol", ["LTC-ANM", "DOGE-ANM", "ZEC-ANM"]).update({ active: false });
  }
};
