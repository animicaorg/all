-- CreateEnum
CREATE TYPE "AppCategory" AS ENUM ('GAMES', 'AI_AGENTS', 'TOOLS', 'DEV_APPS', 'MOBILE', 'WEB');

-- CreateEnum
CREATE TYPE "BuildStatus" AS ENUM ('UPLOADED', 'CHECKS_FAILED', 'PENDING_REVIEW', 'APPROVED', 'REJECTED', 'DELISTED');

-- CreateEnum
CREATE TYPE "StoreAssetKind" AS ENUM ('ICON', 'SCREENSHOT', 'BANNER');

-- CreateEnum
CREATE TYPE "LicenseKind" AS ENUM ('PERPETUAL', 'SUBSCRIPTION');

-- AlterEnum
-- This migration adds more than one value to an enum.
-- With PostgreSQL versions 11 and earlier, this is not possible
-- in a single migration. This can be worked around by creating
-- multiple migrations, each migration adding only one value to
-- the enum.


ALTER TYPE "ListingType" ADD VALUE 'APP';
ALTER TYPE "ListingType" ADD VALUE 'DIGITAL_GOOD';

-- AlterTable
ALTER TABLE "Listing" ADD COLUMN     "anmDomainId" TEXT,
ADD COLUMN     "appCategory" "AppCategory",
ADD COLUMN     "packageName" TEXT,
ADD COLUMN     "pinnedCertSha256" TEXT;

-- AlterTable
ALTER TABLE "Purchase" ADD COLUMN     "autoRenew" BOOLEAN NOT NULL DEFAULT false,
ADD COLUMN     "consentId" TEXT,
ADD COLUMN     "graceUntil" TIMESTAMP(3),
ADD COLUMN     "parentPurchaseId" TEXT;

-- CreateTable
CREATE TABLE "AppBuild" (
    "id" TEXT NOT NULL,
    "listingId" TEXT NOT NULL,
    "versionCode" INTEGER NOT NULL,
    "versionName" TEXT NOT NULL,
    "channel" TEXT NOT NULL DEFAULT 'stable',
    "packageName" TEXT NOT NULL,
    "path" TEXT NOT NULL,
    "sizeBytes" BIGINT NOT NULL,
    "sha3" TEXT NOT NULL,
    "certSha256" TEXT NOT NULL,
    "minSdk" INTEGER NOT NULL,
    "targetSdk" INTEGER NOT NULL,
    "permissionsJson" JSONB NOT NULL,
    "badgingJson" JSONB NOT NULL,
    "releaseNotes" TEXT,
    "status" "BuildStatus" NOT NULL DEFAULT 'UPLOADED',
    "reviewNote" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "approvedAt" TIMESTAMP(3),

    CONSTRAINT "AppBuild_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "StoreAsset" (
    "id" TEXT NOT NULL,
    "listingId" TEXT NOT NULL,
    "kind" "StoreAssetKind" NOT NULL,
    "cid" TEXT NOT NULL,
    "width" INTEGER,
    "height" INTEGER,
    "sortOrder" INTEGER NOT NULL DEFAULT 0,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "StoreAsset_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "StorePaymentIntent" (
    "id" TEXT NOT NULL,
    "purchaseId" TEXT NOT NULL,
    "payTo" TEXT NOT NULL,
    "amountNanm" BIGINT NOT NULL,
    "memoHex" TEXT NOT NULL,
    "nonce" TEXT NOT NULL,
    "expiresAt" TIMESTAMP(3) NOT NULL,
    "submittedTxid" TEXT,
    "verifiedAt" TIMESTAMP(3),
    "failReason" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "StorePaymentIntent_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "License" (
    "id" TEXT NOT NULL,
    "purchaseId" TEXT NOT NULL,
    "buyerAddress" TEXT NOT NULL,
    "listingId" TEXT NOT NULL,
    "buildId" TEXT,
    "buildSha3" TEXT,
    "certSha256" TEXT,
    "kind" "LicenseKind" NOT NULL,
    "issuedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "expiresAt" TIMESTAMP(3),
    "revokedAt" TIMESTAMP(3),
    "leafHash" TEXT NOT NULL,
    "anchorId" TEXT,
    "merkleProofJson" JSONB,

    CONSTRAINT "License_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "LicenseAnchor" (
    "id" TEXT NOT NULL,
    "seq" INTEGER NOT NULL,
    "merkleRoot" TEXT NOT NULL,
    "leafCount" INTEGER NOT NULL,
    "prevTxid" TEXT,
    "txid" TEXT,
    "blockHeight" INTEGER,
    "status" TEXT NOT NULL DEFAULT 'pending',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "confirmedAt" TIMESTAMP(3),

    CONSTRAINT "LicenseAnchor_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "StoreConsent" (
    "id" TEXT NOT NULL,
    "accountId" TEXT NOT NULL,
    "purchaseId" TEXT,
    "purpose" TEXT NOT NULL,
    "message" TEXT NOT NULL,
    "signatureHex" TEXT NOT NULL,
    "pubkeyHex" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "StoreConsent_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "ChallengeBurn" (
    "id" TEXT NOT NULL,
    "mac" TEXT NOT NULL,
    "usedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ChallengeBurn_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "AppBuild_listingId_status_idx" ON "AppBuild"("listingId", "status");

-- CreateIndex
CREATE UNIQUE INDEX "AppBuild_listingId_channel_versionCode_key" ON "AppBuild"("listingId", "channel", "versionCode");

-- CreateIndex
CREATE INDEX "StoreAsset_listingId_idx" ON "StoreAsset"("listingId");

-- CreateIndex
CREATE UNIQUE INDEX "StorePaymentIntent_purchaseId_key" ON "StorePaymentIntent"("purchaseId");

-- CreateIndex
CREATE UNIQUE INDEX "StorePaymentIntent_nonce_key" ON "StorePaymentIntent"("nonce");

-- CreateIndex
CREATE INDEX "StorePaymentIntent_submittedTxid_idx" ON "StorePaymentIntent"("submittedTxid");

-- CreateIndex
CREATE UNIQUE INDEX "License_purchaseId_key" ON "License"("purchaseId");

-- CreateIndex
CREATE INDEX "License_buyerAddress_idx" ON "License"("buyerAddress");

-- CreateIndex
CREATE INDEX "License_listingId_idx" ON "License"("listingId");

-- CreateIndex
CREATE UNIQUE INDEX "LicenseAnchor_seq_key" ON "LicenseAnchor"("seq");

-- CreateIndex
CREATE INDEX "StoreConsent_accountId_idx" ON "StoreConsent"("accountId");

-- CreateIndex
CREATE UNIQUE INDEX "ChallengeBurn_mac_key" ON "ChallengeBurn"("mac");

-- CreateIndex
CREATE INDEX "ChallengeBurn_usedAt_idx" ON "ChallengeBurn"("usedAt");

-- CreateIndex
CREATE UNIQUE INDEX "Listing_packageName_key" ON "Listing"("packageName");

-- CreateIndex
CREATE INDEX "Listing_appCategory_idx" ON "Listing"("appCategory");

-- AddForeignKey
ALTER TABLE "Listing" ADD CONSTRAINT "Listing_anmDomainId_fkey" FOREIGN KEY ("anmDomainId") REFERENCES "AnmDomain"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Purchase" ADD CONSTRAINT "Purchase_parentPurchaseId_fkey" FOREIGN KEY ("parentPurchaseId") REFERENCES "Purchase"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "AppBuild" ADD CONSTRAINT "AppBuild_listingId_fkey" FOREIGN KEY ("listingId") REFERENCES "Listing"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "StoreAsset" ADD CONSTRAINT "StoreAsset_listingId_fkey" FOREIGN KEY ("listingId") REFERENCES "Listing"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "StorePaymentIntent" ADD CONSTRAINT "StorePaymentIntent_purchaseId_fkey" FOREIGN KEY ("purchaseId") REFERENCES "Purchase"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "License" ADD CONSTRAINT "License_purchaseId_fkey" FOREIGN KEY ("purchaseId") REFERENCES "Purchase"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "License" ADD CONSTRAINT "License_buildId_fkey" FOREIGN KEY ("buildId") REFERENCES "AppBuild"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "License" ADD CONSTRAINT "License_anchorId_fkey" FOREIGN KEY ("anchorId") REFERENCES "LicenseAnchor"("id") ON DELETE SET NULL ON UPDATE CASCADE;

