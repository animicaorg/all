-- Add BitGo configuration table

CREATE TYPE "BitgoEnvironment" AS ENUM ('TEST', 'PROD');

CREATE TABLE "bitgo_configs" (
    "id" TEXT PRIMARY KEY DEFAULT 'default',
    "environment" "BitgoEnvironment" NOT NULL DEFAULT 'TEST',
    "base_url" TEXT,
    "access_token_encrypted" TEXT,
    "webhook_secret_encrypted" TEXT,
    "wallets" JSONB,
    "coins" JSONB,
    "enabled" BOOLEAN NOT NULL DEFAULT FALSE,
    "updated_by" UUID,
    "created_at" TIMESTAMPTZ(3) NOT NULL DEFAULT NOW(),
    "updated_at" TIMESTAMPTZ(3) NOT NULL DEFAULT NOW()
);

CREATE INDEX "bitgo_configs_updated_by_idx" ON "bitgo_configs"("updated_by");

ALTER TABLE "bitgo_configs"
    ADD CONSTRAINT "bitgo_configs_updated_by_fkey"
    FOREIGN KEY ("updated_by") REFERENCES "admins"("id")
    ON DELETE SET NULL ON UPDATE CASCADE;
