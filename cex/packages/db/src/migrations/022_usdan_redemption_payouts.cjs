async function addColumnIfMissing(knex, tableName, columnName, callback) {
  const exists = await knex.schema.hasColumn(tableName, columnName);
  if (!exists) {
    await knex.schema.alterTable(tableName, (table) => callback(table));
  }
}

exports.up = async function up(knex) {
  await addColumnIfMissing(knex, "usdan_config", "min_redemption_cents", (table) => {
    table.integer("min_redemption_cents").notNullable().defaultTo(1000);
  });
  await addColumnIfMissing(knex, "usdan_config", "max_redemption_cents", (table) => {
    table.integer("max_redemption_cents").notNullable().defaultTo(1000000);
  });
  await addColumnIfMissing(knex, "usdan_config", "per_user_daily_redemption_limit_cents", (table) => {
    table.integer("per_user_daily_redemption_limit_cents").notNullable().defaultTo(2500000);
  });
  await addColumnIfMissing(knex, "usdan_config", "bank_payout_fee_cents", (table) => {
    table.integer("bank_payout_fee_cents").notNullable().defaultTo(100);
  });
  await addColumnIfMissing(knex, "usdan_config", "card_payout_fee_bps", (table) => {
    table.integer("card_payout_fee_bps").notNullable().defaultTo(150);
  });
  await addColumnIfMissing(knex, "usdan_config", "card_payout_fee_fixed_cents", (table) => {
    table.integer("card_payout_fee_fixed_cents").notNullable().defaultTo(25);
  });

  await knex.raw(`
    CREATE TABLE IF NOT EXISTS usdan_redemption_requests (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id uuid NOT NULL REFERENCES users(id),
      wallet_address varchar NOT NULL,
      source_type varchar NOT NULL DEFAULT 'EXCHANGE_BALANCE',
      amount_atoms numeric(30,0) NOT NULL,
      usd_amount_cents integer NOT NULL,
      status varchar NOT NULL DEFAULT 'REQUESTED',
      external_reference varchar NOT NULL UNIQUE,
      treasury_reference varchar,
      chain_tx_hash varchar,
      payout_reference varchar,
      idempotency_key varchar NOT NULL,
      failure_reason varchar,
      metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
      created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
      completed_at timestamptz,
      fee_cents integer NOT NULL DEFAULT 0,
      net_payout_cents integer NOT NULL DEFAULT 0,
      payout_destination_type varchar NOT NULL DEFAULT 'BANK_ACCOUNT',
      payout_destination_reference varchar,
      payout_provider varchar,
      UNIQUE (user_id, idempotency_key)
    )
  `);

  await addColumnIfMissing(knex, "usdan_redemption_requests", "fee_cents", (table) => {
    table.integer("fee_cents").notNullable().defaultTo(0);
  });
  await addColumnIfMissing(knex, "usdan_redemption_requests", "net_payout_cents", (table) => {
    table.integer("net_payout_cents").notNullable().defaultTo(0);
  });
  await addColumnIfMissing(knex, "usdan_redemption_requests", "payout_destination_type", (table) => {
    table.string("payout_destination_type").notNullable().defaultTo("BANK_ACCOUNT");
  });
  await addColumnIfMissing(knex, "usdan_redemption_requests", "payout_destination_reference", (table) => {
    table.string("payout_destination_reference");
  });
  await addColumnIfMissing(knex, "usdan_redemption_requests", "payout_provider", (table) => {
    table.string("payout_provider");
  });

  await knex.raw(`
    CREATE INDEX IF NOT EXISTS usdan_redemption_requests_user_status_created_at
    ON usdan_redemption_requests (user_id, status, created_at)
  `);
  await knex.raw(`
    CREATE INDEX IF NOT EXISTS usdan_redemption_requests_status_updated_at
    ON usdan_redemption_requests (status, updated_at)
  `);
};

exports.down = async function down(knex) {
  await knex.schema.dropTableIfExists("usdan_redemption_requests");

  const columns = [
    "card_payout_fee_fixed_cents",
    "card_payout_fee_bps",
    "bank_payout_fee_cents",
    "per_user_daily_redemption_limit_cents",
    "max_redemption_cents",
    "min_redemption_cents",
  ];

  for (const column of columns) {
    const exists = await knex.schema.hasColumn("usdan_config", column);
    if (exists) {
      await knex.schema.alterTable("usdan_config", (table) => {
        table.dropColumn(column);
      });
    }
  }
};
