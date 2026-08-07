-- Animica Python Cloud — additive migration (2026-08-06)
--
-- Generated with:
--   npx prisma migrate diff --from-url "$DATABASE_URL" \
--       --to-schema-datamodel prisma/schema.prisma --script
--
-- Apply BEFORE deploying the code that uses it:
--   npx prisma db execute --file prisma/pythoncloud-migration.sql --schema prisma/schema.prisma
--
-- 100% additive: new tables + new enums only, no DROP and no column removal, so the running
-- app keeps working until the new code ships. Note the subs-migration.sql lesson: an
-- `ALTER TYPE ... ADD VALUE` must not share a transaction with statements that use the new
-- value — this file adds whole enums, so it is safe as a single script.

-- CreateEnum
CREATE TYPE "CloudCategory" AS ENUM ('AI', 'AGENTS', 'DEVELOPER_TOOLS', 'AUTOMATION', 'DATA', 'GAMES', 'PRODUCTIVITY', 'BLOCKCHAIN', 'UTILITIES', 'APIS');

-- CreateEnum
CREATE TYPE "CloudStatus" AS ENUM ('DRAFT', 'PUBLISHED', 'SUSPENDED', 'ARCHIVED');

-- CreateEnum
CREATE TYPE "CloudVisibility" AS ENUM ('PUBLIC', 'UNLISTED', 'PRIVATE');

-- CreateEnum
CREATE TYPE "CloudPricingModel" AS ENUM ('FREE', 'PAY_PER_USE', 'ONE_TIME', 'SUBSCRIPTION');

-- CreateEnum
CREATE TYPE "CloudDeployStatus" AS ENUM ('DRAFT', 'VALIDATING', 'BUILDING', 'AWAITING_SIGNATURE', 'BROADCASTING', 'CONFIRMING', 'ACTIVE', 'FAILED', 'PAUSED', 'ARCHIVED');

-- CreateEnum
CREATE TYPE "CloudAgentStatus" AS ENUM ('ACTIVE', 'PAUSED', 'DISABLED', 'SUSPENDED');

-- CreateEnum
CREATE TYPE "CloudExecStatus" AS ENUM ('QUEUED', 'DISPATCHED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'TIMEOUT', 'CANCELLED', 'REJECTED');

-- CreateEnum
CREATE TYPE "CloudProviderStatus" AS ENUM ('ACTIVE', 'IDLE', 'SUSPENDED', 'DISABLED');

-- CreateEnum
CREATE TYPE "CloudJobStatus" AS ENUM ('PENDING', 'CLAIMED', 'RUNNING', 'DONE', 'FAILED', 'EXPIRED');

-- CreateTable
CREATE TABLE "CloudApp" (
    "id" TEXT NOT NULL,
    "slug" TEXT NOT NULL,
    "ownerId" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "tagline" TEXT NOT NULL DEFAULT '',
    "description" TEXT NOT NULL DEFAULT '',
    "category" "CloudCategory" NOT NULL DEFAULT 'UTILITIES',
    "iconEmoji" TEXT NOT NULL DEFAULT '🐍',
    "iconUrl" TEXT,
    "bannerUrl" TEXT,
    "docsMd" TEXT NOT NULL DEFAULT '',
    "tags" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "status" "CloudStatus" NOT NULL DEFAULT 'DRAFT',
    "visibility" "CloudVisibility" NOT NULL DEFAULT 'PUBLIC',
    "pricingModel" "CloudPricingModel" NOT NULL DEFAULT 'PAY_PER_USE',
    "priceNanm" BIGINT NOT NULL DEFAULT 0,
    "capabilities" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "execCount" INTEGER NOT NULL DEFAULT 0,
    "installCount" INTEGER NOT NULL DEFAULT 0,
    "ratingSum" INTEGER NOT NULL DEFAULT 0,
    "ratingCount" INTEGER NOT NULL DEFAULT 0,
    "revenueNanm" BIGINT NOT NULL DEFAULT 0,
    "publishedAt" TIMESTAMP(3),
    "suspendedAt" TIMESTAMP(3),
    "suspendedReason" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "CloudApp_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CloudFunction" (
    "id" TEXT NOT NULL,
    "ownerId" TEXT NOT NULL,
    "appId" TEXT,
    "slug" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "description" TEXT NOT NULL DEFAULT '',
    "runtime" TEXT NOT NULL DEFAULT 'python3.12',
    "entrypoint" TEXT NOT NULL DEFAULT 'main',
    "status" "CloudStatus" NOT NULL DEFAULT 'DRAFT',
    "visibility" "CloudVisibility" NOT NULL DEFAULT 'PUBLIC',
    "timeoutMs" INTEGER NOT NULL DEFAULT 30000,
    "memoryMb" INTEGER NOT NULL DEFAULT 256,
    "capabilities" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "priceModel" "CloudPricingModel" NOT NULL DEFAULT 'PAY_PER_USE',
    "perCallNanm" BIGINT NOT NULL DEFAULT 0,
    "requiresAuth" BOOLEAN NOT NULL DEFAULT false,
    "currentVersion" INTEGER NOT NULL DEFAULT 0,
    "execCount" INTEGER NOT NULL DEFAULT 0,
    "revenueNanm" BIGINT NOT NULL DEFAULT 0,
    "suspendedAt" TIMESTAMP(3),
    "suspendedReason" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "CloudFunction_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CloudFunctionVersion" (
    "id" TEXT NOT NULL,
    "functionId" TEXT NOT NULL,
    "version" INTEGER NOT NULL,
    "source" TEXT NOT NULL,
    "sourceSha3" TEXT NOT NULL,
    "artifactSha3" TEXT NOT NULL,
    "sizeBytes" INTEGER NOT NULL,
    "entrypoint" TEXT NOT NULL,
    "packages" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "envKeys" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "validationJson" TEXT NOT NULL DEFAULT '{}',
    "estimateNanm" BIGINT NOT NULL DEFAULT 0,
    "createdById" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "CloudFunctionVersion_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CloudDeployment" (
    "id" TEXT NOT NULL,
    "functionId" TEXT NOT NULL,
    "versionId" TEXT NOT NULL,
    "status" "CloudDeployStatus" NOT NULL DEFAULT 'DRAFT',
    "daBlobId" TEXT,
    "anchorTxid" TEXT,
    "anchorHeight" INTEGER,
    "anchorConfirms" INTEGER NOT NULL DEFAULT 0,
    "registryName" TEXT,
    "deployerAddress" TEXT,
    "endpoint" TEXT,
    "error" TEXT,
    "logsJson" TEXT NOT NULL DEFAULT '[]',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "activatedAt" TIMESTAMP(3),

    CONSTRAINT "CloudDeployment_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CloudAgent" (
    "id" TEXT NOT NULL,
    "ownerId" TEXT NOT NULL,
    "slug" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "description" TEXT NOT NULL DEFAULT '',
    "appId" TEXT,
    "functionId" TEXT NOT NULL,
    "address" TEXT,
    "status" "CloudAgentStatus" NOT NULL DEFAULT 'PAUSED',
    "capabilities" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "maxSpendPerRunNanm" BIGINT NOT NULL DEFAULT 0,
    "dailySpendCapNanm" BIGINT NOT NULL DEFAULT 0,
    "spentTodayNanm" BIGINT NOT NULL DEFAULT 0,
    "spendDayKey" TEXT NOT NULL DEFAULT '',
    "lastRunAt" TIMESTAMP(3),
    "runsTotal" INTEGER NOT NULL DEFAULT 0,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "CloudAgent_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CloudExecution" (
    "id" TEXT NOT NULL,
    "requestId" TEXT NOT NULL,
    "functionId" TEXT NOT NULL,
    "versionId" TEXT NOT NULL,
    "appId" TEXT,
    "agentId" TEXT,
    "callerAccountId" TEXT,
    "callerKind" TEXT NOT NULL DEFAULT 'user',
    "developerAccountId" TEXT NOT NULL,
    "providerId" TEXT,
    "parentExecutionId" TEXT,
    "rootId" TEXT,
    "depth" INTEGER NOT NULL DEFAULT 0,
    "status" "CloudExecStatus" NOT NULL DEFAULT 'QUEUED',
    "lane" TEXT NOT NULL DEFAULT 'local',
    "errorCode" TEXT,
    "error" TEXT,
    "httpStatus" INTEGER,
    "queuedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "startedAt" TIMESTAMP(3),
    "finishedAt" TIMESTAMP(3),
    "durationMs" INTEGER NOT NULL DEFAULT 0,
    "cpuMs" INTEGER NOT NULL DEFAULT 0,
    "memoryMbMs" BIGINT NOT NULL DEFAULT 0,
    "aiTokensIn" INTEGER NOT NULL DEFAULT 0,
    "aiTokensOut" INTEGER NOT NULL DEFAULT 0,
    "aiCalls" INTEGER NOT NULL DEFAULT 0,
    "egressBytes" INTEGER NOT NULL DEFAULT 0,
    "bytesIn" INTEGER NOT NULL DEFAULT 0,
    "bytesOut" INTEGER NOT NULL DEFAULT 0,
    "quotedNanm" BIGINT NOT NULL DEFAULT 0,
    "priceNanm" BIGINT NOT NULL DEFAULT 0,
    "platformFeeNanm" BIGINT NOT NULL DEFAULT 0,
    "developerNanm" BIGINT NOT NULL DEFAULT 0,
    "providerNanm" BIGINT NOT NULL DEFAULT 0,
    "feeBps" INTEGER NOT NULL DEFAULT 2000,
    "cogsNanm" BIGINT NOT NULL DEFAULT 0,
    "cogsAiNanm" BIGINT NOT NULL DEFAULT 0,
    "cogsComputeNanm" BIGINT NOT NULL DEFAULT 0,
    "cogsInfraNanm" BIGINT NOT NULL DEFAULT 0,
    "cogsPromoNanm" BIGINT NOT NULL DEFAULT 0,
    "contributionNanm" BIGINT NOT NULL DEFAULT 0,
    "creditNanm" BIGINT NOT NULL DEFAULT 0,
    "billed" BOOLEAN NOT NULL DEFAULT false,
    "freeTier" BOOLEAN NOT NULL DEFAULT false,
    "pricingPolicyId" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "CloudExecution_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CloudExecutionLog" (
    "id" TEXT NOT NULL,
    "executionId" TEXT NOT NULL,
    "ts" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "level" TEXT NOT NULL DEFAULT 'info',
    "message" TEXT NOT NULL,
    "seq" INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT "CloudExecutionLog_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CloudProvider" (
    "id" TEXT NOT NULL,
    "accountId" TEXT,
    "address" TEXT NOT NULL,
    "name" TEXT NOT NULL DEFAULT '',
    "keyHash" TEXT NOT NULL,
    "capabilities" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "cpuCores" INTEGER NOT NULL DEFAULT 1,
    "memoryMb" INTEGER NOT NULL DEFAULT 1024,
    "gpu" TEXT,
    "status" "CloudProviderStatus" NOT NULL DEFAULT 'ACTIVE',
    "lastSeenAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "jobsClaimed" INTEGER NOT NULL DEFAULT 0,
    "jobsDone" INTEGER NOT NULL DEFAULT 0,
    "jobsFailed" INTEGER NOT NULL DEFAULT 0,
    "reputation" INTEGER NOT NULL DEFAULT 0,
    "earnedNanm" BIGINT NOT NULL DEFAULT 0,
    "suspendedReason" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "CloudProvider_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CloudJob" (
    "id" TEXT NOT NULL,
    "executionId" TEXT NOT NULL,
    "providerId" TEXT,
    "status" "CloudJobStatus" NOT NULL DEFAULT 'PENDING',
    "priority" INTEGER NOT NULL DEFAULT 0,
    "attempts" INTEGER NOT NULL DEFAULT 0,
    "leaseUntil" TIMESTAMP(3),
    "payloadJson" TEXT NOT NULL DEFAULT '{}',
    "resultJson" TEXT,
    "error" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "claimedAt" TIMESTAMP(3),
    "finishedAt" TIMESTAMP(3),

    CONSTRAINT "CloudJob_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CloudGrant" (
    "id" TEXT NOT NULL,
    "accountId" TEXT NOT NULL,
    "subjectKind" TEXT NOT NULL,
    "subjectId" TEXT NOT NULL,
    "capabilities" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "maxPerCallNanm" BIGINT NOT NULL DEFAULT 0,
    "maxPerExecNanm" BIGINT NOT NULL DEFAULT 0,
    "dailyCapNanm" BIGINT NOT NULL DEFAULT 0,
    "spentTodayNanm" BIGINT NOT NULL DEFAULT 0,
    "spendDayKey" TEXT NOT NULL DEFAULT '',
    "allowedPayees" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "expiresAt" TIMESTAMP(3),
    "revokedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "CloudGrant_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CloudSecret" (
    "id" TEXT NOT NULL,
    "ownerId" TEXT NOT NULL,
    "functionId" TEXT,
    "name" TEXT NOT NULL,
    "ciphertext" TEXT NOT NULL,
    "hint" TEXT NOT NULL DEFAULT '',
    "lastUsedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "CloudSecret_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CloudSchedule" (
    "id" TEXT NOT NULL,
    "functionId" TEXT NOT NULL,
    "ownerId" TEXT NOT NULL,
    "kind" TEXT NOT NULL DEFAULT 'interval',
    "intervalMinutes" INTEGER,
    "cron" TEXT,
    "payloadJson" TEXT NOT NULL DEFAULT '{}',
    "enabled" BOOLEAN NOT NULL DEFAULT true,
    "nextRunAt" TIMESTAMP(3),
    "lastRunAt" TIMESTAMP(3),
    "lastStatus" TEXT,
    "runsTotal" INTEGER NOT NULL DEFAULT 0,
    "failures" INTEGER NOT NULL DEFAULT 0,
    "disabledReason" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "CloudSchedule_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CloudAppPurchase" (
    "id" TEXT NOT NULL,
    "appId" TEXT NOT NULL,
    "accountId" TEXT NOT NULL,
    "kind" TEXT NOT NULL,
    "amountNanm" BIGINT NOT NULL DEFAULT 0,
    "platformFeeNanm" BIGINT NOT NULL DEFAULT 0,
    "developerNanm" BIGINT NOT NULL DEFAULT 0,
    "feeBps" INTEGER NOT NULL DEFAULT 2000,
    "status" TEXT NOT NULL DEFAULT 'ACTIVE',
    "expiresAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "CloudAppPurchase_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CloudReview" (
    "id" TEXT NOT NULL,
    "appId" TEXT NOT NULL,
    "accountId" TEXT NOT NULL,
    "rating" INTEGER NOT NULL,
    "body" TEXT NOT NULL DEFAULT '',
    "hidden" BOOLEAN NOT NULL DEFAULT false,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "CloudReview_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CloudReport" (
    "id" TEXT NOT NULL,
    "subjectKind" TEXT NOT NULL,
    "subjectId" TEXT NOT NULL,
    "reporterId" TEXT,
    "reason" TEXT NOT NULL,
    "detail" TEXT NOT NULL DEFAULT '',
    "status" TEXT NOT NULL DEFAULT 'OPEN',
    "resolution" TEXT,
    "resolvedBy" TEXT,
    "resolvedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "CloudReport_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CloudAuditLog" (
    "id" TEXT NOT NULL,
    "actor" TEXT NOT NULL,
    "action" TEXT NOT NULL,
    "subject" TEXT NOT NULL,
    "before" TEXT NOT NULL DEFAULT '{}',
    "after" TEXT NOT NULL DEFAULT '{}',
    "reason" TEXT NOT NULL DEFAULT '',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "CloudAuditLog_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CloudCodeDenylist" (
    "sha3" TEXT NOT NULL,
    "reason" TEXT NOT NULL,
    "addedBy" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "CloudCodeDenylist_pkey" PRIMARY KEY ("sha3")
);

-- CreateTable
CREATE TABLE "PricingPolicy" (
    "id" TEXT NOT NULL,
    "version" INTEGER NOT NULL,
    "active" BOOLEAN NOT NULL DEFAULT false,
    "note" TEXT NOT NULL DEFAULT '',
    "baseCallNanm" BIGINT NOT NULL DEFAULT 100000,
    "cpuMsNanm" BIGINT NOT NULL DEFAULT 20,
    "memMbMsNanm" BIGINT NOT NULL DEFAULT 1,
    "aiTokenInNanm" BIGINT NOT NULL DEFAULT 1000,
    "aiTokenOutNanm" BIGINT NOT NULL DEFAULT 3000,
    "egressKbNanm" BIGINT NOT NULL DEFAULT 50,
    "gpuMsNanm" BIGINT NOT NULL DEFAULT 400,
    "platformFeeBps" INTEGER NOT NULL DEFAULT 2000,
    "providerShareBps" INTEGER NOT NULL DEFAULT 1000,
    "costCpuMsNanm" BIGINT NOT NULL DEFAULT 6,
    "costMemMbMsNanm" BIGINT NOT NULL DEFAULT 1,
    "costAiTokenNanm" BIGINT NOT NULL DEFAULT 400,
    "costEgressKbNanm" BIGINT NOT NULL DEFAULT 10,
    "costPerCallNanm" BIGINT NOT NULL DEFAULT 20000,
    "targetMarginBps" INTEGER NOT NULL DEFAULT 6000,
    "enforceMinMargin" BOOLEAN NOT NULL DEFAULT true,
    "freeExecutionsPerDay" INTEGER NOT NULL DEFAULT 50,
    "freeExecutionsPerMonth" INTEGER NOT NULL DEFAULT 500,
    "freeAiTokensPerDay" INTEGER NOT NULL DEFAULT 20000,
    "freeTierMonthlyCeilingNanm" BIGINT NOT NULL DEFAULT 0,
    "anmUsdFloorMicros" BIGINT NOT NULL DEFAULT 0,
    "createdBy" TEXT NOT NULL DEFAULT 'system',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "executionsPriced" INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT "PricingPolicy_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "PricingChange" (
    "id" TEXT NOT NULL,
    "actor" TEXT NOT NULL,
    "field" TEXT NOT NULL,
    "oldValue" TEXT NOT NULL,
    "newValue" TEXT NOT NULL,
    "policyId" TEXT,
    "reason" TEXT NOT NULL DEFAULT '',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "PricingChange_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "AnmPriceSnapshot" (
    "id" TEXT NOT NULL,
    "usdMicros" BIGINT NOT NULL,
    "source" TEXT NOT NULL,
    "observedAt" TIMESTAMP(3) NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "AnmPriceSnapshot_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "BillingPayment" (
    "id" TEXT NOT NULL,
    "accountId" TEXT NOT NULL,
    "subscriptionId" TEXT,
    "planKey" TEXT,
    "paypalCaptureId" TEXT NOT NULL,
    "paypalSubscriptionId" TEXT,
    "amountCents" INTEGER NOT NULL,
    "currency" TEXT NOT NULL DEFAULT 'USD',
    "status" TEXT NOT NULL DEFAULT 'COMPLETED',
    "kind" TEXT NOT NULL DEFAULT 'subscription',
    "payerEmail" TEXT,
    "occurredAt" TIMESTAMP(3) NOT NULL,
    "rawJson" TEXT NOT NULL DEFAULT '{}',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "BillingPayment_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "UsageCharge" (
    "id" TEXT NOT NULL,
    "accountId" TEXT NOT NULL,
    "period" TEXT NOT NULL,
    "feature" TEXT NOT NULL,
    "units" BIGINT NOT NULL DEFAULT 0,
    "unitPriceCents" INTEGER NOT NULL DEFAULT 0,
    "amountCents" INTEGER NOT NULL DEFAULT 0,
    "amountNanm" BIGINT NOT NULL DEFAULT 0,
    "asset" TEXT NOT NULL DEFAULT 'USD',
    "status" TEXT NOT NULL DEFAULT 'ACCRUING',
    "invoicedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "UsageCharge_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "FinanceDaily" (
    "day" TEXT NOT NULL,
    "grossVolumeNanm" BIGINT NOT NULL DEFAULT 0,
    "platformRevNanm" BIGINT NOT NULL DEFAULT 0,
    "developerPayNanm" BIGINT NOT NULL DEFAULT 0,
    "providerPayNanm" BIGINT NOT NULL DEFAULT 0,
    "cogsNanm" BIGINT NOT NULL DEFAULT 0,
    "contributionNanm" BIGINT NOT NULL DEFAULT 0,
    "freeTierCogsNanm" BIGINT NOT NULL DEFAULT 0,
    "usdRevenueCents" INTEGER NOT NULL DEFAULT 0,
    "mrrCents" INTEGER NOT NULL DEFAULT 0,
    "newMrrCents" INTEGER NOT NULL DEFAULT 0,
    "churnedMrrCents" INTEGER NOT NULL DEFAULT 0,
    "executions" INTEGER NOT NULL DEFAULT 0,
    "freeExecutions" INTEGER NOT NULL DEFAULT 0,
    "activeDevelopers" INTEGER NOT NULL DEFAULT 0,
    "payingAccounts" INTEGER NOT NULL DEFAULT 0,
    "anmUsdMicros" BIGINT NOT NULL DEFAULT 0,
    "computedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "FinanceDaily_pkey" PRIMARY KEY ("day")
);

-- CreateTable
CREATE TABLE "ReconciliationReport" (
    "id" TEXT NOT NULL,
    "day" TEXT NOT NULL,
    "scope" TEXT NOT NULL,
    "ok" BOOLEAN NOT NULL DEFAULT true,
    "expected" TEXT NOT NULL DEFAULT '0',
    "observed" TEXT NOT NULL DEFAULT '0',
    "deltaAbs" TEXT NOT NULL DEFAULT '0',
    "detail" TEXT NOT NULL DEFAULT '{}',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ReconciliationReport_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "FinanceAlert" (
    "id" TEXT NOT NULL,
    "kind" TEXT NOT NULL,
    "severity" TEXT NOT NULL DEFAULT 'warn',
    "title" TEXT NOT NULL,
    "detail" TEXT NOT NULL DEFAULT '{}',
    "subject" TEXT,
    "resolvedAt" TIMESTAMP(3),
    "resolvedBy" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "FinanceAlert_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "EnterpriseInquiry" (
    "id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "company" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "workload" TEXT NOT NULL DEFAULT '',
    "aiNeeds" TEXT NOT NULL DEFAULT '',
    "computeNeeds" TEXT NOT NULL DEFAULT '',
    "monthlyExecs" TEXT NOT NULL DEFAULT '',
    "dedicated" BOOLEAN NOT NULL DEFAULT false,
    "message" TEXT NOT NULL DEFAULT '',
    "accountId" TEXT,
    "status" TEXT NOT NULL DEFAULT 'NEW',
    "notes" TEXT NOT NULL DEFAULT '',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "EnterpriseInquiry_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "FoundingDeveloper" (
    "id" TEXT NOT NULL,
    "accountId" TEXT NOT NULL,
    "seq" INTEGER,
    "status" TEXT NOT NULL DEFAULT 'APPLIED',
    "handle" TEXT NOT NULL DEFAULT '',
    "email" TEXT NOT NULL DEFAULT '',
    "pitch" TEXT NOT NULL DEFAULT '',
    "proUntil" TIMESTAMP(3),
    "feeBps" INTEGER NOT NULL DEFAULT 1000,
    "feeUntil" TIMESTAMP(3),
    "creditsNanm" BIGINT NOT NULL DEFAULT 0,
    "featured" BOOLEAN NOT NULL DEFAULT false,
    "acceptedAt" TIMESTAMP(3),
    "acceptedBy" TEXT,
    "revokedAt" TIMESTAMP(3),
    "revokedReason" TEXT,
    "notes" TEXT NOT NULL DEFAULT '',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "FoundingDeveloper_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CloudCredit" (
    "id" TEXT NOT NULL,
    "accountId" TEXT NOT NULL,
    "grantedNanm" BIGINT NOT NULL DEFAULT 0,
    "usedNanm" BIGINT NOT NULL DEFAULT 0,
    "reason" TEXT NOT NULL DEFAULT '',
    "source" TEXT NOT NULL DEFAULT 'promo',
    "expiresAt" TIMESTAMP(3),
    "revokedAt" TIMESTAMP(3),
    "createdBy" TEXT NOT NULL DEFAULT 'system',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "CloudCredit_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "CloudApp_slug_key" ON "CloudApp"("slug");

-- CreateIndex
CREATE INDEX "CloudApp_status_visibility_publishedAt_idx" ON "CloudApp"("status", "visibility", "publishedAt");

-- CreateIndex
CREATE INDEX "CloudApp_ownerId_idx" ON "CloudApp"("ownerId");

-- CreateIndex
CREATE INDEX "CloudApp_category_status_idx" ON "CloudApp"("category", "status");

-- CreateIndex
CREATE INDEX "CloudFunction_status_visibility_idx" ON "CloudFunction"("status", "visibility");

-- CreateIndex
CREATE INDEX "CloudFunction_appId_idx" ON "CloudFunction"("appId");

-- CreateIndex
CREATE UNIQUE INDEX "CloudFunction_ownerId_slug_key" ON "CloudFunction"("ownerId", "slug");

-- CreateIndex
CREATE INDEX "CloudFunctionVersion_sourceSha3_idx" ON "CloudFunctionVersion"("sourceSha3");

-- CreateIndex
CREATE UNIQUE INDEX "CloudFunctionVersion_functionId_version_key" ON "CloudFunctionVersion"("functionId", "version");

-- CreateIndex
CREATE UNIQUE INDEX "CloudDeployment_anchorTxid_key" ON "CloudDeployment"("anchorTxid");

-- CreateIndex
CREATE INDEX "CloudDeployment_functionId_createdAt_idx" ON "CloudDeployment"("functionId", "createdAt");

-- CreateIndex
CREATE INDEX "CloudDeployment_status_idx" ON "CloudDeployment"("status");

-- CreateIndex
CREATE UNIQUE INDEX "CloudAgent_slug_key" ON "CloudAgent"("slug");

-- CreateIndex
CREATE UNIQUE INDEX "CloudAgent_address_key" ON "CloudAgent"("address");

-- CreateIndex
CREATE INDEX "CloudAgent_ownerId_status_idx" ON "CloudAgent"("ownerId", "status");

-- CreateIndex
CREATE UNIQUE INDEX "CloudExecution_requestId_key" ON "CloudExecution"("requestId");

-- CreateIndex
CREATE INDEX "CloudExecution_functionId_createdAt_idx" ON "CloudExecution"("functionId", "createdAt");

-- CreateIndex
CREATE INDEX "CloudExecution_developerAccountId_createdAt_idx" ON "CloudExecution"("developerAccountId", "createdAt");

-- CreateIndex
CREATE INDEX "CloudExecution_callerAccountId_createdAt_idx" ON "CloudExecution"("callerAccountId", "createdAt");

-- CreateIndex
CREATE INDEX "CloudExecution_status_queuedAt_idx" ON "CloudExecution"("status", "queuedAt");

-- CreateIndex
CREATE INDEX "CloudExecution_rootId_idx" ON "CloudExecution"("rootId");

-- CreateIndex
CREATE INDEX "CloudExecution_appId_createdAt_idx" ON "CloudExecution"("appId", "createdAt");

-- CreateIndex
CREATE INDEX "CloudExecution_createdAt_idx" ON "CloudExecution"("createdAt");

-- CreateIndex
CREATE INDEX "CloudExecutionLog_executionId_seq_idx" ON "CloudExecutionLog"("executionId", "seq");

-- CreateIndex
CREATE INDEX "CloudExecutionLog_ts_idx" ON "CloudExecutionLog"("ts");

-- CreateIndex
CREATE UNIQUE INDEX "CloudProvider_keyHash_key" ON "CloudProvider"("keyHash");

-- CreateIndex
CREATE INDEX "CloudProvider_status_lastSeenAt_idx" ON "CloudProvider"("status", "lastSeenAt");

-- CreateIndex
CREATE INDEX "CloudProvider_address_idx" ON "CloudProvider"("address");

-- CreateIndex
CREATE UNIQUE INDEX "CloudJob_executionId_key" ON "CloudJob"("executionId");

-- CreateIndex
CREATE INDEX "CloudJob_status_priority_createdAt_idx" ON "CloudJob"("status", "priority", "createdAt");

-- CreateIndex
CREATE INDEX "CloudJob_providerId_status_idx" ON "CloudJob"("providerId", "status");

-- CreateIndex
CREATE INDEX "CloudJob_leaseUntil_idx" ON "CloudJob"("leaseUntil");

-- CreateIndex
CREATE INDEX "CloudGrant_accountId_idx" ON "CloudGrant"("accountId");

-- CreateIndex
CREATE UNIQUE INDEX "CloudGrant_accountId_subjectKind_subjectId_key" ON "CloudGrant"("accountId", "subjectKind", "subjectId");

-- CreateIndex
CREATE INDEX "CloudSecret_ownerId_idx" ON "CloudSecret"("ownerId");

-- CreateIndex
CREATE UNIQUE INDEX "CloudSecret_ownerId_functionId_name_key" ON "CloudSecret"("ownerId", "functionId", "name");

-- CreateIndex
CREATE INDEX "CloudSchedule_enabled_nextRunAt_idx" ON "CloudSchedule"("enabled", "nextRunAt");

-- CreateIndex
CREATE INDEX "CloudSchedule_ownerId_idx" ON "CloudSchedule"("ownerId");

-- CreateIndex
CREATE INDEX "CloudAppPurchase_accountId_createdAt_idx" ON "CloudAppPurchase"("accountId", "createdAt");

-- CreateIndex
CREATE UNIQUE INDEX "CloudAppPurchase_appId_accountId_kind_key" ON "CloudAppPurchase"("appId", "accountId", "kind");

-- CreateIndex
CREATE INDEX "CloudReview_appId_createdAt_idx" ON "CloudReview"("appId", "createdAt");

-- CreateIndex
CREATE UNIQUE INDEX "CloudReview_appId_accountId_key" ON "CloudReview"("appId", "accountId");

-- CreateIndex
CREATE INDEX "CloudReport_status_createdAt_idx" ON "CloudReport"("status", "createdAt");

-- CreateIndex
CREATE INDEX "CloudReport_subjectKind_subjectId_idx" ON "CloudReport"("subjectKind", "subjectId");

-- CreateIndex
CREATE INDEX "CloudAuditLog_createdAt_idx" ON "CloudAuditLog"("createdAt");

-- CreateIndex
CREATE INDEX "CloudAuditLog_action_createdAt_idx" ON "CloudAuditLog"("action", "createdAt");

-- CreateIndex
CREATE UNIQUE INDEX "PricingPolicy_version_key" ON "PricingPolicy"("version");

-- CreateIndex
CREATE INDEX "PricingPolicy_active_idx" ON "PricingPolicy"("active");

-- CreateIndex
CREATE INDEX "PricingChange_createdAt_idx" ON "PricingChange"("createdAt");

-- CreateIndex
CREATE INDEX "AnmPriceSnapshot_observedAt_idx" ON "AnmPriceSnapshot"("observedAt");

-- CreateIndex
CREATE UNIQUE INDEX "BillingPayment_paypalCaptureId_key" ON "BillingPayment"("paypalCaptureId");

-- CreateIndex
CREATE INDEX "BillingPayment_accountId_occurredAt_idx" ON "BillingPayment"("accountId", "occurredAt");

-- CreateIndex
CREATE INDEX "BillingPayment_occurredAt_idx" ON "BillingPayment"("occurredAt");

-- CreateIndex
CREATE INDEX "BillingPayment_status_occurredAt_idx" ON "BillingPayment"("status", "occurredAt");

-- CreateIndex
CREATE INDEX "UsageCharge_period_status_idx" ON "UsageCharge"("period", "status");

-- CreateIndex
CREATE UNIQUE INDEX "UsageCharge_accountId_period_feature_key" ON "UsageCharge"("accountId", "period", "feature");

-- CreateIndex
CREATE INDEX "FinanceDaily_computedAt_idx" ON "FinanceDaily"("computedAt");

-- CreateIndex
CREATE INDEX "ReconciliationReport_ok_createdAt_idx" ON "ReconciliationReport"("ok", "createdAt");

-- CreateIndex
CREATE UNIQUE INDEX "ReconciliationReport_day_scope_key" ON "ReconciliationReport"("day", "scope");

-- CreateIndex
CREATE INDEX "FinanceAlert_resolvedAt_createdAt_idx" ON "FinanceAlert"("resolvedAt", "createdAt");

-- CreateIndex
CREATE INDEX "FinanceAlert_kind_createdAt_idx" ON "FinanceAlert"("kind", "createdAt");

-- CreateIndex
CREATE INDEX "EnterpriseInquiry_status_createdAt_idx" ON "EnterpriseInquiry"("status", "createdAt");

-- CreateIndex
CREATE UNIQUE INDEX "FoundingDeveloper_accountId_key" ON "FoundingDeveloper"("accountId");

-- CreateIndex
CREATE UNIQUE INDEX "FoundingDeveloper_seq_key" ON "FoundingDeveloper"("seq");

-- CreateIndex
CREATE INDEX "FoundingDeveloper_status_createdAt_idx" ON "FoundingDeveloper"("status", "createdAt");

-- CreateIndex
CREATE INDEX "CloudCredit_accountId_expiresAt_idx" ON "CloudCredit"("accountId", "expiresAt");

-- AddForeignKey
ALTER TABLE "CloudApp" ADD CONSTRAINT "CloudApp_ownerId_fkey" FOREIGN KEY ("ownerId") REFERENCES "Account"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudFunction" ADD CONSTRAINT "CloudFunction_ownerId_fkey" FOREIGN KEY ("ownerId") REFERENCES "Account"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudFunction" ADD CONSTRAINT "CloudFunction_appId_fkey" FOREIGN KEY ("appId") REFERENCES "CloudApp"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudFunctionVersion" ADD CONSTRAINT "CloudFunctionVersion_functionId_fkey" FOREIGN KEY ("functionId") REFERENCES "CloudFunction"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudDeployment" ADD CONSTRAINT "CloudDeployment_functionId_fkey" FOREIGN KEY ("functionId") REFERENCES "CloudFunction"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudDeployment" ADD CONSTRAINT "CloudDeployment_versionId_fkey" FOREIGN KEY ("versionId") REFERENCES "CloudFunctionVersion"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudAgent" ADD CONSTRAINT "CloudAgent_ownerId_fkey" FOREIGN KEY ("ownerId") REFERENCES "Account"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudAgent" ADD CONSTRAINT "CloudAgent_appId_fkey" FOREIGN KEY ("appId") REFERENCES "CloudApp"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudAgent" ADD CONSTRAINT "CloudAgent_functionId_fkey" FOREIGN KEY ("functionId") REFERENCES "CloudFunction"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudExecution" ADD CONSTRAINT "CloudExecution_functionId_fkey" FOREIGN KEY ("functionId") REFERENCES "CloudFunction"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudExecution" ADD CONSTRAINT "CloudExecution_versionId_fkey" FOREIGN KEY ("versionId") REFERENCES "CloudFunctionVersion"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudExecution" ADD CONSTRAINT "CloudExecution_appId_fkey" FOREIGN KEY ("appId") REFERENCES "CloudApp"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudExecution" ADD CONSTRAINT "CloudExecution_agentId_fkey" FOREIGN KEY ("agentId") REFERENCES "CloudAgent"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudExecution" ADD CONSTRAINT "CloudExecution_callerAccountId_fkey" FOREIGN KEY ("callerAccountId") REFERENCES "Account"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudExecution" ADD CONSTRAINT "CloudExecution_developerAccountId_fkey" FOREIGN KEY ("developerAccountId") REFERENCES "Account"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudExecution" ADD CONSTRAINT "CloudExecution_providerId_fkey" FOREIGN KEY ("providerId") REFERENCES "CloudProvider"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudExecution" ADD CONSTRAINT "CloudExecution_parentExecutionId_fkey" FOREIGN KEY ("parentExecutionId") REFERENCES "CloudExecution"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudExecutionLog" ADD CONSTRAINT "CloudExecutionLog_executionId_fkey" FOREIGN KEY ("executionId") REFERENCES "CloudExecution"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudProvider" ADD CONSTRAINT "CloudProvider_accountId_fkey" FOREIGN KEY ("accountId") REFERENCES "Account"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudJob" ADD CONSTRAINT "CloudJob_executionId_fkey" FOREIGN KEY ("executionId") REFERENCES "CloudExecution"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudJob" ADD CONSTRAINT "CloudJob_providerId_fkey" FOREIGN KEY ("providerId") REFERENCES "CloudProvider"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudGrant" ADD CONSTRAINT "CloudGrant_accountId_fkey" FOREIGN KEY ("accountId") REFERENCES "Account"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudSecret" ADD CONSTRAINT "CloudSecret_ownerId_fkey" FOREIGN KEY ("ownerId") REFERENCES "Account"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudSecret" ADD CONSTRAINT "CloudSecret_functionId_fkey" FOREIGN KEY ("functionId") REFERENCES "CloudFunction"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudSchedule" ADD CONSTRAINT "CloudSchedule_functionId_fkey" FOREIGN KEY ("functionId") REFERENCES "CloudFunction"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudAppPurchase" ADD CONSTRAINT "CloudAppPurchase_appId_fkey" FOREIGN KEY ("appId") REFERENCES "CloudApp"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudAppPurchase" ADD CONSTRAINT "CloudAppPurchase_accountId_fkey" FOREIGN KEY ("accountId") REFERENCES "Account"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudReview" ADD CONSTRAINT "CloudReview_appId_fkey" FOREIGN KEY ("appId") REFERENCES "CloudApp"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudReview" ADD CONSTRAINT "CloudReview_accountId_fkey" FOREIGN KEY ("accountId") REFERENCES "Account"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "BillingPayment" ADD CONSTRAINT "BillingPayment_accountId_fkey" FOREIGN KEY ("accountId") REFERENCES "Account"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "UsageCharge" ADD CONSTRAINT "UsageCharge_accountId_fkey" FOREIGN KEY ("accountId") REFERENCES "Account"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "FoundingDeveloper" ADD CONSTRAINT "FoundingDeveloper_accountId_fkey" FOREIGN KEY ("accountId") REFERENCES "Account"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CloudCredit" ADD CONSTRAINT "CloudCredit_accountId_fkey" FOREIGN KEY ("accountId") REFERENCES "Account"("id") ON DELETE CASCADE ON UPDATE CASCADE;

