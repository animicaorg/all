CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "citext";

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'UserStatus') THEN
    CREATE TYPE "UserStatus" AS ENUM ('ACTIVE', 'SUSPENDED', 'CLOSED');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'UserRole') THEN
    CREATE TYPE "UserRole" AS ENUM ('USER', 'ADMIN', 'OPS', 'COMPLIANCE');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'AdminRole') THEN
    CREATE TYPE "AdminRole" AS ENUM ('SUPERADMIN', 'OPS', 'COMPLIANCE', 'SUPPORT', 'READONLY');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'AdminStatus') THEN
    CREATE TYPE "AdminStatus" AS ENUM ('ACTIVE', 'DISABLED');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'AuditActorType') THEN
    CREATE TYPE "AuditActorType" AS ENUM ('USER', 'ADMIN', 'SYSTEM');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'BitgoEnvironment') THEN
    CREATE TYPE "BitgoEnvironment" AS ENUM ('TEST', 'PROD');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'RiskFlagStatus') THEN
    CREATE TYPE "RiskFlagStatus" AS ENUM ('OPEN', 'CLOSED');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'RiskFlagSeverity') THEN
    CREATE TYPE "RiskFlagSeverity" AS ENUM ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL');
  END IF;
END $$;

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS phone TEXT,
  ADD COLUMN IF NOT EXISTS status "UserStatus" NOT NULL DEFAULT 'ACTIVE'::"UserStatus",
  ADD COLUMN IF NOT EXISTS role "UserRole" NOT NULL DEFAULT 'USER'::"UserRole",
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ(3) NOT NULL DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS users_phone_key ON users(phone) WHERE phone IS NOT NULL;

CREATE TABLE IF NOT EXISTS admins (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email CITEXT NOT NULL,
  password_hash TEXT NOT NULL,
  totp_secret_encrypted TEXT,
  role "AdminRole" NOT NULL,
  status "AdminStatus" NOT NULL DEFAULT 'ACTIVE'::"AdminStatus",
  last_login_at TIMESTAMPTZ(3),
  created_at TIMESTAMPTZ(3) NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ(3) NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS admins_email_key ON admins(email);
CREATE INDEX IF NOT EXISTS admins_status_idx ON admins(status);

CREATE TABLE IF NOT EXISTS admin_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  admin_id UUID NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
  refresh_token_hash TEXT NOT NULL,
  expires_at TIMESTAMPTZ(3) NOT NULL,
  created_at TIMESTAMPTZ(3) NOT NULL DEFAULT NOW(),
  revoked_at TIMESTAMPTZ(3),
  ip INET,
  user_agent TEXT
);

CREATE INDEX IF NOT EXISTS admin_sessions_admin_id_idx ON admin_sessions(admin_id);
CREATE INDEX IF NOT EXISTS admin_sessions_expires_at_idx ON admin_sessions(expires_at);

CREATE TABLE IF NOT EXISTS audit_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_type TEXT NOT NULL DEFAULT 'ADMIN_ACTION',
  resource_type TEXT NOT NULL DEFAULT 'UNKNOWN',
  resource_id TEXT NOT NULL DEFAULT '',
  actor_user_id UUID REFERENCES users(id),
  actor_admin_id UUID REFERENCES admins(id),
  actor_type "AuditActorType" NOT NULL,
  action TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT,
  request_id TEXT,
  ip INET,
  user_agent TEXT,
  before JSONB,
  after JSONB,
  changes JSONB NOT NULL DEFAULT '{}'::jsonb,
  metadata JSONB,
  ip_address TEXT,
  previous_hash TEXT,
  entry_hash TEXT,
  sequence_number BIGINT,
  created_at TIMESTAMPTZ(3) NOT NULL DEFAULT NOW()
);

ALTER TABLE audit_logs
  ADD COLUMN IF NOT EXISTS actor_user_id UUID REFERENCES users(id),
  ADD COLUMN IF NOT EXISTS actor_admin_id UUID REFERENCES admins(id),
  ADD COLUMN IF NOT EXISTS action TEXT,
  ADD COLUMN IF NOT EXISTS entity_type TEXT,
  ADD COLUMN IF NOT EXISTS entity_id TEXT,
  ADD COLUMN IF NOT EXISTS request_id TEXT,
  ADD COLUMN IF NOT EXISTS ip INET,
  ADD COLUMN IF NOT EXISTS user_agent TEXT,
  ADD COLUMN IF NOT EXISTS before JSONB,
  ADD COLUMN IF NOT EXISTS after JSONB;

UPDATE audit_logs
SET
  action = COALESCE(action, event_type, 'legacy'),
  entity_type = COALESCE(entity_type, resource_type, 'UNKNOWN')
WHERE action IS NULL OR entity_type IS NULL;

ALTER TABLE audit_logs
  ALTER COLUMN action SET NOT NULL,
  ALTER COLUMN entity_type SET NOT NULL,
  ALTER COLUMN event_type SET DEFAULT 'ADMIN_ACTION',
  ALTER COLUMN resource_type SET DEFAULT 'UNKNOWN',
  ALTER COLUMN resource_id SET DEFAULT '',
  ALTER COLUMN changes SET DEFAULT '{}'::jsonb,
  ALTER COLUMN metadata SET DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS audit_logs_actor_user_id_created_at_idx ON audit_logs(actor_user_id, created_at);
CREATE INDEX IF NOT EXISTS audit_logs_actor_admin_id_created_at_idx ON audit_logs(actor_admin_id, created_at);
CREATE INDEX IF NOT EXISTS audit_logs_entity_type_entity_id_idx ON audit_logs(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS audit_logs_created_at_idx ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS audit_logs_request_id_idx ON audit_logs(request_id);

CREATE TABLE IF NOT EXISTS bitgo_configs (
  id TEXT PRIMARY KEY DEFAULT 'default',
  environment "BitgoEnvironment" NOT NULL DEFAULT 'TEST'::"BitgoEnvironment",
  base_url TEXT,
  access_token_encrypted TEXT,
  webhook_secret_encrypted TEXT,
  wallets JSONB,
  coins JSONB,
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  updated_by UUID REFERENCES admins(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ(3) NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ(3) NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS bitgo_configs_updated_by_idx ON bitgo_configs(updated_by);

CREATE TABLE IF NOT EXISTS risk_flags (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  code TEXT NOT NULL,
  severity "RiskFlagSeverity" NOT NULL,
  note TEXT,
  status "RiskFlagStatus" NOT NULL DEFAULT 'OPEN'::"RiskFlagStatus",
  created_by UUID NOT NULL REFERENCES admins(id),
  created_at TIMESTAMPTZ(3) NOT NULL DEFAULT NOW(),
  closed_at TIMESTAMPTZ(3)
);

CREATE INDEX IF NOT EXISTS risk_flags_user_id_status_idx ON risk_flags(user_id, status);
CREATE INDEX IF NOT EXISTS risk_flags_code_idx ON risk_flags(code);
CREATE INDEX IF NOT EXISTS risk_flags_created_at_idx ON risk_flags(created_at);
