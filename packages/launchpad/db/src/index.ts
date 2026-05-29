import { PrismaClient } from "@prisma/client";

declare global {
  // eslint-disable-next-line no-var
  var __launchpadPrisma: PrismaClient | undefined;
}

export const prisma =
  global.__launchpadPrisma ??
  new PrismaClient({
    log: process.env.NODE_ENV === "development" ? ["warn", "error"] : ["error"]
  });

if (process.env.NODE_ENV !== "production") {
  global.__launchpadPrisma = prisma;
}

export * from "@prisma/client";
