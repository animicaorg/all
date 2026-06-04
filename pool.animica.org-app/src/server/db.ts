// PrismaClient singleton — Next.js HMR safe.

import { PrismaClient } from "@prisma/client";

declare global {
  // eslint-disable-next-line no-var
  var __animicaGatewayPrisma: PrismaClient | undefined;
}

export const prisma =
  global.__animicaGatewayPrisma ??
  new PrismaClient({
    log: process.env.NODE_ENV === "development" ? ["warn", "error"] : ["error"],
  });

if (process.env.NODE_ENV !== "production") {
  global.__animicaGatewayPrisma = prisma;
}
