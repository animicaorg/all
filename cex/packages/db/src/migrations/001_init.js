exports.up = async function up(knex) {
  await knex.raw('CREATE EXTENSION IF NOT EXISTS "pgcrypto"');

  await knex.schema.createTable("users", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.string("email").notNullable().unique();
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
  });

  await knex.schema.createTable("markets", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.string("symbol").notNullable().unique();
    table.string("base_asset").notNullable();
    table.string("quote_asset").notNullable();
    table.boolean("active").notNullable().defaultTo(true);
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
  });

  await knex.schema.createTable("orders", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.uuid("user_id").notNullable().references("id").inTable("users");
    table.string("client_order_id").notNullable();
    table.string("market").notNullable();
    table.string("side").notNullable();
    table.decimal("price", 18, 8).notNullable();
    table.decimal("quantity", 18, 8).notNullable();
    table.string("status").notNullable().defaultTo("accepted");
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    table.unique(["user_id", "client_order_id"]);
  });

  await knex.schema.createTable("journal_entries", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.string("event_id").notNullable();
    table.string("account_id").notNullable();
    table.string("asset").notNullable();
    table.decimal("amount", 30, 10).notNullable();
    table.string("direction").notNullable();
    table.string("description").notNullable();
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
  });

  await knex.schema.createTable("balances", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.string("account_id").notNullable();
    table.string("asset").notNullable();
    table.decimal("available", 30, 10).notNullable().defaultTo(0);
    table.decimal("locked", 30, 10).notNullable().defaultTo(0);
    table.timestamp("updated_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    table.unique(["account_id", "asset"]);
  });

  await knex.schema.createTable("processed_events", (table) => {
    table.string("event_id").primary();
    table.string("consumer").notNullable();
    table.timestamp("processed_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
  });
};

exports.down = async function down(knex) {
  await knex.schema.dropTableIfExists("processed_events");
  await knex.schema.dropTableIfExists("balances");
  await knex.schema.dropTableIfExists("journal_entries");
  await knex.schema.dropTableIfExists("orders");
  await knex.schema.dropTableIfExists("markets");
  await knex.schema.dropTableIfExists("users");
};
