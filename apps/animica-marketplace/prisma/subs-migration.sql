-- Platform subscriptions (fiat SaaS tiers) + Animica Workers — docs/subscriptions.md
-- Additive-only (audited: 0 DROPs). Generated via
--   prisma migrate diff --from-url $DATABASE_URL --to-schema-datamodel prisma/schema.prisma --script
-- Apply BEFORE deploying code that references these tables (autocommit path — the two
-- ALTER TYPE ... ADD VALUE statements must not share a transaction with statements that
-- use the new values; nothing in this file does):
--   npx prisma db execute --file prisma/subs-migration.sql --schema prisma/schema.prisma
-- Then: npm run build (prisma generate) + restart animica-marketplace.

-- CreateEnum
CREATE TYPE "SubStatus" AS ENUM ('PENDING', 'ACTIVE', 'PAST_DUE', 'GRACE_PERIOD', 'SUSPENDED', 'CANCELED');

-- CreateEnum
CREATE TYPE "WorkerStatus" AS ENUM ('ACTIVE', 'PAUSED', 'DISABLED', 'PLAN_LIMIT');

-- CreateEnum
CREATE TYPE "WorkerRunStatus" AS ENUM ('PENDING', 'RUNNING', 'DONE', 'FAILED', 'CANCELLED', 'TIMEOUT');

-- CreateEnum
CREATE TYPE "WorkspaceRole" AS ENUM ('OWNER', 'ADMIN', 'MEMBER');

-- AlterEnum
ALTER TYPE "DomainStatus" ADD VALUE 'SUSPENDED';

-- AlterEnum
ALTER TYPE "KeyStatus" ADD VALUE 'SUSPENDED';

-- AlterTable
ALTER TABLE "ApiKey" ADD COLUMN     "kind" TEXT NOT NULL DEFAULT 'basic';

-- CreateTable
CREATE TABLE "Plan" (
    "id" TEXT NOT NULL,
    "key" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "tagline" TEXT NOT NULL DEFAULT '',
    "description" TEXT NOT NULL DEFAULT '',
    "features" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "icon" TEXT NOT NULL DEFAULT '⚡',
    "priceUsdCents" INTEGER NOT NULL DEFAULT 0,
    "paypalPlanId" TEXT,
    "limitsJson" TEXT NOT NULL DEFAULT '{}',
    "featured" BOOLEAN NOT NULL DEFAULT false,
    "active" BOOLEAN NOT NULL DEFAULT true,
    "sortOrder" INTEGER NOT NULL DEFAULT 100,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "Plan_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "PlanSubscription" (
    "id" TEXT NOT NULL,
    "accountId" TEXT NOT NULL,
    "planKey" TEXT NOT NULL,
    "status" "SubStatus" NOT NULL DEFAULT 'PENDING',
    "priceUsdCents" INTEGER NOT NULL,
    "paypalPlanId" TEXT,
    "paypalSubscriptionId" TEXT,
    "payerEmail" TEXT,
    "currentPeriodEnd" TIMESTAMP(3),
    "graceUntil" TIMESTAMP(3),
    "lastPaymentAt" TIMESTAMP(3),
    "lastPaymentAmountCents" INTEGER,
    "failedPayments" INTEGER NOT NULL DEFAULT 0,
    "lastWebhookAt" TIMESTAMP(3),
    "lastWebhookType" TEXT,
    "lastError" TEXT,
    "canceledAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "PlanSubscription_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "BillingEvent" (
    "id" TEXT NOT NULL,
    "eventType" TEXT NOT NULL,
    "resourceId" TEXT,
    "subscriptionId" TEXT,
    "accountId" TEXT,
    "summary" TEXT,
    "payloadJson" TEXT NOT NULL DEFAULT '{}',
    "processedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "BillingEvent_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "UsageCounter" (
    "id" TEXT NOT NULL,
    "accountId" TEXT NOT NULL,
    "feature" TEXT NOT NULL,
    "period" TEXT NOT NULL,
    "used" INTEGER NOT NULL DEFAULT 0,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "UsageCounter_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Worker" (
    "id" TEXT NOT NULL,
    "accountId" TEXT NOT NULL,
    "workspaceId" TEXT,
    "name" TEXT NOT NULL,
    "purpose" TEXT NOT NULL DEFAULT '',
    "status" "WorkerStatus" NOT NULL DEFAULT 'ACTIVE',
    "statusReason" TEXT,
    "systemPrompt" TEXT NOT NULL DEFAULT '',
    "taskPrompt" TEXT NOT NULL DEFAULT '',
    "model" TEXT NOT NULL DEFAULT 'kimi-k3',
    "temperature" DOUBLE PRECISION NOT NULL DEFAULT 0.7,
    "toolsJson" TEXT NOT NULL DEFAULT '[]',
    "webhookUrl" TEXT,
    "anmName" TEXT,
    "scheduleKind" TEXT NOT NULL DEFAULT 'manual',
    "intervalMinutes" INTEGER,
    "nextRunAt" TIMESTAMP(3),
    "lastRunAt" TIMESTAMP(3),
    "maxRunSeconds" INTEGER NOT NULL DEFAULT 120,
    "failureThreshold" INTEGER NOT NULL DEFAULT 5,
    "consecutiveFailures" INTEGER NOT NULL DEFAULT 0,
    "triggerTokenHash" TEXT,
    "runsTotal" INTEGER NOT NULL DEFAULT 0,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "Worker_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "WorkerRun" (
    "id" TEXT NOT NULL,
    "workerId" TEXT NOT NULL,
    "accountId" TEXT NOT NULL,
    "status" "WorkerRunStatus" NOT NULL DEFAULT 'PENDING',
    "trigger" TEXT NOT NULL DEFAULT 'schedule',
    "priority" INTEGER NOT NULL DEFAULT 0,
    "attempts" INTEGER NOT NULL DEFAULT 0,
    "leaseUntil" TIMESTAMP(3),
    "scheduledFor" TIMESTAMP(3),
    "startedAt" TIMESTAMP(3),
    "finishedAt" TIMESTAMP(3),
    "durationMs" INTEGER,
    "output" TEXT,
    "error" TEXT,
    "tokensIn" INTEGER NOT NULL DEFAULT 0,
    "tokensOut" INTEGER NOT NULL DEFAULT 0,
    "logsJson" TEXT NOT NULL DEFAULT '[]',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "WorkerRun_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "WorkerEngineState" (
    "id" TEXT NOT NULL DEFAULT 'workers',
    "paused" BOOLEAN NOT NULL DEFAULT false,
    "note" TEXT,
    "lastTickAt" TIMESTAMP(3),
    "lastTickInfo" TEXT,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "WorkerEngineState_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Workspace" (
    "id" TEXT NOT NULL,
    "ownerId" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "kind" TEXT NOT NULL DEFAULT 'team',
    "brandName" TEXT,
    "brandLogoUrl" TEXT,
    "brandColor" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "Workspace_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "WorkspaceMember" (
    "id" TEXT NOT NULL,
    "workspaceId" TEXT NOT NULL,
    "accountId" TEXT NOT NULL,
    "role" "WorkspaceRole" NOT NULL DEFAULT 'MEMBER',
    "status" TEXT NOT NULL DEFAULT 'active',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "WorkspaceMember_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "BillingAnalyticsEvent" (
    "id" TEXT NOT NULL,
    "kind" TEXT NOT NULL,
    "accountId" TEXT,
    "planKey" TEXT,
    "fromPlanKey" TEXT,
    "meta" TEXT NOT NULL DEFAULT '{}',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "BillingAnalyticsEvent_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "Plan_key_key" ON "Plan"("key");

-- CreateIndex
CREATE UNIQUE INDEX "PlanSubscription_paypalSubscriptionId_key" ON "PlanSubscription"("paypalSubscriptionId");

-- CreateIndex
CREATE INDEX "PlanSubscription_accountId_status_idx" ON "PlanSubscription"("accountId", "status");

-- CreateIndex
CREATE INDEX "PlanSubscription_status_updatedAt_idx" ON "PlanSubscription"("status", "updatedAt");

-- CreateIndex
CREATE INDEX "PlanSubscription_planKey_idx" ON "PlanSubscription"("planKey");

-- CreateIndex
CREATE INDEX "BillingEvent_subscriptionId_processedAt_idx" ON "BillingEvent"("subscriptionId", "processedAt");

-- CreateIndex
CREATE INDEX "BillingEvent_eventType_processedAt_idx" ON "BillingEvent"("eventType", "processedAt");

-- CreateIndex
CREATE INDEX "UsageCounter_accountId_period_idx" ON "UsageCounter"("accountId", "period");

-- CreateIndex
CREATE UNIQUE INDEX "UsageCounter_accountId_feature_period_key" ON "UsageCounter"("accountId", "feature", "period");

-- CreateIndex
CREATE UNIQUE INDEX "Worker_triggerTokenHash_key" ON "Worker"("triggerTokenHash");

-- CreateIndex
CREATE INDEX "Worker_accountId_status_idx" ON "Worker"("accountId", "status");

-- CreateIndex
CREATE INDEX "Worker_status_nextRunAt_idx" ON "Worker"("status", "nextRunAt");

-- CreateIndex
CREATE INDEX "Worker_workspaceId_idx" ON "Worker"("workspaceId");

-- CreateIndex
CREATE INDEX "WorkerRun_status_priority_createdAt_idx" ON "WorkerRun"("status", "priority", "createdAt");

-- CreateIndex
CREATE INDEX "WorkerRun_workerId_createdAt_idx" ON "WorkerRun"("workerId", "createdAt");

-- CreateIndex
CREATE INDEX "WorkerRun_accountId_createdAt_idx" ON "WorkerRun"("accountId", "createdAt");

-- CreateIndex
CREATE INDEX "Workspace_ownerId_idx" ON "Workspace"("ownerId");

-- CreateIndex
CREATE INDEX "WorkspaceMember_accountId_idx" ON "WorkspaceMember"("accountId");

-- CreateIndex
CREATE UNIQUE INDEX "WorkspaceMember_workspaceId_accountId_key" ON "WorkspaceMember"("workspaceId", "accountId");

-- CreateIndex
CREATE INDEX "BillingAnalyticsEvent_kind_createdAt_idx" ON "BillingAnalyticsEvent"("kind", "createdAt");

-- AddForeignKey
ALTER TABLE "PlanSubscription" ADD CONSTRAINT "PlanSubscription_accountId_fkey" FOREIGN KEY ("accountId") REFERENCES "Account"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "PlanSubscription" ADD CONSTRAINT "PlanSubscription_planKey_fkey" FOREIGN KEY ("planKey") REFERENCES "Plan"("key") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Worker" ADD CONSTRAINT "Worker_accountId_fkey" FOREIGN KEY ("accountId") REFERENCES "Account"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Worker" ADD CONSTRAINT "Worker_workspaceId_fkey" FOREIGN KEY ("workspaceId") REFERENCES "Workspace"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "WorkerRun" ADD CONSTRAINT "WorkerRun_workerId_fkey" FOREIGN KEY ("workerId") REFERENCES "Worker"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Workspace" ADD CONSTRAINT "Workspace_ownerId_fkey" FOREIGN KEY ("ownerId") REFERENCES "Account"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "WorkspaceMember" ADD CONSTRAINT "WorkspaceMember_workspaceId_fkey" FOREIGN KEY ("workspaceId") REFERENCES "Workspace"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "WorkspaceMember" ADD CONSTRAINT "WorkspaceMember_accountId_fkey" FOREIGN KEY ("accountId") REFERENCES "Account"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

