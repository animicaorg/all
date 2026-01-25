/**
 * Migration 006: Animica Asset Infrastructure
 * 
 * Creates tables for managing Animica (ANM) deposits and withdrawals using a local node:
 * - animica_scan_state: cursor for block scanning with leader election
 * - animica_blocks: block hash chain for reorg detection
 * - animica_seen_txs: deduplication of processed transactions
 */

exports.up = async function up(knex) {
  // Animica scan state table - cursor with leader election
  await knex.schema.createTable("animica_scan_state", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.uuid("asset_network_id").notNullable().references("id").inTable("asset_networks").unique();
    table.bigInteger("cursor_height").notNullable().defaultTo(0);
    table.string("cursor_hash"); // hash at cursor_height
    table.bigInteger("finalized_height"); // optional, for finality tracking
    table.timestamp("updated_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    // Leader election fields
    table.string("lock_owner"); // instance id holding the scan lock
    table.timestamp("lock_expires_at", { useTz: true });
    
    table.index(["asset_network_id"]);
    table.index(["lock_expires_at"]);
  });

  // Animica blocks table - reorg safety
  await knex.schema.createTable("animica_blocks", (table) => {
    table.bigInteger("height").notNullable();
    table.uuid("asset_network_id").notNullable().references("id").inTable("asset_networks");
    table.string("hash").notNullable();
    table.string("parent_hash").notNullable();
    table.boolean("canonical").notNullable().defaultTo(true);
    table.timestamp("seen_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    table.primary(["asset_network_id", "height"]);
    table.index(["canonical", "height"]);
    table.index(["hash"]);
  });

  // Animica seen transactions table - deduplication
  await knex.schema.createTable("animica_seen_txs", (table) => {
    table.string("key").primary(); // "<txid>:<vout>" or "<txid>:<logIndex>"
    table.uuid("asset_network_id").notNullable().references("id").inTable("asset_networks");
    table.string("txid").notNullable();
    table.bigInteger("height").notNullable();
    table.string("address").notNullable();
    table.decimal("amount_atoms", 30, 0).notNullable();
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    table.index(["txid"]);
    table.index(["asset_network_id", "height"]);
    table.index(["address"]);
  });
};

exports.down = async function down(knex) {
  await knex.schema.dropTableIfExists("animica_seen_txs");
  await knex.schema.dropTableIfExists("animica_blocks");
  await knex.schema.dropTableIfExists("animica_scan_state");
};
