-- AlterTable
ALTER TABLE "Account" ADD COLUMN     "handle" TEXT,
ADD COLUMN     "websiteUrl" TEXT;

-- CreateIndex
CREATE UNIQUE INDEX "Account_handle_key" ON "Account"("handle");

