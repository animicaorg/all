const { v4: uuidv4 } = require("uuid");

exports.seed = async function seed(knex) {
  const userId = uuidv4();

  await knex("users").insert({
    id: userId,
    email: "test@cex.local"
  }).onConflict("email").ignore();

  const markets = [
    {
      symbol: "BTC-ANM",
      base_asset: "BTC",
      quote_asset: "ANM",
      price_tick: "0.00000001",
      size_step: "0.00000001",
      min_order_size: "0.0001",
      maker_fee_bps: 10,
      taker_fee_bps: 20,
      fee_asset: "ANM",
      active: true
    },
    {
      symbol: "ETH-ANM",
      base_asset: "ETH",
      quote_asset: "ANM",
      price_tick: "0.00000001",
      size_step: "0.00000001",
      min_order_size: "0.001",
      maker_fee_bps: 10,
      taker_fee_bps: 20,
      fee_asset: "ANM",
      active: true
    },
    {
      symbol: "SOL-ANM",
      base_asset: "SOL",
      quote_asset: "ANM",
      price_tick: "0.00000001",
      size_step: "0.00000001",
      min_order_size: "0.01",
      maker_fee_bps: 10,
      taker_fee_bps: 20,
      fee_asset: "ANM",
      active: true
    }
  ];

  for (const market of markets) {
    const [row] = await knex("markets")
      .insert({ id: uuidv4(), ...market })
      .onConflict("symbol")
      .merge(market)
      .returning(["id"]);

    const marketId = row?.id;
    if (marketId && await knex.schema.hasTable("market_sequence")) {
      await knex("market_sequence")
        .insert({ market_id: marketId, last_seq: 0 })
        .onConflict("market_id")
        .ignore();
    }
  }

  const existingUser = await knex("users").where({ email: "test@cex.local" }).first("id");
  if (existingUser) {
    await knex("balances").insert({
      id: uuidv4(),
      account_id: existingUser.id,
      asset: "ANM",
      available: 1000,
      locked: 0
    }).onConflict(["account_id", "asset"]).ignore();
  }
};
