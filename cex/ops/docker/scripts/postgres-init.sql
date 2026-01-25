-- CEX PostgreSQL Initialization Script
-- This script runs on database initialization

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";

-- Create application user if not exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_user WHERE usename = 'cex_app') THEN
        CREATE USER cex_app WITH PASSWORD 'change_me_in_production';
    END IF;
END
$$;

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE cex_exchange TO cex_app;

-- Create schema version tracking table
CREATE TABLE IF NOT EXISTS migrations (
    version VARCHAR(255) PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

-- Log initialization
INSERT INTO migrations (version, description) 
VALUES ('000_init', 'Initial database setup')
ON CONFLICT (version) DO NOTHING;
