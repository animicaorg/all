/**
 * Migration 003: Double-Entry Ledger Schema
 * 
 * This migration creates a proper double-entry accounting system with:
 * - ledger_accounts: Chart of accounts (USER:AVAILABLE, USER:LOCKED, SYSTEM:FEE, etc.)
 * - ledger_transactions: Headers for each ledger transaction (TRADE_SETTLE, TRANSFER, etc.)
 * - ledger_entries: Individual debit/credit entries (must balance per transaction)
 * - ledger_event_offsets: Track processed event sequences per market
 * - reconciliation_reports: Store reconciliation results
 * - order_locks: Track locked funds per order (optional, for release calculation)
 */

exports.up = async function up(knex) {
  // Ledger accounts table - chart of accounts
  // Each user has multiple accounts per asset (AVAILABLE, LOCKED, etc.)
  // System has special accounts (CLEARING, FEE, INSURANCE)
  await knex.schema.createTable("ledger_accounts", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.string("account_type").notNullable(); // USER, SYSTEM
    table.string("account_name").notNullable(); // AVAILABLE, LOCKED, FEE, CLEARING, etc.
    table.uuid("user_id").references("id").inTable("users"); // null for system accounts
    table.string("asset_id").notNullable(); // ANM, USDT, BTC, etc.
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    // Unique constraint: one account per (user_id, asset_id, account_name)
    // For system accounts, user_id is null
    table.unique(["user_id", "asset_id", "account_name"]);
    table.index(["account_type", "asset_id"]);
  });

  // Ledger transactions table - headers for each balanced transaction
  // Every change to balances must go through a ledger transaction
  await knex.schema.createTable("ledger_transactions", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.string("tx_type").notNullable(); // TRADE_SETTLE, TRANSFER, DEPOSIT, WITHDRAWAL, FEE
    table.uuid("market_id").references("id").inTable("markets"); // null for non-market txs
    table.bigInteger("seq"); // sequence number from source event (for ordering)
    table.jsonb("metadata").notNullable().defaultTo("{}"); // trade_id, order_ids, etc.
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    table.index(["market_id", "seq"]);
    table.index(["tx_type", "created_at"]);
  });

  // Ledger entries table - individual debits and credits
  // MUST balance per transaction and per asset
  // Append-only, never update or delete
  await knex.schema.createTable("ledger_entries", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.uuid("transaction_id").notNullable().references("id").inTable("ledger_transactions");
    table.uuid("account_id").notNullable().references("id").inTable("ledger_accounts");
    table.string("asset_id").notNullable(); // redundant but useful for queries
    table.string("direction").notNullable(); // DEBIT or CREDIT
    table.decimal("amount_atoms", 30, 0).notNullable(); // BigInt as decimal string
    table.string("description").notNullable();
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    table.index(["transaction_id"]);
    table.index(["account_id", "created_at"]);
    table.index(["asset_id", "created_at"]);
  });

  // Ledger event offsets - track processed sequences per market
  // Ensures exactly-once processing and detects gaps
  await knex.schema.createTable("ledger_event_offsets", (table) => {
    table.uuid("market_id").primary().references("id").inTable("markets");
    table.string("consumer_group").notNullable().defaultTo("ledger-service");
    table.bigInteger("last_trade_seq").notNullable().defaultTo(0);
    table.bigInteger("last_order_seq").notNullable().defaultTo(0);
    table.timestamp("updated_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    table.index(["consumer_group", "market_id"]);
  });

  // Order locks table - track locked funds per order
  // Used to calculate how much to release on fill/cancel
  await knex.schema.createTable("order_locks", (table) => {
    table.uuid("order_id").primary().references("id").inTable("orders");
    table.uuid("user_id").notNullable().references("id").inTable("users");
    table.string("asset_id").notNullable();
    table.decimal("locked_atoms", 30, 0).notNullable().defaultTo(0);
    table.decimal("used_atoms", 30, 0).notNullable().defaultTo(0);
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    table.timestamp("updated_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    table.index(["user_id", "asset_id"]);
  });

  // Reconciliation reports table - track reconciliation job results
  await knex.schema.createTable("reconciliation_reports", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.string("job_type").notNullable(); // BALANCE_RECOMPUTE, INVARIANT_CHECK, GAP_DETECT
    table.boolean("ok").notNullable();
    table.jsonb("mismatches").notNullable().defaultTo("[]");
    table.jsonb("summary").notNullable().defaultTo("{}");
    table.timestamp("run_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    table.index(["job_type", "run_at"]);
    table.index(["ok", "run_at"]);
  });

  // Update balances table to use atoms (BigInt) instead of decimals
  // Add columns for atomic precision
  await knex.schema.alterTable("balances", (table) => {
    table.decimal("available_atoms", 30, 0).notNullable().defaultTo(0);
    table.decimal("locked_atoms", 30, 0).notNullable().defaultTo(0);
  });

  // Update journal_entries to link to ledger if needed (or deprecate it)
  // For now, we'll keep both tables for backward compatibility
  // New code should use ledger_entries
};

exports.down = async function down(knex) {
  await knex.schema.alterTable("balances", (table) => {
    table.dropColumn("available_atoms");
    table.dropColumn("locked_atoms");
  });

  await knex.schema.dropTableIfExists("reconciliation_reports");
  await knex.schema.dropTableIfExists("order_locks");
  await knex.schema.dropTableIfExists("ledger_event_offsets");
  await knex.schema.dropTableIfExists("ledger_entries");
  await knex.schema.dropTableIfExists("ledger_transactions");
  await knex.schema.dropTableIfExists("ledger_accounts");
};
