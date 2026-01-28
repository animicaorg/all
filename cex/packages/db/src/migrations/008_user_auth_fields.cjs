/**
 * Add user authentication fields
 * Adds password hashing and OAuth support
 */

exports.up = async function(knex) {
  const hasUsersTable = await knex.schema.hasTable('users');
  
  if (hasUsersTable) {
    // Add authentication columns
    const hasPasswordHash = await knex.schema.hasColumn('users', 'password_hash');
    
    if (!hasPasswordHash) {
      await knex.schema.table('users', (table) => {
        table.string('full_name', 255).nullable();
        table.string('password_hash', 512).nullable(); // Argon2id hash
        
        // OAuth fields
        table.string('google_id', 255).nullable().unique();
        table.string('oauth_provider', 50).nullable(); // 'google', 'github', etc.
        table.timestamp('last_login_at').nullable();
        
        // Session tracking
        table.string('current_session_id', 255).nullable();
        
        // Account status
        table.boolean('active').notNullable().defaultTo(true);
        table.boolean('email_verified').notNullable().defaultTo(false);
        table.timestamp('email_verified_at').nullable();
      });
      
      // Add indexes
      await knex.raw('CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id)');
      await knex.raw('CREATE INDEX IF NOT EXISTS idx_users_session ON users(current_session_id)');
      await knex.raw('CREATE INDEX IF NOT EXISTS idx_users_last_login ON users(last_login_at)');
    }
    
    console.log('✅ User authentication fields added');
  }
};

exports.down = async function(knex) {
  const hasUsersTable = await knex.schema.hasTable('users');
  
  if (hasUsersTable) {
    const hasPasswordHash = await knex.schema.hasColumn('users', 'password_hash');
    
    if (hasPasswordHash) {
      await knex.schema.table('users', (table) => {
        table.dropColumn('full_name');
        table.dropColumn('password_hash');
        table.dropColumn('google_id');
        table.dropColumn('oauth_provider');
        table.dropColumn('last_login_at');
        table.dropColumn('current_session_id');
        table.dropColumn('active');
        table.dropColumn('email_verified');
        table.dropColumn('email_verified_at');
      });
      
      await knex.raw('DROP INDEX IF EXISTS idx_users_google_id');
      await knex.raw('DROP INDEX IF EXISTS idx_users_session');
      await knex.raw('DROP INDEX IF EXISTS idx_users_last_login');
    }
    
    console.log('✅ User authentication fields removed');
  }
};
