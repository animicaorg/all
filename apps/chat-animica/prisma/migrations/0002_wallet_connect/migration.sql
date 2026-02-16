-- CreateTable
CREATE TABLE "WalletSession" (
  "id" TEXT NOT NULL,
  "userId" TEXT NOT NULL,
  "type" TEXT NOT NULL,
  "accounts" TEXT[],
  "status" TEXT NOT NULL,
  "metadata" JSONB,
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "lastUsedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "WalletSession_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "WalletConnectRequest" (
  "id" TEXT NOT NULL,
  "userId" TEXT NOT NULL,
  "nonce" TEXT NOT NULL,
  "payload" JSONB NOT NULL,
  "signature" TEXT NOT NULL,
  "status" TEXT NOT NULL,
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "expiresAt" TIMESTAMP(3) NOT NULL,
  CONSTRAINT "WalletConnectRequest_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "WalletConnectRequest_nonce_key" ON "WalletConnectRequest"("nonce");

ALTER TABLE "WalletSession" ADD CONSTRAINT "WalletSession_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "WalletConnectRequest" ADD CONSTRAINT "WalletConnectRequest_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
