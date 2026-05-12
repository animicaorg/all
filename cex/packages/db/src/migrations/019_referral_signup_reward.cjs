const DEFAULT_REWARD_ATOMS = "100000000000"; // 100 ANM with 9 decimals

async function addColumnIfMissing(knex, tableName, columnName, callback) {
  const exists = await knex.schema.hasColumn(tableName, columnName);
  if (!exists) {
    await knex.schema.alterTable(tableName, (table) => callback(table));
  }
}

exports.up = async function up(knex) {
  await addColumnIfMissing(knex, "referrals", "referred_reward_atoms", (table) => {
    table.decimal("referred_reward_atoms", 30, 0).notNullable().defaultTo(DEFAULT_REWARD_ATOMS);
  });

  await addColumnIfMissing(knex, "referrals", "referred_rewarded_at", (table) => {
    table.timestamp("referred_rewarded_at", { useTz: true });
  });

  await addColumnIfMissing(knex, "referral_reward_events", "reward_role", (table) => {
    table.string("reward_role").notNullable().defaultTo("referrer");
  });

  await addColumnIfMissing(knex, "referral_reward_events", "recipient_user_id", (table) => {
    table.uuid("recipient_user_id").references("id").inTable("users");
  });

  await knex.raw(`
    UPDATE referral_reward_events AS event
    SET recipient_user_id = referrals.referrer_user_id
    FROM referrals
    WHERE event.referral_id = referrals.id
      AND event.recipient_user_id IS NULL
  `);

  await knex.raw(`
    ALTER TABLE referral_reward_events
    ALTER COLUMN recipient_user_id SET NOT NULL
  `);

  await knex.raw(`
    ALTER TABLE referral_reward_events
    DROP CONSTRAINT IF EXISTS referral_reward_events_reward_role_check
  `);
  await knex.raw(`
    ALTER TABLE referral_reward_events
    ADD CONSTRAINT referral_reward_events_reward_role_check
    CHECK (reward_role IN ('referrer', 'referred'))
  `);

  await knex.raw(`
    CREATE UNIQUE INDEX IF NOT EXISTS referral_reward_events_one_credit_per_role
    ON referral_reward_events (referral_id, reward_role)
    WHERE status = 'credited'
  `);

  await knex.raw(`
    UPDATE referrals
    SET referred_rewarded_at = rewarded_at
    WHERE status = 'rewarded'
      AND referred_rewarded_at IS NULL
      AND EXISTS (
        SELECT 1
        FROM referral_reward_events
        WHERE referral_reward_events.referral_id = referrals.id
          AND referral_reward_events.status = 'credited'
          AND referral_reward_events.reward_role = 'referred'
      )
  `);
};

exports.down = async function down(knex) {
  await knex.raw("DROP INDEX IF EXISTS referral_reward_events_one_credit_per_role");
  await knex.raw(`
    ALTER TABLE referral_reward_events
    DROP CONSTRAINT IF EXISTS referral_reward_events_reward_role_check
  `);

  const hasRecipient = await knex.schema.hasColumn("referral_reward_events", "recipient_user_id");
  if (hasRecipient) {
    await knex.schema.alterTable("referral_reward_events", (table) => {
      table.dropColumn("recipient_user_id");
    });
  }

  const hasRewardRole = await knex.schema.hasColumn("referral_reward_events", "reward_role");
  if (hasRewardRole) {
    await knex.schema.alterTable("referral_reward_events", (table) => {
      table.dropColumn("reward_role");
    });
  }

  const hasReferredRewardedAt = await knex.schema.hasColumn("referrals", "referred_rewarded_at");
  if (hasReferredRewardedAt) {
    await knex.schema.alterTable("referrals", (table) => {
      table.dropColumn("referred_rewarded_at");
    });
  }

  const hasReferredRewardAtoms = await knex.schema.hasColumn("referrals", "referred_reward_atoms");
  if (hasReferredRewardAtoms) {
    await knex.schema.alterTable("referrals", (table) => {
      table.dropColumn("referred_reward_atoms");
    });
  }
};
