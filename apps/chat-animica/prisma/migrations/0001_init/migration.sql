-- CreateTable
CREATE TABLE "User" (
  "id" TEXT NOT NULL,
  "email" TEXT NOT NULL,
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "User_pkey" PRIMARY KEY ("id")
);
CREATE UNIQUE INDEX "User_email_key" ON "User"("email");

CREATE TABLE "Subscription" (
  "id" TEXT NOT NULL,
  "userId" TEXT,
  "subscriptionId" TEXT NOT NULL,
  "status" TEXT NOT NULL,
  "nextBillingTime" TIMESTAMP(3),
  "payerEmail" TEXT,
  "payerId" TEXT,
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMP(3) NOT NULL,
  CONSTRAINT "Subscription_pkey" PRIMARY KEY ("id")
);
CREATE UNIQUE INDEX "Subscription_subscriptionId_key" ON "Subscription"("subscriptionId");

CREATE TABLE "Project" (
  "id" TEXT NOT NULL,
  "userId" TEXT NOT NULL,
  "name" TEXT NOT NULL,
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "Project_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "Thread" (
  "id" TEXT NOT NULL,
  "projectId" TEXT NOT NULL,
  "title" TEXT NOT NULL,
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "Thread_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "Contract" (
  "id" TEXT NOT NULL,
  "projectId" TEXT NOT NULL,
  "threadId" TEXT NOT NULL,
  "source" TEXT NOT NULL,
  "bytecode" TEXT,
  "abiJson" JSONB,
  "manifestJson" JSONB,
  "compileOutput" JSONB,
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "Contract_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "DeployJob" (
  "id" TEXT NOT NULL,
  "userId" TEXT NOT NULL,
  "contractId" TEXT NOT NULL,
  "status" TEXT NOT NULL,
  "txDraft" JSONB,
  "txHash" TEXT,
  "rpcAttempts" JSONB,
  "simulationOutput" JSONB,
  "receipt" JSONB,
  "error" JSONB,
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMP(3) NOT NULL,
  CONSTRAINT "DeployJob_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "UsageDaily" (
  "id" TEXT NOT NULL,
  "userId" TEXT NOT NULL,
  "day" TIMESTAMP(3) NOT NULL,
  "messageCount" INTEGER NOT NULL DEFAULT 0,
  CONSTRAINT "UsageDaily_pkey" PRIMARY KEY ("id")
);
CREATE UNIQUE INDEX "UsageDaily_userId_day_key" ON "UsageDaily"("userId", "day");

ALTER TABLE "Subscription" ADD CONSTRAINT "Subscription_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE SET NULL ON UPDATE CASCADE;
ALTER TABLE "Project" ADD CONSTRAINT "Project_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "Thread" ADD CONSTRAINT "Thread_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "Project"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "Contract" ADD CONSTRAINT "Contract_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "Project"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "Contract" ADD CONSTRAINT "Contract_threadId_fkey" FOREIGN KEY ("threadId") REFERENCES "Thread"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "DeployJob" ADD CONSTRAINT "DeployJob_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "DeployJob" ADD CONSTRAINT "DeployJob_contractId_fkey" FOREIGN KEY ("contractId") REFERENCES "Contract"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "UsageDaily" ADD CONSTRAINT "UsageDaily_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
