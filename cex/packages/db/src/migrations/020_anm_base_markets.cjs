const ANM_BASE_MARKETS = [
  { symbol: "ANM-BTC", base_asset: "ANM", quote_asset: "BTC", fee_asset: "BTC" },
  { symbol: "ANM-DOGE", base_asset: "ANM", quote_asset: "DOGE", fee_asset: "DOGE" },
  { symbol: "ANM-LTC", base_asset: "ANM", quote_asset: "LTC", fee_asset: "LTC" },
  { symbol: "ANM-ZEC", base_asset: "ANM", quote_asset: "ZEC", fee_asset: "ZEC" },
];

const OLD_ANM_QUOTE_MARKETS = ["BTC-ANM", "DOGE-ANM", "LTC-ANM", "ZEC-ANM"];

exports.up = async function up(knex) {
  await knex("markets").whereIn("symbol", OLD_ANM_QUOTE_MARKETS).update({ active: false });

  await knex("markets")
    .insert(
      ANM_BASE_MARKETS.map((market) => ({
        id: knex.raw("gen_random_uuid()"),
        ...market,
        active: true,
        price_tick: "0.00000001",
        size_step: "0.00000001",
        min_order_size: "1",
        maker_fee_bps: 10,
        taker_fee_bps: 20,
      }))
    )
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

  const hasMarketSequence = await knex.schema.hasTable("market_sequence");
  if (hasMarketSequence) {
    await knex.raw(
      `
        INSERT INTO market_sequence (market_id, last_seq)
        SELECT id, 0
        FROM markets
        WHERE symbol = ANY(?::text[])
        ON CONFLICT (market_id) DO NOTHING
      `,
      [ANM_BASE_MARKETS.map((market) => market.symbol)]
    );
  }
};

exports.down = async function down(knex) {
  await knex("markets")
    .whereIn(
      "symbol",
      ANM_BASE_MARKETS.map((market) => market.symbol)
    )
    .update({ active: false });

  await knex("markets").whereIn("symbol", OLD_ANM_QUOTE_MARKETS).update({ active: true });
};
