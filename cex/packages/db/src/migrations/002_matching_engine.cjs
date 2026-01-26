exports.up = async function up(knex) {
  // Market configuration with tick/step/fees
  await knex.schema.alterTable("markets", (table) => {
    table.decimal("price_tick", 30, 10).notNullable().defaultTo("0.01");
    table.decimal("size_step", 30, 10).notNullable().defaultTo("0.001");
    table.decimal("min_order_size", 30, 10).notNullable().defaultTo("0.001");
    table.integer("maker_fee_bps").notNullable().defaultTo(10); // 0.1%
    table.integer("taker_fee_bps").notNullable().defaultTo(20); // 0.2%
    table.string("fee_asset").notNullable().defaultTo("USDT");
  });

  // Extend orders table for matching engine
  await knex.schema.alterTable("orders", (table) => {
    table.uuid("market_id").references("id").inTable("markets");
    table.string("order_type").notNullable().defaultTo("LIMIT"); // LIMIT, MARKET
    table.string("time_in_force").notNullable().defaultTo("GTC"); // GTC, IOC, FOK, POST_ONLY
    table.decimal("filled_quantity", 18, 8).notNullable().defaultTo(0);
    table.decimal("remaining_quantity", 18, 8);
    table.boolean("post_only").notNullable().defaultTo(false);
    table.timestamp("accepted_at", { useTz: true });
    table.uuid("replace_of").references("id").inTable("orders");
    table.string("reject_reason");
    table.timestamp("completed_at", { useTz: true });
    
    // Update status to use full lifecycle
    // NEW -> ACCEPTED -> PARTIAL_FILL -> FILLED / CANCELED / REJECTED / EXPIRED
    table.index(["market_id", "status"]);
    table.index(["market_id", "accepted_at"]);
  });

  // Trades table
  await knex.schema.createTable("trades", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.uuid("market_id").notNullable().references("id").inTable("markets");
    table.uuid("maker_order_id").notNullable().references("id").inTable("orders");
    table.uuid("taker_order_id").notNullable().references("id").inTable("orders");
    table.decimal("price", 18, 8).notNullable();
    table.decimal("size", 18, 8).notNullable();
    table.decimal("quote_amount", 30, 10).notNullable();
    table.decimal("maker_fee", 30, 10).notNullable();
    table.decimal("taker_fee", 30, 10).notNullable();
    table.string("fee_asset").notNullable();
    table.integer("fee_bps_maker").notNullable();
    table.integer("fee_bps_taker").notNullable();
    table.bigInteger("sequence").notNullable();
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    table.index(["market_id", "sequence"]);
    table.index(["maker_order_id"]);
    table.index(["taker_order_id"]);
    table.unique(["market_id", "sequence"]);
  });

  // Order events for audit trail
  await knex.schema.createTable("order_events", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.uuid("order_id").notNullable().references("id").inTable("orders");
    table.uuid("market_id").notNullable().references("id").inTable("markets");
    table.string("event_type").notNullable(); // ACCEPTED, PARTIAL_FILL, FILLED, CANCELED, REJECTED, EXPIRED
    table.bigInteger("sequence").notNullable();
    table.jsonb("payload").notNullable();
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    table.index(["order_id", "created_at"]);
    table.index(["market_id", "sequence"]);
    table.unique(["market_id", "sequence"]);
  });

  // Market sequence for deterministic ordering
  await knex.schema.createTable("market_sequence", (table) => {
    table.uuid("market_id").primary().references("id").inTable("markets");
    table.bigInteger("last_seq").notNullable().defaultTo(0);
    table.timestamp("updated_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
  });

  // Outbox events for exactly-once publishing
  await knex.schema.createTable("outbox_events", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.uuid("market_id").notNullable().references("id").inTable("markets");
    table.bigInteger("seq").notNullable();
    table.string("type").notNullable(); // ORDER_EVENT, TRADE_EVENT
    table.string("key").notNullable(); // dedup key
    table.jsonb("payload").notNullable();
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    table.timestamp("published_at", { useTz: true });
    
    table.unique(["market_id", "seq"]);
    table.unique(["key"]);
    table.index(["published_at"]);
  });

  // Idempotency keys
  await knex.schema.createTable("idempotency_keys", (table) => {
    table.string("key").primary();
    table.string("consumer").notNullable();
    table.jsonb("result").notNullable();
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    table.timestamp("expires_at", { useTz: true });
    
    table.index(["expires_at"]);
  });
};

exports.down = async function down(knex) {
  await knex.schema.dropTableIfExists("idempotency_keys");
  await knex.schema.dropTableIfExists("outbox_events");
  await knex.schema.dropTableIfExists("market_sequence");
  await knex.schema.dropTableIfExists("order_events");
  await knex.schema.dropTableIfExists("trades");
  
  await knex.schema.alterTable("orders", (table) => {
    table.dropColumn("market_id");
    table.dropColumn("order_type");
    table.dropColumn("time_in_force");
    table.dropColumn("filled_quantity");
    table.dropColumn("remaining_quantity");
    table.dropColumn("post_only");
    table.dropColumn("accepted_at");
    table.dropColumn("replace_of");
    table.dropColumn("reject_reason");
    table.dropColumn("completed_at");
  });
  
  await knex.schema.alterTable("markets", (table) => {
    table.dropColumn("price_tick");
    table.dropColumn("size_step");
    table.dropColumn("min_order_size");
    table.dropColumn("maker_fee_bps");
    table.dropColumn("taker_fee_bps");
    table.dropColumn("fee_asset");
  });
};
