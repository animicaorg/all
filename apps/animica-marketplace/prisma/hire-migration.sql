-- CreateTable
CREATE TABLE "HireService" (
    "id" TEXT NOT NULL,
    "slug" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "tagline" TEXT NOT NULL DEFAULT '',
    "description" TEXT NOT NULL DEFAULT '',
    "features" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "icon" TEXT NOT NULL DEFAULT '🛠️',
    "kind" TEXT NOT NULL DEFAULT 'subscription',
    "priceUsdMonthly" INTEGER,
    "setupFeeUsd" INTEGER NOT NULL DEFAULT 0,
    "paypalPlanId" TEXT,
    "active" BOOLEAN NOT NULL DEFAULT true,
    "sortOrder" INTEGER NOT NULL DEFAULT 100,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "HireService_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "HireCustomer" (
    "id" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "passwordHash" TEXT NOT NULL,
    "name" TEXT NOT NULL DEFAULT '',
    "company" TEXT,
    "discord" TEXT,
    "sessionEpoch" INTEGER NOT NULL DEFAULT 1,
    "lastLoginAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "HireCustomer_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "ServicesOrder" (
    "id" TEXT NOT NULL,
    "orderId" TEXT NOT NULL,
    "userId" TEXT,
    "serviceId" TEXT NOT NULL,
    "serviceType" TEXT NOT NULL,
    "serviceName" TEXT NOT NULL,
    "monthlyPriceUsd" INTEGER,
    "setupFeeUsd" INTEGER NOT NULL DEFAULT 0,
    "customerName" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "company" TEXT,
    "domain" TEXT,
    "discordUsername" TEXT,
    "notes" TEXT,
    "paypalPlanId" TEXT,
    "paypalOrderId" TEXT,
    "paypalSubscriptionId" TEXT,
    "paypalTransactionId" TEXT,
    "paymentStatus" TEXT NOT NULL DEFAULT 'draft',
    "deploymentStatus" TEXT NOT NULL DEFAULT 'awaiting_payment',
    "adminNotes" TEXT,
    "customerIp" TEXT,
    "userAgent" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "ServicesOrder_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "HireTicket" (
    "id" TEXT NOT NULL,
    "ticketId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "orderId" TEXT,
    "subject" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'open',
    "priority" TEXT NOT NULL DEFAULT 'normal',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "HireTicket_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "HireTicketMessage" (
    "id" TEXT NOT NULL,
    "ticketId" TEXT NOT NULL,
    "author" TEXT NOT NULL,
    "body" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "HireTicketMessage_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "HireService_slug_key" ON "HireService"("slug");

-- CreateIndex
CREATE UNIQUE INDEX "HireCustomer_email_key" ON "HireCustomer"("email");

-- CreateIndex
CREATE UNIQUE INDEX "ServicesOrder_orderId_key" ON "ServicesOrder"("orderId");

-- CreateIndex
CREATE UNIQUE INDEX "ServicesOrder_paypalSubscriptionId_key" ON "ServicesOrder"("paypalSubscriptionId");

-- CreateIndex
CREATE INDEX "ServicesOrder_email_idx" ON "ServicesOrder"("email");

-- CreateIndex
CREATE INDEX "ServicesOrder_userId_idx" ON "ServicesOrder"("userId");

-- CreateIndex
CREATE INDEX "ServicesOrder_deploymentStatus_idx" ON "ServicesOrder"("deploymentStatus");

-- CreateIndex
CREATE INDEX "ServicesOrder_paymentStatus_createdAt_idx" ON "ServicesOrder"("paymentStatus", "createdAt");

-- CreateIndex
CREATE UNIQUE INDEX "HireTicket_ticketId_key" ON "HireTicket"("ticketId");

-- CreateIndex
CREATE INDEX "HireTicket_userId_updatedAt_idx" ON "HireTicket"("userId", "updatedAt");

-- CreateIndex
CREATE INDEX "HireTicket_status_updatedAt_idx" ON "HireTicket"("status", "updatedAt");

-- CreateIndex
CREATE INDEX "HireTicketMessage_ticketId_createdAt_idx" ON "HireTicketMessage"("ticketId", "createdAt");

-- AddForeignKey
ALTER TABLE "ServicesOrder" ADD CONSTRAINT "ServicesOrder_userId_fkey" FOREIGN KEY ("userId") REFERENCES "HireCustomer"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ServicesOrder" ADD CONSTRAINT "ServicesOrder_serviceId_fkey" FOREIGN KEY ("serviceId") REFERENCES "HireService"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "HireTicket" ADD CONSTRAINT "HireTicket_userId_fkey" FOREIGN KEY ("userId") REFERENCES "HireCustomer"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "HireTicket" ADD CONSTRAINT "HireTicket_orderId_fkey" FOREIGN KEY ("orderId") REFERENCES "ServicesOrder"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "HireTicketMessage" ADD CONSTRAINT "HireTicketMessage_ticketId_fkey" FOREIGN KEY ("ticketId") REFERENCES "HireTicket"("id") ON DELETE CASCADE ON UPDATE CASCADE;

