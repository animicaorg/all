const DISABLED_ASSETS = ["BNB", "USDT"];

exports.up = async function up(knex) {
  await knex("assets").whereIn("symbol", DISABLED_ASSETS).update({ active: false });

  const hasAssetNetworks = await knex.schema.hasTable("asset_networks");
  if (hasAssetNetworks) {
    await knex.raw(
      `
        UPDATE asset_networks AS an
        SET deposits_enabled = false,
            withdrawals_enabled = false
        FROM assets AS a
        WHERE a.id = an.asset_id
          AND a.symbol = ANY(?::text[])
      `,
      [DISABLED_ASSETS]
    );
  }

  const hasMarkets = await knex.schema.hasTable("markets");
  if (hasMarkets) {
    await knex("markets")
      .whereIn("base_asset", DISABLED_ASSETS)
      .orWhereIn("quote_asset", DISABLED_ASSETS)
      .update({ active: false });
  }
};

exports.down = async function down(knex) {
  await knex("assets").whereIn("symbol", DISABLED_ASSETS).update({ active: true });

  const hasAssetNetworks = await knex.schema.hasTable("asset_networks");
  if (hasAssetNetworks) {
    await knex.raw(
      `
        UPDATE asset_networks AS an
        SET deposits_enabled = true,
            withdrawals_enabled = true
        FROM assets AS a
        WHERE a.id = an.asset_id
          AND a.symbol = ANY(?::text[])
      `,
      [DISABLED_ASSETS]
    );
  }
};
