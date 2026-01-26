/**
 * Migration 004: Deposits Infrastructure
 * 
 * Creates tables for managing cryptocurrency deposits from BitGo:
 * - networks: blockchain networks (BTC, ETH, etc.)
 * - assets: tradeable assets (BTC, ETH, USDT, etc.)
 * - asset_networks: maps assets to networks with contract addresses
 * - wallets: BitGo wallet tracking
 * - user_deposit_addresses: user-assigned deposit addresses
 * - deposits: deposit transaction records
 * - audit_logs: audit trail for all deposit operations
 * - deposit_outbox: outbox pattern for crediting balances via ledger-service
 */

exports.up = async function up(knex) {
  // Networks table - blockchain networks
  await knex.schema.createTable("networks", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.string("code").notNullable().unique(); // BTC, ETH, ETH_SEPOLIA, etc.
    table.string("name").notNullable(); // "Bitcoin Mainnet", "Ethereum", etc.
    table.string("type").notNullable(); // UTXO, EVM, MEMO_BASED
    table.integer("confirmations_required").notNullable().defaultTo(6);
    table.boolean("active").notNullable().defaultTo(true);
    table.jsonb("metadata").notNullable().defaultTo("{}"); // chain_id, explorer_url, etc.
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    table.index(["code", "active"]);
  });

  // Assets table - tradeable assets
  await knex.schema.createTable("assets", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.string("symbol").notNullable().unique(); // BTC, ETH, USDT, USDC, etc.
    table.string("name").notNullable(); // "Bitcoin", "Tether USD", etc.
    table.integer("decimals").notNullable(); // 8 for BTC, 18 for ETH, 6 for USDT
    table.boolean("active").notNullable().defaultTo(true);
    table.jsonb("metadata").notNullable().defaultTo("{}");
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
  });

  // Asset Networks table - maps assets to networks
  await knex.schema.createTable("asset_networks", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.uuid("asset_id").notNullable().references("id").inTable("assets");
    table.uuid("network_id").notNullable().references("id").inTable("networks");
    table.string("contract_address"); // null for native assets, contract for tokens
    table.string("bitgo_coin"); // BitGo coin identifier (e.g., "btc", "eth", "erc20:usdt")
    table.boolean("deposits_enabled").notNullable().defaultTo(true);
    table.boolean("withdrawals_enabled").notNullable().defaultTo(true);
    table.decimal("min_deposit_atoms", 30, 0); // minimum deposit amount
    table.integer("confirmations_override"); // override network default if needed
    table.jsonb("metadata").notNullable().defaultTo("{}");
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    table.unique(["asset_id", "network_id", "contract_address"]);
    table.index(["asset_id", "deposits_enabled"]);
    table.index(["network_id", "deposits_enabled"]);
  });

  // Wallets table - BitGo wallet tracking
  await knex.schema.createTable("wallets", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.string("provider").notNullable().defaultTo("BITGO");
    table.string("wallet_id").notNullable(); // BitGo wallet ID
    table.uuid("asset_network_id").notNullable().references("id").inTable("asset_networks");
    table.string("status").notNullable().defaultTo("ACTIVE"); // ACTIVE, PAUSED, ARCHIVED
    table.jsonb("metadata").notNullable().defaultTo("{}"); // BitGo wallet details
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    table.timestamp("updated_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    table.unique(["provider", "wallet_id"]);
    table.index(["asset_network_id", "status"]);
  });

  // User Deposit Addresses table - assigned addresses per user
  await knex.schema.createTable("user_deposit_addresses", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.uuid("user_id").notNullable().references("id").inTable("users");
    table.uuid("asset_network_id").notNullable().references("id").inTable("asset_networks");
    table.uuid("wallet_id").notNullable().references("id").inTable("wallets");
    table.string("address").notNullable();
    table.string("tag"); // memo/destination tag for MEMO_BASED networks
    table.string("label"); // user-friendly label
    table.string("status").notNullable().defaultTo("ACTIVE"); // ACTIVE, DISABLED
    table.timestamp("assigned_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    table.timestamp("last_used_at", { useTz: true });
    
    table.unique(["asset_network_id", "address", "tag"]);
    table.index(["user_id", "asset_network_id"]);
    table.index(["wallet_id"]);
    table.index(["address", "tag"]);
  });

  // Deposits table - deposit transaction records
  await knex.schema.createTable("deposits", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.uuid("user_id").references("id").inTable("users"); // null if unassigned
    table.uuid("asset_network_id").notNullable().references("id").inTable("asset_networks");
    table.string("provider").notNullable().defaultTo("BITGO");
    table.string("provider_event_id"); // unique per webhook/transfer
    table.string("wallet_id").notNullable(); // BitGo wallet ID
    table.string("transfer_id"); // BitGo transfer ID
    table.string("txid").notNullable();
    table.string("vout"); // UTXO output index or EVM log index
    table.string("address").notNullable();
    table.string("tag"); // memo/destination tag
    table.decimal("amount_atoms", 30, 0).notNullable();
    table.integer("confirmations").notNullable().defaultTo(0);
    table.integer("confirmations_required").notNullable();
    table.bigInteger("block_height");
    table.string("block_hash");
    table.string("status").notNullable().defaultTo("DETECTED"); // DETECTED, CONFIRMED, CREDITED, FAILED, REORGED, HOLD
    table.timestamp("detected_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    table.timestamp("confirmed_at", { useTz: true });
    table.timestamp("credited_at", { useTz: true });
    table.boolean("unassigned").notNullable().defaultTo(false); // true if no user mapping
    table.boolean("risk_hold").notNullable().defaultTo(false); // true if flagged by risk
    table.string("risk_reason"); // reason for hold
    table.jsonb("raw").notNullable().defaultTo("{}"); // raw webhook payload
    table.jsonb("metadata").notNullable().defaultTo("{}");
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    table.timestamp("updated_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    // Uniqueness: one deposit per economic unit
    table.unique(["asset_network_id", "txid", "address", "tag", "vout"]);
    
    table.index(["user_id", "status"]);
    table.index(["status", "confirmations"]);
    table.index(["txid"]);
    table.index(["address", "tag"]);
    table.index(["created_at"]);
  });

  // Partial unique index for provider_event_id (only index non-null values)
  await knex.raw(`
    CREATE UNIQUE INDEX "deposits_provider_event_id_unique" ON "deposits" ("provider_event_id") WHERE "provider_event_id" IS NOT NULL
  `);

  // Audit Logs table - comprehensive audit trail
  await knex.schema.createTable("audit_logs", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.string("event_type").notNullable(); // DEPOSIT_DETECTED, DEPOSIT_CONFIRMED, DEPOSIT_CREDITED, etc.
    table.string("resource_type").notNullable(); // DEPOSIT, USER, WITHDRAWAL, etc.
    table.string("resource_id").notNullable();
    table.uuid("user_id").references("id").inTable("users"); // affected user
    table.uuid("actor_id").references("id").inTable("users"); // admin who performed action
    table.string("actor_type").notNullable().defaultTo("SYSTEM"); // SYSTEM, ADMIN, USER
    table.jsonb("changes").notNullable().defaultTo("{}"); // before/after or event details
    table.jsonb("metadata").notNullable().defaultTo("{}");
    table.string("ip_address");
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    table.index(["event_type", "created_at"]);
    table.index(["resource_type", "resource_id"]);
    table.index(["user_id", "created_at"]);
  });

  // Deposit Outbox table - outbox pattern for ledger credits
  await knex.schema.createTable("deposit_outbox", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.uuid("deposit_id").notNullable().references("id").inTable("deposits");
    table.string("idempotency_key").notNullable().unique(); // "deposit:<deposit_id>"
    table.jsonb("payload").notNullable(); // command to send to ledger-service
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    table.timestamp("processed_at", { useTz: true });
    table.integer("retry_count").notNullable().defaultTo(0);
    table.timestamp("last_retry_at", { useTz: true });
    table.jsonb("last_error");
    
    table.index(["processed_at"]);
    table.index(["created_at"]);
    table.index(["retry_count", "processed_at"]);
  });

  // Extend idempotency_keys if needed
  // Already exists from migration 002, but we'll use it for webhook deduplication
};

exports.down = async function down(knex) {
  await knex.schema.dropTableIfExists("deposit_outbox");
  await knex.schema.dropTableIfExists("audit_logs");
  await knex.schema.dropTableIfExists("deposits");
  await knex.schema.dropTableIfExists("user_deposit_addresses");
  await knex.schema.dropTableIfExists("wallets");
  await knex.schema.dropTableIfExists("asset_networks");
  await knex.schema.dropTableIfExists("assets");
  await knex.schema.dropTableIfExists("networks");
};
