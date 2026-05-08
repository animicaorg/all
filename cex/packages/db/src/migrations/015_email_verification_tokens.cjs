exports.up = async function up(knex) {
  const hasTable = await knex.schema.hasTable("email_verification_tokens");
  if (!hasTable) {
    await knex.schema.createTable("email_verification_tokens", (table) => {
      table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
      table.uuid("user_id").notNullable().references("id").inTable("users").onDelete("CASCADE");
      table.string("email").notNullable();
      table.string("token_hash", 64).notNullable().unique();
      table.timestamp("expires_at", { useTz: true }).notNullable();
      table.timestamp("consumed_at", { useTz: true });
      table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());

      table.index(["user_id", "consumed_at"]);
      table.index(["email"]);
      table.index(["expires_at"]);
    });
  }

  await knex.raw("CREATE INDEX IF NOT EXISTS idx_users_email_verified ON users(email_verified)");
};

exports.down = async function down(knex) {
  await knex.raw("DROP INDEX IF EXISTS idx_users_email_verified");
  await knex.schema.dropTableIfExists("email_verification_tokens");
};
