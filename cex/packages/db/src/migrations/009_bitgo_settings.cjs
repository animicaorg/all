/**
 * Add BitGo settings table
 */

exports.up = async function (knex) {
  const hasTable = await knex.schema.hasTable("bitgo_configs");

  if (!hasTable) {
    await knex.schema.createTable("bitgo_configs", (table) => {
      table.string("id").primary().defaultTo("default");
      table.string("environment", 16).notNullable().defaultTo("test");
      table.string("base_url", 512).nullable();
      table.text("access_token_encrypted").nullable();
      table.text("webhook_secret_encrypted").nullable();
      table.jsonb("wallets").nullable();
      table.jsonb("coins").nullable();
      table.boolean("enabled").notNullable().defaultTo(false);
      table.uuid("updated_by").nullable();
      table.timestamp("created_at").notNullable().defaultTo(knex.fn.now());
      table.timestamp("updated_at").notNullable().defaultTo(knex.fn.now());
    });

    await knex.raw("CREATE INDEX IF NOT EXISTS idx_bitgo_configs_updated_by ON bitgo_configs(updated_by)");
  }
};

exports.down = async function (knex) {
  const hasTable = await knex.schema.hasTable("bitgo_configs");
  if (hasTable) {
    await knex.schema.dropTable("bitgo_configs");
  }
};
