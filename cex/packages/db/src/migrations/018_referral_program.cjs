const DEFAULT_REWARD_ATOMS = "100000000000"; // 100 ANM with 9 decimals

exports.up = async function up(knex) {
  const hasReferralCodes = await knex.schema.hasTable("referral_codes");
  if (!hasReferralCodes) {
    await knex.schema.createTable("referral_codes", (table) => {
      table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
      table.uuid("user_id").notNullable().references("id").inTable("users").onDelete("CASCADE").unique();
      table.string("code").notNullable();
      table.boolean("active").notNullable().defaultTo(true);
      table.jsonb("metadata").notNullable().defaultTo(knex.raw("'{}'::jsonb"));
      table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
      table.timestamp("updated_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());

      table.index(["user_id", "active"]);
      table.index(["created_at"]);
    });
  }

  await knex.raw(`
    CREATE UNIQUE INDEX IF NOT EXISTS referral_codes_code_unique_ci
    ON referral_codes (UPPER(code))
  `);

  const hasReferrals = await knex.schema.hasTable("referrals");
  if (!hasReferrals) {
    await knex.schema.createTable("referrals", (table) => {
      table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
      table.uuid("referrer_user_id").notNullable().references("id").inTable("users").onDelete("RESTRICT");
      table.uuid("referred_user_id").notNullable().references("id").inTable("users").onDelete("CASCADE").unique();
      table.string("referral_code").notNullable();
      table.string("status").notNullable().defaultTo("pending");
      table.text("qualification_reason");
      table.decimal("reward_atoms", 30, 0).notNullable().defaultTo(DEFAULT_REWARD_ATOMS);
      table.timestamp("rewarded_at", { useTz: true });
      table.string("ip_address");
      table.string("user_agent");
      table.string("device_fingerprint");
      table.jsonb("metadata").notNullable().defaultTo(knex.raw("'{}'::jsonb"));
      table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
      table.timestamp("updated_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());

      table.check("referrer_user_id <> referred_user_id", [], "referrals_not_self_check");
      table.index(["referrer_user_id", "status", "created_at"]);
      table.index(["status", "updated_at"]);
      table.index(["referral_code"]);
      table.index(["ip_address", "created_at"]);
      table.index(["device_fingerprint", "created_at"]);
    });
  }

  await knex.raw(`
    ALTER TABLE referrals
    DROP CONSTRAINT IF EXISTS referrals_status_check
  `);
  await knex.raw(`
    ALTER TABLE referrals
    ADD CONSTRAINT referrals_status_check
    CHECK (status IN ('pending', 'qualified', 'rewarded', 'rejected', 'pending_insufficient_pool'))
  `);

  const hasRewardEvents = await knex.schema.hasTable("referral_reward_events");
  if (!hasRewardEvents) {
    await knex.schema.createTable("referral_reward_events", (table) => {
      table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
      table.uuid("referral_id").notNullable().references("id").inTable("referrals").onDelete("CASCADE");
      table.decimal("amount_atoms", 30, 0).notNullable();
      table.string("asset").notNullable().defaultTo("ANM");
      table.string("source").notNullable().defaultTo("airdrop_pool");
      table.string("status").notNullable();
      table.uuid("ledger_transaction_id").references("id").inTable("ledger_transactions");
      table.jsonb("metadata").notNullable().defaultTo(knex.raw("'{}'::jsonb"));
      table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());

      table.index(["referral_id", "created_at"]);
      table.index(["status", "created_at"]);
    });
  }

  await knex.raw(`
    ALTER TABLE referral_reward_events
    DROP CONSTRAINT IF EXISTS referral_reward_events_status_check
  `);
  await knex.raw(`
    ALTER TABLE referral_reward_events
    ADD CONSTRAINT referral_reward_events_status_check
    CHECK (status IN ('credited', 'insufficient_pool', 'skipped', 'failed'))
  `);
};

exports.down = async function down(knex) {
  await knex.schema.dropTableIfExists("referral_reward_events");
  await knex.schema.dropTableIfExists("referrals");
  await knex.schema.dropTableIfExists("referral_codes");
};
