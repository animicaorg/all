const AIRDROP_SETTINGS_ID = "default";
const AIRDROP_ASSET = "ANM";
const AIRDROP_POOL_ACCOUNT_ID = "system:airdrop";
const ANM_DECIMALS = 9n;
const DEFAULT_CLAIM_AMOUNT = "1.0000000000";
const DEFAULT_CLAIM_AMOUNT_ATOMS = "1000000000";
const DEFAULT_COOLDOWN_SECONDS = 4 * 60 * 60;
const DEFAULT_POOL_AMOUNT = "100000.0000000000";
const DEFAULT_POOL_AMOUNT_ATOMS = "100000000000000";

exports.up = async function up(knex) {
  await knex.schema.createTable("airdrop_settings", (table) => {
    table.string("id").primary();
    table.string("asset").notNullable().defaultTo(AIRDROP_ASSET);
    table.decimal("claim_amount", 30, 10).notNullable().defaultTo(DEFAULT_CLAIM_AMOUNT);
    table.decimal("claim_amount_atoms", 30, 0).notNullable().defaultTo(DEFAULT_CLAIM_AMOUNT_ATOMS);
    table.integer("cooldown_seconds").notNullable().defaultTo(DEFAULT_COOLDOWN_SECONDS);
    table.boolean("enabled").notNullable().defaultTo(true);
    table.string("pool_account_id").notNullable().defaultTo(AIRDROP_POOL_ACCOUNT_ID);
    table.jsonb("metadata").notNullable().defaultTo(knex.raw("'{}'::jsonb"));
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    table.timestamp("updated_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
  });

  await knex.schema.createTable("airdrop_claims", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.uuid("user_id").notNullable().references("id").inTable("users");
    table.string("asset").notNullable();
    table.decimal("amount", 30, 10).notNullable();
    table.decimal("amount_atoms", 30, 0).notNullable();
    table.timestamp("claimed_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());

    table.index(["user_id", "claimed_at"]);
    table.index(["asset", "claimed_at"]);
  });

  await knex.schema.createTable("airdrop_deposits", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.uuid("user_id").notNullable().references("id").inTable("users");
    table.string("asset").notNullable();
    table.decimal("amount", 30, 10).notNullable();
    table.decimal("amount_atoms", 30, 0).notNullable();
    table.timestamp("deposited_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());

    table.index(["user_id", "deposited_at"]);
    table.index(["asset", "deposited_at"]);
  });

  await knex.schema.createTable("api_keys", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.uuid("user_id").notNullable().references("id").inTable("users");
    table.string("name", 120).notNullable();
    table.string("key_prefix", 32).notNullable();
    table.string("key_hash", 128).notNullable().unique();
    table.jsonb("scopes").notNullable().defaultTo(knex.raw("'[\"read\"]'::jsonb"));
    table.timestamp("last_used_at", { useTz: true }).nullable();
    table.timestamp("revoked_at", { useTz: true }).nullable();
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());

    table.index(["user_id", "created_at"]);
    table.index(["user_id", "revoked_at"]);
    table.index("key_prefix");
  });

  await knex.schema.createTable("trading_bots", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.uuid("user_id").notNullable().references("id").inTable("users");
    table.string("mode", 32).notNullable();
    table.string("market").notNullable();
    table.string("status", 32).notNullable().defaultTo("STOPPED");
    table.jsonb("config").notNullable().defaultTo(knex.raw("'{}'::jsonb"));
    table.jsonb("last_order_ids").notNullable().defaultTo(knex.raw("'[]'::jsonb"));
    table.text("last_error").nullable();
    table.timestamp("last_run_at", { useTz: true }).nullable();
    table.timestamp("next_run_at", { useTz: true }).nullable();
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    table.timestamp("updated_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());

    table.index(["user_id", "created_at"]);
    table.index(["status", "next_run_at"]);
    table.index(["user_id", "status"]);
  });

  await knex.raw(`
    ALTER TABLE trading_bots
    ADD CONSTRAINT trading_bots_mode_check
    CHECK (mode IN ('DCA', 'GRID', 'MAKER'))
  `);

  await knex.raw(`
    ALTER TABLE trading_bots
    ADD CONSTRAINT trading_bots_status_check
    CHECK (status IN ('RUNNING', 'STOPPED', 'ERROR'))
  `);

  await knex.raw(`
    CREATE UNIQUE INDEX trading_bots_one_running_per_user
    ON trading_bots(user_id)
    WHERE status = 'RUNNING'
  `);

  await knex("airdrop_settings")
    .insert({
      id: AIRDROP_SETTINGS_ID,
      asset: AIRDROP_ASSET,
      claim_amount: DEFAULT_CLAIM_AMOUNT,
      claim_amount_atoms: DEFAULT_CLAIM_AMOUNT_ATOMS,
      cooldown_seconds: DEFAULT_COOLDOWN_SECONDS,
      enabled: true,
      pool_account_id: AIRDROP_POOL_ACCOUNT_ID,
      metadata: JSON.stringify({ defaultDecimals: Number(ANM_DECIMALS) }),
    })
    .onConflict("id")
    .ignore();

  await knex.raw(
    `
      INSERT INTO balances (account_id, asset, available, locked, available_atoms, locked_atoms, updated_at)
      VALUES (?, ?, ?::numeric, 0, ?::numeric, 0, NOW())
      ON CONFLICT (account_id, asset)
      DO UPDATE SET
        available = GREATEST(balances.available, EXCLUDED.available),
        available_atoms = GREATEST(balances.available_atoms, EXCLUDED.available_atoms),
        updated_at = NOW()
    `,
    [AIRDROP_POOL_ACCOUNT_ID, AIRDROP_ASSET, DEFAULT_POOL_AMOUNT, DEFAULT_POOL_AMOUNT_ATOMS]
  );
};

exports.down = async function down(knex) {
  await knex.raw("DROP INDEX IF EXISTS trading_bots_one_running_per_user");
  await knex.schema.dropTableIfExists("trading_bots");
  await knex.schema.dropTableIfExists("api_keys");
  await knex.schema.dropTableIfExists("airdrop_deposits");
  await knex.schema.dropTableIfExists("airdrop_claims");
  await knex.schema.dropTableIfExists("airdrop_settings");

  await knex("balances")
    .where({ account_id: AIRDROP_POOL_ACCOUNT_ID, asset: AIRDROP_ASSET })
    .delete();
};
