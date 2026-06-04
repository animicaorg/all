-- CreateEnum
CREATE TYPE "CreditPurchaseStatus" AS ENUM ('pending', 'paid', 'failed', 'expired');

-- CreateTable
CREATE TABLE "CreditPurchase" (
    "id" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "amountUsd" TEXT NOT NULL,
    "status" "CreditPurchaseStatus" NOT NULL DEFAULT 'pending',
    "payCurrency" TEXT,
    "actuallyPaidUsd" TEXT,
    "npInvoiceId" TEXT,
    "npPaymentId" TEXT,
    "invoiceUrl" TEXT,
    "creditedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "CreditPurchase_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "CreditPurchase_userId_createdAt_idx" ON "CreditPurchase"("userId", "createdAt");

-- CreateIndex
CREATE INDEX "CreditPurchase_npPaymentId_idx" ON "CreditPurchase"("npPaymentId");

-- AddForeignKey
ALTER TABLE "CreditPurchase" ADD CONSTRAINT "CreditPurchase_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;
