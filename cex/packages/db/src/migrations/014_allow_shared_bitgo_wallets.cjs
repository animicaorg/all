exports.up = async function up(knex) {
  await knex.raw(`
    ALTER TABLE wallets
    DROP CONSTRAINT IF EXISTS wallets_provider_wallet_id_unique
  `);

  await knex.raw(`
    DROP INDEX IF EXISTS wallets_provider_wallet_id_unique
  `);

  await knex.raw(`
    CREATE UNIQUE INDEX IF NOT EXISTS wallets_provider_wallet_id_asset_network_id_unique
    ON wallets (provider, wallet_id, asset_network_id)
  `);
};

exports.down = async function down(knex) {
  await knex.raw(`
    DROP INDEX IF EXISTS wallets_provider_wallet_id_asset_network_id_unique
  `);

  await knex.raw(`
    CREATE UNIQUE INDEX IF NOT EXISTS wallets_provider_wallet_id_unique
    ON wallets (provider, wallet_id)
  `);
};
