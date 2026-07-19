-- AlterTable
ALTER TABLE "Listing" ADD COLUMN     "playCount" INTEGER NOT NULL DEFAULT 0;

-- CreateTable
CREATE TABLE "GamePlay" (
    "id" TEXT NOT NULL,
    "listingId" TEXT NOT NULL,
    "playerKey" TEXT NOT NULL,
    "startedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "GamePlay_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "GameScore" (
    "id" TEXT NOT NULL,
    "listingId" TEXT NOT NULL,
    "playerKey" TEXT NOT NULL,
    "score" INTEGER NOT NULL,
    "achievedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "meta" TEXT,

    CONSTRAINT "GameScore_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "GamePlay_listingId_startedAt_idx" ON "GamePlay"("listingId", "startedAt");

-- CreateIndex
CREATE INDEX "GamePlay_listingId_playerKey_startedAt_idx" ON "GamePlay"("listingId", "playerKey", "startedAt");

-- CreateIndex
CREATE INDEX "GameScore_listingId_score_idx" ON "GameScore"("listingId", "score");

-- CreateIndex
CREATE UNIQUE INDEX "GameScore_listingId_playerKey_key" ON "GameScore"("listingId", "playerKey");

-- AddForeignKey
ALTER TABLE "GamePlay" ADD CONSTRAINT "GamePlay_listingId_fkey" FOREIGN KEY ("listingId") REFERENCES "Listing"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "GameScore" ADD CONSTRAINT "GameScore_listingId_fkey" FOREIGN KEY ("listingId") REFERENCES "Listing"("id") ON DELETE CASCADE ON UPDATE CASCADE;

