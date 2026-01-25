/**
 * Security Hardening Migration
 * Adds tables and columns for enhanced security features:
 * - Backup codes for 2FA
 * - Anti-phishing phrases
 * - Device tracking
 * - Session management enhancements
 * - Withdrawal address book
 * - Login attempt tracking
 * - Audit log immutability features
 */

exports.up = async function(knex) {
  // Add backup codes table
  await knex.schema.createTable('backup_codes', (table) => {
    table.uuid('id').primary().defaultTo(knex.raw('gen_random_uuid()'));
    table.uuid('user_id').notNullable();
    table.string('code_hash', 512).notNullable(); // Argon2id hash
    table.boolean('used').notNullable().defaultTo(false);
    table.timestamp('used_at').nullable();
    table.timestamp('created_at').notNullable().defaultTo(knex.fn.now());
    
    // Foreign keys (assuming users table exists)
    table.index('user_id');
    table.index(['user_id', 'used']); // Quick lookup for unused codes
  });

  // Add anti-phishing phrases table
  await knex.schema.createTable('anti_phishing_phrases', (table) => {
    table.uuid('id').primary().defaultTo(knex.raw('gen_random_uuid()'));
    table.uuid('user_id').notNullable().unique();
    table.string('phrase', 100).notNullable();
    table.timestamp('created_at').notNullable().defaultTo(knex.fn.now());
    table.timestamp('updated_at').notNullable().defaultTo(knex.fn.now());
    
    table.index('user_id');
  });

  // Add device tracking table
  await knex.schema.createTable('user_devices', (table) => {
    table.uuid('id').primary().defaultTo(knex.raw('gen_random_uuid()'));
    table.uuid('user_id').notNullable();
    table.string('device_fingerprint', 256).notNullable();
    table.string('device_name', 256).nullable(); // User-friendly name
    table.string('user_agent', 512).notNullable();
    table.string('ip_address', 45).notNullable(); // IPv6 compatible
    table.boolean('trusted').notNullable().defaultTo(false);
    table.timestamp('first_seen_at').notNullable().defaultTo(knex.fn.now());
    table.timestamp('last_seen_at').notNullable().defaultTo(knex.fn.now());
    
    table.index('user_id');
    table.index(['user_id', 'device_fingerprint']);
    table.index(['user_id', 'trusted']);
  });

  // Add withdrawal address book
  await knex.schema.createTable('withdrawal_addresses', (table) => {
    table.uuid('id').primary().defaultTo(knex.raw('gen_random_uuid()'));
    table.uuid('user_id').notNullable();
    table.string('asset', 20).notNullable(); // BTC, ETH, USDT, etc.
    table.string('address', 256).notNullable();
    table.string('label', 100).notNullable(); // User-friendly name
    table.string('network', 50).nullable(); // mainnet, testnet, polygon, etc.
    table.enum('status', ['pending', 'active', 'disabled']).notNullable().defaultTo('pending');
    table.timestamp('confirmed_at').nullable(); // When user confirmed via 2FA
    table.timestamp('active_at').nullable(); // When address became active (after cooldown)
    table.integer('cooldown_hours').notNullable().defaultTo(24);
    table.timestamp('created_at').notNullable().defaultTo(knex.fn.now());
    
    table.index('user_id');
    table.index(['user_id', 'asset', 'status']);
    table.unique(['user_id', 'asset', 'address', 'network']);
  });

  // Add login attempts tracking
  await knex.schema.createTable('login_attempts', (table) => {
    table.uuid('id').primary().defaultTo(knex.raw('gen_random_uuid()'));
    table.string('identifier', 256).notNullable(); // Email, IP, or user_id
    table.enum('identifier_type', ['email', 'ip', 'user_id']).notNullable();
    table.boolean('success').notNullable();
    table.string('ip_address', 45).notNullable();
    table.string('user_agent', 512).nullable();
    table.string('failure_reason', 100).nullable(); // invalid_password, invalid_totp, etc.
    table.timestamp('attempted_at').notNullable().defaultTo(knex.fn.now());
    
    table.index('identifier');
    table.index(['identifier', 'attempted_at']);
    table.index(['identifier', 'success', 'attempted_at']);
  });

  // Add account lockouts table
  await knex.schema.createTable('account_lockouts', (table) => {
    table.uuid('id').primary().defaultTo(knex.raw('gen_random_uuid()'));
    table.uuid('user_id').nullable(); // Can be null for IP-based lockouts
    table.string('ip_address', 45).nullable();
    table.enum('lockout_type', ['user', 'ip', 'both']).notNullable();
    table.string('reason', 256).notNullable();
    table.integer('attempt_count').notNullable();
    table.timestamp('locked_at').notNullable().defaultTo(knex.fn.now());
    table.timestamp('unlocks_at').notNullable();
    table.timestamp('unlocked_at').nullable();
    
    table.index('user_id');
    table.index('ip_address');
    table.index(['user_id', 'unlocks_at']);
    table.index(['ip_address', 'unlocks_at']);
  });

  // Add API key rotation tracking
  await knex.schema.createTable('api_key_rotations', (table) => {
    table.uuid('id').primary().defaultTo(knex.raw('gen_random_uuid()'));
    table.uuid('api_key_id').notNullable();
    table.string('old_key_id', 64).notNullable();
    table.string('new_key_id', 64).notNullable();
    table.uuid('rotated_by_admin_id').nullable();
    table.string('rotation_reason', 256).notNullable();
    table.timestamp('rotated_at').notNullable().defaultTo(knex.fn.now());
    
    table.index('api_key_id');
    table.index('rotated_at');
  });

  // Add audit log hash chain for immutability
  await knex.schema.table('audit_logs', (table) => {
    table.string('previous_hash', 64).nullable(); // SHA-256 of previous entry
    table.string('entry_hash', 64).nullable(); // SHA-256 of this entry
    table.bigInteger('sequence_number').nullable(); // Monotonic sequence
    
    table.index('sequence_number');
  });

  // Create sequence for audit logs
  await knex.raw(`
    CREATE SEQUENCE IF NOT EXISTS audit_log_sequence START 1;
  `);

  // Add service tokens table for service-to-service auth
  await knex.schema.createTable('service_tokens', (table) => {
    table.uuid('id').primary().defaultTo(knex.raw('gen_random_uuid()'));
    table.string('service_id', 100).notNullable();
    table.string('key_id', 64).notNullable();
    table.string('key_hash', 512).notNullable(); // Hashed service key
    table.jsonb('scopes').notNullable().defaultTo('[]');
    table.boolean('active').notNullable().defaultTo(true);
    table.timestamp('created_at').notNullable().defaultTo(knex.fn.now());
    table.timestamp('last_used_at').nullable();
    table.timestamp('expires_at').nullable();
    
    table.unique(['service_id', 'key_id']);
    table.index('service_id');
    table.index(['service_id', 'active']);
  });

  // Add KYC status and limits (if users table exists)
  // Note: This is conditional and should be adjusted based on actual schema
  const hasUsersTable = await knex.schema.hasTable('users');
  if (hasUsersTable) {
    const hasKycStatus = await knex.schema.hasColumn('users', 'kyc_status');
    if (!hasKycStatus) {
      await knex.schema.table('users', (table) => {
        table.enum('kyc_status', ['none', 'pending', 'approved', 'rejected']).notNullable().defaultTo('none');
        table.timestamp('kyc_approved_at').nullable();
        table.decimal('daily_withdrawal_limit_usd', 15, 2).nullable();
        table.decimal('monthly_withdrawal_limit_usd', 15, 2).nullable();
      });
    }
  }

  // Add travel rule data for withdrawals (if withdrawals table exists)
  const hasWithdrawalsTable = await knex.schema.hasTable('withdrawals');
  if (hasWithdrawalsTable) {
    const hasTravelRule = await knex.schema.hasColumn('withdrawals', 'travel_rule_data');
    if (!hasTravelRule) {
      await knex.schema.table('withdrawals', (table) => {
        table.jsonb('travel_rule_data').nullable(); // Counterparty info for compliance
        table.boolean('requires_travel_rule').notNullable().defaultTo(false);
        table.boolean('sanctions_checked').notNullable().defaultTo(false);
        table.timestamp('sanctions_checked_at').nullable();
      });
    }
  }

  // Add TOTP fields to admins table (if it doesn't have them)
  const hasAdminsTable = await knex.schema.hasTable('admins');
  if (hasAdminsTable) {
    const hasTotpEnabled = await knex.schema.hasColumn('admins', 'totp_enabled');
    if (!hasTotpEnabled) {
      await knex.schema.table('admins', (table) => {
        table.boolean('totp_enabled').notNullable().defaultTo(false);
        table.string('totp_secret_encrypted', 512).nullable();
        table.timestamp('totp_enabled_at').nullable();
      });
    }
  }

  // Add 2FA fields to users table (if it doesn't have them)
  if (hasUsersTable) {
    const has2faEnabled = await knex.schema.hasColumn('users', 'two_fa_enabled');
    if (!has2faEnabled) {
      await knex.schema.table('users', (table) => {
        table.boolean('two_fa_enabled').notNullable().defaultTo(false);
        table.string('totp_secret_encrypted', 512).nullable();
        table.timestamp('two_fa_enabled_at').nullable();
      });
    }
  }

  console.log('✅ Security hardening migration completed');
};

exports.down = async function(knex) {
  // Drop new tables
  await knex.schema.dropTableIfExists('service_tokens');
  await knex.schema.dropTableIfExists('api_key_rotations');
  await knex.schema.dropTableIfExists('account_lockouts');
  await knex.schema.dropTableIfExists('login_attempts');
  await knex.schema.dropTableIfExists('withdrawal_addresses');
  await knex.schema.dropTableIfExists('user_devices');
  await knex.schema.dropTableIfExists('anti_phishing_phrases');
  await knex.schema.dropTableIfExists('backup_codes');

  // Drop sequence
  await knex.raw('DROP SEQUENCE IF EXISTS audit_log_sequence;');

  // Remove added columns (only if tables exist)
  const hasAuditLogs = await knex.schema.hasTable('audit_logs');
  if (hasAuditLogs) {
    await knex.schema.table('audit_logs', (table) => {
      table.dropColumn('previous_hash');
      table.dropColumn('entry_hash');
      table.dropColumn('sequence_number');
    });
  }

  const hasWithdrawalsTable = await knex.schema.hasTable('withdrawals');
  if (hasWithdrawalsTable) {
    const hasTravelRule = await knex.schema.hasColumn('withdrawals', 'travel_rule_data');
    if (hasTravelRule) {
      await knex.schema.table('withdrawals', (table) => {
        table.dropColumn('travel_rule_data');
        table.dropColumn('requires_travel_rule');
        table.dropColumn('sanctions_checked');
        table.dropColumn('sanctions_checked_at');
      });
    }
  }

  const hasUsersTable = await knex.schema.hasTable('users');
  if (hasUsersTable) {
    const hasKycStatus = await knex.schema.hasColumn('users', 'kyc_status');
    if (hasKycStatus) {
      await knex.schema.table('users', (table) => {
        table.dropColumn('kyc_status');
        table.dropColumn('kyc_approved_at');
        table.dropColumn('daily_withdrawal_limit_usd');
        table.dropColumn('monthly_withdrawal_limit_usd');
      });
    }

    const has2faEnabled = await knex.schema.hasColumn('users', 'two_fa_enabled');
    if (has2faEnabled) {
      await knex.schema.table('users', (table) => {
        table.dropColumn('two_fa_enabled');
        table.dropColumn('totp_secret_encrypted');
        table.dropColumn('two_fa_enabled_at');
      });
    }
  }

  const hasAdminsTable = await knex.schema.hasTable('admins');
  if (hasAdminsTable) {
    const hasTotpEnabled = await knex.schema.hasColumn('admins', 'totp_enabled');
    if (hasTotpEnabled) {
      await knex.schema.table('admins', (table) => {
        table.dropColumn('totp_enabled');
        table.dropColumn('totp_secret_encrypted');
        table.dropColumn('totp_enabled_at');
      });
    }
  }

  console.log('✅ Security hardening migration rolled back');
};
