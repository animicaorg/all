-- AlterTable
ALTER TABLE "ServicesOrder" ADD COLUMN     "serverEndAt" TIMESTAMP(3),
ADD COLUMN     "serverLabel" TEXT,
ADD COLUMN     "serverStartAt" TIMESTAMP(3);

