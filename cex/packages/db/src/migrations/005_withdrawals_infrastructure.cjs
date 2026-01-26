/**
 * Migration 005: Withdrawals Infrastructure
 * 
 * Creates tables for managing cryptocurrency withdrawals via BitGo:
 * - withdrawals: withdrawal transaction records with full lifecycle tracking
 * - withdrawal_approvals: approval workflow (single/multi-approver policy)
 * - withdrawal_ledger_links: links withdrawals to ledger transactions (lock, broadcast, cancel)
 * - withdrawal_policies: configurable policies (velocity limits, thresholds, KYC requirements)
 * - withdrawal_outbox: outbox pattern for BitGo submission and ledger integration
 * - withdrawal_audit_log: comprehensive audit trail
 * - withdrawal_idempotency: HTTP request idempotency for withdrawal requests
 */

exports.up = async function up(knex) {
  // Withdrawal policies table - configurable policies by asset/network
  await knex.schema.createTable("withdrawal_policies", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.uuid("asset_network_id").notNullable().references("id").inTable("asset_networks");
    table.decimal("min_withdrawal_atoms", 30, 0).notNullable();
    table.decimal("max_withdrawal_atoms", 30, 0); // null = unlimited
    table.decimal("daily_limit_atoms", 30, 0); // null = unlimited
    table.integer("daily_limit_count"); // null = unlimited
    table.jsonb("kyc_tier_required").notNullable().defaultTo('["VERIFIED"]'); // ["VERIFIED", "ENHANCED"]
    table.integer("required_approvals").notNullable().defaultTo(1);
    table.decimal("high_risk_threshold_atoms", 30, 0); // requires extra approvals if exceeded
    table.integer("high_risk_approvals").notNullable().defaultTo(2);
    table.boolean("whitelist_only").notNullable().defaultTo(false);
    table.boolean("enabled").notNullable().defaultTo(true);
    table.jsonb("metadata").notNullable().defaultTo("{}");
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    table.timestamp("updated_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    table.unique(["asset_network_id"]);
  });

  // Withdrawals table - main withdrawal records
  await knex.schema.createTable("withdrawals", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.uuid("user_id").notNullable().references("id").inTable("users");
    table.uuid("asset_network_id").notNullable().references("id").inTable("asset_networks");
    table.string("destination_address").notNullable();
    table.string("destination_tag"); // memo/destination tag for MEMO_BASED networks
    table.decimal("amount", 30, 0).notNullable(); // user requested amount
    table.decimal("fee_amount", 30, 0).notNullable(); // network fee
    table.decimal("total_debit_amount", 30, 0).notNullable(); // amount + fee
    
    // Status tracking
    table.string("status").notNullable().defaultTo("REQUESTED");
    // REQUESTED -> RISK_REVIEW -> APPROVED -> SIGNING -> BROADCAST -> CONFIRMED
    // Cancel/fail paths: CANCELED, REJECTED, FAILED
    
    // Idempotency
    table.string("idempotency_key").notNullable().unique();
    table.string("client_withdrawal_id"); // optional user-provided ID
    
    // Provider tracking (BitGo)
    table.string("provider").notNullable().defaultTo("BITGO");
    table.string("provider_ref"); // BitGo transfer id
    table.string("txid"); // on-chain transaction hash (null until broadcast)
    
    // Risk scoring
    table.decimal("risk_score", 5, 2); // 0.00 to 100.00
    table.jsonb("risk_flags").notNullable().defaultTo("[]"); // ["HIGH_AMOUNT", "NEW_ADDRESS", etc.]
    table.string("risk_reason"); // human-readable reason
    
    // Timestamps
    table.timestamp("requested_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    table.timestamp("approved_at", { useTz: true });
    table.timestamp("broadcast_at", { useTz: true });
    table.timestamp("confirmed_at", { useTz: true });
    
    // Failure tracking
    table.string("failure_code"); // INSUFFICIENT_FUNDS, INVALID_ADDRESS, BITGO_ERROR, etc.
    table.string("failure_message");
    
    // Retry mechanism
    table.integer("attempt_count").notNullable().defaultTo(0);
    table.timestamp("next_retry_at", { useTz: true });
    
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    table.timestamp("updated_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    // Indexes
    table.index(["user_id", "status"]);
    table.index(["status", "next_retry_at"]);
    table.index(["created_at"]);
  });

  // Partial indexes for provider_ref and txid (only index non-null values)
  await knex.raw(`
    CREATE INDEX "withdrawals_provider_ref_index" ON "withdrawals" ("provider_ref") WHERE "provider_ref" IS NOT NULL
  `);
  await knex.raw(`
    CREATE INDEX "withdrawals_txid_index" ON "withdrawals" ("txid") WHERE "txid" IS NOT NULL
  `);

  // Withdrawal approvals table - approval workflow
  await knex.schema.createTable("withdrawal_approvals", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.uuid("withdrawal_id").notNullable().references("id").inTable("withdrawals").onDelete("CASCADE");
    table.uuid("approver_id").notNullable().references("id").inTable("users");
    table.string("approver_role").notNullable(); // ADMIN, SUPER_ADMIN, etc.
    table.string("action").notNullable(); // APPROVE, REJECT
    table.string("reason"); // optional comment
    table.jsonb("metadata").notNullable().defaultTo("{}");
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    // Prevent same approver from approving twice
    table.unique(["withdrawal_id", "approver_id"]);
    table.index(["withdrawal_id", "action"]);
    table.index(["approver_id", "created_at"]);
  });

  // Withdrawal ledger links - track ledger transactions for each withdrawal
  await knex.schema.createTable("withdrawal_ledger_links", (table) => {
    table.uuid("withdrawal_id").primary().references("id").inTable("withdrawals").onDelete("CASCADE");
    table.uuid("lock_tx_id").references("id").inTable("ledger_transactions"); // lock funds on request
    table.uuid("broadcast_tx_id").references("id").inTable("ledger_transactions"); // move to system on broadcast
    table.uuid("cancel_tx_id").references("id").inTable("ledger_transactions"); // release lock on cancel
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    table.timestamp("updated_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    table.index(["lock_tx_id"]);
    table.index(["broadcast_tx_id"]);
    table.index(["cancel_tx_id"]);
  });

  // Withdrawal outbox table - outbox pattern for reliable async operations
  await knex.schema.createTable("withdrawal_outbox", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.uuid("withdrawal_id").notNullable().references("id").inTable("withdrawals").onDelete("CASCADE");
    table.string("type").notNullable(); // APPLY_LEDGER_LOCK, SUBMIT_TO_BITGO, APPLY_LEDGER_BROADCAST, APPLY_LEDGER_CANCEL
    table.jsonb("payload").notNullable(); // operation-specific payload
    table.string("status").notNullable().defaultTo("PENDING"); // PENDING, PROCESSING, COMPLETED, FAILED
    table.integer("attempt_count").notNullable().defaultTo(0);
    table.timestamp("next_retry_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    table.jsonb("last_error");
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    table.timestamp("processed_at", { useTz: true });
    table.timestamp("updated_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    table.index(["status", "next_retry_at"]);
    table.index(["withdrawal_id", "type"]);
    table.index(["created_at"]);
  });

  // Withdrawal audit log table - comprehensive audit trail
  await knex.schema.createTable("withdrawal_audit_log", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.string("event_type").notNullable(); // WITHDRAWAL_REQUESTED, APPROVED, REJECTED, BROADCAST, CONFIRMED, etc.
    table.uuid("withdrawal_id").notNullable().references("id").inTable("withdrawals");
    table.uuid("user_id").references("id").inTable("users"); // affected user
    table.uuid("actor_id").references("id").inTable("users"); // admin who performed action
    table.string("actor_type").notNullable().defaultTo("SYSTEM"); // SYSTEM, ADMIN, USER
    table.jsonb("changes").notNullable().defaultTo("{}"); // before/after or event details
    table.jsonb("metadata").notNullable().defaultTo("{}");
    table.string("ip_address");
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    
    table.index(["event_type", "created_at"]);
    table.index(["withdrawal_id", "created_at"]);
    table.index(["user_id", "created_at"]);
  });

  // Withdrawal idempotency table - HTTP request idempotency
  await knex.schema.createTable("withdrawal_idempotency", (table) => {
    table.uuid("id").primary().defaultTo(knex.raw("gen_random_uuid()"));
    table.string("idempotency_key").notNullable();
    table.uuid("user_id").notNullable().references("id").inTable("users");
    table.string("endpoint").notNullable(); // POST /withdrawals
    table.uuid("withdrawal_id").notNullable().references("id").inTable("withdrawals");
    table.jsonb("request_body").notNullable();
    table.jsonb("response_body").notNullable();
    table.integer("response_status").notNullable();
    table.timestamp("created_at", { useTz: true }).notNullable().defaultTo(knex.fn.now());
    table.timestamp("expires_at", { useTz: true }).notNullable();
    
    table.unique(["idempotency_key", "user_id", "endpoint"]);
    table.index(["withdrawal_id"]);
    table.index(["expires_at"]);
  });
};

exports.down = async function down(knex) {
  await knex.schema.dropTableIfExists("withdrawal_idempotency");
  await knex.schema.dropTableIfExists("withdrawal_audit_log");
  await knex.schema.dropTableIfExists("withdrawal_outbox");
  await knex.schema.dropTableIfExists("withdrawal_ledger_links");
  await knex.schema.dropTableIfExists("withdrawal_approvals");
  await knex.schema.dropTableIfExists("withdrawals");
  await knex.schema.dropTableIfExists("withdrawal_policies");
};
